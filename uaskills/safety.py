"""Path, archive, and content checks. These checks do not execute files."""

from __future__ import annotations

import io
import os
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from uaskills.errors import UnsafeArchive, UnsafePath

SKIP_DIR_NAMES = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "node_modules",
    "site-packages",
    "venv",
    "dist",
    "build",
}

WINDOWS_RESERVED = {
    "con",
    "prn",
    "aux",
    "nul",
    "com1",
    "com2",
    "com3",
    "com4",
    "lpt1",
    "lpt2",
    "lpt3",
}

QUARANTINE_EXTENSIONS = {".exe", ".dll", ".scr", ".com", ".msi", ".sys", ".drv", ".bat", ".cmd", ".vbs"}
SCRIPT_EXTENSIONS = {".ps1", ".sh", ".bash", ".command"}

_INSTRUCTION_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    ("pipe_to_shell", re.compile(r"(?i)(curl|wget)\b[^\n]{0,120}\|\s*(ba)?sh\b"), "high"),
    ("rm_rf_root", re.compile(r"(?i)\brm\s+-rf\s+/(?:\s|$)"), "high"),
    (
        "credential_theft",
        re.compile(r"(?i)\b(exfiltrate|steal|harvest)\b[^\n]{0,60}\b(secrets?|credentials?|tokens?|passwords?)\b"),
        "high",
    ),
    (
        "ignore_instructions",
        re.compile(r"(?i)\bignore\b[^\n]{0,40}\b(all|any|previous|prior|above)\b[^\n]{0,20}\b(instructions|rules|prompts)\b"),
        "warning",
    ),
    (
        "disable_safety",
        re.compile(r"(?i)\b(disable|bypass|turn off)\b[^\n]{0,40}\b(safety|guardrails|sandbox|security checks)\b"),
        "warning",
    ),
]


@dataclass
class Finding:
    id: str
    severity: str
    message: str
    path: str | None = None

    def as_dict(self) -> dict[str, str | None]:
        return {"id": self.id, "severity": self.severity, "message": self.message, "path": self.path}


@dataclass
class ExtractReport:
    files: list[str] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)


def safe_segment(name: str, fallback: str = "unnamed") -> str:
    cleaned = name.strip().replace("\\", "/").split("/")[-1]
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", cleaned).strip(".- ")
    if not cleaned:
        cleaned = fallback
    if cleaned.lower() in WINDOWS_RESERVED or cleaned in {".", ".."}:
        cleaned = f"_{cleaned}"
    return cleaned[:80]


def is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except (ValueError, OSError):
        return False
    return True


def safe_join(root: Path, relative: str) -> Path:
    if "\x00" in relative:
        raise UnsafePath("path contains a null byte")
    relative = relative.replace("\\", "/")
    if relative.startswith("/") or re.match(r"^[A-Za-z]:", relative):
        raise UnsafePath(f"absolute path is not allowed: {relative}")
    parts = [part for part in relative.split("/") if part not in {"", "."}]
    if any(part == ".." for part in parts):
        raise UnsafePath(f"path escapes its root: {relative}")
    candidate = root.joinpath(*parts)
    if not is_within(candidate, root):
        raise UnsafePath(f"path escapes its root: {relative}")
    return candidate


