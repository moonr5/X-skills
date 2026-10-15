# Generic agents

Use this layout when the agent has no special skill directory:

```
python tools/export_skills.py --target generic --dest PROJECT --query "words" --on-conflict backup
```

Files are copied to `PROJECT/skills/<skill-name>/`. Point the agent at `MASTER_SKILL.md` and tell it to search this library rather than reading every exported skill.
