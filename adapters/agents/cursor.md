Cursor project skills are directories under `.cursor/skills/<skill-name>/` with a `SKILL.md` file. Personal skills use the same layout under `~/.cursor/skills/`. Cursor does not read this library's `skills/` tree automatically.

Export with:

```
python tools/export_skills.py --target cursor --dest PROJECT_OR_HOME --query "words" --on-conflict backup
```

The default conflict policy copies an existing target skill to a `.bak-<timestamp>` directory before replacing it.