def safe_extract_zip(archive: zipfile.ZipFile, dest: Path, limits: dict) -> ExtractReport:
    """Extract members that pass path, size, and compression checks.

    The whole archive is rejected if any member fails. Nothing is executed.
    """
    dest = dest.resolve()
    dest.mkdir(parents=True, exist_ok=True)
    members = archive.infolist()
    if len(members) > int(limits["max_archive_files"]):
        raise UnsafeArchive(f"archive has {len(members)} members; limit is {limits['max_archive_files']}")
    planned: list[tuple[zipfile.ZipInfo, Path, str]] = []
    total = 0
    max_file = int(limits["max_file_bytes"])
    max_total = int(limits["max_archive_uncompressed_bytes"])
    max_ratio = float(limits["max_compression_ratio"])
    for info in members:
        name = info.filename.replace("\\", "/")
        if name.endswith("/"):
            continue
        if "\x00" in name or name.startswith("/") or re.match(r"^[A-Za-z]:", name):
            raise UnsafeArchive(f"unsafe archive path: {name}")
        parts = [part for part in name.split("/") if part not in {"", "."}]
        if not parts or any(part == ".." for part in parts):
            raise UnsafeArchive(f"unsafe archive path: {name}")
        declared = int(info.file_size)
        compressed = int(info.compress_size)
        if declared > max_file:
            raise UnsafeArchive(f"archive member exceeds file size limit: {name}")
        if compressed > 0 and (declared / compressed) > max_ratio:
            raise UnsafeArchive(f"archive member compression ratio is too high: {name}")
        total += declared
        if total > max_total:
            raise UnsafeArchive("archive uncompressed size exceeds the configured limit")
        target = dest.joinpath(*[safe_segment(part) for part in parts])
        if not is_within(target, dest):
            raise UnsafeArchive(f"archive member escapes destination: {name}")
        planned.append((info, target, "/".join(parts)))
    report = ExtractReport()
    for info, target, rel in planned:
        target.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        with archive.open(info, "r") as source, target.open("wb") as output:
            while True:
                chunk = source.read(65_536)
                if not chunk:
                    break
                written += len(chunk)
                if written > max_file:
                    output.close()
                    target.unlink(missing_ok=True)
                    raise UnsafeArchive(f"archive member grew past the file size limit: {rel}")
                output.write(chunk)
        report.files.append(rel)
    return report


def open_zip_bytes(data: bytes, limits: dict) -> zipfile.ZipFile:
    if len(data) > int(limits["max_archive_bytes"]):
        raise UnsafeArchive("archive download exceeds the configured size limit")
    try:
        return zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise UnsafeArchive("file is not a valid zip archive") from exc


def scan_instructions(text: str, path: str | None = None) -> list[Finding]:
    findings = []
    for finding_id, pattern, severity in _INSTRUCTION_PATTERNS:
        if pattern.search(text):
            findings.append(
                Finding(
                    id=finding_id,
                    severity=severity,
                    message=(
                        "Heuristic pattern matched. This is not proof that the skill is malicious "
                        f"or safe ({finding_id})."
                    ),
                    path=path,
                )
            )
    return findings


def assess_filename(relative: str) -> list[Finding]:
    suffix = Path(relative).suffix.lower()
    findings = []
    if suffix in QUARANTINE_EXTENSIONS:
        findings.append(
            Finding(
                id="executable_file",
                severity="high",
                message=f"File extension {suffix} is treated as an executable payload and quarantined.",
                path=relative,
            )
        )
    elif suffix in SCRIPT_EXTENSIONS:
        findings.append(
            Finding(
                id="script_file",
                severity="warning",
                message=(
                    f"Script file {relative} was stored but not executed. "
                    "Review it before an agent runs it."
                ),
                path=relative,
            )
        )
    return findings


def should_quarantine(findings: list[Finding]) -> bool:
    return any(item.id in {"executable_file", "path_traversal", "symlink_escape"} and item.severity == "high" for item in findings)


def read_regular_file(path: Path, root: Path, max_bytes: int) -> bytes:
    if "\x00" in str(path):
        raise UnsafePath("path contains a null byte")
    if path.is_symlink():
        try:
            target = path.resolve()
        except OSError as exc:
            raise UnsafePath(f"symlink could not be resolved: {path}") from exc
        if not is_within(target, root):
            raise UnsafePath(f"symlink escapes the source root: {path}")
    if not path.is_file():
        raise UnsafePath(f"not a regular file: {path}")
    size = path.stat().st_size
    if size > max_bytes:
        from uaskills.errors import FileTooLarge

        raise FileTooLarge(f"file exceeds {max_bytes} bytes: {path.name}")
    return path.read_bytes()


def walk_source(root: Path, max_depth: int):
    """Yield (directory, filenames) without following directory symlinks."""
    root = root.resolve()
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(dirpath)
        try:
            depth = len(current.resolve().relative_to(root).parts)
        except ValueError:
            dirnames[:] = []
            continue
        dirnames[:] = [name for name in dirnames if name not in SKIP_DIR_NAMES and name != ".git"]
        if depth >= max_depth:
            dirnames[:] = []
        yield current, filenames
