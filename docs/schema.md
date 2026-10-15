# Skill manifest

Each imported skill keeps its original files. The catalog stores a normalized manifest in `index/metadata/<id>.json` and in SQLite. The JSON Schema is `schemas/skill-manifest.schema.json`.

Missing information stays null or `unknown`. The importer does not write a description, license, runtime, or capability that the source did not provide.

| Field | Meaning |
| --- | --- |
| `id` | Stable id derived from the source host, owner, repository, and skill path. A new commit does not create a new id. |
| `name` | Declared skill name, or the directory name when no name was declared. `name_source` says which. |
| `description` | Declared description only. |
| `source_url` | Source location, including the commit when it came from GitHub. |
| `repository` | Host, owner, name, and repository URL. |
| `commit_sha` | Commit used for a GitHub import, when available. |
| `version` | Declared version, when present. |
| `local_path` | Path of the copied skill relative to the library root. |
| `origin_path` | Local directory that was imported, when the source was a directory. |
| `original_format` | `agentskills.skill_md`, `skill.manifest.json`, or `skill.manifest.yaml`. |
| `normalized_format` | `uaskills.manifest.v1` for the sidecar. The original file is not rewritten. |
| `category` | A configured category, or `other` when classification is unknown or tied. |
| `tags`, `capabilities`, `supported_runtimes`, `required_tools`, `dependencies` | Declared lists, or null when absent. |
| `license.status` | `unknown`, `declared`, or `reviewed`. |
| `license.identifier` | A license token such as `MIT` or `Apache-2.0` when the source presented one. |
| `license.evidence` | Where that token or file was seen. |
| `content_checksum` | SHA-256 of the primary skill file. |
| `tree_checksum` | SHA-256 over the imported files. |
| `verification_status` | `unverified` until a person sets `tested` or `verified`. |
| `security_scan_status` | `not_scanned` or `screened`. |
| `security_findings` | Heuristic findings. They are not a safety verdict. |
| `quality_assessment` | Whether the name, description, and instructions are present. |
| `deprecation_status` | `deprecated` only when the skill declares that. |
| `lifecycle_status` | `parsed`, `quarantined`, or `deprecated` after import. |
| `duplicate_of` | Canonical skill id when the instruction checksum matches an earlier skill. |
| `extensions` | Source topics, observed script languages, the raw frontmatter, and the file list. |

`license.reviewed` is not inferred from a GitHub SPDX field. That field produces `declared`.
