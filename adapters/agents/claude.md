Claude Code can load `SKILL.md` directories. This adapter writes them to `.claude/skills/<skill-name>/`. Claude can also install skills from a plugin marketplace; that is a product-specific install path and is not required by this library.

Export with:

```
python tools/export_skills.py --target claude --dest PROJECT --query "words" --on-conflict backup
```

If the agent cannot discover the exported directory, give it `MASTER_SKILL.md` and the one selected `SKILL.md`.
