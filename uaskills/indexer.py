"""Rebuild the SQLite full-text index from stored files. This does not delete skill files."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from uaskills import catalog
from uaskills.importer import import_local_directory
from uaskills.library import Library
from uaskills.parser import MANIFEST_NAMES, SKILL_MD_NAMES
from uaskills.textutil import utc_now


def rebuild_index(library: Library, *, from_files: bool = False) -> dict[str, Any]:
    restored = 0
    missing = 0
    if from_files:
        restored, missing = _restore_from_metadata(library)
        _import_untracked(library)
    indexed = catalog.rebuild_fts(
        library.conn,
        lambda skill: _body_for(library, skill),
        batch=int(library.limit("index_batch_size")),
    )
    storage = storage_report(library)
    summary = catalog.counts(library.conn)
    _write_skills_index(library, summary, storage)
    library.conn.commit()
    return {
        "indexed": indexed,
        "restored_from_metadata": restored,
        "missing_files": missing,
        "counts": summary,
        "storage": storage,
    }


def _body_for(library: Library, skill: dict[str, Any]) -> str:
    local = skill.get("local_path")
    if not local:
        return skill.get("body_excerpt") or ""
    directory = library.root / local
    if not directory.is_dir():
        return skill.get("body_excerpt") or ""
    primary_name = (skill.get("extensions") or {}).get("primary_file") or "SKILL.md"
    candidate = directory / primary_name
    if not candidate.is_file():
        for child in directory.rglob("*"):
            if child.is_file() and child.name.lower() in SKILL_MD_NAMES | MANIFEST_NAMES:
                candidate = child
                break
    if not candidate.is_file():
        return skill.get("body_excerpt") or ""
    cap = int(library.limit("body_index_max_bytes"))
    data = candidate.read_bytes()[:cap]
    return data.decode("utf-8", errors="replace")


def _restore_from_metadata(library: Library) -> tuple[int, int]:
    """Reinsert catalog rows from metadata sidecars. Skill directories are left in place."""
    restored = 0
    missing = 0
    meta_root = library.root / "index" / "metadata"
    if not meta_root.is_dir():
        return 0, 0
    for path in sorted(meta_root.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        local = data.get("local_path")
        skill_key = data.get("id")
        if not local or not skill_key:
            continue
        if not (library.root / local).is_dir():
            missing += 1
            continue
        if catalog.get_skill(library.conn, skill_key):
            continue
        _insert_from_manifest(library, data)
        restored += 1
    library.conn.commit()
    return restored, missing


def _insert_from_manifest(library: Library, data: dict[str, Any]) -> None:
    repository = data.get("repository") or {}
    license_info = data.get("license") or {}
    extensions = data.get("extensions") or {}
    library.conn.execute(
        """
        INSERT INTO skills (
            id, name, description, source_url, source_host, repo_owner, repo_name, repo_url,
            commit_sha, version, local_path, origin_path, skill_relpath, original_format,
            category, license_identifier, license_evidence, license_status, content_checksum,
            tree_checksum, import_timestamp, update_timestamp, verification_status,
            security_scan_status, security_findings_json, compatibility_status, compatibility,
            quality_assessment, deprecation_status, lifecycle_status, resource_kind,
            is_synthetic, duplicate_of, canonical, body_excerpt, capabilities_json,
            runtimes_json, tools_json, dependencies_json, tags_json, extensions_json
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        """,
        (
            data.get("id"),
            data.get("name"),
            data.get("description"),
            data.get("source_url"),
            repository.get("host"),
            repository.get("owner"),
            repository.get("name"),
            repository.get("url"),
            data.get("commit_sha"),
            data.get("version"),
            data.get("local_path"),
            data.get("origin_path"),
            data.get("skill_relpath"),
            data.get("original_format"),
            data.get("category"),
            license_info.get("identifier"),
            license_info.get("evidence"),
            license_info.get("status") or "unknown",
            data.get("content_checksum"),
            data.get("tree_checksum"),
            data.get("import_timestamp"),
            utc_now(),
            data.get("verification_status") or "unverified",
            data.get("security_scan_status") or "not_scanned",
            json.dumps(data.get("security_findings") or []),
            data.get("compatibility_status") or "unknown",
            data.get("compatibility"),
            data.get("quality_assessment") or "unassessed",
            data.get("deprecation_status") or "unknown",
            data.get("lifecycle_status") or "parsed",
            data.get("resource_kind") or "skill",
            1 if data.get("is_synthetic") else 0,
            data.get("duplicate_of"),
            0 if data.get("duplicate_of") else 1,
            "",
            json.dumps(data.get("capabilities")),
            json.dumps(data.get("supported_runtimes")),
            json.dumps(data.get("required_tools")),
            json.dumps(data.get("dependencies")),
            json.dumps(data.get("tags")),
            json.dumps(extensions),
        ),
    )


def _import_untracked(library: Library) -> None:
    known = {
        row["local_path"]
        for row in library.conn.execute("SELECT local_path FROM skills WHERE local_path IS NOT NULL")
    }
    found: list[Path] = []
    for base_name in ("skills", "quarantine"):
        base = library.root / base_name
        if not base.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [name for name in dirnames if name not in {".git", "node_modules"}]
            if not any(name.lower() in SKILL_MD_NAMES or name.lower() in MANIFEST_NAMES for name in filenames):
                continue
            relative = Path(dirpath).resolve().relative_to(library.root.resolve()).as_posix()
            if relative in known:
                continue
            found.append(Path(dirpath))
    for directory in found:
        import_local_directory(library, directory, job_kind="rebuild-untracked")


def storage_report(library: Library) -> dict[str, Any]:
    report = {}
    for name in ("skills", "quarantine", "index"):
        files, total = _directory_usage(library.root / name)
        report[name] = {"files": files, "bytes": total}
    database = library.root / "index" / "skills.sqlite"
    report["database_bytes"] = database.stat().st_size if database.is_file() else 0
    return report


def _directory_usage(path: Path) -> tuple[int, int]:
    if not path.is_dir():
        return 0, 0
    files = 0
    total = 0
    for dirpath, _dirnames, filenames in os.walk(path):
        for name in filenames:
            files += 1
            try:
                total += (Path(dirpath) / name).stat().st_size
            except OSError:
                continue
    return files, total


def _write_skills_index(library: Library, summary: dict[str, Any], storage: dict[str, Any]) -> None:
    lines = [
        "# Skills index",
        "",
        "Generated by `python tools/build_index.py`. This is a summary, not a dump of every skill.",
        "Search with `python tools/search_skills.py` so an agent loads only the skills a task needs.",
        "",
        f"- Genuine skills with local files: {summary.get('stored_skills', summary['skills'])}",
        f"- Withheld for license (source link only): {summary.get('withheld', 0)}",
        f"- Quarantined: {summary['quarantined']}",
        f"- Duplicate records hidden from default search: {summary['duplicates']}",
        f"- Non-skill resources: {summary['resources']}",
        f"- Synthetic test rows: {summary['synthetic']}",
        "",
        "## Categories",
        "",
    ]
    if summary["by_category"]:
        for category, count in sorted(summary["by_category"].items()):
            lines.append(f"- {category}: {count}")
    else:
        lines.append("No skills have been imported.")
    lines.extend(["", "## Lifecycle", ""])
    if summary["by_lifecycle"]:
        for status, count in sorted(summary["by_lifecycle"].items()):
            lines.append(f"- {status}: {count}")
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Storage",
            "",
            f"- Skill files: {storage['skills']['files']} files, {storage['skills']['bytes']} bytes",
            f"- Quarantine: {storage['quarantine']['files']} files, {storage['quarantine']['bytes']} bytes",
            f"- Index directory: {storage['index']['files']} files, {storage['index']['bytes']} bytes",
            f"- SQLite database: {storage['database_bytes']} bytes",
            "",
            "Synthetic rows, when present, exist only in a test database and are excluded from normal search.",
            "A license status of `declared` means a license string or file was seen. It does not mean the license was reviewed.",
            "A security status of `screened` means a heuristic scan ran. It does not mean the skill is safe.",
            "",
        ]
    )
    sample = library.conn.execute(
        """
        SELECT name, category, lifecycle_status, license_status, local_path, source_url
        FROM skills
        WHERE is_synthetic = 0
        ORDER BY name COLLATE NOCASE ASC
        LIMIT 50
        """
    ).fetchall()
    lines.extend(["## Sample", ""])
    if not sample:
        lines.append("Import a source to populate this list.")
    else:
        lines.append("| Name | Category | Lifecycle | License | Path |")
        lines.append("| --- | --- | --- | --- | --- |")
        for row in sample:
            lines.append(
                f"| {row['name'] or ''} | {row['category'] or ''} | {row['lifecycle_status']} | "
                f"{row['license_status']} | `{row['local_path'] or ''}` |"
            )
        if summary["skills"] > len(sample):
            lines.append("")
            lines.append(f"Showing 50 of {summary['skills']} skills. Use search for the rest.")
    lines.append("")
    (library.root / "SKILLS_INDEX.md").write_text("\n".join(lines), encoding="utf-8")
