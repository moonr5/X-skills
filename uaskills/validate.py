"""Structural validation. A passing scan is not a safety or legal review."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jsonschema

from uaskills import catalog
from uaskills.library import Library
from uaskills.safety import Finding, assess_filename, is_within, safe_join, scan_instructions
from uaskills.textutil import local_markdown_links, utc_now


@dataclass
class Issue:
    severity: str
    code: str
    message: str
    skill_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "skill_id": self.skill_id,
        }


@dataclass
class ValidationReport:
    skills_checked: int = 0
    issues: list[Issue] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        errors = [item for item in self.issues if item.severity == "error"]
        warnings = [item for item in self.issues if item.severity != "error"]
        return {
            "skills_checked": self.skills_checked,
            "errors": len(errors),
            "warnings": len(warnings),
            "issues": [item.as_dict() for item in self.issues],
        }


def validate_library(library: Library, *, runtime: str | None = None) -> ValidationReport:
    report = ValidationReport()
    schema = json.loads(library.schema_file("skill-manifest.schema.json").read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    seen_checksums: dict[str, str] = {}
    batch = 0
    for skill in catalog.iter_skills(library.conn, synthetic=False, batch=100):
        report.skills_checked += 1
        report.issues.extend(_validate_skill(library, skill, validator, runtime, seen_checksums))
        batch += 1
        if batch >= 100:
            library.conn.commit()
            batch = 0
    library.conn.commit()
    return report


def _validate_skill(library: Library, skill: dict[str, Any], validator, runtime: str | None, seen: dict[str, str]) -> list[Issue]:
    issues: list[Issue] = []
    skill_id = skill["id"]
    manifest = catalog.manifest_from_row(skill)
    errors = sorted(validator.iter_errors(manifest), key=lambda item: list(item.path))
    for error in errors:
        issues.append(Issue("error", "invalid_manifest", error.message, skill_id))
    if skill["lifecycle_status"] == "quarantined":
        issues.append(Issue("error", "quarantined", "Skill is quarantined and is excluded from default search.", skill_id))
    if skill["license_status"] == "unknown":
        issues.append(
            Issue(
                "warning",
                "license_unknown",
                "No license identifier or license file was found. This is not a legal review.",
                skill_id,
            )
        )
    if skill["quality_assessment"] in {"missing_description", "missing_declared_name"}:
        issues.append(
            Issue(
                "warning",
                skill["quality_assessment"],
                "Required skill metadata is incomplete. Missing values were left unknown.",
                skill_id,
            )
        )
    local = skill.get("local_path")
    skill_dir = (library.root / local) if local else None
    primary = _primary_path(skill_dir, skill) if skill_dir else None
    if skill_dir is None or not skill_dir.is_dir() or primary is None or not primary.is_file():
        issues.append(Issue("error", "missing_files", "Skill files are not present at the recorded local path.", skill_id))
        _update_scan(library, skill, issues)
        return issues
    if not is_within(skill_dir, library.root):
        issues.append(Issue("error", "unsafe_path", "Recorded skill path is outside the library.", skill_id))
        return issues
    text = primary.read_text(encoding="utf-8", errors="replace")
    body = skill.get("body_excerpt") or ""
    if skill["quality_assessment"] == "missing_instructions" or not text.strip():
        issues.append(Issue("error", "empty_instructions", "Skill instructions are missing or empty.", skill_id))
    for link in local_markdown_links(text):
        try:
            target = safe_join(skill_dir, link)
        except Exception as exc:
            issues.append(Issue("error", "unsafe_reference", str(exc), skill_id))
            continue
        if not target.is_file():
            issues.append(Issue("warning", "broken_reference", f"Relative reference was not found: {link}", skill_id))
    for dependency in skill.get("dependencies") or []:
        if _looks_like_file(dependency):
            try:
                target = safe_join(skill_dir, dependency)
            except Exception:
                issues.append(Issue("warning", "missing_dependency", f"Declared dependency path is unsafe: {dependency}", skill_id))
                continue
            if not target.exists():
                issues.append(Issue("warning", "missing_dependency", f"Declared dependency file was not found: {dependency}", skill_id))
    findings: list[Finding] = []
    for relative in (skill.get("extensions") or {}).get("files") or []:
        try:
            safe_join(skill_dir, relative)
        except Exception as exc:
            issues.append(Issue("error", "unsafe_path", str(exc), skill_id))
            continue
        findings.extend(assess_filename(relative))
        candidate = skill_dir / relative
        if candidate.is_file() and candidate.suffix.lower() in {".md", ".txt", ".py", ".sh", ".ps1", ".json", ".yaml", ".yml"}:
            sample = candidate.read_text(encoding="utf-8", errors="replace")[:200_000]
            findings.extend(scan_instructions(sample, relative))
    findings.extend(scan_instructions(text, primary.name))
    for finding in findings:
        severity = "error" if finding.severity == "high" and finding.id == "executable_file" else "warning"
        issues.append(Issue(severity, finding.id, finding.message, skill_id))
    if runtime:
        declared = [item.lower() for item in (skill.get("supported_runtimes") or [])]
        if declared and runtime.lower() not in declared:
            issues.append(Issue("warning", "runtime_mismatch", f"Skill does not declare runtime '{runtime}'.", skill_id))
        elif not declared:
            issues.append(Issue("warning", "runtime_unknown", "Skill does not declare a supported runtime.", skill_id))
    checksum = skill.get("content_checksum")
    if checksum:
        previous = seen.get(checksum)
        if previous and previous != skill_id:
            issues.append(Issue("warning", "duplicate_content", f"Instruction checksum matches {previous}.", skill_id))
        else:
            seen[checksum] = skill_id
    upstream = (skill.get("extensions") or {}).get("upstream_presence")
    if upstream == "missing":
        issues.append(Issue("warning", "missing_upstream", "The skill path was not present in the latest source tree.", skill_id))
    _update_scan(library, skill, issues, findings)
    return issues


def _update_scan(library: Library, skill: dict[str, Any], issues: list[Issue], findings: list[Finding] | None = None) -> None:
    """Record that a heuristic scan ran. This does not mark the skill verified or safe."""
    payload = [item.as_dict() for item in (findings or [])]
    library.conn.execute(
        """
        UPDATE skills
        SET security_scan_status = 'screened',
            security_findings_json = ?,
            update_timestamp = ?
        WHERE id = ? AND verification_status != 'verified'
        """,
        (json.dumps(payload, ensure_ascii=False), utc_now(), skill["id"]),
    )
    # Keep a verified skill's human status, but still store the latest heuristic findings.
    if skill.get("verification_status") == "verified":
        library.conn.execute(
            "UPDATE skills SET security_findings_json = ? WHERE id = ?",
            (json.dumps(payload, ensure_ascii=False), skill["id"]),
        )


def _primary_path(skill_dir: Path | None, skill: dict[str, Any]) -> Path | None:
    if skill_dir is None:
        return None
    primary = (skill.get("extensions") or {}).get("primary_file") or "SKILL.md"
    direct = skill_dir / primary
    if direct.is_file():
        return direct
    for candidate in skill_dir.rglob("*"):
        if candidate.is_file() and candidate.name.lower() in {"skill.md", "skill.json", "skill.yaml", "skill.yml"}:
            return candidate
    return None


def _looks_like_file(value: str) -> bool:
    return "/" in value or "\\" in value or value.lower().endswith((".py", ".md", ".txt", ".json", ".yaml", ".yml", ".sh"))
