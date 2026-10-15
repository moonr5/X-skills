"""Configuration loading. Tokens are never read from YAML."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from uaskills.errors import ConfigError

BUNDLED_ROOT = Path(__file__).resolve().parents[1]

CATEGORY_IDS = [
    "programming",
    "frontend",
    "backend",
    "fullstack",
    "architecture",
    "ui-ux-design",
    "image-generation",
    "video-generation",
    "audio",
    "ai-agents",
    "agent-orchestration",
    "automation",
    "cybersecurity",
    "testing",
    "research",
    "data-analysis",
    "databases",
    "devops",
    "cloud",
    "documents",
    "business",
    "finance",
    "marketing",
    "education",
    "industrial-iot",
    "other",
]

DEFAULT_LIMITS: dict[str, Any] = {
    "max_repos_per_run": 10,
    "max_skills_per_repo": 100,
    "max_files_per_skill": 40,
    "max_file_bytes": 400_000,
    "max_repo_download_bytes": 20_000_000,
    "max_tree_entries": 50_000,
    "max_archive_bytes": 30_000_000,
    "max_archive_files": 2_000,
    "max_archive_uncompressed_bytes": 50_000_000,
    "max_compression_ratio": 80,
    "concurrency": 1,
    "min_interval_seconds": 1.0,
    "raw_min_interval_seconds": 0.35,
    "max_retries": 3,
    "discover_per_page": 20,
    "search_default_limit": 20,
    "search_max_limit": 100,
    "index_batch_size": 200,
    "body_index_max_bytes": 200_000,
    "excerpt_chars": 500,
    "max_walk_depth": 12,
    "request_timeout_seconds": 30,
}

DEFAULT_SETTINGS: dict[str, Any] = {
    "github": {
        "api_base": "https://api.github.com",
        "raw_base": "https://raw.githubusercontent.com",
        "token_env": ["GITHUB_TOKEN", "GH_TOKEN"],
        "user_agent": "universal-ai-skills/0.1 (local skill catalog)",
    },
    "limits": dict(DEFAULT_LIMITS),
    "semantic": {"enabled": False, "provider": "none"},
}

_TOKEN_KEYS = {"token", "access_token", "github_token", "api_token", "password", "secret"}


class _NoAliasLoader(yaml.SafeLoader):
    def ignore_aliases(self, data: Any) -> bool:
        return True


def library_root(explicit: str | Path | None = None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    env = os.environ.get("UASKILLS_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    return BUNDLED_ROOT


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    raw = path.read_bytes()
    if len(raw) > 1_000_000:
        raise ConfigError(f"Config file exceeds 1 MB: {path}")
    try:
        loaded = yaml.load(raw.decode("utf-8-sig"), Loader=_NoAliasLoader) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"Could not parse {path}: {exc}") from exc
    if not isinstance(loaded, dict):
        raise ConfigError(f"Config file must be a mapping: {path}")
    _reject_secrets(loaded, path)
    return loaded


def _reject_secrets(value: Any, path: Path) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).lower() in _TOKEN_KEYS:
                raise ConfigError(
                    f"{path} contains '{key}'. Do not store access tokens in config files. "
                    "Set the GITHUB_TOKEN or GH_TOKEN environment variable instead."
                )
            _reject_secrets(child, path)
    elif isinstance(value, list):
        for child in value:
            _reject_secrets(child, path)


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_settings(root: Path) -> dict[str, Any]:
    raw = load_yaml(root / "config" / "settings.yaml")
    settings = _merge(DEFAULT_SETTINGS, raw)
    limits = _merge(DEFAULT_LIMITS, settings.get("limits") or {})
    for key, value in list(limits.items()):
        if key in DEFAULT_LIMITS and not isinstance(value, type(DEFAULT_LIMITS[key])):
            limits[key] = DEFAULT_LIMITS[key]
    if float(limits["min_interval_seconds"]) < 0:
        limits["min_interval_seconds"] = DEFAULT_LIMITS["min_interval_seconds"]
    if int(limits["concurrency"]) < 1:
        limits["concurrency"] = 1
    settings["limits"] = limits
    github = settings.get("github") or {}
    envs = github.get("token_env") or ["GITHUB_TOKEN", "GH_TOKEN"]
    if isinstance(envs, str):
        envs = [envs]
    github["token_env"] = [str(item) for item in envs]
    settings["github"] = github
    semantic = settings.get("semantic") or {}
    semantic["enabled"] = bool(semantic.get("enabled", False))
    semantic["provider"] = str(semantic.get("provider") or "none")
    settings["semantic"] = semantic
    return settings


def load_categories(root: Path) -> dict[str, Any]:
    raw = load_yaml(root / "config" / "categories.yaml")
    items = raw.get("categories")
    if not isinstance(items, list) or not items:
        return {
            "categories": [{"id": cid, "description": "", "keywords": []} for cid in CATEGORY_IDS]
        }
    cleaned = []
    seen = set()
    for item in items:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        cid = str(item["id"])
        if cid in seen:
            continue
        seen.add(cid)
        keywords = item.get("keywords") or []
        if not isinstance(keywords, list):
            keywords = []
        cleaned.append(
            {
                "id": cid,
                "description": str(item.get("description") or ""),
                "keywords": [str(word).lower() for word in keywords if str(word).strip()],
            }
        )
    if "other" not in seen:
        cleaned.append({"id": "other", "description": "Unclassified skills.", "keywords": []})
    return {"categories": cleaned}


def load_sources(root: Path) -> list[dict[str, Any]]:
    raw = load_yaml(root / "config" / "sources.yaml")
    items = raw.get("sources")
    if not isinstance(items, list):
        return []
    sources = []
    for item in items:
        if isinstance(item, dict) and item.get("id") and item.get("type"):
            sources.append(item)
    return sources


def github_token(settings: dict[str, Any]) -> str | None:
    for name in settings.get("github", {}).get("token_env", []):
        value = os.environ.get(str(name), "").strip()
        if value:
            return value
    return None


def category_ids(config: dict[str, Any]) -> list[str]:
    return [item["id"] for item in config.get("categories", [])]
