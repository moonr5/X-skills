"""Library root, layout, and database handle."""

from __future__ import annotations

import os
from pathlib import Path

from uaskills import catalog
from uaskills.config import (
    BUNDLED_ROOT,
    category_ids,
    github_token,
    library_root,
    load_categories,
    load_settings,
    load_sources,
)
from uaskills.db import connect, init_db


class Library:
    def __init__(self, root: str | Path | None = None):
        self.root = library_root(root)
        self.root.mkdir(parents=True, exist_ok=True)
        config_root = self.root if (self.root / "config" / "settings.yaml").is_file() else BUNDLED_ROOT
        self.settings = load_settings(config_root)
        self.category_config = load_categories(config_root)
        self.configured_sources = load_sources(config_root)
        self.categories = category_ids(self.category_config)
        self.conn = connect(self.root / "index" / "skills.sqlite")
        init_db(self.conn)
        self.ensure_layout()

    def close(self) -> None:
        self.conn.close()

    def limit(self, key: str):
        return self.settings["limits"][key]

    def token(self) -> str | None:
        return github_token(self.settings)

    def schema_file(self, name: str) -> Path:
        local = self.root / "schemas" / name
        if local.is_file():
            return local
        return BUNDLED_ROOT / "schemas" / name

    def ensure_layout(self) -> None:
        for category in self.categories:
            (self.root / "skills" / category).mkdir(parents=True, exist_ok=True)
        for relative in (
            "sources",
            "index/keyword_index",
            "index/embeddings",
            "index/metadata",
            "index/staging",
            "index/backup",
            "index/resources",
            "quarantine",
            "config",
            "schemas",
            "docs",
            "adapters/generic",
            "adapters/agents",
            "adapters/coding_assistants",
        ):
            (self.root / relative).mkdir(parents=True, exist_ok=True)

    def metadata_path(self, skill_id: str) -> Path:
        return self.root / "index" / "metadata" / f"{skill_id}.json"

    def allow_synthetic(self) -> bool:
        if self.root.resolve() != BUNDLED_ROOT.resolve():
            return True
        return os.environ.get("UASKILLS_ALLOW_SYNTHETIC") == "1"

    def counts(self) -> dict:
        return catalog.counts(self.conn)
