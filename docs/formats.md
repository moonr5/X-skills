# Supported formats

A directory is imported as a skill when it contains one of these files:

- `SKILL.md`, the Agent Skills format. YAML frontmatter may declare `name`, `description`, `license`, `compatibility`, `metadata`, and `allowed-tools`. The markdown body is the instruction text. The specification is published at [agentskills.io/specification](https://agentskills.io/specification).
- `skill.json` or `skill.yaml` / `skill.yml` when it is a mapping. An `instructions` field is indexed as the body. The original file is preserved; the importer does not invent a `SKILL.md`.

These locations are recognized while scanning a repository, but a `SKILL.md` in another directory is still a skill:

- `skills/`
- `.cursor/skills/`
- `.claude/skills/`
- `.agents/skills/`

The following are recorded as resources, not skills, when no skill file is present:

| Evidence | Resource kind |
| --- | --- |
| `AGENTS.md`, `CLAUDE.md`, `.cursorrules`, or `copilot-instructions.md` | `agent_instructions` |
| `mcp.json` | `mcp_descriptor` |
| Anything else | `not_a_skill` |

A repository that is a library, application, framework, prompt collection, template, or dataset stays in that resource record. It is not given a fake skill name. Cursor rule files and `AGENTS.md` remain project instructions, not portable skills, unless they sit beside a real `SKILL.md` inside a skill directory.

Files inside `node_modules`, virtual environments, build directories, and `.git` are skipped. Files above `max_file_bytes` are skipped and reported. Symbolic links that point outside the source directory are rejected.

Original bytes are copied. Line endings are not rewritten.
