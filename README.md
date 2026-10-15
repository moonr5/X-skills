# Universal AI Skills Library

A local catalog of reusable AI agent skills. It discovers public skill sources, imports the skill files, indexes them in SQLite, and helps an agent pick a small set for a task.

This is not a web application, a hosted model, or a marketplace. It does not run imported skills during import.

## Install

Python 3.11 or newer is required. From this directory:

```
python -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements-dev.txt
python -m pytest
```

On macOS or Linux, activate the virtual environment with `source .venv/bin/activate`.

The tools work without a virtual environment when `PyYAML` and `jsonschema` are installed. Tests need `pytest`. No paid API is required.

## Discover and import

Discovery records candidates. Import downloads or copies skill files.

```
python tools/discover_skills.py --query "AI agent skills"
python tools/import_skills.py --repo OWNER/REPOSITORY
python tools/import_skills.py --source-file sources/my-repositories.txt
python tools/import_skills.py --local-dir C:\path\to\skills
python tools/import_skills.py --from-discovered --limit 10
```

A source file may contain `owner/repo` lines, GitHub URLs, `#` comments, and `local:` paths. Add ongoing sources in `config/sources.yaml` instead of editing the importer.

Imports are idempotent. Running the same import again updates a skill when its content changed and otherwise leaves the existing row in place. `--resume` continues an interrupted job. Use `--replace` when an update should remove files that disappeared from a skill directory; a backup is kept under `index/backup/`.

## Search

```
python tools/search_skills.py --query "build a React application"
python tools/search_skills.py --category frontend
python tools/search_skills.py --query "pdf" --json
```

Search uses SQLite full-text. It does not load every skill into memory and it does not send skill text to an external service. `--semantic` is reserved for a future local or explicitly configured embedding provider and currently reports that semantic search is off.

## Plan a task

```
python tools/plan_task.py --task "Create a secure REST API" --json
```

The plan names candidate skills, why they were selected, warnings, tools, dependencies, a proposed sequence, and checks. `execution_performed` is false. The connected agent reads the selected `SKILL.md` files and uses its own tools. It must not claim a skill ran if it was only read.

Give the agent `MASTER_SKILL.md` as the coordinator. Do not paste the catalog into the prompt.

## Connect an agent

Export copies selected skills into a product directory. The default conflict policy saves the previous directory with a `.bak-<timestamp>` suffix before replacing it.

```
python tools/export_skills.py --target cursor --dest C:\path\to\project --query "react" --on-conflict backup
```

Targets are `cursor` (`.cursor/skills/`), `claude` (`.claude/skills/`), `codex` (`.agents/skills/`), and `generic` (`skills/`). Details and manual fallbacks are in `docs/adapters.md`.

## Validate, deduplicate, and rebuild

```
python tools/validate_skills.py
python tools/deduplicate_skills.py
python tools/deduplicate_skills.py --apply
python tools/update_skills.py
python tools/build_index.py
python tools/build_index.py --from-files
```

`deduplicate_skills.py --apply` marks duplicates. It does not delete files. `build_index.py` rebuilds the search index and `SKILLS_INDEX.md`. It does not delete imported skill files. `--from-files` restores catalog rows from `index/metadata/` when the SQLite file was removed.

Validation exits with code 2 when it finds errors such as missing files, empty instructions, or quarantined skills. Warnings, including an unknown license, do not by themselves change the exit code.

## GitHub authentication

Public repositories can be read without a token, subject to GitHub's unauthenticated rate limit. To raise that limit, set `GITHUB_TOKEN` or `GH_TOKEN` in the environment. Do not put a token in `config/settings.yaml`, source files, or logs. The tools redact recognized token strings from error logs.

## License and security statuses

| Status | What it means |
| --- | --- |
| `license_status=unknown` | No license string or license file was found. |
| `license_status=declared` | A license identifier or license file was recorded. This is not a legal review. |
| `license_status=reviewed` | A person set this with `python -m uaskills set-status` and a note. |
| `security_scan_status=screened` | A heuristic scan ran during import or validation. |
| `lifecycle_status=quarantined` | The skill contained a blocked executable type or an unsafe path. Default search hides it. |
| `verification_status=unverified` | No person has recorded a test or review. |

Imported files are untrusted. The importer copies and parses them. It does not execute them. A clean heuristic scan does not prove a skill is safe. Quarantine is a storage decision, not a malware verdict.

## Layout

Skill files are stored by category under `skills/` or, when quarantined, under `quarantine/`. Generated manifests are stored separately in `index/metadata/`. The SQLite catalog, including the full-text index, is `index/skills.sqlite`.

## Current limitations

- The collection target is 100,000 unique skill packages. The live count and the remaining gap are in `docs/collection-report.md`. That file is regenerated from the catalog. It is not a claim that the target has been reached.
- GitHub repository search returns at most 1000 results per query. Unauthenticated API use stops when the rate limit is reached. Set `GITHUB_TOKEN` or `GH_TOKEN`, then continue with `python tools/collect_skills.py`.
- This project does not claim a 12 million skill collection.
- `google/skills` is listed in the source registry with `enabled: false` because it is large. Enable it explicitly if you want it in default discovery.
- Semantic search is an interface only.
- Catalog writes are single-threaded. `concurrency` above 1 parallelizes file downloads, still paced by `min_interval_seconds`.
- Updates do not delete a skill that disappeared upstream. They mark `extensions.upstream_presence` as `missing`.
- SQLite full-text is the search engine. A different database is not required at the current size. See `docs/scaling.md` before replacing it.
- Only GitHub and local directories are import sources. Other forges can be added as new source types.
- Archive import accepts zip files and rejects unsafe paths, oversized members, and extreme compression ratios. It does not execute archive contents.

## Tests

```
python -m pytest
```

Network tests are not part of the default run. Live imports are done with the import command.

## Further documentation

- `docs/cli.md` — commands and exit codes
- `docs/schema.md` — manifest fields
- `docs/formats.md` — which files count as skills
- `docs/adapters.md` — agent export layouts
- `docs/security.md` — trust boundaries
- `docs/scaling.md` — growth without a new database yet
