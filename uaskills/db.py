"""SQLite catalog. Skill bodies live in files; this database stores metadata and FTS."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    locator TEXT NOT NULL,
    status TEXT NOT NULL,
    detail TEXT,
    discovered_at TEXT,
    updated_at TEXT,
    metadata_json TEXT
);

CREATE TABLE IF NOT EXISTS skills (
    id TEXT PRIMARY KEY,
    name TEXT,
    description TEXT,
    source_url TEXT,
    source_host TEXT,
    repo_owner TEXT,
    repo_name TEXT,
    repo_url TEXT,
    commit_sha TEXT,
    version TEXT,
    local_path TEXT,
    origin_path TEXT,
    skill_relpath TEXT,
    original_format TEXT,
    category TEXT,
    license_identifier TEXT,
    license_evidence TEXT,
    license_status TEXT NOT NULL,
    content_checksum TEXT,
    tree_checksum TEXT,
    import_timestamp TEXT,
    update_timestamp TEXT,
    verification_status TEXT NOT NULL,
    security_scan_status TEXT NOT NULL,
    security_findings_json TEXT,
    compatibility_status TEXT NOT NULL,
    compatibility TEXT,
    quality_assessment TEXT NOT NULL,
    deprecation_status TEXT NOT NULL,
    lifecycle_status TEXT NOT NULL,
    resource_kind TEXT NOT NULL,
    is_synthetic INTEGER NOT NULL DEFAULT 0,
    duplicate_of TEXT,
    canonical INTEGER NOT NULL DEFAULT 1,
    body_excerpt TEXT,
    capabilities_json TEXT,
    runtimes_json TEXT,
    tools_json TEXT,
    dependencies_json TEXT,
    tags_json TEXT,
    extensions_json TEXT
);

CREATE TABLE IF NOT EXISTS resources (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    source_url TEXT,
    repo_owner TEXT,
    repo_name TEXT,
    local_path TEXT,
    reason TEXT,
    evidence TEXT,
    discovered_at TEXT,
    metadata_json TEXT
);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    cursor_json TEXT,
    params_json TEXT,
    message TEXT,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    level TEXT NOT NULL,
    action TEXT NOT NULL,
    source TEXT,
    skill_id TEXT,
    message TEXT,
    details_json TEXT
);

CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_id TEXT,
    task TEXT,
    outcome TEXT,
    notes TEXT,
    created_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_skills_category ON skills(category);
CREATE INDEX IF NOT EXISTS idx_skills_checksum ON skills(content_checksum);
CREATE INDEX IF NOT EXISTS idx_skills_tree ON skills(tree_checksum);
CREATE INDEX IF NOT EXISTS idx_skills_source ON skills(source_host, repo_owner, repo_name);
CREATE INDEX IF NOT EXISTS idx_skills_license ON skills(license_status);
CREATE INDEX IF NOT EXISTS idx_skills_lifecycle ON skills(lifecycle_status);
CREATE INDEX IF NOT EXISTS idx_skills_security ON skills(security_scan_status);
CREATE INDEX IF NOT EXISTS idx_skills_name ON skills(name);
CREATE INDEX IF NOT EXISTS idx_skills_synthetic ON skills(is_synthetic);
CREATE INDEX IF NOT EXISTS idx_skills_version ON skills(version);

CREATE TABLE IF NOT EXISTS inspections (
    repo TEXT PRIMARY KEY,
    inspected_at TEXT,
    skill_files INTEGER,
    license_spdx TEXT,
    classification TEXT,
    readme_links INTEGER,
    notes TEXT
);
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    _ensure_fts(conn)
    conn.execute(
        "INSERT INTO meta(key, value) VALUES('schema_version', '1') "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value"
    )
    conn.commit()


def _ensure_fts(conn: sqlite3.Connection) -> None:
    existing = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'skills_fts'"
    ).fetchone()
    if existing:
        return
    tokenizers = ("porter unicode61", "unicode61")
    last_error: Exception | None = None
    for tokenizer in tokenizers:
        try:
            conn.execute(
                f"""
                CREATE VIRTUAL TABLE skills_fts USING fts5(
                    skill_id UNINDEXED,
                    name,
                    description,
                    tags,
                    capabilities,
                    category,
                    source_topics,
                    body,
                    tokenize = '{tokenizer}'
                )
                """
            )
            conn.execute(
                "INSERT INTO meta(key, value) VALUES('fts_tokenizer', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (tokenizer,),
            )
            return
        except sqlite3.OperationalError as exc:
            last_error = exc
    raise sqlite3.OperationalError(f"FTS5 is unavailable: {last_error}")


def dumps(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def loads(value: str | None, default: Any) -> Any:
    if value in (None, ""):
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default
