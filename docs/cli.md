# Commands

Run these from the library root. `--root` selects another catalog. `--json` prints a machine-readable object. Exit code `0` means the command finished. Exit code `1` means an operational error, including a partial import that recorded errors. Exit code `2` means invalid arguments or validation errors.

| Command | Purpose |
| --- | --- |
| `python tools/discover_skills.py --query "AI agent skills"` | Record GitHub search candidates |
| `python tools/discover_skills.py --topic agent-skills` | Record repositories for a topic |
| `python tools/discover_skills.py --org OWNER` | Record public repositories from an organization |
| `python tools/discover_skills.py --repo OWNER/REPO` | Record one repository without downloading it |
| `python tools/discover_skills.py --local-dir PATH` | Record a local directory |
| `python tools/discover_skills.py --source-file FILE` | Record locators from a text file |
| `python tools/import_skills.py --repo OWNER/REPO` | Import skills from GitHub |
| `python tools/import_skills.py --source-file FILE` | Import every locator in a file |
| `python tools/import_skills.py --local-dir PATH` | Import `SKILL.md` directories from disk |
| `python tools/import_skills.py --archive FILE.zip` | Extract a zip safely, then import it |
| `python tools/import_skills.py --from-discovered --limit 10` | Import recorded candidates |
| `python tools/update_skills.py` | Refresh sources whose commit changed |
| `python tools/search_skills.py --query TEXT` | Full-text search |
| `python tools/search_skills.py --category frontend` | Filter by category |
| `python tools/validate_skills.py` | Structural and heuristic checks |
| `python tools/deduplicate_skills.py` | Report identical instruction checksums |
| `python tools/deduplicate_skills.py --apply` | Mark duplicates without deleting files |
| `python tools/build_index.py` | Rebuild search and `SKILLS_INDEX.md` |
| `python tools/build_index.py --from-files` | Restore rows from metadata sidecars |
| `python tools/plan_task.py --task TEXT` | Select skills and write a plan |
| `python tools/export_skills.py --target cursor --dest PATH` | Copy skills into an agent layout |

Search filters also include `--capability`, `--source`, `--license`, `--runtime`, `--version`, `--security-status`, `--name`, `--limit`, and `--offset`. `--include-quarantined`, `--include-deprecated`, and `--include-duplicates` show records hidden by default.

`--resume` on discover and import continues an interrupted job. Re-running an import without `--resume` is safe; matching skills are updated or left unchanged instead of duplicated.

Human review commands:

```
python -m uaskills feedback --skill-id ID --task "..." --outcome succeeded --notes "..."
python -m uaskills set-status --skill-id ID --verification tested --note "what was checked"
```

`tested`, `verified`, and license `reviewed` require a note. Import never sets those values.

The same commands are available as `python -m uaskills discover`, `import`, `update`, `search`, `validate`, `dedupe`, `build-index`, `plan`, and `export`.
