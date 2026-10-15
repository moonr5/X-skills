"""Parse user-supplied repository locators. Only GitHub and local paths are accepted."""

from __future__ import annotations

import re
from pathlib import Path

from uaskills.errors import LibraryError

_NAME = re.compile(r"[A-Za-z0-9_.-]+")


def parse_github_locator(text: str) -> tuple[str, str, str | None]:
    """Return owner, repository, and an optional subdirectory."""
    raw = text.strip()
    if not raw or raw.startswith("#"):
        raise LibraryError("Empty repository locator")
    raw = raw.removesuffix(".git")
    raw = raw.split("?", 1)[0].split("#", 1)[0].rstrip("/")
    lowered = raw.lower()
    for prefix in ("https://github.com/", "http://github.com/", "github.com/"):
        if lowered.startswith(prefix):
            raw = raw[len(prefix) :]
            break
    else:
        if "://" in raw:
            raise LibraryError("Only github.com repository URLs are supported")
    parts = [part for part in raw.split("/") if part]
    if len(parts) < 2 or not _NAME.fullmatch(parts[0]) or not _NAME.fullmatch(parts[1]):
        raise LibraryError(f"Not a GitHub owner/repository locator: {text}")
    if parts[0] in {".", ".."} or parts[1] in {".", ".."}:
        raise LibraryError(f"Invalid repository name: {text}")
    subpath = None
    if len(parts) > 2:
        marker = parts[2]
        if marker in {"tree", "blob"} and len(parts) >= 4:
            extra = parts[4:]
            if marker == "blob" and extra:
                extra = extra[:-1]
            subpath = "/".join(extra) or None
        else:
            subpath = "/".join(parts[2:])
    if subpath and (".." in subpath.split("/") or subpath.startswith("/")):
        raise LibraryError(f"Unsafe repository path: {subpath}")
    return parts[0], parts[1], subpath


def read_locator_file(path: Path) -> list[str]:
    if not path.is_file():
        raise LibraryError(f"Source file not found: {path}")
    lines = []
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        lines.append(line)
    return lines
