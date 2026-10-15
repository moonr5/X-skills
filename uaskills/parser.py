"""Parse skill files without inventing missing metadata."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from uaskills.models import SkillRecord
from uaskills.safety import Finding, assess_filename, scan_instructions, should_quarantine
from uaskills.textutil import (
    excerpt,
    looks_like_license_id,
    sha256_bytes,
    split_frontmatter,
    tokenize,
    tree_checksum,
    valid_skill_name,
)

SKILL_MD_NAMES = {"skill.md"}
MANIFEST_NAMES = {"skill.json", "skill.yaml", "skill.yml"}
AGENT_INSTRUCTION_NAMES = {"agents.md", "claude.md", ".cursorrules", "copilot-instructions.md"}


def string_list(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        parts = [part.strip() for part in value.replace(",", " ").split() if part.strip()]
        return parts or None
    if isinstance(value, list):
        items = [str(item).strip() for item in value if str(item).strip()]
        return items or []
    return None


def choose_category(
    declared: str | None,
    relpath: str,
    name: str | None,
    description: str | None,
    tags: list[str] | None,
    categories: list[dict[str, Any]],
) -> tuple[str, str]:
    known = {item["id"] for item in categories}
    if declared and declared in known:
        return declared, "declared"
    segments = {part.lower() for part in relpath.replace("\\", "/").split("/") if part}
    for cid in known:
        if cid in segments and cid != "other":
            return cid, "path"
    haystack = " ".join(
        part
        for part in [name or "", description or "", " ".join(tags or [])]
        if part
    ).lower()
    hay_tokens = set(tokenize(haystack))
    scores: dict[str, int] = {}
    for item in categories:
        if item["id"] == "other":
            continue
        score = 0
        for keyword in item.get("keywords") or []:
            if not keyword:
                continue
            if " " in keyword and keyword in haystack:
                score += 1
            elif keyword in hay_tokens:
                score += 1
        if score:
            scores[item["id"]] = score
    if scores:
        best = max(scores.values())
        leaders = [cid for cid, score in scores.items() if score == best]
        if len(leaders) == 1 and best >= 1:
            return leaders[0], "keywords"
    return "other", "unclassified"


def _license_from_frontmatter(raw: Any, repo_license: str | None, repo_evidence: str | None) -> tuple[str | None, str | None, str]:
    evidence_parts = []
    identifier = None
    status = "unknown"
    if isinstance(raw, str) and raw.strip():
        text = raw.strip()
        evidence_parts.append(f"frontmatter.license: {text}")
        if looks_like_license_id(text):
            identifier = text
        status = "declared"
    if repo_license and not identifier:
        identifier = repo_license
        evidence_parts.append(repo_evidence or "repository license field")
        status = "declared"
    elif repo_license and identifier and repo_license != identifier:
        evidence_parts.append(f"repository license field: {repo_license}")
    if not evidence_parts and repo_evidence:
        evidence_parts.append(repo_evidence)
    return identifier, "; ".join(evidence_parts) or None, status


def _quality(name: str | None, description: str | None, body: str, name_declared: bool) -> str:
    if not body.strip():
        return "missing_instructions"
    if not description:
        return "missing_description"
    if not name or not name_declared:
        return "missing_declared_name"
    return "structurally_complete"


def parse_skill_bytes(
    *,
    skill_key: str,
    relpath: str,
    directory_name: str,
    primary_name: str,
    primary_bytes: bytes,
    files: list[tuple[str, bytes]],
    source_host: str,
    repo_owner: str | None,
    repo_name: str | None,
    repo_url: str | None,
    source_url: str | None,
    commit_sha: str | None,
    origin_path: str | None,
    categories: list[dict[str, Any]],
    repo_license: str | None = None,
    repo_license_evidence: str | None = None,
    source_topics: list[str] | None = None,
    observed_languages: list[str] | None = None,
    timestamp: str,
    excerpt_chars: int = 500,
) -> SkillRecord:
    text = primary_bytes.decode("utf-8-sig", errors="replace")
    findings: list[Finding] = []
    original_format = "agentskills.skill_md"
    frontmatter: dict[str, Any] = {}
    body = text
    instructions = ""
    if primary_name.lower() == "skill.md":
        frontmatter, body, error = split_frontmatter(text)
        instructions = body
        if error:
            findings.append(Finding("frontmatter_error", "warning", error, primary_name))
    elif primary_name.lower().endswith((".yaml", ".yml")):
        original_format = "skill.manifest.yaml"
        frontmatter, _, error = split_frontmatter("---\n" + text + "\n---\n") if not text.startswith("---") else split_frontmatter(text)
        if not text.startswith("---"):
            from uaskills.textutil import _NoAliasLoader
            import yaml

            try:
                loaded = yaml.load(text, Loader=_NoAliasLoader) or {}
                frontmatter = loaded if isinstance(loaded, dict) else {}
                if not isinstance(loaded, dict):
                    findings.append(Finding("manifest_shape", "warning", "YAML manifest is not a mapping", primary_name))
            except yaml.YAMLError as exc:
                findings.append(Finding("manifest_shape", "warning", f"YAML manifest could not be parsed: {exc}", primary_name))
                frontmatter = {}
        instructions = str(frontmatter.get("instructions") or "")
        body = instructions
    else:
        original_format = "skill.manifest.json"
        try:
            loaded = json.loads(text)
        except json.JSONDecodeError as exc:
            loaded = {}
            findings.append(Finding("manifest_shape", "warning", f"JSON manifest could not be parsed: {exc}", primary_name))
        if not isinstance(loaded, dict):
            findings.append(Finding("manifest_shape", "warning", "JSON manifest is not an object", primary_name))
            loaded = {}
        frontmatter = loaded
        instructions = str(frontmatter.get("instructions") or "")
        body = instructions

    metadata = frontmatter.get("metadata") if isinstance(frontmatter.get("metadata"), dict) else {}
    declared_name = frontmatter.get("name")
    name = declared_name.strip() if isinstance(declared_name, str) and declared_name.strip() else None
    name_source = "frontmatter" if name else "directory"
    if name is None:
        name = directory_name
        findings.append(
            Finding(
                "missing_declared_name",
                "warning",
                "SKILL.md has no name field. The directory name is used as an observed label.",
                primary_name,
            )
        )
    elif not valid_skill_name(name):
        findings.append(
            Finding(
                "name_nonconformant",
                "warning",
                "Name does not match the Agent Skills lowercase hyphenated form.",
                primary_name,
            )
        )
    if isinstance(declared_name, str) and name and directory_name and name != directory_name:
        findings.append(
            Finding(
                "name_directory_mismatch",
                "info",
                "Declared name does not match the parent directory name.",
                primary_name,
            )
        )

    description = None
    description_source = None
    raw_description = frontmatter.get("description")
    if isinstance(raw_description, str) and raw_description.strip():
        description = raw_description.strip()
        description_source = "frontmatter"
        if len(description) > 1024:
            findings.append(
                Finding(
                    "description_exceeds_spec",
                    "info",
                    "Description is longer than the 1024 character Agent Skills guidance. The full text is preserved.",
                    primary_name,
                )
            )

    tags = string_list(frontmatter.get("tags"))
    if tags is None and isinstance(metadata, dict):
        tags = string_list(metadata.get("tags"))
    capabilities = string_list(frontmatter.get("capabilities"))
    if capabilities is None and isinstance(metadata, dict):
        capabilities = string_list(metadata.get("capabilities"))
    runtimes = string_list(frontmatter.get("runtimes"))
    if runtimes is None and isinstance(metadata, dict):
        runtimes = string_list(metadata.get("runtimes") or metadata.get("runtime"))
    tools = string_list(frontmatter.get("allowed-tools"))
    if tools is None:
        tools = string_list(frontmatter.get("allowed_tools"))
    dependencies = string_list(frontmatter.get("dependencies"))
    if dependencies is None and isinstance(metadata, dict):
        dependencies = string_list(metadata.get("dependencies"))

    declared_category = None
    for candidate in (frontmatter.get("category"), metadata.get("category") if isinstance(metadata, dict) else None):
        if isinstance(candidate, str) and candidate.strip():
            declared_category = candidate.strip()
            break
    category, category_source = choose_category(
        declared_category, relpath, name, description, tags, categories
    )
    if declared_category and category_source != "declared":
        findings.append(
            Finding(
                "unknown_category",
                "info",
                f"Declared category '{declared_category}' is not in the configured category list.",
                primary_name,
            )
        )

    version = None
    if isinstance(metadata, dict) and metadata.get("version") is not None:
        version = str(metadata.get("version"))
    elif frontmatter.get("version") is not None and not isinstance(frontmatter.get("version"), (dict, list)):
        version = str(frontmatter.get("version"))

    compatibility = frontmatter.get("compatibility")
    compatibility_text = compatibility.strip() if isinstance(compatibility, str) and compatibility.strip() else None
    if compatibility_text and len(compatibility_text) > 500:
        findings.append(
            Finding(
                "compatibility_exceeds_spec",
                "info",
                "Compatibility text is longer than the 500 character Agent Skills guidance.",
                primary_name,
            )
        )
    compatibility_status = "declared" if compatibility_text or runtimes else "unknown"

    deprecated_flag = frontmatter.get("deprecated")
    if deprecated_flag is None and isinstance(metadata, dict):
        deprecated_flag = metadata.get("deprecated")
    if deprecated_flag is True or (isinstance(deprecated_flag, str) and deprecated_flag.lower() == "true"):
        deprecation_status = "deprecated"
    elif deprecated_flag is False:
        deprecation_status = "active"
    else:
        deprecation_status = "unknown"

    identifier, evidence, license_status = _license_from_frontmatter(
        frontmatter.get("license"), repo_license, repo_license_evidence
    )
    license_files = [rel for rel, _ in files if Path(rel).name.lower().startswith("license")]
    if license_files:
        extra = "license file: " + ", ".join(license_files)
        evidence = f"{evidence}; {extra}" if evidence else extra
        if license_status == "unknown":
            license_status = "declared"

    for rel, _data in files:
        findings.extend(assess_filename(rel))
    findings.extend(scan_instructions(text, primary_name))
    quarantine = should_quarantine(findings)
    security_status = "screened"
    lifecycle = "quarantined" if quarantine else "parsed"
    if deprecation_status == "deprecated" and lifecycle != "quarantined":
        lifecycle = "deprecated"

    extensions = {
        "name_source": name_source,
        "description_source": description_source,
        "category_source": category_source,
        "declared_category_raw": declared_category,
        "source_topics": source_topics or [],
        "observed_script_languages": observed_languages or [],
        "primary_file": primary_name,
        "files": [rel for rel, _ in files],
        "raw_frontmatter": frontmatter,
        "upstream_presence": "present",
    }
    record = SkillRecord(
        id=skill_key,
        name=name,
        description=description,
        source_url=source_url,
        source_host=source_host,
        repo_owner=repo_owner,
        repo_name=repo_name,
        repo_url=repo_url,
        commit_sha=commit_sha,
        version=version,
        local_path=None,
        origin_path=origin_path,
        skill_relpath=relpath.replace("\\", "/"),
        original_format=original_format,
        category=category,
        license_identifier=identifier,
        license_evidence=evidence,
        license_status=license_status,
        content_checksum=sha256_bytes(primary_bytes),
        tree_checksum=tree_checksum(files),
        import_timestamp=timestamp,
        update_timestamp=timestamp,
        security_scan_status=security_status,
        security_findings=[item.as_dict() for item in findings],
        compatibility_status=compatibility_status,
        compatibility=compatibility_text,
        quality_assessment=_quality(name, description, instructions or body, name_source == "frontmatter"),
        deprecation_status=deprecation_status,
        lifecycle_status=lifecycle,
        body_excerpt=excerpt(instructions or body, excerpt_chars),
        capabilities=capabilities,
        supported_runtimes=runtimes,
        required_tools=tools,
        dependencies=dependencies,
        tags=tags,
        extensions=extensions,
        body=instructions or body,
    )
    return record
