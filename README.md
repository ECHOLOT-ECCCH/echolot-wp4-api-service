# ECHOLOT Reconciliation

Async HTTP API for reconciling entities against Wikidata, orchestrated with [Prefect](https://prefect.io). Three interchangeable reconciliation algorithms are available, chosen per request with `?algorithm=`.

What was added when, by commit, and what is deployed: [CHANGELOG.md](CHANGELOG.md).

## Setup

```bash
uv sync
cp .env.example .env  # configure the algorithms you want to use, see below
```

## Reconciliation algorithms

| `algorithm` | How it works | Configuration | `score` |
| --- | --- | --- | --- |
| `wikifier` (default) | Annotates the entity's JSON with [Wikifier](http://wikifier.org) and keeps the annotations that cover the name. Returns Wikipedia URLs. | `WIKIFIER_API_KEY` | Wikifier's link confidence |
| `llm_based` | From `draft/llm_based`: searches Wikidata for the name, describes the top 5 hits, and asks a language model (Latxa) which one, if any, is the same person. With `HITZ_API_KEY`, HiTZ's hosted service runs this; otherwise the draft runs here against an OpenAI-compatible chat model. | `HITZ_API_KEY` (and optionally `HITZ_API_URL`); or, without it, `OPENAI_BASE_URL`, `OPENAI_API_KEY`, `OPENAI_MODEL`, `OPENAI_MAX_TOKENS`, `OPENAI_TEMPERATURE`, `OPENAI_TOP_P`, `OPENAI_STREAM` (all required) | Always `1.0`: the model picks one candidate or none |
| `rule_based` | From `draft/rule_based`: matches people against a local TSV dump of Wikidata or Getty ULAN using Soundex blocking, biographical rules and Jaro-Winkler name similarity. | `RULE_BASED_TARGET_FILE`, the path to the TSV | The draft's normalised score, 0.9–1 |

Each algorithm returns the same result shape. `llm_based` and `rule_based` return Wikidata entity URLs (`http://www.wikidata.org/entity/Q…`). A `rule_based` match against a ULAN row with no Wikidata link returns the ULAN URL and `"wikidata_item_id": null`.

The draft implementations are included in [reconciliation/algorithms/llm_based/](reconciliation/algorithms/llm_based/) and [reconciliation/algorithms/rule_based/](reconciliation/algorithms/rule_based/) as copies of the draft files. `ruleBasedMatcher.py` is unchanged. In llm_based, the only change is that the files import each other with relative imports (`from .config import ...`), which they need to work inside a package. Each package's `__init__.py` is an adapter that converts between this API's entities and results and the drafts' formats. To update from a newer draft, copy the files again and re-apply those three import edits.

Notes:
- `rule_based` passes each entity through the draft's own Echolot JSON reader (`RuleBasedMatcher.match_file`). That reader matches only `entity_type: "person"` and uses the name, `information.gender` and `identifiers.wikidata`; other `information` fields, such as `birth_year`, are not used.
- `rule_based` indexes the datasheet in memory on first use in each process, and later requests reuse the index. `draft/rule_based/datasheets/ulan.tsv` (291k people) takes about 4 s and 0.2 GB. The full Wikidata dump (`wikidata.tsv.zip`, 5.9M people; unzip it first) takes about 80 s and 3.8 GB. Under Prefect, every job is a new process, so the index is rebuilt once per job.
- `llm_based` with `HITZ_API_KEY` sends each entity to HiTZ's service (`POST https://zerbitzuak.hitz.eus/api/reconciliation`, header `apikey`; get a key at https://zerbitzuak.hitz.eus/tresnak, Profile, Generate new API Key). It takes 6 to 12 s per entity, and well under a second for a name with no candidates. The entity's `information` is sent as one text (`gender: male; publication: A, B`, at most five publications), with `name` and `context`. A key HiTZ refuses answers `503`, not `502`, so callers do not retry it. HiTZ's `vector` option is left off: on 2026-11-12 it missed a match the default found.
- `llm_based` without it (the draft here) makes six Wikidata requests and one model call per entity. Prompts are the same as the draft's batch driver (`obtain_ids_from_wikidata`) produces, but the driver's 6 s pause between entities is not applied. The Prefect flow processes entities concurrently, so large batches can hit Wikidata rate limits, and failed lookups yield no match rather than an error.
- **Known issue (draft dependency):** `rule_based` uses `fuzzy` 1.2.2 for Soundex. On Python 3 that package reads its code table from freed memory, so blocking keys vary between processes. The same query on the same datasheet can match in one run and not in the next. This comes from the draft as it is and needs fixing there.

## Running

Three terminals, start in this order:

**Terminal 1 — Prefect server** (UI at http://localhost:4200)
```bash
uv run prefect server start
```

**Terminal 2 — Flow worker** (registers deployment + executes flow runs)
```bash
uv run python -m reconciliation.serve
```

**Terminal 3 — HTTP API** (http://localhost:8000, docs at http://localhost:8000/docs)
```bash
uv run uvicorn reconciliation.api:app --reload --port 8000
```

## Demo

Submit the sample input and poll until complete:

```bash
uv run python run_job.py              # uses examples/in.json, saves to out.json
uv run python run_job.py my_input.json my_output.json
uv run python run_job.py my_input.json my_output.json rule_based
```

## API

### Submit a reconciliation job

```
POST /reconcile?algorithm=wikifier|llm_based|rule_based
```

Accepts a list of entities and returns a job ID immediately. Reconciliation runs in the background. `algorithm` defaults to `wikifier`. An unknown name returns `422`. A job whose algorithm is not configured fails, and its `error` names the missing setting.

**Request**
```json
{
  "entities": [
    {
      "uri": "https://ehkultura.wikibase.cloud/entity/Q24903",
      "name": "Brian O'Doherty",
      "entity_type": "person",
      "identifiers": {
        "wikidata": "http://www.wikidata.org/entity/Q3777954"
      },
      "information": {
        "birth_year": "1928"
      },
      "context": "Inside the white cube : the ideology of the gallery space / Brian O'Doherty"
    }
  ]
}
```

**Response** `202 Accepted`
```json
{
  "job_id": "3f2e1a4b-...",
  "status": "SCHEDULED",
  "algorithm": "wikifier"
}
```

### Check job status

```
GET /jobs/{job_id}
```

Possible statuses: `SCHEDULED` → `RUNNING` → `COMPLETED` | `FAILED` | `CRASHED`

### Get job result

```
GET /jobs/{job_id}/result
```

Returns `null` for `result` if the job is not yet complete. If an individual entity fails, it is included with `"matches": []` and an `"error"` field — the rest of the batch still completes.

## Adding a new reconciliation algorithm

1. Create `reconciliation/algorithms/<name>.py` (or a `<name>/` package) that defines:
   ```python
   from reconciliation.algorithms import AlgorithmNotConfigured
   from reconciliation.models import Entity, ReconciliationMatch, ReconciliationResult

   # Exception types meaning a remote service failed; the standalone API answers 502.
   UPSTREAM_ERRORS: tuple[type[Exception], ...] = ()

   def check_configured() -> None:
       """Raise AlgorithmNotConfigured if a key, endpoint or data file is missing."""

   def reconcile(entity: Entity) -> ReconciliationResult:
       ...
   ```

2. Register it in [reconciliation/algorithms/\_\_init\_\_.py](reconciliation/algorithms/__init__.py): add a member to `AlgorithmName` and its module path to `_MODULES`.

Both APIs, the Prefect flow and the models are algorithm-agnostic. Modules are imported only when they are selected, so an algorithm's dependencies and configuration don't affect the others.

## curl examples

```bash
# Submit (add ?algorithm=llm_based or ?algorithm=rule_based to switch)
curl -s -X POST http://localhost:8000/reconcile \
  -H "Content-Type: application/json" \
  -d @examples/in.json | jq .

# Poll status
curl -s http://localhost:8000/jobs/<job_id> | jq .status

# Fetch result
curl -s http://localhost:8000/jobs/<job_id>/result | jq .
```

## Docker API without Prefect

The Docker image runs a separate synchronous API on port 5000. It accepts one entity per request, returns the result immediately, and does not install or start Prefect. The original Prefect API above is unchanged.

### Start

1. Create `.env` if needed and configure the algorithms you want (see [Reconciliation algorithms](#reconciliation-algorithms)):

```bash
cp .env.example .env
```

The rule-based datasheets are not part of the image. For `rule_based`, the datasheet must be visible inside the container. Uncomment the `volumes` entry in `docker-compose.yml` and set `RULE_BASED_TARGET_FILE=/data/ulan.tsv` (or `/data/wikidata.tsv` after unzipping it, which needs about 4 GB of memory) in `.env`.

2. Generate the API key stored in `.env`:

```bash
sed -i "s/^API_KEY=.*/API_KEY=$(openssl rand -hex 32)/" .env
```

3. Build and start the container:

```bash
docker compose up --build -d
```

4. Check the service:

```bash
docker compose ps
curl --fail http://localhost:5000/health
```

The API documentation is available at <http://localhost:5000/docs>. View logs with `docker compose logs -f` and stop it with `docker compose down`.

### Reconcile one entity

Load the API key from `.env` and send a single entity object:

```bash
set -a
. ./.env
set +a

curl --fail-with-body --silent --show-error \
  -X POST "http://localhost:5000/reconcile?algorithm=rule_based" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  --data @examples/single_entity.json
```

`algorithm` works as in the Prefect API and defaults to `wikifier`. The response is `{"uri": ..., "algorithm": ..., "matches": [...]}`. Errors: `503` when the chosen algorithm is not configured, `502` when Wikifier or the LLM endpoint fails, `422` for an unknown algorithm. The first `rule_based` request in a fresh container waits while the datasheet is indexed.

To run the image without Compose:

```bash
docker build -t echolot-wp4-api-service .
docker run --env-file .env -p 5000:5000 echolot-wp4-api-service
```

### Enrich a Wikidata item from a Wikipedia page

`POST /enrich` takes a Wikidata item and a Wikipedia article about the same subject. It reads the item's current statements and the properties Wikidata expects for its type (`instance of` → `properties for this type`), then asks an LLM to propose values found in the article. Nothing is written to Wikidata; the response is a list of suggestions for a person to review.

Set the LLM key in `.env` (only Anthropic is implemented so far):

```
ANTHROPIC_API_KEY=...
LLM_PROVIDER=anthropic      # optional, default
LLM_MODEL=claude-opus-5     # optional, default
```

Optional: `LLM_EFFORT` (`low`, `medium`, `high`, `xhigh` or `max`; default `high`), `LLM_REFUSAL_FALLBACK` (default on: a request the model declines on safety grounds is re-run server-side on Anthropic's recommended fallback model; set to `off` to get a plain `502` instead) and `WIKIMEDIA_USER_AGENT` (sent to the Wikimedia APIs; set it to something that identifies your deployment).

```bash
curl --fail-with-body --silent --show-error \
  -X POST http://localhost:5000/enrich \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  --data @examples/enrich_request.json
```

**Request** — `wikidata_id` may be a bare id or a full entity URL; the Wikipedia URL supplies the language and title.
```json
{
  "wikidata_id": "Q937",
  "wikipedia_url": "https://en.wikipedia.org/wiki/Albert_Einstein"
}
```

**Response** (abridged)
```json
{
  "wikidata_id": "Q937",
  "label": "Albert Einstein",
  "description": "German-born theoretical physicist (1879–1955)",
  "wikipedia_url": "https://en.wikipedia.org/wiki/Albert_Einstein",
  "wikipedia_title": "Albert Einstein",
  "instance_of": [{"id": "Q5", "label": "human"}],
  "candidate_source": "properties_for_this_type",
  "candidate_properties": [
    {"id": "P569", "label": "date of birth", "datatype": "time", "has_value": true}
  ],
  "suggestions": [
    {
      "property_id": "P19",
      "property_label": "place of birth",
      "datatype": "wikibase-item",
      "value": "Ulm",
      "status": "confirms",
      "existing_values": ["Ulm (Q3012)"],
      "evidence": "Einstein was born in Ulm, in the Kingdom of Württemberg ...",
      "confidence": 0.97,
      "note": null
    }
  ],
  "model": "claude-opus-5",
  "usage": {"input_tokens": 24000, "output_tokens": 1800}
}
```

Each suggestion has a `status`: `new` (the property is empty, or this is an additional value), `confirms` (matches an existing value) or `conflict` (contradicts one). `evidence` is a verbatim quote from the article. Suggestions are ordered `new`, then `conflict`, then `confirms`, by confidence within each group. External-identifier properties are ignored, since prose does not carry them. When the item's type has no `properties for this type` list, `candidate_source` is `free` and the LLM may propose any property.

Errors: `503` when no LLM key is configured, `422` for a malformed id or URL, `404` when the item or page does not exist, `502` when Wikidata, Wikipedia or the LLM fail.

To add another LLM provider, implement the `LLMProvider` protocol in [reconciliation/enrichment/llm.py](reconciliation/enrichment/llm.py) and register it in `get_provider`.

### Wikidata statements for items

`POST /wikidata/statements` returns what Wikidata holds for up to 50 items, as values a script can use: item values with their QID and label, dates at their precision, quantities with their unit. It is meant for enriching a dataset whose records have (or have been reconciled to) a QID. Without `properties` it lists every property of each item with a count and one example value, which is how to see what a sample's items hold; with `properties` it returns every value of those properties, which is what a script extracts per record. Nothing needs configuring beyond `API_KEY`; set `WIKIMEDIA_USER_AGENT` to identify your deployment to Wikidata.

```bash
curl --fail-with-body --silent --show-error \
  -X POST http://localhost:5000/wikidata/statements \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  --data '{"ids": ["Q5593"], "properties": ["P569", "P19", "P106", "P27"], "lang": "en"}'
```

**Request** — `ids`: 1 to 50 QIDs or entity URLs. `properties` (optional): property ids; omitted or empty means every property, one example each. `lang` (default `en`): the language of labels, falling back to English and then to the label shared by all languages.

**Response** (abridged)
```json
{
  "lang": "en",
  "summary": false,
  "items": [
    {
      "requested": "Q5593",
      "found": true,
      "id": "Q5593",
      "label": "Pablo Picasso",
      "description": "Spanish painter (1881–1973)",
      "statements": [
        {
          "property": "P569", "label": "date of birth", "datatype": "time", "count": 1,
          "values": [{"text": "1881-10-25", "rank": "normal", "time": "+1881-10-25T00:00:00Z", "precision": 11, "calendar": "gregorian"}]
        },
        {
          "property": "P106", "label": "occupation", "datatype": "wikibase-item", "count": 31,
          "values": [{"text": "painter", "rank": "normal", "id": "Q1028181", "label": "painter"}, "..."]
        },
        {"property": "P27", "label": "country of citizenship", "datatype": "wikibase-item", "count": 0, "values": []}
      ]
    }
  ]
}
```

- Items come back in the order asked. `id` is the item after following a redirect, so it can differ from `requested`. An id that does not exist comes back as `"found": false`, not as an error.
- Deprecated statements are left out, and so are statements whose value is "unknown" or "no value". `count` counts the rest; preferred values come first. A property asked for that the item lacks has `count: 0`.
- Every value has `text` (short readable text) and `rank`, plus fields by type: `id` and `label` for items, `time`, `precision` (11 day, 10 month, 9 year, 8 decade, 7 century) and `calendar` for dates, `amount`, `unit` and `unit_label` for quantities, `language` for monolingual text, `latitude` and `longitude` for coordinates. Fields without a value are omitted.
- Qualifiers and references are not included.
- Without `properties` the reply can be large: a famous person has hundreds of properties, most of them external identifiers (Pablo Picasso: 435, 365 of them identifiers).
- Values are Wikidata's as they stand, errors included.

Errors: `422` for a malformed item or property id, more than 50 ids or a malformed language code, `502` when Wikidata fails or does not answer.

## Tests

```bash
uv run pytest
```

`tests/` runs the standalone API against canned Wikidata replies; nothing goes over the network.
