"""Catalog reads and writes. Search never loads the full collection into memory."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from uaskills.db import dumps, loads
from uaskills.models import ResourceRecord, SkillRecord
from uaskills.textutil import fts_query, utc_now


def _row_to_skill(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["security_findings"] = loads(data.pop("security_findings_json", None), [])
    data["capabilities"] = loads(data.pop("capabilities_json", None), None)
    data["supported_runtimes"] = loads(data.pop("runtimes_json", None), None)
    data["required_tools"] = loads(data.pop("tools_json", None), None)
    data["dependencies"] = loads(data.pop("dependencies_json", None), None)
    data["tags"] = loads(data.pop("tags_json", None), None)
    data["extensions"] = loads(data.pop("extensions_json", None), {})
    data["is_synthetic"] = bool(data.get("is_synthetic"))
    data["canonical"] = bool(data.get("canonical"))
    data["normalized_format"] = "uaskills.manifest.v1"
    return data


def manifest_from_row(row: dict[str, Any]) -> dict[str, Any]:
    extensions = row.get("extensions") or {}
    return {
        "id": row["id"],
        "name": row.get("name"),
        "name_source": extensions.get("name_source"),
        "description": row.get("description"),
        "description_source": extensions.get("description_source"),
        "source_url": row.get("source_url"),
        "repository": {
            "host": row.get("source_host"),
            "owner": row.get("repo_owner"),
            "name": row.get("repo_name"),
            "url": row.get("repo_url"),
        },
        "commit_sha": row.get("commit_sha"),
        "version": row.get("version"),
        "local_path": row.get("local_path"),
        "origin_path": row.get("origin_path"),
        "skill_relpath": row.get("skill_relpath"),
        "original_format": row.get("original_format"),
        "normalized_format": "uaskills.manifest.v1",
        "category": row.get("category"),
        "tags": row.get("tags"),
        "capabilities": row.get("capabilities"),
        "supported_runtimes": row.get("supported_runtimes"),
        "required_tools": row.get("required_tools"),
        "dependencies": row.get("dependencies"),
        "license": {
            "identifier": row.get("license_identifier"),
            "evidence": row.get("license_evidence"),
            "status": row.get("license_status"),
        },
        "content_checksum": row.get("content_checksum"),
        "tree_checksum": row.get("tree_checksum"),
        "import_timestamp": row.get("import_timestamp"),
        "update_timestamp": row.get("update_timestamp"),
        "verification_status": row.get("verification_status"),
        "security_scan_status": row.get("security_scan_status"),
        "security_findings": row.get("security_findings") or [],
        "compatibility_status": row.get("compatibility_status"),
        "compatibility": row.get("compatibility"),
        "quality_assessment": row.get("quality_assessment"),
        "deprecation_status": row.get("deprecation_status"),
        "lifecycle_status": row.get("lifecycle_status"),
        "resource_kind": row.get("resource_kind"),
        "duplicate_of": row.get("duplicate_of"),
        "is_synthetic": bool(row.get("is_synthetic")),
        "extensions": extensions,
    }


def record_event(
    conn: sqlite3.Connection,
    *,
    level: str,
    action: str,
    message: str,
    source: str | None = None,
    skill_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO events(ts, level, action, source, skill_id, message, details_json)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (utc_now(), level, action, source, skill_id, message, dumps(details)),
    )


def find_canonical_duplicate(conn: sqlite3.Connection, checksum: str, skill_id: str) -> str | None:
    row = conn.execute(
        """
        SELECT id FROM skills
        WHERE content_checksum = ? AND id != ? AND canonical = 1 AND is_synthetic = 0
        ORDER BY import_timestamp ASC, id ASC
        LIMIT 1
        """,
        (checksum, skill_id),
    ).fetchone()
    return row["id"] if row else None


def upsert_skill(conn: sqlite3.Connection, record: SkillRecord, body: str | None = None) -> str:
    """Insert or update one skill. Returns 'inserted', 'updated', or 'unchanged'."""
    existing = conn.execute("SELECT * FROM skills WHERE id = ?", (record.id,)).fetchone()
    action = "inserted"
    if existing is not None:
        if existing["content_checksum"] == record.content_checksum and existing["tree_checksum"] == record.tree_checksum:
            conn.execute(
                "UPDATE skills SET update_timestamp = ?, commit_sha = COALESCE(?, commit_sha), "
                "source_url = COALESCE(?, source_url) WHERE id = ?",
                (record.update_timestamp, record.commit_sha, record.source_url, record.id),
            )
            record_event(
                conn,
                level="info",
                action="unchanged",
                message="Import matched the stored checksums.",
                source=record.source_url,
                skill_id=record.id,
            )
            return "unchanged"
        record.import_timestamp = existing["import_timestamp"] or record.import_timestamp
        record.verification_status = existing["verification_status"] or record.verification_status
        if existing["license_status"] == "reviewed":
            record.license_status = "reviewed"
            record.license_identifier = existing["license_identifier"]
            record.license_evidence = existing["license_evidence"]
        action = "updated"
    if record.duplicate_of is None and record.content_checksum:
        duplicate = find_canonical_duplicate(conn, record.content_checksum, record.id)
        if duplicate:
            record.duplicate_of = duplicate
            record.canonical = False
    _write_skill_row(conn, record)
    _replace_fts(conn, record, body if body is not None else record.body)
    record_event(
        conn,
        level="info",
        action=action,
        message=f"Skill {action}.",
        source=record.source_url,
        skill_id=record.id,
        details={"checksum": record.content_checksum, "lifecycle": record.lifecycle_status},
    )
    return action


def _write_skill_row(conn: sqlite3.Connection, record: SkillRecord) -> None:
    conn.execute(
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
        ON CONFLICT(id) DO UPDATE SET
            name = excluded.name,
            description = excluded.description,
            source_url = excluded.source_url,
            source_host = excluded.source_host,
            repo_owner = excluded.repo_owner,
            repo_name = excluded.repo_name,
            repo_url = excluded.repo_url,
            commit_sha = excluded.commit_sha,
            version = excluded.version,
            local_path = excluded.local_path,
            origin_path = excluded.origin_path,
            skill_relpath = excluded.skill_relpath,
            original_format = excluded.original_format,
            category = excluded.category,
            license_identifier = excluded.license_identifier,
            license_evidence = excluded.license_evidence,
            license_status = excluded.license_status,
            content_checksum = excluded.content_checksum,
            tree_checksum = excluded.tree_checksum,
            import_timestamp = excluded.import_timestamp,
            update_timestamp = excluded.update_timestamp,
            verification_status = excluded.verification_status,
            security_scan_status = excluded.security_scan_status,
            security_findings_json = excluded.security_findings_json,
            compatibility_status = excluded.compatibility_status,
            compatibility = excluded.compatibility,
            quality_assessment = excluded.quality_assessment,
            deprecation_status = excluded.deprecation_status,
            lifecycle_status = excluded.lifecycle_status,
            resource_kind = excluded.resource_kind,
            is_synthetic = excluded.is_synthetic,
            duplicate_of = excluded.duplicate_of,
            canonical = excluded.canonical,
            body_excerpt = excluded.body_excerpt,
            capabilities_json = excluded.capabilities_json,
            runtimes_json = excluded.runtimes_json,
            tools_json = excluded.tools_json,
            dependencies_json = excluded.dependencies_json,
            tags_json = excluded.tags_json,
            extensions_json = excluded.extensions_json
        """,
        (
            record.id,
            record.name,
            record.description,
            record.source_url,
            record.source_host,
            record.repo_owner,
            record.repo_name,
            record.repo_url,
            record.commit_sha,
            record.version,
            record.local_path,
            record.origin_path,
            record.skill_relpath,
            record.original_format,
            record.category,
            record.license_identifier,
            record.license_evidence,
            record.license_status,
            record.content_checksum,
            record.tree_checksum,
            record.import_timestamp,
            record.update_timestamp,
            record.verification_status,
            record.security_scan_status,
            dumps(record.security_findings),
            record.compatibility_status,
            record.compatibility,
            record.quality_assessment,
            record.deprecation_status,
            record.lifecycle_status,
            record.resource_kind,
            1 if record.is_synthetic else 0,
            record.duplicate_of,
            1 if record.canonical else 0,
            record.body_excerpt,
            dumps(record.capabilities),
            dumps(record.supported_runtimes),
            dumps(record.required_tools),
            dumps(record.dependencies),
            dumps(record.tags),
            dumps(record.extensions),
        ),
    )


