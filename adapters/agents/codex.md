Codex reads repository skills from `.agents/skills/<skill-name>/SKILL.md` and user skills from `~/.agents/skills/`. The format is the Agent Skills `SKILL.md` convention. Codex can also read `AGENTS.md`; that file is a project instruction and should not contain this catalog.

Export with:

```
python tools/export_skills.py --target codex --dest PROJECT --query "words" --on-conflict backup
```

Official overview: `https://developers.openai.com/codex/skills`.
