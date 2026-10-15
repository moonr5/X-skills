# Internet Court

Source: https://github.com/internet-court/internet-court-skill
Commit: `fa89195eeb5c12827e0b11e1c16b4fc8733711d4`

This is a router skill plus connector skills and vendored copies of other projects' skills. 95 packages were imported. No wallet, chain, or API account was connected, and no payment or contract transaction was sent.

The repository license is MIT only for the root `SKILL.md`, the README, and `integrations/`. Files under `vendored/` keep the license that arrived inside each skill. A missing license file stays `unknown`.

The README says some vendored skills need provider credentials, including OKX, ChainGPT, and Chainbase keys. Those keys were not created or stored.

Capabilities are limited to what each imported `SKILL.md` says. This note does not add any.

Catalog paths are under `skills/<category>/internet-court+internet-court-skill/`. Search with:

```
python tools/search_skills.py --source internet-court/internet-court-skill --limit 20
```
