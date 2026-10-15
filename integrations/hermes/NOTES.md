# Oh My Hermes

Source: https://github.com/rlaope/oh-my-hermes
Commit: `8a45e677ef1ba62997e25bbed632b4a2e0c26313`
License: MIT
Requires: [Hermes Agent](https://github.com/NousResearch/hermes-agent). That runtime was not installed, and no global Hermes configuration was changed.

The repository is an operating layer and skill collection for Hermes Agent. The portable packages live in `skills/`. The same names also appear under `agent-skills/` and three Claude-only skills appear under `.claude/skills/`. Only `skills/` was imported, so those copies are not counted again.

Imported: 144 skill packages, 292 files. Catalog paths start with `skills/<category>/rlaope+oh-my-hermes/skills/<skill-name>`.

Search them with:

```
python tools/search_skills.py --source rlaope/oh-my-hermes --limit 20
```

Reading a skill does not start Hermes, a browser, a terminal, or a model provider.
