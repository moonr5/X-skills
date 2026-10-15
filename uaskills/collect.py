"""Inspect seed repositories and import only packages that contain skill files."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from uaskills import catalog
from uaskills.config import BUNDLED_ROOT
from uaskills.errors import GitHubError, LibraryError
from uaskills.importer import import_github_repo
from uaskills.library import Library
from uaskills.licensing import withhold_stored_copies
from uaskills.locators import parse_github_locator
from uaskills.textutil import append_jsonl, redact, utc_now

_LINK_RE = re.compile(r"https?://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)", re.IGNORECASE)
_SKIP_OWNERS = {"topics", "orgs", "features", "settings", "marketplace", "sponsors", "collections", "apps"}


def load_seed_repositories(root: Path | None = None) -> list[dict[str, Any]]:
    candidates = []
    if root is not None:
        candidates.append(root / "config" / "seed-repositories.json")
    candidates.append(BUNDLED_ROOT / "config" / "seed-repositories.json")
    for path in candidates:
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            repos = data.get("repositories")
            if isinstance(repos, list):
                return [item for item in repos if isinstance(item, dict) and item.get("url")]
    return []


def load_discovery_queries(root: Path | None = None) -> list[str]:
    path = BUNDLED_ROOT / "config" / "discovery-queries.yaml"
    if root is not None and (root / "config" / "discovery-queries.yaml").is_file():
        path = root / "config" / "discovery-queries.yaml"
    if not path.is_file():
        return []
    from uaskills.config import load_yaml

    data = load_yaml(path)
    queries = []
    for item in data.get("queries") or []:
        if isinstance(item, str):
            queries.append(item)
        elif isinstance(item, dict) and item.get("query") and item.get("enabled", True):
            queries.append(str(item["query"]))
    return queries


def collect_from_seeds(
    library: Library,
    client: Any,
    *,
    repo_budget: int = 12,
    query_budget: int = 4,
) -> dict[str, Any]:
    """Inspect every configured seed, import skill packages, and record other links.

    Stops when the GitHub API budget is exhausted. Does not invent skill records.
    """
    withheld = withhold_stored_copies(library)
    summary: dict[str, Any] = {
        "withheld_existing": len(withheld),
        "inspected": [],
        "imports": [],
        "candidates": 0,
        "errors": [],
        "stopped": None,
    }
    seeds = load_seed_repositories(library.root)
    imported_repos = 0
    for seed in seeds:
        if summary["stopped"]:
            break
        try:
            owner, repo, _sub = parse_github_locator(str(seed["url"]))
        except LibraryError as exc:
            summary["errors"].append(redact(str(exc)))
            continue
        try:
            inspection = inspect_repository(library, client, owner, repo)
        except GitHubError as exc:
            summary["errors"].append(f"{owner}/{repo}: {redact(str(exc))}")
            if exc.status in {403, 429}:
                summary["stopped"] = "GitHub API rate limit or access limit."
            continue
        summary["inspected"].append(inspection)
        if inspection["skill_files"] and imported_repos < repo_budget:
            try:
                report = import_github_repo(library, client, f"{owner}/{repo}")
                summary["imports"].append({"repo": f"{owner}/{repo}", **report.as_dict()})
                imported_repos += 1
            except GitHubError as exc:
                summary["errors"].append(f"{owner}/{repo}: {redact(str(exc))}")
                if exc.status in {403, 429}:
                    summary["stopped"] = "GitHub API rate limit during import."
                    break
        elif inspection["skill_files"] == 0:
            summary["candidates"] += _harvest_readme_links(library, client, owner, repo, inspection["sha"])
    if summary["stopped"]:
        _save_inspections(library)
        return summary
    for query in load_discovery_queries(library.root)[:query_budget]:
        if summary["stopped"]:
            break
        try:
            found = _search_repositories(library, client, query)
        except GitHubError as exc:
            summary["errors"].append(f"search {query}: {redact(str(exc))}")
            if exc.status in {403, 429}:
                summary["stopped"] = "GitHub API rate limit during discovery."
            continue
        summary["candidates"] += found
    _save_inspections(library)
    return summary


def inspect_repository(library: Library, client: Any, owner: str, repo: str) -> dict[str, Any]:
    meta = client.repo(owner, repo)
    branch = str(meta.get("default_branch") or "HEAD")
    sha = str(client.commit_sha(owner, repo, branch))
    tree = client.tree(owner, repo, sha)
    entries = tree.get("tree") or []
    skill_files = [
        entry.get("path")
        for entry in entries
        if isinstance(entry.get("path"), str) and entry.get("path").split("/")[-1].lower() == "skill.md"
    ]
    license_info = meta.get("license") if isinstance(meta.get("license"), dict) else {}
    spdx = license_info.get("spdx_id")
    if spdx in {None, "", "NOASSERTION", "OTHER"}:
        spdx = None
    if skill_files:
        classification = "contains_skills"
    else:
        names = {str(entry.get("path", "")).split("/")[-1].lower() for entry in entries}
        if names & {"agents.md", "claude.md", ".cursorrules"}:
            classification = "agent_instructions"
        else:
            classification = "no_skill_file"
    inspection = {
        "repo": f"{owner}/{repo}",
        "sha": sha,
        "skill_files": len(skill_files),
        "entries": len(entries),
        "truncated": bool(tree.get("truncated")),
        "license_spdx": spdx,
        "classification": classification,
        "private": bool(meta.get("private")),
        "html_url": meta.get("html_url"),
    }
    library.conn.execute(
        """
        INSERT INTO inspections(repo, inspected_at, skill_files, license_spdx, classification, readme_links, notes)
        VALUES (?, ?, ?, ?, ?, 0, ?)
        ON CONFLICT(repo) DO UPDATE SET
            inspected_at = excluded.inspected_at,
            skill_files = excluded.skill_files,
            license_spdx = excluded.license_spdx,
            classification = excluded.classification,
            notes = excluded.notes
        """,
        (
            f"{owner}/{repo}",
            utc_now(),
            len(skill_files),
            spdx,
            classification,
            "tree truncated" if tree.get("truncated") else "",
        ),
    )
    library.conn.commit()
    catalog.upsert_source(
        library.conn,
        source_id=f"inspected-{owner}-{repo}"[:80],
        kind="github_repo",
        locator=f"{owner}/{repo}",
        status="inspected",
        detail=classification,
        metadata=inspection,
    )
    library.conn.commit()
    return inspection


def _harvest_readme_links(library: Library, client: Any, owner: str, repo: str, sha: str) -> int:
    text = ""
    for name in ("README.md", "readme.md", "README.rst"):
        try:
            data = client.file_bytes(owner, repo, sha, name, 400_000)
            text = data.decode("utf-8", errors="replace")
            break
        except GitHubError:
            continue
    if not text:
        return 0
    found = 0
    seen = set()
    for match in _LINK_RE.finditer(text):
        link_owner, link_repo = match.group(1), match.group(2).removesuffix(".git")
        if link_owner.lower() in _SKIP_OWNERS:
            continue
        if link_owner.lower() == owner.lower() and link_repo.lower() == repo.lower():
            continue
        locator = f"{link_owner}/{link_repo}"
        if locator.lower() in seen:
            continue
        seen.add(locator)
        record = {
            "ts": utc_now(),
            "locator": locator,
            "found_in": f"{owner}/{repo}",
            "commit_sha": sha,
            "status": "candidate",
        }
        append_jsonl(library.root / "sources" / "discovered-candidates.jsonl", record)
        catalog.upsert_source(
            library.conn,
            source_id=f"link-{locator}"[:80],
            kind="github_repo",
            locator=locator,
            status="candidate",
            detail=f"linked from {owner}/{repo}",
            metadata={"found_in": f"{owner}/{repo}"},
        )
        found += 1
    library.conn.execute("UPDATE inspections SET readme_links = ? WHERE repo = ?", (found, f"{owner}/{repo}"))
    library.conn.commit()
    return found


def _search_repositories(library: Library, client: Any, query: str) -> int:
    payload = client.search_repositories(query, 1, 20)
    count = 0
    for item in payload.get("items") or []:
        full = item.get("full_name")
        if not full:
            continue
        record = {
            "ts": utc_now(),
            "locator": full,
            "found_in": f"search:{query}",
            "status": "candidate",
            "html_url": item.get("html_url"),
        }
        append_jsonl(library.root / "sources" / "discovered-candidates.jsonl", record)
        catalog.upsert_source(
            library.conn,
            source_id=f"search-{full}"[:80],
            kind="github_repo",
            locator=str(full),
            status="candidate",
            detail=f"search:{query}",
            metadata={"html_url": item.get("html_url")},
        )
        count += 1
    if payload.get("incomplete_results"):
        append_jsonl(
            library.root / "sources" / "source_errors.jsonl",
            {"ts": utc_now(), "source": query, "error": "GitHub marked this search incomplete.", "recoverable": True},
        )
    library.conn.commit()
    return count


def _save_inspections(library: Library) -> None:
    library.conn.commit()


def collection_counts(library: Library) -> dict[str, Any]:
    def scalar(sql: str, params: tuple = ()) -> int:
        return int(library.conn.execute(sql, params).fetchone()[0])

    unique_skills = scalar(
        """
        SELECT COUNT(*) FROM skills
        WHERE is_synthetic = 0 AND canonical = 1
          AND local_path IS NOT NULL
          AND COALESCE(json_extract(extensions_json, '$.redistribution'), '') != 'withheld'
        """
    )
    return {
        "unique_skill_packages": unique_skills,
        "target": 100_000,
        "remaining_gap": max(0, 100_000 - unique_skills),
        "skill_versions": scalar(
            "SELECT COUNT(*) FROM skills WHERE is_synthetic = 0 AND commit_sha IS NOT NULL"
        ),
        "repositories_discovered": scalar("SELECT COUNT(*) FROM sources"),
        "repositories_inspected": scalar("SELECT COUNT(*) FROM inspections"),
        "repositories_containing_skills": scalar(
            "SELECT COUNT(*) FROM inspections WHERE skill_files > 0"
        ),
        "documentation_or_other_sources": scalar(
            "SELECT COUNT(*) FROM inspections WHERE skill_files = 0"
        ),
        "resources": scalar("SELECT COUNT(*) FROM resources"),
        "duplicates_excluded": scalar(
            "SELECT COUNT(*) FROM skills WHERE is_synthetic = 0 AND canonical = 0"
        ),
        "quarantined": scalar(
            "SELECT COUNT(*) FROM skills WHERE is_synthetic = 0 AND lifecycle_status = 'quarantined'"
        ),
        "withheld_for_license": scalar(
            """
            SELECT COUNT(*) FROM skills
            WHERE COALESCE(json_extract(extensions_json, '$.redistribution'), '') = 'withheld'
            """
        ),
        "failed_imports": scalar("SELECT COUNT(*) FROM events WHERE action = 'error'"),
        "synthetic_rows": scalar("SELECT COUNT(*) FROM skills WHERE is_synthetic = 1"),
    }


def write_collection_report(library: Library, path: Path | None = None) -> Path:
    counts = collection_counts(library)
    inspections = [
        dict(row)
        for row in library.conn.execute(
            "SELECT repo, skill_files, license_spdx, classification, readme_links, notes FROM inspections ORDER BY repo"
        )
    ]
    lines = [
        "# Collection report",
        "",
        "These counts are catalog records, not a claim that the 100,000-skill target has been met.",
        "Synthetic test rows are excluded from the unique skill count.",
        "",
        f"- Unique skill packages with local files: {counts['unique_skill_packages']}",
        f"- Target: {counts['target']}",
        f"- Remaining gap: {counts['remaining_gap']}",
        f"- Skill rows that record a commit: {counts['skill_versions']}",
        f"- Repositories discovered: {counts['repositories_discovered']}",
        f"- Repositories inspected: {counts['repositories_inspected']}",
        f"- Inspected repositories that contain SKILL.md: {counts['repositories_containing_skills']}",
        f"- Inspected repositories without a skill file: {counts['documentation_or_other_sources']}",
        f"- Non-skill resources: {counts['resources']}",
        f"- Duplicate rows excluded from default search: {counts['duplicates_excluded']}",
        f"- Quarantined: {counts['quarantined']}",
        f"- Withheld because the license forbids a local copy: {counts['withheld_for_license']}",
        f"- Recorded import errors: {counts['failed_imports']}",
        "",
        "## Inspections",
        "",
        "| Repository | SKILL.md files seen | License field | Classification | README links |",
        "| --- | --- | --- | --- | --- |",
    ]
    if not inspections:
        lines.append("| none yet |  |  |  |  |")
    for row in inspections:
        lines.append(
            f"| {row['repo']} | {row['skill_files']} | {row['license_spdx'] or ''} | "
            f"{row['classification']} | {row['readme_links']} |"
        )
    lines.extend(
        [
            "",
            "A SKILL.md count from a Git tree is not an imported-skill count. Import stores a package only after its files are copied or, when the license forbids that, a source link is recorded instead.",
            "",
            "GitHub repository search returns at most 1000 results per query, and code search is often unavailable without authentication. Those limits are coverage gaps, not empty results to be filled with generated skills.",
            "",
            "Continue with:",
            "",
            "```",
            "python tools/collect_skills.py",
            "python tools/import_skills.py --repo OWNER/REPOSITORY",
            "python tools/collection_report.py",
            "```",
            "",
        ]
    )
    errors_log = library.root / "sources" / "source_errors.jsonl"
    errors_log.parent.mkdir(parents=True, exist_ok=True)
    errors_log.touch(exist_ok=True)
    destination = path or (library.root / "docs" / "collection-report.md")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("\n".join(lines), encoding="utf-8")
    manifest = library.root / "sources" / "installation-manifest.jsonl"
    with manifest.open("w", encoding="utf-8") as handle:
        for row in library.conn.execute(
            """
            SELECT id, name, local_path, source_url, commit_sha, license_status, license_identifier,
                   lifecycle_status, extensions_json
            FROM skills WHERE is_synthetic = 0 ORDER BY name
            """
        ):
            extensions = json.loads(row["extensions_json"] or "{}")
            handle.write(
                json.dumps(
                    {
                        "id": row["id"],
                        "name": row["name"],
                        "local_path": row["local_path"],
                        "source_url": row["source_url"],
                        "commit_sha": row["commit_sha"],
                        "license_status": row["license_status"],
                        "license_identifier": row["license_identifier"],
                        "lifecycle_status": row["lifecycle_status"],
                        "redistribution": extensions.get("redistribution", "stored"),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
                + "\n"
            )
    return destination
