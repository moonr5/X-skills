"""Group identical skill instructions. Files are kept unless a later export step says otherwise."""

from __future__ import annotations

from typing import Any

from uaskills.library import Library
from uaskills.textutil import utc_now


def duplicate_groups(library: Library, by: str = "content") -> list[dict[str, Any]]:
    column = "tree_checksum" if by == "tree" else "content_checksum"
    rows = library.conn.execute(
        f"""
        SELECT {column} AS checksum, COUNT(*) AS n
        FROM skills
        WHERE is_synthetic = 0 AND {column} IS NOT NULL AND {column} != ''
        GROUP BY {column}
        HAVING n > 1
        ORDER BY n DESC, checksum ASC
        """
    ).fetchall()
    groups = []
    for row in rows:
        members = [
            dict(item)
            for item in library.conn.execute(
                f"""
                SELECT id, name, local_path, source_url, import_timestamp, canonical, duplicate_of
                FROM skills
                WHERE {column} = ? AND is_synthetic = 0
                ORDER BY import_timestamp ASC, id ASC
                """,
                (row["checksum"],),
            )
        ]
        groups.append({"checksum": row["checksum"], "count": row["n"], "by": by, "members": members})
    return groups


def apply_duplicate_groups(library: Library, groups: list[dict[str, Any]]) -> int:
    """Mark the earliest imported copy as canonical. Do not delete skill files."""
    changed = 0
    now = utc_now()
    for group in groups:
        members = group["members"]
        if not members:
            continue
        canonical = members[0]["id"]
        for member in members:
            if member["id"] == canonical:
                library.conn.execute(
                    "UPDATE skills SET canonical = 1, duplicate_of = NULL, update_timestamp = ? WHERE id = ?",
                    (now, member["id"]),
                )
            else:
                library.conn.execute(
                    "UPDATE skills SET canonical = 0, duplicate_of = ?, update_timestamp = ? WHERE id = ?",
                    (canonical, now, member["id"]),
                )
            changed += 1
    library.conn.commit()
    return changed
