"""Copy selected skills into another agent's directory layout.

Provider layouts are isolated here. The catalog itself stays vendor-neutral.
Conflict policy defaults to keeping a backup of any existing target skill.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from uaskills.errors import LibraryError
from uaskills.library import Library
from uaskills.safety import is_within, safe_segment

ADAPTERS: dict[str, dict[str, str]] = {
    "cursor": {
        "layout": ".cursor/skills/{name}",
        "summary": "Project skills for Cursor live in .cursor/skills/<name>/SKILL.md.",
        "manual": "Personal Cursor skills live in ~/.cursor/skills/. Cursor does not automatically read this library's skills/ tree.",
    },
    "claude": {
        "layout": ".claude/skills/{name}",
        "summary": "Claude Code project skills are SKILL.md directories. This adapter uses .claude/skills/<name>/.",
        "manual": "Claude can also install skills through a plugin marketplace. If automatic discovery is unavailable, paste the skill's name and description, then read the one SKILL.md you selected.",
    },
    "codex": {
        "layout": ".agents/skills/{name}",
        "summary": "Codex reads repository skills from .agents/skills and user skills from ~/.agents/skills.",
        "manual": "Codex also reads AGENTS.md, which is a project instruction file rather than a skill. Do not collapse this library into one AGENTS.md.",
    },
    "generic": {
        "layout": "skills/{name}",
        "summary": "A vendor-neutral skills/<name>/SKILL.md directory.",
        "manual": "Point the agent at MASTER_SKILL.md and the search command. If it cannot run commands, give it the selected skill paths one at a time.",
    },
    "gemini": {
        "layout": ".gemini/skills/{name}",
        "summary": "Gemini CLI workspace skills live in .gemini/skills/<name>/SKILL.md. .agents/skills is also scanned.",
        "manual": "User skills can live in ~/.gemini/skills or ~/.agents/skills. Gemini CLI does not read this library's skills/ tree automatically.",
    },
    "copilot": {
        "layout": ".github/skills/{name}",
        "summary": "GitHub Copilot project skills live in .github/skills/<name>/SKILL.md. .agents/skills and .claude/skills are also scanned.",
        "manual": "Personal Copilot skills use ~/.copilot/skills or ~/.agents/skills. Copilot instructions in .github/copilot-instructions.md are not skills.",
    },
}


@dataclass
class ExportReport:
    exported: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    backups: list[str] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "exported": self.exported,
            "skipped": self.skipped,
            "backups": self.backups,
            "messages": self.messages,
        }


def export_skills(
    library: Library,
    skills: list[dict[str, Any]],
    *,
    target: str,
    dest: Path,
    on_conflict: str = "backup",
) -> ExportReport:
    if target not in ADAPTERS:
        raise LibraryError(f"Unknown adapter '{target}'. Choose from: {', '.join(sorted(ADAPTERS))}")
    if on_conflict not in {"backup", "skip", "overwrite"}:
        raise LibraryError("Conflict policy must be backup, skip, or overwrite.")
    report = ExportReport()
    dest = dest.expanduser().resolve()
    dest.mkdir(parents=True, exist_ok=True)
    used_names: set[str] = set()
    for skill in skills:
        source = _skill_dir(library, skill)
        if source is None:
            report.skipped.append(skill.get("id") or "")
            report.messages.append(f"Missing files for {skill.get('name') or skill.get('id')}.")
            continue
        folder = _folder_name(skill, used_names)
        relative = ADAPTERS[target]["layout"].format(name=folder)
        target_dir = dest.joinpath(*relative.split("/"))
        if target_dir.exists():
            if on_conflict == "skip":
                report.skipped.append(str(target_dir))
                report.messages.append(f"Left existing skill in place: {target_dir}")
                continue
            backup = _backup_dir(target_dir)
            shutil.copytree(target_dir, backup)
            report.backups.append(str(backup))
            if on_conflict == "overwrite":
                shutil.rmtree(target_dir)
            else:
                shutil.rmtree(target_dir)
        shutil.copytree(source, target_dir)
        report.exported.append(str(target_dir))
    if not skills:
        report.messages.append("No skills were selected to export.")
    return report


def _skill_dir(library: Library, skill: dict[str, Any]) -> Path | None:
    local = skill.get("local_path")
    if not local:
        return None
    path = (library.root / local).resolve()
    if not path.is_dir():
        return None
    if not (is_within(path, library.root / "skills") or is_within(path, library.root / "quarantine")):
        return None
    return path


def _folder_name(skill: dict[str, Any], used: set[str]) -> str:
    from uaskills.textutil import valid_skill_name

    raw = skill.get("name") or skill.get("id") or "skill"
    name = raw if isinstance(raw, str) and valid_skill_name(raw) else safe_segment(str(raw)).lower()
    name = name.strip("-") or "skill"
    if name in used:
        suffix = str(skill.get("id") or "copy")[:8]
        name = f"{name[:50]}-{suffix}".strip("-")
    used.add(name)
    return name


def _backup_dir(path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return path.with_name(path.name + f".bak-{stamp}")
