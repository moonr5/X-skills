# Coding assistants

Coding assistants usually differ by the directory they scan, not by the `SKILL.md` format. Use the named adapters:

- Cursor: `adapters/agents/cursor.md`
- Claude Code: `adapters/agents/claude.md`
- Codex: `adapters/agents/codex.md`

Other editors can use the generic layout or a manual prompt that includes only the selected skill. Add a new adapter by documenting the directory the product actually scans and registering it in `uaskills.adapters.ADAPTERS`. Do not special-case that product inside the importer.
