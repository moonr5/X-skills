"""Redistribution checks. A license string is not a legal review."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from uaskills import catalog
from uaskills.library import Library
from uaskills.models import SkillRecord
from uaskills.safety import is_within
from uaskills.textutil import append_jsonl, utc_now

# Anthropic's bundled skill license uses this sentence to forbid keeping copies
# outside its own services. Match the phrase, not a generic "proprietary" label.
_FORBIDDEN_MARKER = "extract these materials from the services"


def forbids_redistribution(files: list[tuple[str, bytes]]) -> bool:
    for relative, data in files:
        if not Path(relative).name.lower().startswith("license"):
            continue
        text = data.decode("utf-8", errors="replace").lower()
        if _FORBIDDEN_MARKER in text:
            return True
    return False


def mark_withheld(library: Library, record: SkillRecord) -> None:
    """Record the source link and checksum without storing the skill text."""
    record.description = None
    record.body = ""
    record.body_excerpt = ""
    record.local_path = None
    record.extensions = dict(record.extensions or {})
    record.extensions["redistribution"] = "withheld"
    record.extensions["raw_frontmatter"] = {}
    record.extensions["files"] = []
    record.extensions["withheld_reason"] = (
        "The bundled license forbids retaining copies outside the upstream service. "
        "The catalog stores the source link and checksum only."
    )
    catalog.upsert_skill(library.conn, record, body="")
    _notice(
        library,
        {
            "skill_id": record.id,
            "name": record.name,
            "source_url": record.source_url,
            "action": "withheld",
            "reason": record.extensions["withheld_reason"],
        },
    )
    _write_metadata(library, record)
    library.conn.commit()


def withhold_stored_copies(library: Library) -> list[str]:
    """Remove skill copies already stored when their license forbids retention."""
    root = library.root / "skills"
    if not root.is_dir():
        return []
    removed: list[str] = []
    for license_path in root.rglob("*"):
        if not license_path.is_file() or not license_path.name.lower().startswith("license"):
            continue
        try:
            text = license_path.read_text(encoding="utf-8", errors="replace").lower()
        except OSError:
            continue
        if _FORBIDDEN_MARKER not in text:
            continue
        skill_dir = license_path.parent
        relative = skill_dir.resolve().relative_to(library.root.resolve()).as_posix()
        row = library.conn.execute("SELECT * FROM skills WHERE local_path = ?", (relative,)).fetchone()
        if row is None or not is_within(skill_dir, library.root / "skills"):
            continue
        shutil.rmtree(skill_dir)
        extensions = json.loads(row["extensions_json"] or "{}")
        extensions["redistribution"] = "withheld"
        extensions["raw_frontmatter"] = {}
        extensions["files"] = []
        extensions["withheld_reason"] = (
            "Removed a previously stored copy because the bundled license forbids retaining it."
        )
        library.conn.execute(
            """
            UPDATE skills
            SET description = NULL, body_excerpt = '', local_path = NULL, extensions_json = ?
            WHERE id = ?
            """,
            (json.dumps(extensions, ensure_ascii=False, sort_keys=True), row["id"]),
        )
        library.conn.execute("DELETE FROM skills_fts WHERE skill_id = ?", (row["id"],))
        metadata = library.metadata_path(row["id"])
        if metadata.is_file():
            metadata.unlink()
        _notice(
            library,
            {
                "skill_id": row["id"],
                "name": row["name"],
                "source_url": row["source_url"],
                "action": "withheld",
                "reason": extensions["withheld_reason"],
            },
        )
        removed.append(row["id"])
    library.conn.commit()
    return removed


def _notice(library: Library, record: dict) -> None:
    record = dict(record)
    record["ts"] = utc_now()
    append_jsonl(library.root / "sources" / "third_party_notices.jsonl", record)


def _write_metadata(library: Library, record: SkillRecord) -> None:
    path = library.metadata_path(record.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record.manifest(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