def _replace_fts(conn: sqlite3.Connection, record: SkillRecord, body: str) -> None:
    conn.execute("DELETE FROM skills_fts WHERE skill_id = ?", (record.id,))
    topics = " ".join((record.extensions or {}).get("source_topics") or [])
    conn.execute(
        """
        INSERT INTO skills_fts(skill_id, name, description, tags, capabilities, category, source_topics, body)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            record.id,
            record.name or "",
            record.description or "",
            " ".join(record.tags or []),
            " ".join(record.capabilities or []),
            record.category or "",
            topics,
            body or "",
        ),
    )


def get_skill(conn: sqlite3.Connection, skill_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM skills WHERE id = ?", (skill_id,)).fetchone()
    return _row_to_skill(row) if row else None


def upsert_resource(conn: sqlite3.Connection, record: ResourceRecord) -> None:
    conn.execute(
        """
        INSERT INTO resources(
            id, kind, source_url, repo_owner, repo_name, local_path, reason, evidence,
            discovered_at, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            kind = excluded.kind,
            source_url = excluded.source_url,
            local_path = excluded.local_path,
            reason = excluded.reason,
            evidence = excluded.evidence,
            discovered_at = excluded.discovered_at,
            metadata_json = excluded.metadata_json
        """,
        (
            record.id,
            record.kind,
            record.source_url,
            record.repo_owner,
            record.repo_name,
            record.local_path,
            record.reason,
            record.evidence,
            record.discovered_at,
            dumps(record.metadata),
        ),
    )


def upsert_source(
    conn: sqlite3.Connection,
    *,
    source_id: str,
    kind: str,
    locator: str,
    status: str,
    detail: str | None,
    metadata: dict[str, Any] | None = None,
) -> None:
    now = utc_now()
    conn.execute(
        """
        INSERT INTO sources(id, kind, locator, status, detail, discovered_at, updated_at, metadata_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            kind = excluded.kind,
            locator = excluded.locator,
            status = excluded.status,
            detail = excluded.detail,
            updated_at = excluded.updated_at,
            metadata_json = excluded.metadata_json
        """,
        (source_id, kind, locator, status, detail, now, now, dumps(metadata or {})),
    )


def save_job(
    conn: sqlite3.Connection,
    *,
    job_id: str,
    kind: str,
    status: str,
    cursor: dict[str, Any],
    params: dict[str, Any],
    message: str = "",
) -> None:
    now = utc_now()
    existing = conn.execute("SELECT created_at FROM jobs WHERE id = ?", (job_id,)).fetchone()
    created = existing["created_at"] if existing else now
    conn.execute(
        """
        INSERT INTO jobs(id, kind, status, cursor_json, params_json, message, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            status = excluded.status,
            cursor_json = excluded.cursor_json,
            params_json = excluded.params_json,
            message = excluded.message,
            updated_at = excluded.updated_at
        """,
        (job_id, kind, status, dumps(cursor), dumps(params), message, created, now),
    )


def get_job(conn: sqlite3.Connection, job_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if not row:
        return None
    data = dict(row)
    data["cursor"] = loads(data.pop("cursor_json"), {})
    data["params"] = loads(data.pop("params_json"), {})
    return data


def latest_job(conn: sqlite3.Connection, kind: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT id FROM jobs WHERE kind = ? ORDER BY updated_at DESC LIMIT 1",
        (kind,),
    ).fetchone()
    return get_job(conn, row["id"]) if row else None


def search_skills(
    conn: sqlite3.Connection,
    *,
    query: str = "",
    category: str | None = None,
    capability: str | None = None,
    source: str | None = None,
    license_status: str | None = None,
    runtime: str | None = None,
    version: str | None = None,
    security_status: str | None = None,
    name: str | None = None,
    limit: int = 20,
    offset: int = 0,
    include_quarantined: bool = False,
    include_deprecated: bool = False,
    include_duplicates: bool = False,
    include_synthetic: bool = False,
    include_withheld: bool = False,
    runtime_strict: bool = False,
) -> dict[str, Any]:
    where = ["1 = 1"]
    params: list[Any] = []
    if not include_synthetic:
        where.append("s.is_synthetic = 0")
    if not include_withheld:
        where.append("COALESCE(json_extract(s.extensions_json, '$.redistribution'), '') != 'withheld'")
    if not include_quarantined:
        where.append("s.lifecycle_status != 'quarantined'")
    if not include_deprecated:
        where.append("s.deprecation_status != 'deprecated' AND s.lifecycle_status != 'deprecated'")
    if not include_duplicates:
        where.append("s.canonical = 1")
    if category:
        where.append("s.category = ?")
        params.append(category)
    if license_status:
        where.append("s.license_status = ?")
        params.append(license_status)
    if security_status:
        where.append("s.security_scan_status = ?")
        params.append(security_status)
    if version:
        where.append("s.version = ?")
        params.append(version)
    if name:
        where.append("lower(s.name) = lower(?)")
        params.append(name)
    if source:
        where.append(
            "(lower(s.repo_owner || '/' || s.repo_name) = lower(?) "
            "OR instr(lower(coalesce(s.source_url, '')), lower(?)) > 0 "
            "OR lower(coalesce(s.repo_name, '')) = lower(?))"
        )
        params.extend([source, source, source])
    if capability:
        where.append(
            "EXISTS (SELECT 1 FROM json_each(COALESCE(s.capabilities_json, '[]')) "
            "WHERE lower(json_each.value) = lower(?))"
        )
        params.append(capability)
    if runtime:
        if runtime_strict:
            where.append(
                "EXISTS (SELECT 1 FROM json_each(COALESCE(s.runtimes_json, '[]')) "
                "WHERE lower(json_each.value) = lower(?))"
            )
            params.append(runtime)
        else:
            where.append(
                "(s.runtimes_json IS NULL OR s.runtimes_json = 'null' OR "
                "EXISTS (SELECT 1 FROM json_each(COALESCE(s.runtimes_json, '[]')) "
                "WHERE lower(json_each.value) = lower(?)))"
            )
            params.append(runtime)

    match = fts_query(query) if query else ""
    rank_select = "NULL AS rank"
    join = ""
    order = "s.name COLLATE NOCASE ASC, s.id ASC"
    fts_params: list[Any] = []
    if query and not match:
        return {"total": 0, "offset": offset, "limit": limit, "results": []}
    if match:
        join = "JOIN skills_fts ON skills_fts.skill_id = s.id"
        where.append("skills_fts MATCH ?")
        fts_params.append(match)
        rank_select = "bm25(skills_fts) AS rank"
        order = (
            "CASE WHEN lower(s.name) = lower(?) THEN 0 ELSE 1 END, "
            "bm25(skills_fts) ASC, s.name COLLATE NOCASE ASC, s.id ASC"
        )

    where_sql = " AND ".join(where)
    count_sql = f"SELECT COUNT(*) AS n FROM skills s {join} WHERE {where_sql}"
    total = conn.execute(count_sql, [*params, *fts_params]).fetchone()["n"]
    list_params = [*params, *fts_params]
    if match:
        list_params.append(query)
    list_params.extend([limit, offset])
    sql = (
        f"SELECT s.*, {rank_select} FROM skills s {join} WHERE {where_sql} "
        f"ORDER BY {order} LIMIT ? OFFSET ?"
    )
    rows = conn.execute(sql, list_params).fetchall()
    results = []
    for row in rows:
        item = _row_to_skill(row)
        item["rank"] = row["rank"]
        results.append(item)
    return {"total": total, "offset": offset, "limit": limit, "results": results}


def iter_skills(conn: sqlite3.Connection, *, synthetic: bool | None = False, batch: int = 200):
    clauses = []
    params: list[Any] = []
    if synthetic is False:
        clauses.append("is_synthetic = 0")
    elif synthetic is True:
        clauses.append("is_synthetic = 1")
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    cursor = conn.execute(f"SELECT * FROM skills{where} ORDER BY id", params)
    while True:
        rows = cursor.fetchmany(batch)
        if not rows:
            break
        for row in rows:
            yield _row_to_skill(row)


def rebuild_fts(conn: sqlite3.Connection, body_for, batch: int = 200) -> int:
    """Rebuild the full-text index one page at a time.

    `body_for(skill_row) -> str` supplies the instruction text. It should read
    one file at a time so the process does not hold every skill in memory.
    """
    conn.execute("DELETE FROM skills_fts")
    cursor = conn.execute(
        "SELECT id, name, description, tags_json, capabilities_json, category, extensions_json, "
        "body_excerpt, local_path FROM skills ORDER BY id"
    )
    count = 0
    while True:
        rows = cursor.fetchmany(batch)
        if not rows:
            break
        payload = []
        for row in rows:
            skill = _row_to_skill(row)
            topics = " ".join((skill.get("extensions") or {}).get("source_topics") or [])
            payload.append(
                (
                    skill["id"],
                    skill.get("name") or "",
                    skill.get("description") or "",
                    " ".join(skill.get("tags") or []),
                    " ".join(skill.get("capabilities") or []),
                    skill.get("category") or "",
                    topics,
                    body_for(skill) or "",
                )
            )
        conn.executemany(
            """
            INSERT INTO skills_fts(skill_id, name, description, tags, capabilities, category, source_topics, body)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            payload,
        )
        count += len(payload)
        conn.commit()
    return count


def mark_missing_upstream(conn: sqlite3.Connection, owner: str, repo: str, seen_ids: set[str], commit_sha: str | None) -> list[str]:
    rows = conn.execute(
        """
        SELECT id, extensions_json FROM skills
        WHERE source_host = 'github.com' AND repo_owner = ? AND repo_name = ? AND is_synthetic = 0
        """,
        (owner, repo),
    ).fetchall()
    missing = []
    now = utc_now()
    for row in rows:
        if row["id"] in seen_ids:
            continue
        extensions = loads(row["extensions_json"], {})
        extensions["upstream_presence"] = "missing"
        extensions["missing_at_commit"] = commit_sha
        conn.execute(
            "UPDATE skills SET extensions_json = ?, update_timestamp = ? WHERE id = ?",
            (dumps(extensions), now, row["id"]),
        )
        missing.append(row["id"])
    return missing


def counts(conn: sqlite3.Connection) -> dict[str, Any]:
    def scalar(sql: str) -> int:
        return int(conn.execute(sql).fetchone()[0])

    stored = "is_synthetic = 0 AND COALESCE(json_extract(extensions_json, '$.redistribution'), '') != 'withheld'"
    by_category = {
        row["category"]: row["n"]
        for row in conn.execute(
            f"SELECT category, COUNT(*) AS n FROM skills WHERE {stored} GROUP BY category ORDER BY category"
        )
    }
    by_lifecycle = {
        row["lifecycle_status"]: row["n"]
        for row in conn.execute(
            "SELECT lifecycle_status, COUNT(*) AS n FROM skills WHERE is_synthetic = 0 GROUP BY lifecycle_status"
        )
    }
    return {
        "skills": scalar("SELECT COUNT(*) FROM skills WHERE is_synthetic = 0"),
        "stored_skills": scalar(
            "SELECT COUNT(*) FROM skills WHERE is_synthetic = 0 "
            "AND COALESCE(json_extract(extensions_json, '$.redistribution'), '') != 'withheld'"
        ),
        "withheld": scalar(
            "SELECT COUNT(*) FROM skills WHERE is_synthetic = 0 "
            "AND COALESCE(json_extract(extensions_json, '$.redistribution'), '') = 'withheld'"
        ),
        "synthetic": scalar("SELECT COUNT(*) FROM skills WHERE is_synthetic = 1"),
        "resources": scalar("SELECT COUNT(*) FROM resources"),
        "quarantined": scalar("SELECT COUNT(*) FROM skills WHERE lifecycle_status = 'quarantined' AND is_synthetic = 0"),
        "duplicates": scalar("SELECT COUNT(*) FROM skills WHERE canonical = 0 AND is_synthetic = 0"),
        "by_category": by_category,
        "by_lifecycle": by_lifecycle,
    }


def insert_synthetic(conn: sqlite3.Connection, count: int, batch: int = 500, *, allow: bool = False) -> int:
    """Insert synthetic catalog rows for scale tests. These are not real skills."""
    if not allow:
        raise RuntimeError("Refusing to insert synthetic skills without allow=True. Use a disposable test database.")
    from uaskills.textutil import sha256_bytes

    inserted = 0
    while inserted < count:
        chunk = min(batch, count - inserted)
        rows = []
        fts_rows = []
        for offset in range(chunk):
            number = inserted + offset
            skill_id = f"synthetic-{number:08d}"
            name = f"synthetic-skill-{number}"
            description = f"Synthetic catalog row {number} used only for pagination tests."
            checksum = sha256_bytes(f"synthetic-{number}".encode("utf-8"))
            rows.append(
                (
                    skill_id,
                    name,
                    description,
                    None,
                    "synthetic",
                    "synthetic",
                    "scale-test",
                    None,
                    None,
                    "0",
                    None,
                    None,
                    name,
                    "synthetic",
                    "other",
                    None,
                    "synthetic test row",
                    "unknown",
                    checksum,
                    checksum,
                    "2000-01-01T00:00:00+00:00",
                    "2000-01-01T00:00:00+00:00",
                    "unverified",
                    "not_scanned",
                    "[]",
                    "unknown",
                    None,
                    "unassessed",
                    "unknown",
                    "parsed",
                    "skill",
                    1,
                    None,
                    1,
                    description,
                    None,
                    None,
                    None,
                    None,
                    None,
                    json.dumps({"synthetic": True}),
                )
            )
            fts_rows.append((skill_id, name, description, "", "", "other", "", description))
        conn.executemany(
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
            rows,
        )
        conn.executemany(
            """
            INSERT INTO skills_fts(skill_id, name, description, tags, capabilities, category, source_topics, body)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            fts_rows,
        )
        inserted += chunk
        conn.commit()
    return inserted
