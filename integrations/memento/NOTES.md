# Memento-Skills

Source: https://github.com/Memento-Teams/Memento-Skills
Commit: `ee9b9a45efd093d669c06fe318b4b1dceb246d19`
License: Apache-2.0
Version named in the README: 0.4.0

This repository is an agent application and runtime, not only a skill collection. It includes a desktop app, a Python 3.12+ runtime, CLI, and GUI. None of those were installed. No installer was run.

Four built-in skill packages were imported:

| Skill | What it is | Catalog path |
| --- | --- | --- |
| filesystem | Local file read, write, edit, and search instructions | `skills/business/Memento-Teams+Memento-Skills/builtin/skills/filesystem` |
| skill-creator | Skill authoring and evaluation. The file says it was modified by Memento-Skills contributors from an Apache-2.0 original. | `skills/other/Memento-Teams+Memento-Skills/builtin/skills/skill-creator` |
| uv-pip-install | Instructions for installing Python packages with `uv` inside the Memento environment | `skills/other/Memento-Teams+Memento-Skills/builtin/skills/uv-pip-install` |
| web-search | Web search and page fetch. Declares `httpx`, `beautifulsoup4`, and `markdownify`. | `skills/other/Memento-Teams+Memento-Skills/builtin/skills/web-search` |

These skills assume the Memento runtime. Reading them does not grant filesystem, package-install, or network permission. `uv-pip-install` must not be followed unless you approve a package install.

Desktop app download page, not used: https://memento.run/release
