"""Import skills from a local directory or a public GitHub repository.

Imported files are copied and indexed. They are never executed.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from uaskills import catalog
from uaskills.errors import FileTooLarge, GitHubError, LibraryError, UnsafePath
from uaskills.library import Library
from uaskills.locators import parse_github_locator, read_locator_file
from uaskills.models import ResourceRecord, SkillRecord
from uaskills.parser import (
    AGENT_INSTRUCTION_NAMES,
    MANIFEST_NAMES,
    SKILL_MD_NAMES,
    parse_skill_bytes,
)
from uaskills.safety import (
    SKIP_DIR_NAMES,
    Finding,
    is_within,
    read_regular_file,
    safe_join,
    safe_segment,
    should_quarantine,
    walk_source,
)
from uaskills.textutil import append_jsonl, redact, skill_id, utc_now

LANG_BY_SUFFIX = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".sh": "shell",
    ".ps1": "powershell",
    ".rb": "ruby",
    ".go": "go",
    ".rs": "rust",
}
CONTENT_PREFIXES = (
    "skills",
    ".cursor/skills",
    ".claude/skills",
    ".agents/skills",
    ".github/skills",
    ".gemini/skills",
)


@dataclass
class ImportReport:
    skills_imported: int = 0
    skills_updated: int = 0
    skills_unchanged: int = 0
    skills_quarantined: int = 0
    skills_withheld: int = 0
    resources_recorded: int = 0
    errors: int = 0
    skill_ids: list[str] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "skills_imported": self.skills_imported,
            "skills_updated": self.skills_updated,
            "skills_unchanged": self.skills_unchanged,
            "skills_quarantined": self.skills_quarantined,
            "skills_withheld": self.skills_withheld,
            "resources_recorded": self.resources_recorded,
            "errors": self.errors,
            "skill_ids": self.skill_ids,
            "messages": self.messages,
        }

    def add(self, action: str, record: SkillRecord) -> None:
        if record.lifecycle_status == "quarantined":
            self.skills_quarantined += 1
        if action == "inserted":
            self.skills_imported += 1
        elif action == "updated":
            self.skills_updated += 1
        else:
            self.skills_unchanged += 1
        self.skill_ids.append(record.id)


def import_local_directory(
    library: Library,
    path: str | Path,
    *,
    resume: bool = False,
    replace: bool = False,
    job_kind: str = "import-local",
) -> ImportReport:
    source = Path(path).expanduser().resolve()
    if not source.is_dir():
        raise LibraryError(f"Local skill directory not found: {source}")
    report = ImportReport()
    origin_key = hashlib.sha256(str(source).encode("utf-8")).hexdigest()[:16]
    params = {"path": str(source), "replace": replace}
    job_key = skill_id("job", job_kind, str(source), "v1")
    completed = _completed(library, job_key) if resume else set()
    skill_files, instruction_files = _find_local_markers(source, int(library.limit("max_walk_depth")))
    groups = _group_by_parent(skill_files)
    nested = set(groups)
    if not groups:
        names = [path.name for path in instruction_files] or [item.name for item in source.iterdir()]
        kind, reason = _classify_names(names)
        _record_non_skill(
            library,
            report,
            kind=kind,
            reason=reason,
            evidence=_sample_names(source),
            owner="local",
            repo=source.name,
            source_url=source.as_uri(),
            locator=str(source),
        )
        _finish_job(library, job_key, job_kind, params, completed, "complete", report)
        return report
    ordered = sorted(groups, key=lambda item: str(item))
    try:
        for index, skill_dir in enumerate(ordered):
            if index >= int(library.limit("max_skills_per_repo")):
                report.messages.append("Stopped at max_skills_per_repo.")
                break
            rel = _relative_posix(skill_dir, source)
            marker = rel or "."
            if marker in completed:
                continue
            try:
                files, findings = _read_local_skill(skill_dir, source, nested, library)
                record = _record_from_files(
                    library,
                    files=files,
                    findings=findings,
                    relpath=rel,
                    directory_name=skill_dir.name,
                    source_host="local",
                    owner="local",
                    repo=f"{safe_segment(source.name)}-{origin_key[:8]}",
                    repo_url=None,
                    source_url=source.as_uri(),
                    commit_sha=None,
                    origin_path=str(skill_dir),
                    identity_owner=origin_key,
                    identity_repo=source.name,
                    topics=None,
                    repo_license=None,
                    repo_license_evidence=None,
                )
                action = _commit_skill(library, record, files, report, replace=replace)
                if action:
                    report.add(action, record)
                    _history(library, action, record)
            except KeyboardInterrupt:
                raise
            except (OSError, LibraryError, UnicodeError, ValueError) as exc:
                _fail(library, report, str(source), exc)
            completed.add(marker)
            _checkpoint(library, job_key, job_kind, params, completed, "running")
        _finish_job(library, job_key, job_kind, params, completed, "complete", report)
    except KeyboardInterrupt:
        _checkpoint(library, job_key, job_kind, params, completed, "interrupted")
        raise
    return report


def import_github_repo(
    library: Library,
    client: Any,
    locator: str,
    *,
    resume: bool = False,
    replace: bool = False,
    subpath: str | None = None,
) -> ImportReport:
    owner, repo, parsed_subpath = parse_github_locator(locator)
    subpath = subpath or parsed_subpath
    report = ImportReport()
    params = {"repo": f"{owner}/{repo}", "subpath": subpath, "replace": replace}
    job_key = skill_id("job", "import-github", f"{owner}/{repo}", subpath or "")
    completed = _completed(library, job_key) if resume else set()
    try:
        meta = client.repo(owner, repo)
        branch = str(meta.get("default_branch") or "HEAD")
        sha = str(client.commit_sha(owner, repo, branch))
        if not _is_sha(sha):
            raise LibraryError("Refusing a commit id that is not a git sha")
        license_info = meta.get("license") if isinstance(meta.get("license"), dict) else {}
        spdx = license_info.get("spdx_id")
        if spdx in {None, "", "NOASSERTION", "OTHER"}:
            spdx = None
        topics = [str(item) for item in meta.get("topics") or [] if isinstance(meta.get("topics"), list)]
        blobs, truncated = _github_paths(library, client, owner, repo, sha)
        paths = [item["path"] for item in blobs]
        sizes = {item["path"]: item["size"] for item in blobs}
        grouped = _group_tree_paths(paths)
        if subpath:
            prefix = subpath.strip("/")
            grouped = {
                key: value
                for key, value in grouped.items()
                if key == prefix or key.startswith(prefix + "/")
            }
        if truncated:
            report.messages.append(f"{owner}/{repo} tree was truncated; import used the paths that were returned.")
        if not grouped:
            kind, reason = _classify_names([path.split("/")[-1] for path in paths])
            _record_non_skill(
                library,
                report,
                kind=kind,
                reason=reason,
                evidence="; ".join(paths[:30]),
                owner=owner,
                repo=repo,
                source_url=meta.get("html_url") or f"https://github.com/{owner}/{repo}",
                locator=f"{owner}/{repo}",
                commit_sha=sha,
            )
            _finish_job(library, job_key, "import-github", params, completed, "complete", report)
            return report
        seen_ids: set[str] = set()
        downloaded = 0
        processed = 0
        already = {
            row["id"]
            for row in library.conn.execute(
                """
                SELECT id FROM skills
                WHERE source_host = 'github.com' AND repo_owner = ? AND repo_name = ?
                  AND commit_sha = ? AND is_synthetic = 0
                """,
                (owner, repo, sha),
            )
        }
        for directory in sorted(grouped):
            marker = f"{owner}/{repo}:{directory or '.'}"
            current_id = skill_id("github.com", owner, repo, directory or ".")
            if marker in completed or (current_id in already and not replace):
                seen_ids.add(current_id)
                if current_id in already and marker not in completed:
                    report.skills_unchanged += 1
                completed.add(marker)
                continue
            if processed >= int(library.limit("max_skills_per_repo")):
                report.messages.append("Stopped at max_skills_per_repo. Run the import again to continue.")
                break
            processed += 1
            print(f"importing {marker}", file=sys.stderr)
            try:
                selected = _prioritize(grouped[directory], directory, int(library.limit("max_files_per_skill")))
                files, findings, used = _download_github_files(
                    library, client, owner, repo, sha, directory, selected, sizes
                )
                downloaded += used
                if downloaded > int(library.limit("max_repo_download_bytes")):
                    report.messages.append("Stopped at max_repo_download_bytes.")
                    break
                record = _record_from_files(
                    library,
                    files=files,
                    findings=findings,
                    relpath=directory,
                    directory_name=directory.split("/")[-1] if directory else repo,
                    source_host="github.com",
                    owner=owner,
                    repo=repo,
                    repo_url=meta.get("html_url") or f"https://github.com/{owner}/{repo}",
                    source_url=_blob_url(owner, repo, sha, directory),
                    commit_sha=sha,
                    origin_path=None,
                    identity_owner=owner,
                    identity_repo=repo,
                    topics=topics,
                    repo_license=spdx,
                    repo_license_evidence="github.license.spdx_id" if spdx else None,
                )
                action = _commit_skill(library, record, files, report, replace=replace)
                seen_ids.add(record.id)
                if action:
                    report.add(action, record)
                    _history(library, action, record, commit_sha=sha)
            except KeyboardInterrupt:
                raise
            except (GitHubError, LibraryError, OSError, UnicodeError, ValueError) as exc:
                _fail(library, report, f"{owner}/{repo}", exc)
            completed.add(marker)
            _checkpoint(library, job_key, "import-github", params, completed, "running")
        missing = catalog.mark_missing_upstream(library.conn, owner, repo, seen_ids, sha)
        if missing:
            report.messages.append(f"{len(missing)} previously imported skills were not in this tree.")
        library.conn.commit()
        _finish_job(library, job_key, "import-github", params, completed, "complete", report)
    except KeyboardInterrupt:
        _checkpoint(library, job_key, "import-github", params, completed, "interrupted")
        raise
    except (GitHubError, LibraryError) as exc:
        _fail(library, report, locator, exc)
        _checkpoint(library, job_key, "import-github", params, completed, "interrupted")
    return report


_SECRET_NAMES = {".env", ".env.local", ".env.production", "credentials.json", "secrets.json", "id_rsa", "id_ed25519"}


def import_github_checkout(
    library: Library,
    checkout: str | Path,
    owner: str,
    repo: str,
    *,
    commit_sha: str,
    repo_url: str | None = None,
    license_spdx: str | None = None,
    topics: list[str] | None = None,
    replace: bool = False,
) -> ImportReport:
    """Import skill packages from a local Git checkout without executing files.

    Identity matches a GitHub import, so a package already stored from the same
    repository is updated or left unchanged instead of being copied again.
    """
    source = Path(checkout).expanduser().resolve()
    if not source.is_dir():
        raise LibraryError(f"Checkout not found: {source}")
    if not _is_sha(commit_sha):
        raise LibraryError("Refusing a commit id that is not a git sha")
    report = ImportReport()
    repo_url = repo_url or f"https://github.com/{owner}/{repo}"
    skill_files, _notes = _find_local_markers(source, int(library.limit("max_walk_depth")))
    groups = _group_by_parent(skill_files)
    nested = set(groups)
    if not groups:
        kind, reason = _classify_names([path.name for path in source.rglob("*") if path.is_file()][:40])
        _record_non_skill(
            library,
            report,
            kind=kind,
            reason=reason,
            evidence=_sample_names(source),
            owner=owner,
            repo=repo,
            source_url=repo_url,
            locator=f"{owner}/{repo}",
            commit_sha=commit_sha,
        )
        return report
    processed = 0
    seen_ids: set[str] = set()
    for skill_dir in sorted(groups, key=lambda item: str(item)):
        rel = _relative_posix(skill_dir, source)
        current_id = skill_id("github.com", owner, repo, rel or ".")
        seen_ids.add(current_id)
        existing = catalog.get_skill(library.conn, current_id)
        if existing and existing.get("commit_sha") == commit_sha and not replace:
            report.skills_unchanged += 1
            continue
        if processed >= int(library.limit("max_skills_per_repo")):
            report.messages.append("Stopped at max_skills_per_repo. Run the import again to continue.")
            break
        processed += 1
        try:
            files, findings = _read_local_skill(skill_dir, source, nested, library)
            files, secret_findings = _drop_secret_files(files)
            findings.extend(secret_findings)
            record = _record_from_files(
                library,
                files=files,
                findings=findings,
                relpath=rel,
                directory_name=skill_dir.name,
                source_host="github.com",
                owner=owner,
                repo=repo,
                repo_url=repo_url,
                source_url=f"{repo_url}/tree/{commit_sha}/{rel}" if rel else repo_url,
                commit_sha=commit_sha,
                origin_path=str(skill_dir),
                identity_owner=owner,
                identity_repo=repo,
                topics=topics,
                repo_license=license_spdx,
                repo_license_evidence="checkout.license" if license_spdx else None,
            )
            action = _commit_skill(library, record, files, report, replace=replace)
            if action:
                report.add(action, record)
                _history(library, action, record, commit_sha=commit_sha)
        except (OSError, LibraryError, UnicodeError, ValueError) as exc:
            _fail(library, report, f"{owner}/{repo}", exc)
    missing = catalog.mark_missing_upstream(library.conn, owner, repo, seen_ids, commit_sha)
    if missing:
        report.messages.append(f"{len(missing)} previously imported skills were not in this checkout.")
    library.conn.commit()
    return report


def _drop_secret_files(files: list[tuple[str, bytes]]) -> tuple[list[tuple[str, bytes]], list[Finding]]:
    kept: list[tuple[str, bytes]] = []
    findings: list[Finding] = []
    for relative, data in files:
        name = Path(relative).name.lower()
        if name in _SECRET_NAMES or name.startswith(".env") or Path(relative).suffix.lower() in {".pem", ".key"}:
            findings.append(Finding("secret_file", "high", "Skipped a file that looks like a credential.", relative))
            continue
        kept.append((relative, data))
    return kept, findings


def import_archive(library: Library, archive_path: str | Path, *, replace: bool = False) -> ImportReport:
    """Extract a zip archive with path and size checks, then import it as a local directory."""
    from uaskills.safety import open_zip_bytes, safe_extract_zip

    path = Path(archive_path).expanduser().resolve()
    if not path.is_file():
        raise LibraryError(f"Archive not found: {path}")
    data = path.read_bytes()
    limits = library.settings["limits"]
    destination = library.root / "index" / "tmp" / safe_segment(path.stem)
    if destination.exists():
        shutil.rmtree(destination)
    with open_zip_bytes(data, limits) as archive:
        safe_extract_zip(archive, destination, limits)
    return import_local_directory(library, destination, replace=replace, job_kind="import-archive")


def import_source_file(library: Library, client: Any, path: str | Path, *, resume: bool = False, replace: bool = False) -> ImportReport:
    source = Path(path)
    if not source.is_absolute():
        candidate = library.root / source
        source = candidate if candidate.is_file() else source
    combined = ImportReport()
    for line in read_locator_file(source):
        if line.lower().startswith("local:"):
            part = import_local_directory(library, line.split(":", 1)[1], resume=resume, replace=replace)
        else:
            part = import_github_repo(library, client, line, resume=resume, replace=replace)
        _merge_reports(combined, part)
    return combined


def import_discovered(library: Library, client: Any, *, limit: int | None = None, resume: bool = False, replace: bool = False) -> ImportReport:
    cap = int(limit if limit is not None else library.limit("max_repos_per_run"))
    rows = library.conn.execute(
        """
        SELECT kind, locator, metadata_json FROM sources
        WHERE status = 'candidate'
        ORDER BY updated_at ASC, id ASC
        """
    ).fetchall()
    combined = ImportReport()
    used = 0
    for row in rows:
        if used >= cap:
            break
        metadata = json.loads(row["metadata_json"] or "{}")
        try:
            if row["kind"] == "local":
                part = import_local_directory(library, row["locator"], resume=resume, replace=replace)
            elif row["kind"] == "github_repo":
                part = import_github_repo(
                    library,
                    client,
                    row["locator"],
                    resume=resume,
                    replace=replace,
                    subpath=metadata.get("subpath"),
                )
            else:
                combined.messages.append(f"Skipped source type {row['kind']}.")
                continue
        except (GitHubError, LibraryError) as exc:
            _fail(library, combined, row["locator"], exc)
            continue
        _merge_reports(combined, part)
        used += 1
    return combined


def update_sources(library: Library, client: Any, *, repo: str | None = None, limit: int | None = None) -> ImportReport:
    """Re-import sources whose commit changed. Unchanged commits are not downloaded again."""
    cap = int(limit if limit is not None else library.limit("max_repos_per_run"))
    report = ImportReport()
    clauses = "source_host = 'github.com' AND is_synthetic = 0"
    params: list[Any] = []
    if repo:
        owner, name, _sub = parse_github_locator(repo)
        clauses += " AND repo_owner = ? AND repo_name = ?"
        params.extend([owner, name])
    rows = library.conn.execute(
        f"""
        SELECT repo_owner, repo_name, GROUP_CONCAT(DISTINCT commit_sha) AS shas, COUNT(*) AS n
        FROM skills WHERE {clauses}
        GROUP BY repo_owner, repo_name
        ORDER BY repo_owner, repo_name
        """,
        params,
    ).fetchall()
    processed = 0
    for row in rows:
        if processed >= cap:
            break
        owner, name = row["repo_owner"], row["repo_name"]
        try:
            meta = client.repo(owner, name)
            branch = str(meta.get("default_branch") or "HEAD")
            sha = str(client.commit_sha(owner, name, branch))
        except (GitHubError, LibraryError) as exc:
            _fail(library, report, f"{owner}/{name}", exc)
            processed += 1
            continue
        known = set(filter(None, (row["shas"] or "").split(",")))
        if known == {sha}:
            report.skills_unchanged += int(row["n"])
            report.messages.append(f"{owner}/{name} is already at {sha[:12]}.")
            processed += 1
            continue
        part = import_github_repo(library, client, f"{owner}/{name}", replace=True)
        _merge_reports(report, part)
        processed += 1
    local_rows = library.conn.execute(
        """
        SELECT DISTINCT origin_path FROM skills
        WHERE source_host = 'local' AND origin_path IS NOT NULL AND is_synthetic = 0
        """
    ).fetchall()
    seen_roots: set[str] = set()
    for row in local_rows:
        if processed >= cap:
            break
        origin = Path(row["origin_path"])
        root = origin if origin.is_dir() and (origin / "SKILL.md").is_file() else origin.parent
        # Walk up to the imported root recorded in the URI is not available; re-read the skill directory.
        key = str(origin.resolve()) if origin.exists() else ""
        if not key or key in seen_roots:
            continue
        seen_roots.add(key)
        if not origin.exists():
            report.messages.append(f"Local source is gone: {origin}")
            continue
        source = origin if _contains_skill(origin) else origin.parent
        if not source.is_dir():
            continue
        part = import_local_directory(library, source, replace=True, job_kind="update-local")
        _merge_reports(report, part)
        processed += 1
    return report


def _contains_skill(path: Path) -> bool:
    if not path.is_dir():
        return False
    return any(child.name.lower() in SKILL_MD_NAMES or child.name.lower() in MANIFEST_NAMES for child in path.iterdir() if child.is_file())


def _record_from_files(library: Library, **kwargs: Any) -> SkillRecord:
    files: list[tuple[str, bytes]] = kwargs["files"]
    findings: list[Finding] = kwargs["findings"]
    primary_name, primary_bytes = _primary(files)
    languages = sorted({LANG_BY_SUFFIX[Path(rel).suffix.lower()] for rel, _ in files if Path(rel).suffix.lower() in LANG_BY_SUFFIX})
    record = parse_skill_bytes(
        skill_key=skill_id(
            kwargs["source_host"],
            kwargs["identity_owner"],
            kwargs["identity_repo"],
            kwargs["relpath"] or ".",
        ),
        relpath=kwargs["relpath"] or ".",
        directory_name=kwargs["directory_name"],
        primary_name=primary_name,
        primary_bytes=primary_bytes,
        files=files,
        source_host=kwargs["source_host"],
        repo_owner=kwargs["owner"],
        repo_name=kwargs["repo"],
        repo_url=kwargs["repo_url"],
        source_url=kwargs["source_url"],
        commit_sha=kwargs["commit_sha"],
        origin_path=kwargs["origin_path"],
        categories=library.category_config["categories"],
        repo_license=kwargs["repo_license"],
        repo_license_evidence=kwargs["repo_license_evidence"],
        source_topics=kwargs["topics"],
        observed_languages=languages,
        timestamp=utc_now(),
        excerpt_chars=int(library.limit("excerpt_chars")),
    )
    record.security_findings.extend(item.as_dict() for item in findings)
    if should_quarantine([Finding(item["id"], item["severity"], item["message"], item.get("path")) for item in record.security_findings]):
        record.lifecycle_status = "quarantined"
    record.extensions["upstream_presence"] = "present"
    return record


def _commit_skill(library: Library, record: SkillRecord, files: list[tuple[str, bytes]], report: ImportReport, *, replace: bool) -> str | None:
    """Store a skill, or keep only a source link when the license forbids copies."""
    from uaskills.licensing import forbids_redistribution, mark_withheld

    if forbids_redistribution(files):
        mark_withheld(library, record)
        report.skills_withheld += 1
        report.messages.append(f"Withheld {record.name}: the license forbids retaining a copy.")
        return None
    return _store_skill(library, record, files, replace=replace)


def _store_skill(library: Library, record: SkillRecord, files: list[tuple[str, bytes]], *, replace: bool) -> str:
    dest = _storage_dir(library, record)
    existing = catalog.get_skill(library.conn, record.id)
    same = (
        existing is not None
        and existing["content_checksum"] == record.content_checksum
        and existing["tree_checksum"] == record.tree_checksum
        and existing.get("local_path")
        and (library.root / existing["local_path"]).is_dir()
    )
    if not same:
        _place_files(library, record, dest, files, replace=replace)
        if existing and existing.get("local_path"):
            old = (library.root / existing["local_path"]).resolve()
            if old != dest.resolve() and old.exists() and _managed(library, old):
                shutil.rmtree(old)
    record.local_path = dest.relative_to(library.root).as_posix()
    action = catalog.upsert_skill(library.conn, record, body=_index_body(record, library))
    _write_metadata(library, record)
    library.conn.commit()
    return action


def _storage_dir(library: Library, record: SkillRecord) -> Path:
    base_name = "quarantine" if record.lifecycle_status == "quarantined" else "skills"
    category = record.category if record.category in library.categories else "other"
    owner = safe_segment(record.repo_owner or "local")
    repo = safe_segment(record.repo_name or "repo")
    parts = [safe_segment(part) for part in (record.skill_relpath or "").split("/") if part not in {"", ".", ".."}]
    if not parts:
        parts = [safe_segment(record.name or record.id[:12])]
    dest = library.root.joinpath(base_name, category, f"{owner}+{repo}", *parts)
    if not _managed(library, dest):
        raise UnsafePath(f"refusing to write outside the library: {dest}")
    return dest


def _managed(library: Library, path: Path) -> bool:
    for root_name in ("skills", "quarantine", "index"):
        if is_within(path, library.root / root_name) or path.resolve() == (library.root / root_name).resolve():
            return True
    return False


def _place_files(library: Library, record: SkillRecord, dest: Path, files: list[tuple[str, bytes]], *, replace: bool) -> None:
    if not _managed(library, dest):
        raise UnsafePath(f"refusing to write outside the library: {dest}")
    staging = library.root / "index" / "staging" / record.id
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True, exist_ok=True)
    for rel, data in files:
        target = safe_join(staging, rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    if dest.exists() and replace:
        backup = library.root / "index" / "backup" / record.id
        if backup.exists():
            shutil.rmtree(backup)
        shutil.copytree(dest, backup)
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        shutil.copytree(staging, dest)
    else:
        for rel, data in files:
            target = safe_join(dest, rel)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    shutil.rmtree(staging, ignore_errors=True)


def _write_metadata(library: Library, record: SkillRecord) -> None:
    path = library.metadata_path(record.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record.manifest(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _index_body(record: SkillRecord, library: Library) -> str:
    cap = int(library.limit("body_index_max_bytes"))
    text = record.body or ""
    encoded = text.encode("utf-8")
    if len(encoded) <= cap:
        return text
    return encoded[:cap].decode("utf-8", errors="ignore")


def _find_local_markers(root: Path, max_depth: int) -> tuple[list[Path], list[Path]]:
    skills: list[Path] = []
    notes: list[Path] = []
    for directory, filenames in walk_source(root, max_depth):
        for name in filenames:
            lower = name.lower()
            path = directory / name
            if lower in SKILL_MD_NAMES or lower in MANIFEST_NAMES:
                skills.append(path)
            elif lower in AGENT_INSTRUCTION_NAMES:
                notes.append(path)
    return skills, notes


def _group_by_parent(files: list[Path]) -> dict[Path, list[Path]]:
    grouped: dict[Path, list[Path]] = {}
    for path in files:
        grouped.setdefault(path.parent, []).append(path)
    return grouped


def _read_local_skill(
    skill_dir: Path, source_root: Path, nested: set[Path], library: Library
) -> tuple[list[tuple[str, bytes]], list[Finding]]:
    max_files = int(library.limit("max_files_per_skill"))
    max_bytes = int(library.limit("max_file_bytes"))
    collected: list[tuple[str, Path]] = []
    findings: list[Finding] = []
    for directory, filenames in walk_source(skill_dir, int(library.limit("max_walk_depth"))):
        if _inside_other(directory, skill_dir, nested):
            continue
        for name in filenames:
            path = directory / name
            if _inside_other(path, skill_dir, nested):
                continue
            relative = path.relative_to(skill_dir).as_posix()
            collected.append((relative, path))
    collected.sort(key=lambda item: _priority(item[0]))
    files: list[tuple[str, bytes]] = []
    for relative, path in collected:
        if len(files) >= max_files and Path(relative).name.lower() not in SKILL_MD_NAMES:
            findings.append(Finding("file_limit", "info", "Additional files were skipped because the skill file limit was reached.", relative))
            break
        try:
            data = read_regular_file(path, source_root, max_bytes)
        except FileTooLarge:
            findings.append(Finding("file_too_large", "warning", "File skipped because it exceeds max_file_bytes.", relative))
            continue
        except UnsafePath as exc:
            findings.append(Finding("path_traversal", "high", str(exc), relative))
            continue
        files.append((relative, data))
    if not any(Path(rel).name.lower() in SKILL_MD_NAMES or Path(rel).name.lower() in MANIFEST_NAMES for rel, _ in files):
        raise LibraryError("skill instruction file could not be read")
    return files, findings


def _inside_other(path: Path, skill_dir: Path, nested: set[Path]) -> bool:
    """True when path belongs to a skill nested inside skill_dir.

    An ancestor skill, including a repository-root SKILL.md, is not a reason
    to skip the files of the skill currently being read.
    """
    for other in nested:
        if other == skill_dir or skill_dir not in other.parents:
            continue
        if path == other or other in path.parents:
            return True
    return False


def _group_tree_paths(paths: list[str]) -> dict[str, list[str]]:
    skill_dirs: set[str] = set()
    for path in paths:
        name = path.split("/")[-1].lower()
        if name in SKILL_MD_NAMES or name in MANIFEST_NAMES:
            skill_dirs.add(path.rsplit("/", 1)[0] if "/" in path else "")
    ordered = sorted(skill_dirs, key=len, reverse=True)
    grouped = {directory: [] for directory in skill_dirs}
    for path in paths:
        if _ignored_repo_path(path):
            continue
        owner = None
        for directory in ordered:
            if not directory:
                continue
            if path == directory or path.startswith(directory + "/"):
                owner = directory
                break
        if owner is None and "" in grouped:
            owner = ""
        if owner is not None:
            grouped[owner].append(path)
    return grouped


def _ignored_repo_path(path: str) -> bool:
    return any(part in SKIP_DIR_NAMES for part in path.split("/"))


def _prioritize(paths: list[str], directory: str, limit: int) -> list[str]:
    relatives = [(_relative_to_dir(directory, path), path) for path in paths]
    relatives.sort(key=lambda item: _priority(item[0]))
    chosen = []
    for relative, path in relatives:
        if len(chosen) >= limit and Path(relative).name.lower() not in SKILL_MD_NAMES:
            continue
        chosen.append(path)
        if len(chosen) >= limit and any(item.split("/")[-1].lower() in SKILL_MD_NAMES for item in chosen):
            break
    return chosen


def _relative_to_dir(directory: str, path: str) -> str:
    if not directory:
        return path
    prefix = directory + "/"
    if path.startswith(prefix):
        return path[len(prefix) :]
    return path.split("/")[-1]


def _priority(relative: str) -> tuple:
    name = relative.split("/")[-1].lower()
    if name == "skill.md":
        return (0, relative)
    if name.startswith("license"):
        return (1, relative)
    if relative.startswith(("references/", "scripts/", "assets/", "agents/", "templates/")):
        return (2, relative)
    if "/" not in relative:
        return (3, relative)
    return (4, relative)


def _download_github_files(
    library: Library,
    client: Any,
    owner: str,
    repo: str,
    sha: str,
    directory: str,
    paths: list[str],
    sizes: dict[str, int] | None = None,
):
    max_bytes = int(library.limit("max_file_bytes"))
    workers = max(1, int(library.limit("concurrency")))
    findings: list[Finding] = []
    sizes = sizes or {}

    def fetch(path: str):
        relative = _relative_to_dir(directory, path)
        declared = int(sizes.get(path) or 0)
        if declared > max_bytes:
            return relative, None, FileTooLarge(f"file exceeds {max_bytes} bytes")
        try:
            data = client.file_bytes(owner, repo, sha, path, max_bytes)
            return relative, data, None
        except GitHubError as exc:
            return relative, None, exc

    if workers == 1 or len(paths) <= 1:
        fetched = [fetch(path) for path in paths]
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            fetched = list(pool.map(fetch, paths))
    files = []
    used = 0
    for relative, data, error in fetched:
        if error is not None:
            finding_id = "file_too_large" if isinstance(error, FileTooLarge) else "download_failed"
            findings.append(Finding(finding_id, "warning", redact(str(error)), relative))
            continue
        files.append((relative, data))
        used += len(data)
    if not any(Path(rel).name.lower() in SKILL_MD_NAMES or Path(rel).name.lower() in MANIFEST_NAMES for rel, _ in files):
        details = "; ".join(item.message for item in findings if item.message)
        raise LibraryError(details or "skill instruction file could not be downloaded")
    return files, findings, used


def _github_paths(library: Library, client: Any, owner: str, repo: str, sha: str) -> tuple[list[dict[str, Any]], bool]:
    tree = client.tree(owner, repo, sha)
    entries = tree.get("tree") or []
    truncated = bool(tree.get("truncated"))
    cap = int(library.limit("max_tree_entries"))
    if len(entries) > cap:
        entries = entries[:cap]
        truncated = True
    blobs = []
    for entry in entries:
        path = entry.get("path")
        if entry.get("type") == "blob" and isinstance(path, str):
            blobs.append({"path": path, "size": int(entry.get("size") or 0)})
    if truncated and not any(item["path"].split("/")[-1].lower() in SKILL_MD_NAMES for item in blobs):
        for path in _contents_fallback(library, client, owner, repo, sha):
            blobs.append({"path": path, "size": 0})
    return blobs, truncated


def _contents_fallback(library: Library, client: Any, owner: str, repo: str, sha: str) -> list[str]:
    found: list[str] = []
    queue = list(CONTENT_PREFIXES)
    seen = 0
    cap = int(library.limit("max_tree_entries"))
    while queue and seen < cap:
        current = queue.pop(0)
        try:
            items = client.list_contents(owner, repo, current, sha)
        except GitHubError:
            continue
        for item in items:
            seen += 1
            item_type = item.get("type")
            item_path = item.get("path")
            if not isinstance(item_path, str):
                continue
            if item_type == "dir" and not _ignored_repo_path(item_path):
                queue.append(item_path)
            elif item_type == "file":
                found.append(item_path)
            if seen >= cap:
                break
    return found


def _primary(files: list[tuple[str, bytes]]) -> tuple[str, bytes]:
    for rel, data in files:
        if Path(rel).name.lower() == "skill.md":
            return Path(rel).name, data
    for rel, data in files:
        if Path(rel).name.lower() in MANIFEST_NAMES:
            return Path(rel).name, data
    raise LibraryError("no primary skill file")


def _classify_names(names: list[str]) -> tuple[str, str]:
    lowered = {name.lower() for name in names}
    if lowered & SKILL_MD_NAMES or lowered & MANIFEST_NAMES:
        return "skill", "supported skill file present"
    if lowered & AGENT_INSTRUCTION_NAMES:
        return "agent_instructions", "agent instruction file present and no SKILL.md"
    if "mcp.json" in lowered:
        return "mcp_descriptor", "mcp.json present and no SKILL.md"
    return "not_a_skill", "no supported skill file found"


def _record_non_skill(library: Library, report: ImportReport, **kwargs: Any) -> None:
    record = ResourceRecord(
        id=skill_id(kwargs.get("owner") or "", kwargs.get("repo") or "", kwargs.get("locator") or "", "resource"),
        kind=kwargs["kind"],
        source_url=kwargs.get("source_url"),
        repo_owner=kwargs.get("owner"),
        repo_name=kwargs.get("repo"),
        local_path=None,
        reason=kwargs["reason"],
        evidence=kwargs.get("evidence") or "",
        discovered_at=utc_now(),
        metadata={"commit_sha": kwargs.get("commit_sha"), "locator": kwargs.get("locator")},
    )
    catalog.upsert_resource(library.conn, record)
    catalog.record_event(
        library.conn,
        level="info",
        action="classified",
        message=kwargs["reason"],
        source=kwargs.get("source_url"),
        details={"kind": kwargs["kind"], "locator": kwargs.get("locator")},
    )
    library.conn.commit()
    report.resources_recorded += 1
    report.messages.append(f"Recorded {kwargs['kind']}: {kwargs.get('locator') or kwargs.get('repo')}. {kwargs['reason']}.")


def _sample_names(root: Path) -> str:
    names = []
    for directory, filenames in walk_source(root, 2):
        for name in filenames:
            names.append(name)
            if len(names) >= 30:
                return ", ".join(names)
    return ", ".join(names)


def _relative_posix(path: Path, root: Path) -> str:
    relative = path.resolve().relative_to(root.resolve()).as_posix()
    return "" if relative == "." else relative


def _blob_url(owner: str, repo: str, sha: str, directory: str) -> str:
    base = f"https://github.com/{owner}/{repo}/tree/{sha}"
    return base if not directory else f"{base}/{directory}"


def _is_sha(value: str) -> bool:
    return len(value) == 40 and all(char in "0123456789abcdef" for char in value.lower())


def _history(library: Library, action: str, record: SkillRecord, commit_sha: str | None = None) -> None:
    append_jsonl(
        library.root / "sources" / "import_history.jsonl",
        {
            "ts": utc_now(),
            "action": action,
            "skill_id": record.id,
            "name": record.name,
            "source_url": record.source_url,
            "commit_sha": commit_sha or record.commit_sha,
            "checksum": record.content_checksum,
            "lifecycle": record.lifecycle_status,
            "license_status": record.license_status,
        },
    )


def _fail(library: Library, report: ImportReport, source: str, exc: Exception) -> None:
    message = redact(str(exc))
    report.errors += 1
    report.messages.append(message)
    catalog.record_event(library.conn, level="error", action="error", message=message, source=source)
    append_jsonl(
        library.root / "sources" / "source_errors.jsonl",
        {"ts": utc_now(), "source": source, "error": message, "recoverable": True},
    )
    library.conn.commit()


def _completed(library: Library, job_key: str) -> set[str]:
    job = catalog.get_job(library.conn, job_key)
    if not job or job["status"] not in {"running", "interrupted"}:
        return set()
    return set(job["cursor"].get("completed") or [])


def _checkpoint(library: Library, job_key: str, kind: str, params: dict, completed: set[str], status: str) -> None:
    catalog.save_job(
        library.conn,
        job_id=job_key,
        kind=kind,
        status=status,
        cursor={"completed": sorted(completed)},
        params=params,
        message=status,
    )
    library.conn.commit()


def _finish_job(library: Library, job_key: str, kind: str, params: dict, completed: set[str], status: str, report: ImportReport) -> None:
    catalog.save_job(
        library.conn,
        job_id=job_key,
        kind=kind,
        status=status,
        cursor={"completed": sorted(completed)},
        params=params,
        message=json.dumps(report.as_dict()),
    )
    library.conn.commit()


def _merge_reports(target: ImportReport, part: ImportReport) -> None:
    target.skills_imported += part.skills_imported
    target.skills_updated += part.skills_updated
    target.skills_unchanged += part.skills_unchanged
    target.skills_quarantined += part.skills_quarantined
    target.skills_withheld += part.skills_withheld
    target.resources_recorded += part.resources_recorded
    target.errors += part.errors
    target.skill_ids.extend(part.skill_ids)
    target.messages.extend(part.messages)
