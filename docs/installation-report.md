# Installation report

All 15 repositories were inspected. Skill files are in the catalog. Programs, websites, and cloud tools named by those repositories were not installed.

Counts below are catalog records checked against files on disk.

## Totals

1. Repositories inspected: 15 of 15
2. Unique skill packages with local files: 602
3. New packages stored in this batch: 468
4. Duplicates excluded: 1. The frontend-slides Claude plugin copy was not stored again. The Hermes `agent-skills/` and `.claude/skills/` trees were also left out.
5. Non-skill resources: 3 (`agentskills/agentskills`, `andreasbm/web-skills`, `farion1231/cc-switch`)
6. Integrations documented: Memento, CC Switch, Oh My Hermes, and the mcp-use framework. Hermes, Memento, and mcp-use also contributed skill packages. The mcp-use framework source was not copied.
7. Quarantined packages: 0
8. Withheld for license: 4 Anthropic document skills (`docx`, `pdf`, `pptx`, `xlsx`)
9. Failures: the first Internet Court import reported 94 read errors and the retry imported those packages. Two scientific reference files were over the 800 KB download cap and were not copied. Drive C filled during repository 13; after space returned, repositories 13, 14, and 15 imported with 0 errors.
10. Tests: 22 passed (`python -m pytest tests/test_catalog.py --tb=short`).
11. Approval still required: do not install global packages, change agent settings, connect accounts, or start cloud services. Those steps were not taken.

Search the imported skills with:

```
python tools/search_skills.py --query "your task"
python tools/plan_task.py --task "your task"
```

## Repository table

| # | URL | Type | Found | Imported | Duplicates | Resources | Integration | License | Validation | Remaining |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | https://github.com/anthropics/skills | Skill collection | 20 | 16 stored, 4 withheld | 0 new | spec not copied as a skill | no | Apache-2.0 for examples; proprietary for docx, pdf, pptx, xlsx | Already at `dbd4588f9e10` | none |
| 2 | https://github.com/ibelick/ui-skills | Skills plus a website | 8 | 8 | 0 | site not copied | CLI not installed | MIT | 0 errors | `npx ui-skills` not run |
| 3 | https://github.com/alchaincyf/huashu-design | One skill | 1 | 1 | 0 | references and scripts copied | no | MIT | 0 errors | Playwright, pdf-lib, pptxgenjs, sharp not installed |
| 4 | https://github.com/andreasbm/web-skills | Educational site | 0 | 0 | 0 | 1 | no | MIT | No SKILL.md in the tree | Site assets not copied |
| 5 | https://github.com/Memento-Teams/Memento-Skills | Agent app with skills | 4 | 4 | 0 | 0 | documented | Apache-2.0 | 0 errors | Desktop app and Python 3.12 runtime not installed |
| 6 | https://github.com/ConardLi/garden-skills | Skill collection | 6 | 6 | 0 | cover images not copied | no | MIT | 0 errors | Image API and video render tools not installed |
| 7 | https://github.com/addyosmani/web-quality-skills | Skill collection | 6 | 6 | 0 | 0 | no | MIT | 0 errors | Lighthouse and Chrome DevTools not installed |
| 8 | https://github.com/browserbase/skills | Skill collection | 18 | 18 | 0 | 0 | cloud CLI not installed | MIT | 0 errors | Do not sync cookies without approval |
| 9 | https://github.com/farion1231/cc-switch | Desktop provider switcher | 0 | 0 | 0 | 1 | documented | MIT | No SKILL.md | Not installed; no config was changed |
| 10 | https://github.com/rlaope/oh-my-hermes | Hermes layer plus skills | 144 in `skills/` | 144 | duplicate trees skipped | 0 | documented | MIT | 0 errors | Hermes Agent not installed |
| 11 | https://github.com/internet-court/internet-court-skill | Router plus vendored skills | 95 | 95 | 0 | 0 | no | MIT for first-party only | Retry had 0 errors | No wallet or API was connected |
| 12 | https://github.com/K-Dense-AI/scientific-agent-skills | Scientific skills | 177 | 177 | 0 | 0 | BYOK app not installed | MIT | 177 directories exist | Two files over 800 KB were skipped |
| 13 | https://github.com/mcp-use/mcp-use | Framework plus skills | 6 | 6 | 0 | framework not copied | documented, not installed | MIT | 0 errors | No server was started |
| 14 | https://github.com/zarazhangrui/frontend-slides | One skill plus a plugin copy | 2 | 1 | plugin copy excluded | 0 | no | MIT | 0 errors | Scripts, Playwright, and Vercel were not run |
| 15 | https://github.com/supabase/agent-skills | Two database skills | 2 | 2 | 0 | 0 | CLI not installed | MIT | 0 errors | No Supabase project was opened |

Placement notes are in `docs/install-notes/` and in the destination folders named in the request. Skill bytes stay in the catalog paths `skills/<category>/<owner>+<repo>/...` so the same package is not stored twice.
