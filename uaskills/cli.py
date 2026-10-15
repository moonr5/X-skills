"""Command-line interface shared by the tools/ scripts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from uaskills import __version__
from uaskills.adapters import ADAPTERS, export_skills
from uaskills.catalog import get_skill, search_skills
from uaskills.dedupe import apply_duplicate_groups, duplicate_groups
from uaskills.discover import discover
from uaskills.errors import LibraryError
from uaskills.github_api import GitHubClient
from uaskills.importer import (
    import_archive,
    import_discovered,
    import_github_repo,
    import_local_directory,
    import_source_file,
    update_sources,
)
from uaskills.indexer import rebuild_index
from uaskills.library import Library
from uaskills.planner import plan_task
from uaskills.semantic import SemanticIndex
from uaskills.textutil import redact, utc_now
from uaskills.validate import validate_library


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def _root_parser(description: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--root", help="Library root. Defaults to this project or UASKILLS_ROOT.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    parser.add_argument("--verbose", action="store_true", help="Print progress messages to stderr.")
    return parser


def _library(args: argparse.Namespace) -> Library:
    return Library(args.root)


def _client(library: Library) -> GitHubClient:
    token_set = library.token() is not None
    if token_set:
        print("GitHub authentication: using an environment token.", file=sys.stderr)
    return GitHubClient(
        token=library.token(),
        api_base=library.settings["github"]["api_base"],
        raw_base=library.settings["github"]["raw_base"],
        user_agent=library.settings["github"]["user_agent"],
        min_interval=float(library.limit("min_interval_seconds")),
        raw_min_interval=float(library.limit("raw_min_interval_seconds")),
        timeout=float(library.limit("request_timeout_seconds")),
        max_retries=int(library.limit("max_retries")),
    )


def _emit(payload: dict, as_json: bool, human) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        human(payload)


def main(argv: list[str] | None = None) -> int:
    _configure_stdio()
    parser = argparse.ArgumentParser(prog="uaskills", description="Universal AI Skills Library")
    parser.add_argument("--version", action="store_true")
    sub = parser.add_subparsers(dest="command")
    _add_discover(sub)
    _add_import(sub)
    _add_update(sub)
    _add_search(sub)
    _add_validate(sub)
    _add_dedupe(sub)
    _add_index(sub)
    _add_plan(sub)
    _add_export(sub)
    _add_feedback(sub)
    _add_status(sub)
    _add_collect(sub)
    _add_report(sub)
    args = parser.parse_args(argv)
    if args.version or not args.command:
        print(f"universal-ai-skills {__version__}")
        if not args.command:
            parser.print_help()
        return 0
    try:
        return int(args.func(args))
    except LibraryError as exc:
        print(redact(str(exc)), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted. Re-run with --resume where that flag is available.", file=sys.stderr)
        return 1


def _add_discover(sub) -> None:
    parser = sub.add_parser("discover", help="Record candidate skill sources.")
    _common(parser)
    parser.add_argument("--query")
    parser.add_argument("--topic")
    parser.add_argument("--org")
    parser.add_argument("--repo")
    parser.add_argument("--url")
    parser.add_argument("--source-file")
    parser.add_argument("--local-dir")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--resume", action="store_true")
    parser.set_defaults(func=_cmd_discover)


def _add_import(sub) -> None:
    parser = sub.add_parser("import", help="Import skills from GitHub, a local directory, or an archive.")
    _common(parser)
    parser.add_argument("--repo")
    parser.add_argument("--url")
    parser.add_argument("--source-file")
    parser.add_argument("--local-dir")
    parser.add_argument("--archive")
    parser.add_argument("--from-discovered", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--replace", action="store_true", help="Replace managed skill files after writing a backup.")
    parser.set_defaults(func=_cmd_import)


def _add_update(sub) -> None:
    parser = sub.add_parser("update", help="Refresh imported sources when their commit changed.")
    _common(parser)
    parser.add_argument("--repo")
    parser.add_argument("--limit", type=int)
    parser.set_defaults(func=_cmd_update)


def _add_search(sub) -> None:
    parser = sub.add_parser("search", help="Search the local skill catalog.")
    _common(parser)
    parser.add_argument("--query", default="")
    parser.add_argument("--category")
    parser.add_argument("--capability")
    parser.add_argument("--source")
    parser.add_argument("--license", dest="license_status")
    parser.add_argument("--runtime")
    parser.add_argument("--runtime-strict", action="store_true")
    parser.add_argument("--version")
    parser.add_argument("--security-status")
    parser.add_argument("--name")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--include-quarantined", action="store_true")
    parser.add_argument("--include-deprecated", action="store_true")
    parser.add_argument("--include-duplicates", action="store_true")
    parser.add_argument("--semantic", action="store_true", help="Use the optional semantic interface.")
    parser.set_defaults(func=_cmd_search)


def _add_validate(sub) -> None:
    parser = sub.add_parser("validate", help="Check skill structure, metadata, and heuristic safety signals.")
    _common(parser)
    parser.add_argument("--runtime")
    parser.set_defaults(func=_cmd_validate)


def _add_dedupe(sub) -> None:
    parser = sub.add_parser("dedupe", help="Report or mark duplicate skill content.")
    _common(parser)
    parser.add_argument("--apply", action="store_true", help="Mark duplicates. Does not delete files.")
    parser.add_argument("--by", choices=("content", "tree"), default="content")
    parser.set_defaults(func=_cmd_dedupe)


def _add_index(sub) -> None:
    parser = sub.add_parser("build-index", help="Rebuild full-text search from stored skills.")
    _common(parser)
    parser.add_argument("--from-files", action="store_true", help="Restore catalog rows from metadata and skill files.")
    parser.set_defaults(func=_cmd_index)


def _add_plan(sub) -> None:
    parser = sub.add_parser("plan", help="Select skills and write an execution plan without running them.")
    _common(parser)
    parser.add_argument("--task", required=True)
    parser.add_argument("--runtime")
    parser.add_argument("--limit", type=int, default=5)
    parser.set_defaults(func=_cmd_plan)


def _add_export(sub) -> None:
    parser = sub.add_parser("export", help="Copy selected skills into an agent-specific directory.")
    _common(parser)
    parser.add_argument("--target", required=True, choices=sorted(ADAPTERS))
    parser.add_argument("--dest", required=True)
    parser.add_argument("--query")
    parser.add_argument("--category")
    parser.add_argument("--skill-id")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--on-conflict", choices=("backup", "skip", "overwrite"), default="backup")
    parser.set_defaults(func=_cmd_export)


def _add_feedback(sub) -> None:
    parser = sub.add_parser("feedback", help="Record local execution feedback. This does not verify a skill.")
    _common(parser)
    parser.add_argument("--skill-id", required=True)
    parser.add_argument("--task", required=True)
    parser.add_argument("--outcome", required=True, choices=("succeeded", "failed", "partial"))
    parser.add_argument("--notes", default="")
    parser.set_defaults(func=_cmd_feedback)


def _add_collect(sub) -> None:
    parser = sub.add_parser("collect", help="Inspect seed repositories and import skill packages.")
    _common(parser)
    parser.add_argument("--repo-budget", type=int, default=12)
    parser.add_argument("--query-budget", type=int, default=4)
    parser.set_defaults(func=_cmd_collect)


def _add_report(sub) -> None:
    parser = sub.add_parser("report", help="Write the collection report from catalog records.")
    _common(parser)
    parser.set_defaults(func=_cmd_report)


def _add_status(sub) -> None:
    parser = sub.add_parser("set-status", help="Record a human review decision.")
    _common(parser)
    parser.add_argument("--skill-id", required=True)
    parser.add_argument("--verification", choices=("unverified", "tested", "verified"))
    parser.add_argument("--license-status", choices=("unknown", "declared", "reviewed"))
    parser.add_argument("--note", default="")
    parser.set_defaults(func=_cmd_status)


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--root", help="Library root. Defaults to this project or UASKILLS_ROOT.")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--verbose", action="store_true")


def _cmd_discover(args) -> int:
    library = _library(args)
    needs_network = not any([args.repo, args.url, args.source_file, args.local_dir]) or bool(args.query or args.topic or args.org)
    explicit_offline = any([args.repo, args.url, args.source_file, args.local_dir]) and not any([args.query, args.topic, args.org])
    client = None if explicit_offline else _client(library)
    if needs_network and client is None and not explicit_offline:
        client = _client(library)
    report = discover(
        library,
        client,
        query=args.query,
        topic=args.topic,
        org=args.org,
        repo=args.repo,
        url=args.url,
        source_file=args.source_file,
        local_dir=args.local_dir,
        limit=args.limit,
        resume=args.resume,
    )
    _emit(report.as_dict(), args.json, _print_discover)
    library.close()
    return 1 if report.errors else 0


def _cmd_import(args) -> int:
    library = _library(args)
    chosen = [bool(args.repo or args.url), bool(args.source_file), bool(args.local_dir), bool(args.archive), bool(args.from_discovered)]
    if sum(chosen) != 1:
        print("Choose one of --repo, --url, --source-file, --local-dir, --archive, or --from-discovered.", file=sys.stderr)
        return 2
    if args.local_dir:
        report = import_local_directory(library, args.local_dir, resume=args.resume, replace=args.replace)
    elif args.archive:
        report = import_archive(library, args.archive, replace=args.replace)
    else:
        client = _client(library)
        if args.source_file:
            report = import_source_file(library, client, args.source_file, resume=args.resume, replace=args.replace)
        elif args.from_discovered:
            report = import_discovered(library, client, limit=args.limit, resume=args.resume, replace=args.replace)
        else:
            report = import_github_repo(library, client, args.repo or args.url, resume=args.resume, replace=args.replace)
    _emit(report.as_dict(), args.json, _print_import)
    library.close()
    return 1 if report.errors else 0


def _cmd_update(args) -> int:
    library = _library(args)
    report = update_sources(library, _client(library), repo=args.repo, limit=args.limit)
    _emit(report.as_dict(), args.json, _print_import)
    library.close()
    return 1 if report.errors else 0


def _cmd_search(args) -> int:
    library = _library(args)
    if args.semantic:
        SemanticIndex(library.settings).search(args.query or "", args.limit or 20)
    limit = args.limit or int(library.limit("search_default_limit"))
    limit = max(1, min(limit, int(library.limit("search_max_limit"))))
    offset = max(0, args.offset)
    result = search_skills(
        library.conn,
        query=args.query,
        category=args.category,
        capability=args.capability,
        source=args.source,
        license_status=args.license_status,
        runtime=args.runtime,
        version=args.version,
        security_status=args.security_status,
        name=args.name,
        limit=limit,
        offset=offset,
        include_quarantined=args.include_quarantined,
        include_deprecated=args.include_deprecated,
        include_duplicates=args.include_duplicates,
        runtime_strict=args.runtime_strict,
    )
    payload = {
        "total": result["total"],
        "offset": result["offset"],
        "limit": result["limit"],
        "results": [_public_skill(item) for item in result["results"]],
    }
    _emit(payload, args.json, _print_search)
    library.close()
    return 0


def _cmd_validate(args) -> int:
    library = _library(args)
    report = validate_library(library, runtime=args.runtime)
    _emit(report.as_dict(), args.json, _print_validate)
    library.close()
    return 2 if any(item.severity == "error" for item in report.issues) else 0


def _cmd_dedupe(args) -> int:
    library = _library(args)
    groups = duplicate_groups(library, by=args.by)
    changed = apply_duplicate_groups(library, groups) if args.apply else 0
    payload = {"groups": groups, "marked": changed}
    _emit(payload, args.json, _print_dedupe)
    library.close()
    return 0


def _cmd_index(args) -> int:
    library = _library(args)
    payload = rebuild_index(library, from_files=args.from_files)
    _emit(payload, args.json, lambda item: print(json.dumps(item, indent=2, ensure_ascii=False)))
    library.close()
    return 0


def _cmd_plan(args) -> int:
    library = _library(args)
    payload = plan_task(library, args.task, runtime=args.runtime, limit=max(1, args.limit))
    _emit(payload, args.json, _print_plan)
    library.close()
    return 0


def _cmd_export(args) -> int:
    library = _library(args)
    if args.skill_id:
        skill = get_skill(library.conn, args.skill_id)
        skills = [skill] if skill else []
    else:
        found = search_skills(
            library.conn,
            query=args.query or "",
            category=args.category,
            limit=args.limit,
        )
        skills = found["results"]
    report = export_skills(
        library,
        skills,
        target=args.target,
        dest=Path(args.dest),
        on_conflict=args.on_conflict,
    )
    _emit(report.as_dict(), args.json, lambda item: print(json.dumps(item, indent=2, ensure_ascii=False)))
    library.close()
    return 0


def _cmd_feedback(args) -> int:
    library = _library(args)
    if not get_skill(library.conn, args.skill_id):
        print(f"Unknown skill id: {args.skill_id}", file=sys.stderr)
        return 1
    library.conn.execute(
        "INSERT INTO feedback(skill_id, task, outcome, notes, created_at) VALUES (?, ?, ?, ?, ?)",
        (args.skill_id, args.task, args.outcome, args.notes, utc_now()),
    )
    library.conn.commit()
    print(json.dumps({"recorded": True, "skill_id": args.skill_id, "verification_status": "unchanged"}))
    library.close()
    return 0


def _cmd_collect(args) -> int:
    from uaskills.collect import collect_from_seeds, write_collection_report

    library = _library(args)
    summary = collect_from_seeds(
        library,
        _client(library),
        repo_budget=max(1, args.repo_budget),
        query_budget=max(0, args.query_budget),
    )
    write_collection_report(library)
    _emit(summary, args.json, _print_collect)
    library.close()
    return 1 if summary["errors"] and not summary["inspected"] else 0


def _cmd_report(args) -> int:
    from uaskills.collect import collection_counts, write_collection_report

    library = _library(args)
    path = write_collection_report(library)
    payload = collection_counts(library)
    payload["report"] = str(path)
    _emit(payload, True, lambda item: None)
    library.close()
    return 0


def _cmd_status(args) -> int:
    library = _library(args)
    skill = get_skill(library.conn, args.skill_id)
    if not skill:
        print(f"Unknown skill id: {args.skill_id}", file=sys.stderr)
        return 1
    if args.verification is None and args.license_status is None:
        print("Pass --verification or --license-status.", file=sys.stderr)
        return 2
    human = args.verification in {"tested", "verified"} or args.license_status == "reviewed"
    if human and not args.note.strip():
        print("A --note is required when recording a human review.", file=sys.stderr)
        return 2
    if args.verification:
        library.conn.execute(
            "UPDATE skills SET verification_status = ?, update_timestamp = ? WHERE id = ?",
            (args.verification, utc_now(), args.skill_id),
        )
    if args.license_status:
        library.conn.execute(
            "UPDATE skills SET license_status = ?, update_timestamp = ? WHERE id = ?",
            (args.license_status, utc_now(), args.skill_id),
        )
    library.conn.execute(
        "INSERT INTO events(ts, level, action, skill_id, message, details_json) VALUES (?, 'info', 'human_status', ?, ?, ?)",
        (
            utc_now(),
            args.skill_id,
            args.note or "status updated",
            json.dumps({"verification": args.verification, "license_status": args.license_status}),
        ),
    )
    library.conn.commit()
    print(json.dumps({"updated": args.skill_id, "note": args.note}))
    library.close()
    return 0


def _public_skill(skill: dict) -> dict:
    return {
        "id": skill["id"],
        "name": skill.get("name"),
        "description": skill.get("description"),
        "category": skill.get("category"),
        "local_path": skill.get("local_path"),
        "source_url": skill.get("source_url"),
        "repository": f"{skill.get('repo_owner')}/{skill.get('repo_name')}" if skill.get("repo_owner") else None,
        "commit_sha": skill.get("commit_sha"),
        "version": skill.get("version"),
        "license_status": skill.get("license_status"),
        "license_identifier": skill.get("license_identifier"),
        "lifecycle_status": skill.get("lifecycle_status"),
        "verification_status": skill.get("verification_status"),
        "security_scan_status": skill.get("security_scan_status"),
        "compatibility_status": skill.get("compatibility_status"),
        "supported_runtimes": skill.get("supported_runtimes"),
        "required_tools": skill.get("required_tools"),
        "tags": skill.get("tags"),
        "capabilities": skill.get("capabilities"),
        "duplicate_of": skill.get("duplicate_of"),
        "rank": skill.get("rank"),
    }


def _print_discover(payload: dict) -> None:
    print(f"Candidates recorded: {payload['candidates']}")
    for item in payload["repositories"]:
        print(f"- {item['locator']} ({item['kind']})")
    for message in payload["messages"]:
        print(message)


def _print_import(payload: dict) -> None:
    print(
        "Imported {skills_imported}, updated {skills_updated}, unchanged {skills_unchanged}, "
        "quarantined {skills_quarantined}, withheld {skills_withheld}, resources {resources_recorded}, "
        "errors {errors}.".format(**payload)
    )
    for message in payload["messages"]:
        print(message)


def _print_search(payload: dict) -> None:
    print(f"{payload['total']} skills (showing {len(payload['results'])} from offset {payload['offset']})")
    for item in payload["results"]:
        print(f"{item['name']}  [{item['category']}]  {item['lifecycle_status']}  license={item['license_status']}")
        if item.get("description"):
            print(f"  {item['description']}")
        print(f"  {item['local_path']}")
        if item.get("source_url"):
            print(f"  {item['source_url']}")


def _print_validate(payload: dict) -> None:
    print(f"Checked {payload['skills_checked']} skills: {payload['errors']} errors, {payload['warnings']} warnings.")
    for issue in payload["issues"]:
        print(f"- {issue['severity']} {issue['code']}: {issue['message']} ({issue['skill_id']})")


def _print_dedupe(payload: dict) -> None:
    print(f"Duplicate groups: {len(payload['groups'])}; records marked: {payload['marked']}")
    for group in payload["groups"]:
        names = ", ".join(member["name"] or member["id"] for member in group["members"])
        print(f"- {group['count']} via {group['by']}: {names}")


def _print_plan(payload: dict) -> None:
    print(f"Task: {payload['task']}")
    print("Execution performed: false")
    print("Selected skills:")
    if not payload["selected_skills"]:
        print("- none")
    for skill in payload["selected_skills"]:
        print(f"- {skill['name']} ({skill['local_path']})")
        print(f"  reason: {skill['reason']}")
    if payload["compatibility_warnings"]:
        print("Warnings:")
        for warning in payload["compatibility_warnings"]:
            print(f"- {warning}")
    print("Limitations:")
    for note in payload["limitations"]:
        print(f"- {note}")


def main_discover(argv: list[str] | None = None) -> int:
    return _delegate("discover", argv)


def main_import(argv: list[str] | None = None) -> int:
    return _delegate("import", argv)


def main_update(argv: list[str] | None = None) -> int:
    return _delegate("update", argv)


def main_search(argv: list[str] | None = None) -> int:
    return _delegate("search", argv)


def main_validate(argv: list[str] | None = None) -> int:
    return _delegate("validate", argv)


def main_dedupe(argv: list[str] | None = None) -> int:
    return _delegate("dedupe", argv)


def main_index(argv: list[str] | None = None) -> int:
    return _delegate("build-index", argv)


def main_plan(argv: list[str] | None = None) -> int:
    return _delegate("plan", argv)


def _print_collect(payload: dict) -> None:
    print(f"Withheld existing copies: {payload['withheld_existing']}")
    for item in payload["inspected"]:
        print(
            f"- {item['repo']}: {item['classification']}, "
            f"SKILL.md files in tree {item['skill_files']}, license {item.get('license_spdx')}"
        )
    for item in payload["imports"]:
        print(
            f"  imported {item['repo']}: new {item['skills_imported']}, "
            f"unchanged {item['skills_unchanged']}, withheld {item['skills_withheld']}, errors {item['errors']}"
        )
    print(f"Candidate links recorded: {payload['candidates']}")
    if payload.get("stopped"):
        print(payload["stopped"])
    for error in payload["errors"]:
        print(error)


def main_collect(argv: list[str] | None = None) -> int:
    return _delegate("collect", argv)


def main_report(argv: list[str] | None = None) -> int:
    return _delegate("report", argv)


def main_export(argv: list[str] | None = None) -> int:
    return _delegate("export", argv)


def _delegate(command: str, argv: list[str] | None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    return main([command, *args])
