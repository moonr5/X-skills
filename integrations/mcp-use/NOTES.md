# mcp-use

Source: https://github.com/mcp-use/mcp-use
Commit: `ad8fbee97af6c39fc95b46486a04142d68644822`
License: MIT (Copyright (c) 2025 pietrozullo)

The repository is a TypeScript framework for MCP. The framework source was not copied, no package was installed, and no server was started.

Six skill packages were imported. `purchasing` and `refunds` are example workflows shipped inside the server package. They are instructions, not a live purchasing system.

| Skill | Catalog path |
| --- | --- |
| chatgpt-app-builder | `skills/other/mcp-use+mcp-use/skills/chatgpt-app-builder` |
| mcp-apps-builder | `skills/other/mcp-use+mcp-use/skills/mcp-apps-builder` |
| mcp-builder | `skills/other/mcp-use+mcp-use/skills/mcp-builder` |
| openapi-to-mcp | `skills/other/mcp-use+mcp-use/skills/openapi-to-mcp` |
| purchasing | `skills/other/mcp-use+mcp-use/libraries/typescript/packages/server/examples/skills-over-mcp/skills/purchasing` |
| refunds | `skills/other/mcp-use+mcp-use/libraries/typescript/packages/server/examples/skills-over-mcp/skills/refunds` |

The three builder skills overlap in wording and are stored as separate variants. Search with:

```
python tools/search_skills.py --source mcp-use/mcp-use --limit 10
```

`create-mcp-use-app`, the mcp-use inspector, and any cloud deploy named in those skills were not run.
