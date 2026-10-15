# Collection report

These counts are catalog records, not a claim that the 100,000-skill target has been met.
Synthetic test rows are excluded from the unique skill count.

- Unique skill packages with local files: 134
- Target: 100000
- Remaining gap: 99866
- Skill rows that record a commit: 138
- Repositories discovered: 305
- Repositories inspected: 8
- Inspected repositories that contain SKILL.md: 5
- Inspected repositories without a skill file: 3
- Non-skill resources: 1
- Duplicate rows excluded from default search: 0
- Quarantined: 0
- Withheld because the license forbids a local copy: 4
- Recorded import errors: 0

## Inspections

| Repository | SKILL.md files seen | License field | Classification | README links |
| --- | --- | --- | --- | --- |
| VoltAgent/awesome-agent-skills | 0 | MIT | no_skill_file | 380 |
| anthropics/skills | 20 |  | contains_skills | 0 |
| junminhong/awesome-agent-skills | 0 | MIT | no_skill_file | 34 |
| open-agent-craft/awesome-agent-skills | 2 | MIT | contains_skills | 0 |
| praxstack/awesome-claude-skills | 0 | MIT | no_skill_file | 380 |
| seb1n/awesome-ai-agent-skills | 103 | MIT | contains_skills | 0 |
| vercel-labs/agent-skills | 9 |  | contains_skills | 0 |
| vivy-yi/awesome-skills | 9 | MIT | contains_skills | 0 |

A SKILL.md count from a Git tree is not an imported-skill count. Import stores a package only after its files are copied or, when the license forbids that, a source link is recorded instead.

GitHub repository search returns at most 1000 results per query, and code search is often unavailable without authentication. Those limits are coverage gaps, not empty results to be filled with generated skills.

Continue with:

```
python tools/collect_skills.py
python tools/import_skills.py --repo OWNER/REPOSITORY
python tools/collection_report.py
```
