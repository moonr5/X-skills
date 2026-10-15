# Adapters

The catalog does not depend on a model vendor. Adapters only define where a copy of a selected skill should be placed. Export copies original skill files. It does not copy the generated manifest into the skill directory.

```
python tools/export_skills.py --target TARGET --dest PROJECT --query "task words" --on-conflict backup
```

| Target | Directory | Basis |
| --- | --- | --- |
| `cursor` | `PROJECT/.cursor/skills/<name>/` | Cursor project skills. Personal skills use `~/.cursor/skills/`. |
| `claude` | `PROJECT/.claude/skills/<name>/` | Claude Code skill directories. Claude can also install skills through a plugin marketplace. |
| `codex` | `PROJECT/.agents/skills/<name>/` | Codex repository skills. User skills use `~/.agents/skills/`. Documented at the OpenAI Codex skills guide. |
| `generic` | `PROJECT/skills/<name>/` | A vendor-neutral directory of `SKILL.md` folders. |
| `gemini` | `PROJECT/.gemini/skills/<name>/` | Gemini CLI workspace skills. `~/.gemini/skills` and `.agents/skills` are also scanned. |
| `copilot` | `PROJECT/.github/skills/<name>/` | GitHub Copilot CLI project skills. `~/.copilot/skills` and `.agents/skills` are also scanned. |

`--on-conflict backup` is the default. If the target directory exists, it is copied to a sibling ending in `.bak-<timestamp>` and then replaced. `--on-conflict skip` leaves the existing directory untouched. `--on-conflict overwrite` also keeps that timestamped backup, then replaces the active directory. The exporter will not write a skill whose recorded path is outside this library.

## Manual use

Some agents cannot discover an external catalog.

1. Give the agent `MASTER_SKILL.md`.
2. Ask it to run `python tools/search_skills.py --query "..." --json`, or run that command yourself.
3. Paste only the selected skill's `SKILL.md` and the files it references. Do not paste the rest of the catalog.
4. Ask the agent to follow the skill with the tools it already has, then check the result.

Codex also reads `AGENTS.md`. That file is a project instruction, not a place to dump every skill. Keep `MASTER_SKILL.md` as the coordinator and leave the skills in their own directories.

## Adapter interface

Python callers use `uaskills.adapters.export_skills`. A target is a layout string in `ADAPTERS`. A new target needs a layout relative to the destination, a short summary, and a manual-discovery note. It should not change the catalog schema or the importer.
