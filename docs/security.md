# Security and licensing

Imported skills are untrusted. Import copies files, parses text, computes checksums, and records metadata. It does not run skill scripts, open a shell, or install dependencies.

## Path and archive checks

- Skill paths must stay inside the library's `skills/` or `quarantine/` directories.
- Archive members with absolute paths, drive letters, `..`, or null bytes reject the whole archive.
- Declared uncompressed size, file count, and compression ratio are capped by `config/settings.yaml`.
- Symbolic links that resolve outside the source directory are not copied.
- `.exe`, `.dll`, `.scr`, `.com`, `.msi`, `.sys`, `.drv`, `.bat`, `.cmd`, and `.vbs` files quarantine the skill. The bytes remain in `quarantine/` for inspection and are hidden from default search.
- Shell and PowerShell scripts are stored and flagged. They are not run.

These checks are not a malware detector. A skill can be harmful without a blocked file extension.

## Instruction heuristics

The scanner looks for a few high-risk text patterns, including a download piped into a shell, a recursive delete of the filesystem root, and instructions to steal credentials. A match is a finding. It does not prove intent, and the absence of a match does not prove the skill is safe. `security_scan_status=screened` only means the heuristic ran.

## Licenses

The license field records what GitHub, a `LICENSE` file, or the skill frontmatter said. `declared` is not `reviewed`. Reusing or redistributing an imported skill is governed by that skill's own license. The MIT license in this repository covers the catalog tooling, not the imported content.

## Tokens

`GITHUB_TOKEN` and `GH_TOKEN` are read from the environment. Settings files that contain a token, password, or similar key are rejected. Error logs pass through a redaction step for common GitHub token shapes and `token=` assignments. Do not put secrets in skill files you import.

## Network

GitHub requests are limited to `api.github.com`, `raw.githubusercontent.com`, and `codeload.githubusercontent.com`. Other hosts in a repository URL are rejected. Skill content is not sent to an embedding API. Semantic search stays disabled until a provider is configured, and this version does not implement one.

## What remains your decision

Quarantine, validation, and planning do not authorize execution. Before an agent runs a script from a skill, read it and use the permissions you intended to give that agent.
