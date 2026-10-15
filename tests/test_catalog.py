"""Catalog behavior tests. Network calls are mocked."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import jsonschema
import pytest

from uaskills.adapters import export_skills
from uaskills.catalog import get_skill, insert_synthetic, manifest_from_row, search_skills
from uaskills.config import BUNDLED_ROOT, load_settings
from uaskills.discover import discover
from uaskills.errors import ConfigError, GitHubError, UnsafeArchive, UnsafePath
from uaskills.importer import import_archive, import_github_repo, import_local_directory, update_sources
from uaskills.indexer import rebuild_index
from uaskills.library import Library
from uaskills.planner import plan_task
from uaskills.safety import open_zip_bytes, safe_extract_zip, safe_join
from uaskills.textutil import redact
from uaskills.validate import validate_library


def skill_text(name: str, description: str, body: str, extra: str = "") -> str:
    return f"---\nname: {name}\ndescription: {description}\n{extra}---\n\n{body}\n"


def write_skill(root: Path, folder: str, name: str, description: str, body: str, extra: str = "") -> Path:
    directory = root / folder
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(
        skill_text(name, description, body, extra),
        encoding="utf-8",
    )
    return directory


@pytest.fixture
def library(tmp_path: Path) -> Library:
    lib = Library(tmp_path / "catalog")
    yield lib
    lib.close()


def test_manifest_and_parser_do_not_invent_description(library: Library, tmp_path: Path):
    source = write_skill(
        tmp_path,
        "status-writer",
        "status-writer",
        "Write a short status note when the user asks for a status update.",
        "Write three sentences and stop.",
        extra='license: MIT\nmetadata:\n  version: "1.0.0"\n  category: documents\n',
    )
    report = import_local_directory(library, source)
    assert report.errors == 0
    assert report.skills_imported == 1
    skill = get_skill(library.conn, report.skill_ids[0])
    manifest = manifest_from_row(skill)
    schema = json.loads((BUNDLED_ROOT / "schemas" / "skill-manifest.schema.json").read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(schema).validate(manifest)
    assert manifest["license"]["status"] == "declared"
    assert manifest["license"]["identifier"] == "MIT"
    assert manifest["license"]["status"] != "reviewed"
    assert manifest["verification_status"] == "unverified"
    assert manifest["version"] == "1.0.0"
    assert manifest["category"] == "documents"
    copied = (library.root / skill["local_path"] / "SKILL.md").read_bytes()
    assert copied == (source / "SKILL.md").read_bytes()

    bare = tmp_path / "bare"
    bare.mkdir()
    (bare / "SKILL.md").write_text("# Just a heading\n\nNo frontmatter description.\n", encoding="utf-8")
    imported = import_local_directory(library, bare)
    parsed = get_skill(library.conn, imported.skill_ids[0])
    assert parsed["description"] is None
    assert parsed["quality_assessment"] == "missing_description"


def test_duplicate_import_is_idempotent_and_marked(library: Library, tmp_path: Path):
    body = skill_text("same-skill", "Repeat the same local instruction.", "Do the same thing once.")
    first = tmp_path / "one" / "same-skill"
    second = tmp_path / "two" / "same-skill"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    (first / "SKILL.md").write_bytes(body.encode("utf-8"))
    (second / "SKILL.md").write_bytes(body.encode("utf-8"))
    import_local_directory(library, tmp_path / "one")
    again = import_local_directory(library, tmp_path / "one")
    assert again.skills_unchanged == 1
    assert library.conn.execute("SELECT COUNT(*) FROM skills").fetchone()[0] == 1
    import_local_directory(library, tmp_path / "two")
    assert library.conn.execute("SELECT COUNT(*) FROM skills").fetchone()[0] == 2
    visible = search_skills(library.conn, query="repeat local instruction")
    assert visible["total"] == 1
    hidden = search_skills(library.conn, query="repeat local instruction", include_duplicates=True)
    assert hidden["total"] == 2


def test_search_ranking_category_and_runtime(library: Library, tmp_path: Path):
    write_skill(
        tmp_path / "src",
        "react-builder",
        "react-builder",
        "Build a React application interface.",
        "Create a React application with accessible components.",
        extra="metadata:\n  category: frontend\n  runtimes: react-runtime\ncapabilities:\n  - build-ui\n",
    )
    write_skill(
        tmp_path / "src",
        "note-pad",
        "note-pad",
        "Keep a plain text note.",
        "Store a note in a file.",
    )
    import_local_directory(library, tmp_path / "src")
    found = search_skills(library.conn, query="build a React application", limit=10)
    assert found["results"][0]["name"] == "react-builder"
    frontend = search_skills(library.conn, category="frontend")
    assert [item["name"] for item in frontend["results"]] == ["react-builder"]
    capable = search_skills(library.conn, capability="build-ui")
    assert capable["total"] == 1
    strict = search_skills(library.conn, runtime="react-runtime", runtime_strict=True)
    assert [item["name"] for item in strict["results"]] == ["react-builder"]
    inclusive = search_skills(library.conn, query="note", runtime="cursor")
    assert any(item["name"] == "note-pad" for item in inclusive["results"])


def test_license_unknown_and_source_metadata(library: Library, tmp_path: Path):
    source = write_skill(tmp_path, "plain-skill", "plain-skill", "Describe a plain local workflow.", "Follow the steps.")
    import_local_directory(library, source)
    skill = library.conn.execute("SELECT license_status, source_url, origin_path FROM skills").fetchone()
    assert skill["license_status"] == "unknown"
    assert skill["source_url"].startswith("file:")
    assert Path(skill["origin_path"]).name == "plain-skill"


def test_incremental_local_update(library: Library, tmp_path: Path):
    source = write_skill(tmp_path, "changer", "changer", "Track a changing instruction.", "Version one.")
    import_local_directory(library, source)
    original = library.conn.execute("SELECT id, content_checksum FROM skills").fetchone()
    (source / "SKILL.md").write_text(
        skill_text("changer", "Track a changing instruction.", "Version two."),
        encoding="utf-8",
    )
    second = import_local_directory(library, source)
    assert second.skills_updated == 1
    updated = library.conn.execute("SELECT id, content_checksum FROM skills").fetchone()
    assert updated["id"] == original["id"]
    assert updated["content_checksum"] != original["content_checksum"]
    assert library.conn.execute("SELECT COUNT(*) FROM skills").fetchone()[0] == 1


def test_non_skill_is_not_imported_as_a_skill(library: Library, tmp_path: Path):
    package = tmp_path / "just-a-lib"
    package.mkdir()
    (package / "README.md").write_text("This is a library, not a skill.\n", encoding="utf-8")
    (package / "mcp.json").write_text("{}\n", encoding="utf-8")
    report = import_local_directory(library, package)
    assert report.skills_imported == 0
    assert report.resources_recorded == 1
    kind = library.conn.execute("SELECT kind, reason FROM resources").fetchone()
    assert kind["kind"] == "mcp_descriptor"
    assert library.conn.execute("SELECT COUNT(*) FROM skills").fetchone()[0] == 0


def test_quarantine_executable_and_no_execution(library: Library, tmp_path: Path):
    source = write_skill(tmp_path, "risky", "risky", "A skill that also contains a binary payload.", "Read this text only.")
    (source / "payload.exe").write_bytes(b"MZ-not-really")
    (source / "scripts").mkdir()
    (source / "scripts" / "run.py").write_text("raise SystemExit('should not run')\n", encoding="utf-8")
    report = import_local_directory(library, source)
    assert report.skills_quarantined == 1
    skill = get_skill(library.conn, report.skill_ids[0])
    assert skill["lifecycle_status"] == "quarantined"
    assert skill["local_path"].startswith("quarantine/")
    assert search_skills(library.conn, query="binary payload")["total"] == 0
    visible = search_skills(library.conn, query="binary payload", include_quarantined=True)
    assert visible["total"] == 1
    stored = library.root / skill["local_path"] / "scripts" / "run.py"
    assert stored.read_text(encoding="utf-8") == "raise SystemExit('should not run')\n"
    assert "subprocess" not in Path(__import__("uaskills.importer", fromlist=["importer"]).__file__).read_text(encoding="utf-8")


def test_path_traversal_and_malicious_archive(tmp_path: Path, library: Library):
    outside = tmp_path / "outside.txt"
    with pytest.raises(UnsafePath):
        safe_join(tmp_path / "root", "../outside.txt")
    limits = library.settings["limits"]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("../evil.txt", "owned")
        archive.writestr("ok/SKILL.md", "hello")
    buffer.seek(0)
    with zipfile.ZipFile(buffer) as archive:
        with pytest.raises(UnsafeArchive):
            safe_extract_zip(archive, tmp_path / "extract", limits)
    assert not outside.exists()
    assert not (tmp_path / "evil.txt").exists()

    bomb = io.BytesIO()
    with zipfile.ZipFile(bomb, "w") as archive:
        info = zipfile.ZipInfo("big.txt")
        info.file_size = 10**12
        info.compress_size = 10
        archive.writestr("small.txt", "ok")
    bomb.seek(0)
    with zipfile.ZipFile(bomb) as archive:
        # ZipInfo written via writestr may not keep the fake sizes, so set them on the open archive.
        archive.getinfo("small.txt").file_size = 10**12
        archive.getinfo("small.txt").compress_size = 10
        with pytest.raises(UnsafeArchive):
            safe_extract_zip(archive, tmp_path / "bomb", limits)

    good = io.BytesIO()
    with zipfile.ZipFile(good, "w") as archive:
        archive.writestr(
            "zip-skill/SKILL.md",
            skill_text("zip-skill", "Import a skill from a checked zip archive.", "Use the extracted instructions."),
        )
    archive_path = tmp_path / "skills.zip"
    archive_path.write_bytes(good.getvalue())
    report = import_archive(library, archive_path)
    assert report.errors == 0
    assert report.skills_imported == 1
    assert not (tmp_path / "evil.txt").exists()


def test_failed_download_keeps_successful_skill(library: Library):
    sha = "a" * 40
    good = "skills/kept/SKILL.md"
    bad = "skills/lost/SKILL.md"
    files = {
        good: skill_text("kept-skill", "Keep this skill when another download fails.", "Stay imported.").encode(),
        bad: skill_text("lost-skill", "This download will fail.", "Do not appear.").encode(),
    }

    class Fake:
        def repo(self, owner, repo):
            return {
                "default_branch": "main",
                "html_url": "https://github.com/acme/widgets",
                "license": {"spdx_id": "Apache-2.0"},
                "topics": ["agent-skills"],
            }

        def commit_sha(self, owner, repo, branch):
            return sha

        def tree(self, owner, repo, commit):
            return {"truncated": False, "tree": [{"path": path, "type": "blob"} for path in files]}

        def file_bytes(self, owner, repo, commit, path, max_bytes):
            if path == bad:
                raise GitHubError(500, "download failed")
            return files[path]

        def list_contents(self, owner, repo, path, ref):
            return []

    report = import_github_repo(library, Fake(), "acme/widgets")
    assert report.errors == 1
    assert report.skills_imported == 1
    names = [row["name"] for row in library.conn.execute("SELECT name FROM skills")]
    assert names == ["kept-skill"]
    skill = library.conn.execute(
        "SELECT license_identifier, license_status, commit_sha, source_url FROM skills"
    ).fetchone()
    assert skill["license_identifier"] == "Apache-2.0"
    assert skill["license_status"] == "declared"
    assert skill["commit_sha"] == sha
    assert sha in skill["source_url"]
    errors = (library.root / "sources" / "source_errors.jsonl").read_text(encoding="utf-8")
    assert "download failed" in errors


def test_interrupted_import_resumes(library: Library, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    write_skill(tmp_path / "pack", "alpha", "alpha-skill", "First resumable skill.", "Do the first step.")
    write_skill(tmp_path / "pack", "beta", "beta-skill", "Second resumable skill.", "Do the second step.")
    import uaskills.importer as importer

    original = importer._store_skill
    calls = {"n": 0}

    def stop_on_second(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise KeyboardInterrupt()
        return original(*args, **kwargs)

    monkeypatch.setattr(importer, "_store_skill", stop_on_second)
    with pytest.raises(KeyboardInterrupt):
        import_local_directory(library, tmp_path / "pack")
    assert library.conn.execute("SELECT COUNT(*) FROM skills").fetchone()[0] == 1
    monkeypatch.setattr(importer, "_store_skill", original)
    resumed = import_local_directory(library, tmp_path / "pack", resume=True)
    assert resumed.skills_imported == 1
    assert library.conn.execute("SELECT COUNT(*) FROM skills").fetchone()[0] == 2


def test_repeated_import_continues_past_the_repo_cap(library: Library):
    sha = "d" * 40
    files = {
        f"skills/skill-{name}/SKILL.md": skill_text(
            f"skill-{name}",
            f"Describe skill {name} for the cap test.",
            f"Do skill {name}.",
        ).encode("utf-8")
        for name in ("a", "b")
    }
    downloads: list[str] = []

    class Fake:
        def repo(self, owner, repo):
            return {"default_branch": "main", "html_url": "https://github.com/acme/capped", "license": {"spdx_id": "MIT"}, "topics": []}

        def commit_sha(self, owner, repo, branch):
            return sha

        def tree(self, owner, repo, commit):
            return {"truncated": False, "tree": [{"path": path, "type": "blob", "size": len(data)} for path, data in files.items()]}

        def file_bytes(self, owner, repo, commit, path, max_bytes):
            downloads.append(path)
            return files[path]

        def list_contents(self, *args):
            return []

    library.settings["limits"]["max_skills_per_repo"] = 1
    client = Fake()
    first = import_github_repo(library, client, "acme/capped")
    assert first.skills_imported == 1
    assert "Stopped at max_skills_per_repo" in " ".join(first.messages)
    second = import_github_repo(library, client, "acme/capped")
    assert second.skills_imported == 1
    assert second.skills_unchanged == 1
    assert library.conn.execute("SELECT COUNT(*) FROM skills").fetchone()[0] == 2
    assert len(downloads) == 2


def test_github_update_skips_unchanged_commit(library: Library):
    sha = {"value": "b" * 40}
    downloads = {"n": 0}
    body = skill_text("remote-skill", "Update this remote skill when the commit changes.", "Body one.")
    files = {"skills/remote-skill/SKILL.md": body.encode("utf-8")}

    class Fake:
        def repo(self, owner, repo):
            return {"default_branch": "main", "html_url": "https://github.com/acme/remote", "license": None, "topics": []}

        def commit_sha(self, owner, repo, branch):
            return sha["value"]

        def tree(self, owner, repo, commit):
            return {"truncated": False, "tree": [{"path": path, "type": "blob"} for path in files]}

        def file_bytes(self, owner, repo, commit, path, max_bytes):
            downloads["n"] += 1
            return files[path]

        def list_contents(self, *args):
            return []

    client = Fake()
    import_github_repo(library, client, "acme/remote")
    assert downloads["n"] == 1
    unchanged = update_sources(library, client, repo="acme/remote")
    assert downloads["n"] == 1
    assert unchanged.skills_unchanged == 1
    sha["value"] = "c" * 40
    files["skills/remote-skill/SKILL.md"] = skill_text(
        "remote-skill",
        "Update this remote skill when the commit changes.",
        "Body two.",
    ).encode("utf-8")
    changed = update_sources(library, client, repo="acme/remote")
    assert changed.skills_updated == 1
    assert downloads["n"] == 2
    assert library.conn.execute("SELECT COUNT(*) FROM skills").fetchone()[0] == 1


def test_rebuild_index_without_deleting_files(library: Library, tmp_path: Path):
    source = write_skill(tmp_path, "indexed", "indexed-skill", "Find this skill after rebuilding the index.", "Keep the file.")
    import_local_directory(library, source)
    skill_file = next((library.root / "skills").rglob("SKILL.md"))
    before = skill_file.read_bytes()
    library.close()
    index = library.root / "index"
    for path in index.glob("skills.sqlite*"):
        path.unlink()
    restored = Library(library.root)
    try:
        summary = rebuild_index(restored, from_files=True)
        assert summary["restored_from_metadata"] == 1
        found = search_skills(restored.conn, query="rebuilding the index")
        assert found["total"] == 1
        assert found["results"][0]["name"] == "indexed-skill"
        assert skill_file.read_bytes() == before
    finally:
        restored.close()


def test_adapter_backup_and_skip(library: Library, tmp_path: Path):
    source = write_skill(tmp_path, "export-me", "export-me", "Export this skill into a target agent directory.", "Follow the exported instructions.")
    imported = import_local_directory(library, source)
    skill = get_skill(library.conn, imported.skill_ids[0])
    dest = tmp_path / "agent"
    target = dest / ".cursor" / "skills" / "export-me"
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text("OLD\n", encoding="utf-8")
    report = export_skills(library, [skill], target="cursor", dest=dest, on_conflict="backup")
    assert report.backups
    assert (target / "SKILL.md").read_text(encoding="utf-8").startswith("---")
    backup_text = Path(report.backups[0], "SKILL.md").read_text(encoding="utf-8")
    assert backup_text == "OLD\n"
    skipped = export_skills(library, [skill], target="cursor", dest=dest, on_conflict="skip")
    assert skipped.skipped
    assert (target / "SKILL.md").read_text(encoding="utf-8").startswith("---")


def test_planner_selects_relevant_skill_without_executing(library: Library, tmp_path: Path):
    write_skill(
        tmp_path / "src",
        "status-writer",
        "status-writer",
        "Write a short status note when the user asks for a status update.",
        "Write three sentences.",
        extra="allowed-tools: Read\nmetadata:\n  category: documents\n",
    )
    write_skill(
        tmp_path / "src",
        "cursor-only",
        "cursor-only",
        "Write a short status note for the Cursor runtime only.",
        "Use Cursor-only steps.",
        extra="metadata:\n  runtimes: cursor\n",
    )
    import_local_directory(library, tmp_path / "src")
    plan = plan_task(library, "Write a short status note", runtime="generic-agent", limit=3)
    schema = json.loads((BUNDLED_ROOT / "schemas" / "execution-plan.schema.json").read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(schema).validate(plan)
    assert plan["execution_performed"] is False
    names = [item["name"] for item in plan["selected_skills"]]
    assert "status-writer" in names
    assert "cursor-only" not in names
    assert plan["required_tools"] == ["Read"]
    assert plan["selected_skills"][0]["local_path"]
    assert any("does not execute" in note for note in plan["limitations"])


def test_validation_reports_empty_instructions_and_broken_links(library: Library, tmp_path: Path):
    source = write_skill(
        tmp_path,
        "linked",
        "linked-skill",
        "Check a local reference before editing.",
        "See [details](references/details.md).",
    )
    import_local_directory(library, source)
    report = validate_library(library)
    assert any(issue.code == "broken_reference" for issue in report.issues)
    empty = tmp_path / "empty-skill"
    empty.mkdir()
    (empty / "SKILL.md").write_text("---\nname: empty-skill\ndescription: Nothing to do.\n---\n\n", encoding="utf-8")
    import_local_directory(library, empty)
    checked = validate_library(library)
    assert any(issue.code == "empty_instructions" for issue in checked.issues)


def test_pagination_does_not_overlap(library: Library):
    insert_synthetic(library.conn, 2500, allow=True)
    first = search_skills(library.conn, limit=100, offset=0, include_synthetic=True)
    second = search_skills(library.conn, limit=100, offset=100, include_synthetic=True)
    assert first["total"] == 2500
    assert len(first["results"]) == 100
    assert len(second["results"]) == 100
    assert {item["id"] for item in first["results"]}.isdisjoint({item["id"] for item in second["results"]})
    normal = search_skills(library.conn, query="synthetic catalog")
    assert normal["total"] == 0


def test_discover_records_source_file_without_network(library: Library, tmp_path: Path):
    listing = tmp_path / "repos.txt"
    listing.write_text("# comment\nhttps://github.com/acme/widgets\nlocal:C:/skills\n", encoding="utf-8")
    report = discover(library, client=None, source_file=str(listing))
    assert report.errors == 0
    assert report.candidates == 2
    locators = {item["locator"] for item in report.repositories}
    assert "acme/widgets" in locators


def test_settings_and_logs_do_not_keep_tokens(library: Library, tmp_path: Path):
    config = tmp_path / "config"
    config.mkdir()
    (config / "settings.yaml").write_text("github:\n  token: ghp_notallowedtokenvalue1\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_settings(tmp_path)
    from uaskills.importer import ImportReport, _fail

    _fail(library, ImportReport(), "acme/widgets", GitHubError(403, "token ghp_" + ("a" * 24)))
    logged = (library.root / "sources" / "source_errors.jsonl").read_text(encoding="utf-8")
    assert "ghp_" not in logged
    assert "[REDACTED]" in redact("authorization: Bearer ghp_" + ("b" * 24))


def test_proprietary_license_is_not_copied(library: Library, tmp_path: Path):
    source = write_skill(
        tmp_path,
        "locked-skill",
        "locked-skill",
        "This text must not be stored.",
        "Do not keep this instruction.",
    )
    (source / "LICENSE.txt").write_text(
        "Extract these materials from the Services or retain copies outside the Services.\n",
        encoding="utf-8",
    )
    report = import_local_directory(library, source)
    assert report.skills_withheld == 1
    assert report.skills_imported == 0
    assert list((library.root / "skills").rglob("SKILL.md")) == []
    assert search_skills(library.conn, query="must not be stored")["total"] == 0
    notices = (library.root / "sources" / "third_party_notices.jsonl").read_text(encoding="utf-8")
    assert "withheld" in notices


def test_seed_file_includes_requested_repositories():
    from uaskills.collect import load_seed_repositories

    urls = {item["url"] for item in load_seed_repositories(BUNDLED_ROOT)}
    for url in (
        "https://github.com/anthropics/skills",
        "https://github.com/vercel-labs/agent-skills",
        "https://github.com/VoltAgent/awesome-agent-skills",
        "https://github.com/seb1n/awesome-ai-agent-skills",
        "https://github.com/junminhong/awesome-agent-skills",
        "https://github.com/praxstack/awesome-claude-skills",
        "https://github.com/open-agent-craft/awesome-agent-skills",
        "https://github.com/vivy-yi/awesome-skills",
        "https://github.com/Emmraan/agent-skills",
        "https://github.com/vercel-labs/skills",
        "https://github.com/agentskills/agentskills",
        "https://github.com/google-gemini/gemini-cli",
        "https://github.com/github/copilot-cli",
        "https://github.com/AkshitIreddy/agent-skills",
        "https://github.com/sentenz/skills",
        "https://github.com/Breklin/awesome-claude-skills",
    ):
        assert url in urls


def test_master_skill_and_commands_exist():
    master = (BUNDLED_ROOT / "MASTER_SKILL.md").read_text(encoding="utf-8")
    for heading in (
        "Understand the task",
        "Discover capabilities",
        "Evaluate candidates",
        "Select a minimal effective skill set",
        "Compose workflows",
        "Execute responsibly",
        "Verify results",
        "Learn from results",
    ):
        assert heading in master
    assert "python tools/search_skills.py" in master
    assert "python tools/plan_task.py" in master
    for name in (
        "discover_skills.py",
        "import_skills.py",
        "update_skills.py",
        "search_skills.py",
        "validate_skills.py",
        "deduplicate_skills.py",
        "build_index.py",
        "plan_task.py",
    ):
        assert (BUNDLED_ROOT / "tools" / name).is_file()
