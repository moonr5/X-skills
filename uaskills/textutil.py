"""Text helpers: frontmatter, checksums, redaction, and FTS queries."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from uaskills.errors import ConfigError

_TOKEN_RE = re.compile(
    r"(?i)(token|authorization|api[_-]?key|password|secret)([=:\s]+)(\S+)"
)
_GITHUB_TOKEN_RE = re.compile(r"ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}")
_FTS_TOKEN_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_-]{1,}")
_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "in",
    "into",
    "of",
    "on",
    "or",
    "the",
    "to",
    "with",
    "using",
    "use",
    "via",
    "that",
    "this",
    "it",
    "is",
    "my",
    "our",
    "your",
}
_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
_SPDX_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+-]{1,63}$")
_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class _NoAliasLoader(yaml.SafeLoader):
    def ignore_aliases(self, data: Any) -> bool:
        return True


def redact(text: str) -> str:
    text = _GITHUB_TOKEN_RE.sub("[REDACTED]", text)
    return _TOKEN_RE.sub(lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", text)


def redact_obj(value: Any) -> Any:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {str(key): redact_obj(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_obj(item) for item in value]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return redact(str(value))


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def tree_checksum(files: list[tuple[str, bytes]]) -> str:
    digest = hashlib.sha256()
    for rel, data in sorted(files, key=lambda item: item[0]):
        digest.update(rel.replace("\\", "/").encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(data).digest())
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def skill_id(host: str, owner: str, repo: str, relpath: str) -> str:
    raw = "\n".join([host or "", owner or "", repo or "", relpath.replace("\\", "/")])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def split_frontmatter(text: str) -> tuple[dict[str, Any], str, str | None]:
    """Return (mapping, body, error). Does not invent fields."""
    if text.startswith("\ufeff"):
        text = text[1:]
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.startswith("---\n") and not normalized.startswith("---\r"):
        if normalized.startswith("---"):
            first, _, rest = normalized.partition("\n")
            if first.strip() != "---":
                return {}, text, None
        else:
            return {}, text, None
    lines = normalized.split("\n")
    if not lines or lines[0].strip() != "---":
        return {}, text, None
    end = None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            end = index
            break
    if end is None:
        return {}, text, "frontmatter is not closed"
    raw = "\n".join(lines[1:end])
    body = "\n".join(lines[end + 1 :])
    if len(raw.encode("utf-8")) > 65_536:
        return {}, body, "frontmatter exceeds 64 KB and was not parsed"
    if not raw.strip():
        return {}, body, None
    try:
        loaded = yaml.load(raw, Loader=_NoAliasLoader)
    except yaml.YAMLError as exc:
        return {}, body, f"frontmatter YAML could not be parsed: {exc}"
    if loaded is None:
        return {}, body, None
    if not isinstance(loaded, dict):
        return {}, body, "frontmatter is not a mapping"
    return json_safe(loaded), body, None


def tokenize(text: str) -> list[str]:
    tokens = []
    seen = set()
    for match in _FTS_TOKEN_RE.findall(text.lower()):
        if match in _STOPWORDS or match in seen:
            continue
        seen.add(match)
        tokens.append(match)
    return tokens


def fts_query(text: str, limit: int = 16) -> str:
    tokens = tokenize(text)[:limit]
    if not tokens:
        return ""
    return " OR ".join(f'"{token}"' for token in tokens)


def excerpt(text: str, limit: int) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "…"


def local_markdown_links(markdown: str) -> list[str]:
    links = []
    for raw in _LINK_RE.findall(markdown):
        target = raw.strip()
        if not target or target.startswith("<"):
            continue
        if " " in target and not target.startswith('"'):
            target = target.split()[0]
        target = target.strip("<>")
        lowered = target.lower()
        if lowered.startswith(("#", "http://", "https://", "mailto:", "data:")):
            continue
        target = target.split("#", 1)[0].strip()
        if target:
            links.append(target)
    return links


def looks_like_license_id(value: str) -> bool:
    return bool(_SPDX_RE.match(value.strip())) and " " not in value


def valid_skill_name(name: str) -> bool:
    return bool(name) and len(name) <= 64 and bool(_NAME_RE.match(name))


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(redact_obj(record), ensure_ascii=False, sort_keys=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def read_text_capped(path: Path, max_bytes: int) -> str:
    data = path.read_bytes()
    if len(data) > max_bytes:
        raise ConfigError(f"{path} exceeds {max_bytes} bytes")
    return data.decode("utf-8-sig", errors="replace")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
