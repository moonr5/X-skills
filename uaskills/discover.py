"""Find candidate skill sources. Discovery records candidates; it does not import them."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from uaskills.errors import GitHubError, LibraryError
from uaskills.library import Library
from uaskills.locators import parse_github_locator, read_locator_file
from uaskills.textutil import append_jsonl, redact, skill_id, utc_now
from uaskills import catalog


@dataclass
class DiscoverReport:
    candidates: int = 0
    errors: int = 0
    repositories: list[dict[str, Any]] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "candidates": self.candidates,
            "errors": self.errors,
            "repositories": self.repositories,
            "messages": self.messages,
        }


def _source_id(kind: str, locator: str) -> str:
    return hashlib.sha256(f"{kind}\n{locator}".encode("utf-8")).hexdigest()[:24]


def record_candidate(
    library: Library,
    *,
    kind: str,
    locator: str,
    detail: str,
    metadata: dict[str, Any] | None = None,
    report: DiscoverReport,
) -> None:
    source_key = _source_id(kind, locator)
    catalog.upsert_source(
        library.conn,
        source_id=source_key,
        kind=kind,
        locator=locator,
        status="candidate",
        detail=detail,
        metadata=metadata or {},
    )
    record = {
        "id": source_key,
        "kind": kind,
        "locator": locator,
        "status": "candidate",
        "detail": detail,
        "metadata": metadata or {},
        "discovered_at": utc_now(),
    }
    append_jsonl(library.root / "sources" / "repositories.jsonl", record)
    report.candidates += 1
    report.repositories.append({"kind": kind, "locator": locator, "detail": detail})


def discover(
    library: Library,
    client: Any | None,
    *,
    query: str | None = None,
    topic: str | None = None,
    org: str | None = None,
    repo: str | None = None,
    url: str | None = None,
    source_file: str | None = None,
    local_dir: str | None = None,
    use_config: bool = False,
    limit: int | None = None,
    resume: bool = False,
) -> DiscoverReport:
    """Record candidate sources. Network calls happen only for search and org listings."""
    report = DiscoverReport()
    cap = int(limit if limit is not None else library.limit("max_repos_per_run"))
    per_page = min(100, int(library.limit("discover_per_page")))
    requests = _requests(
        library,
        query=query,
        topic=topic,
        org=org,
        repo=repo,
        url=url,
        source_file=source_file,
        local_dir=local_dir,
        use_config=use_config,
    )
    if not requests:
        report.messages.append("No discovery sources were provided.")
        return report
    job_params = {
        "query": query,
        "topic": topic,
        "org": org,
        "repo": repo,
        "url": url,
        "source_file": source_file,
        "local_dir": local_dir,
        "use_config": use_config,
        "limit": cap,
    }
    job_key = skill_id("job", "discover", json.dumps(job_params, sort_keys=True), "v1")
    cursor = {"completed": [], "page": 1}
    if resume:
        existing = catalog.get_job(library.conn, job_key)
        if existing and existing["status"] in {"running", "interrupted"}:
            cursor = existing["cursor"] or cursor
    completed = set(cursor.get("completed") or [])
    try:
        for request in requests:
            if report.candidates >= cap:
                break
            marker = json.dumps(request, sort_keys=True)
            if marker in completed:
                continue
            _run_request(
                library,
                client,
                request,
                report,
                cap=cap,
                per_page=per_page,
                start_page=int(cursor.get("page") or 1) if not completed else 1,
            )
            completed.add(marker)
            cursor = {"completed": sorted(completed), "page": 1}
            catalog.save_job(
                library.conn,
                job_id=job_key,
                kind="discover",
                status="running",
                cursor=cursor,
                params=job_params,
            )
            library.conn.commit()
        catalog.save_job(
            library.conn,
            job_id=job_key,
            kind="discover",
            status="complete",
            cursor=cursor,
            params=job_params,
            message=f"{report.candidates} candidates",
        )
        library.conn.commit()
    except KeyboardInterrupt:
        catalog.save_job(
            library.conn,
            job_id=job_key,
            kind="discover",
            status="interrupted",
            cursor={"completed": sorted(completed), "page": cursor.get("page", 1)},
            params=job_params,
            message="Discovery interrupted",
        )
        library.conn.commit()
        raise
    except GitHubError as exc:
        report.errors += 1
        report.messages.append(redact(str(exc)))
        catalog.save_job(
            library.conn,
            job_id=job_key,
            kind="discover",
            status="interrupted",
            cursor={"completed": sorted(completed), "page": cursor.get("page", 1)},
            params=job_params,
            message=redact(str(exc)),
        )
        library.conn.commit()
    return report


def _requests(library: Library, **flags: Any) -> list[dict[str, Any]]:
    requests: list[dict[str, Any]] = []
    if flags.get("query"):
        requests.append({"type": "github_search", "query": flags["query"]})
    if flags.get("topic"):
        requests.append({"type": "github_search", "query": f"topic:{flags['topic']}"})
    if flags.get("org"):
        requests.append({"type": "github_org", "org": flags["org"]})
    if flags.get("repo"):
        requests.append({"type": "github_repo", "repo": flags["repo"]})
    if flags.get("url"):
        requests.append({"type": "github_repo", "repo": flags["url"]})
    if flags.get("source_file"):
        for line in read_locator_file(library.root / flags["source_file"] if not _is_absolute(flags["source_file"]) else __import__("pathlib").Path(flags["source_file"])):
            requests.append({"type": "locator", "locator": line})
    if flags.get("local_dir"):
        requests.append({"type": "local", "path": flags["local_dir"]})
    explicit = any(flags.get(key) for key in ("query", "topic", "org", "repo", "url", "source_file", "local_dir"))
    if flags.get("use_config") or not explicit:
        for source in library.configured_sources:
            if source.get("enabled", True) is False:
                continue
            requests.append(dict(source))
    return requests


def _is_absolute(value: str) -> bool:
    from pathlib import Path

    return Path(value).is_absolute() or (len(value) > 2 and value[1] == ":")


def _run_request(library: Library, client: Any, request: dict[str, Any], report: DiscoverReport, cap: int, per_page: int, start_page: int) -> None:
    kind = str(request.get("type") or "")
    if kind == "locator":
        _record_locator(library, str(request["locator"]), report)
        return
    if kind == "local":
        record_candidate(library, kind="local", locator=str(request.get("path")), detail="local directory", report=report)
        return
    if kind == "github_repo":
        owner, repo, subpath = parse_github_locator(str(request.get("repo") or request.get("locator")))
        locator = f"{owner}/{repo}" + (f"/{subpath}" if subpath else "")
        record_candidate(
            library,
            kind="github_repo",
            locator=locator,
            detail="configured repository",
            metadata={"subpath": subpath},
            report=report,
        )
        return
    if client is None:
        raise LibraryError("This discovery source needs a GitHub client.")
    if kind == "github_search":
        query = str(request.get("query") or "")
        page = max(1, start_page)
        while report.candidates < cap and (page - 1) * per_page < 1000:
            payload = client.search_repositories(query, page, per_page)
            items = payload.get("items") or []
            if not items:
                break
            for item in items:
                if report.candidates >= cap:
                    break
                full = item.get("full_name")
                if not full:
                    continue
                record_candidate(
                    library,
                    kind="github_repo",
                    locator=str(full),
                    detail=f"search:{query}",
                    metadata={
                        "html_url": item.get("html_url"),
                        "description": item.get("description"),
                        "license": (item.get("license") or {}).get("spdx_id") if isinstance(item.get("license"), dict) else None,
                        "incomplete_results": bool(payload.get("incomplete_results")),
                    },
                    report=report,
                )
            if len(items) < per_page:
                break
            page += 1
        if (page - 1) * per_page >= 1000:
            report.messages.append(
                "GitHub repository search returns at most 1000 results per query. Add another source to go further."
            )
        return
    if kind == "github_org":
        org = str(request.get("org") or "")
        page = max(1, start_page)
        while report.candidates < cap:
            items, has_next = client.list_org_repos(org, page, per_page)
            if not items:
                break
            for item in items:
                if report.candidates >= cap:
                    break
                full = item.get("full_name")
                if full:
                    record_candidate(
                        library,
                        kind="github_repo",
                        locator=str(full),
                        detail=f"org:{org}",
                        metadata={"html_url": item.get("html_url")},
                        report=report,
                    )
            if not has_next:
                break
            page += 1
        return
    report.messages.append(f"Skipped unsupported source type '{kind}'.")


def _record_locator(library: Library, locator: str, report: DiscoverReport) -> None:
    if locator.lower().startswith("local:"):
        record_candidate(library, kind="local", locator=locator.split(":", 1)[1], detail="source file", report=report)
        return
    owner, repo, subpath = parse_github_locator(locator)
    record_candidate(
        library,
        kind="github_repo",
        locator=f"{owner}/{repo}" + (f"/{subpath}" if subpath else ""),
        detail="source file",
        metadata={"subpath": subpath},
        report=report,
    )
