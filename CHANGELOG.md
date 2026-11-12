# Changelog

What was added when, by commit, newest first. Each section names the branch the commits are on;
`main` is what has been merged. The ECHOLOT workbench that calls this API
([echolot-wp4-agentic-app](https://github.com/ECHOLOT-ECCCH/echolot-wp4-agentic-app)) keeps its
own changelog and decision log.

## Deployments

| Date | Where | Commit | What changed |
|---|---|---|---|
| 2026-11-11 | https://echolot.ijs.si/service/ (in the workbench's Compose project) | `eecc6a9` | First deployment: the three algorithms (`rule_based` with the Getty ULAN table, `llm_based` through OpenRouter), `/enrich` without a server key, `/wikidata/statements` |
| 2026-11-12 | same | `d0af95e` | This changelog only; the running code is unchanged |
| 2026-11-12 | same | `ce6175f` | `llm_based` through HiTZ's hosted Latxa service, with the server's own `HITZ_API_KEY` |

The host keeps the same log, with the exact commits checked out, in `/opt/echolot/README.md`.

## Branch `feat/wikidata-statements` (not merged; builds on `llm-and-rule-based`)

| Commit | Date | Author | What it adds | Where |
|---|---|---|---|---|
| (this commit) | 2026-11-12 | andrazrepar | The deployment of `ce6175f` recorded, and its hash filled in below | `CHANGELOG.md` |
| `ce6175f` | 2026-11-12 | andrazrepar | **`llm_based` through HiTZ's hosted service:** with `HITZ_API_KEY` set, each entity goes to `POST https://zerbitzuak.hitz.eus/api/reconciliation` (the Latxa pipeline of the draft, run by HiTZ) instead of the local draft and its OpenAI-compatible endpoint; same match format. A key HiTZ refuses gives 503 instead of 502. Tests with a fake HiTZ. | `reconciliation/algorithms/llm_based/__init__.py`, `standalone_api.py`, `tests/test_llm_based_hitz.py`, `README.md`, `.env.example` |
| `d0af95e` | 2026-11-12 | andrazrepar | This changelog | `CHANGELOG.md` |
| `eecc6a9` | 2026-11-11 | andrazrepar | **`POST /wikidata/statements`:** what Wikidata holds for up to 50 items as values a script can use. Item values come with QID and label, dates at their precision, quantities with their unit, preferred values first. Without `properties`, every property with a count and one example (exploring); with `properties`, every value of those (extracting). The first tests in this branch, with pytest as a dev dependency; a README section. | `reconciliation/enrichment/statements.py`, `models.py`, `standalone_api.py`, `tests/test_statements_api.py`, `README.md` |

## Branch `llm-and-rule-based` (not merged)

| Commit | Date | Author | What it adds |
|---|---|---|---|
| `bdfd048` | 2026-11-10 | pzajec | The `llm_based` and `rule_based` algorithms beside `wikifier`, chosen per request with `?algorithm=`, adapted from the drafts in [echolot-WP4](https://github.com/ECHOLOT-ECCCH/echolot-WP4) |

## Branch `feat/enrich-model-and-key` (not merged; not part of the branches above)

| Commit | Date | Author | What it adds |
|---|---|---|---|
| `3d26da8` | 2026-11-05 | andrazrepar | `/enrich` callers choose the model and can send their own LLM key (`X-LLM-API-Key`), with tests |

## `main`

| Commit | Date | Author | What it adds |
|---|---|---|---|
| `1d6b173` | 2026-10-28 | andrazrepar | The repository is named echolot-wp4-api-service |
| `59961cc` | 2026-10-28 | andrazrepar | `POST /enrich`: suggested Wikidata statements for an item from its Wikipedia article, by an LLM |
| `9464611` | 2026-08-28 | pzajec | The reconciliation API (Wikifier), with Prefect and the standalone API |
| `508d364`, `920c88b` | 2026-06-17 | pzajec | The first commits |
