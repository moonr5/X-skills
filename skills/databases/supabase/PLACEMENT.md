# Supabase agent skills

Source: https://github.com/supabase/agent-skills
Commit: `c9be0e931b7930f7d02126d04774d904c381e7d7`
License: MIT (Copyright (c) 2026 Supabase)

Two skill packages were imported. No Supabase project was opened or changed, and the Supabase CLI was not installed.

| Skill | Version in file | Catalog path |
| --- | --- | --- |
| supabase | 0.1.2 | `skills/databases/supabase+agent-skills/skills/supabase` |
| supabase-postgres-best-practices | 1.1.1 | `skills/databases/supabase+agent-skills/skills/supabase-postgres-best-practices` |

Reading either skill does not connect an account and does not run SQL. Search with:

```
python tools/search_skills.py --source supabase/agent-skills
```
