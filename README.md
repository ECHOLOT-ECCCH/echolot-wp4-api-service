# ECHOLOT Reconciliation

Async HTTP API for reconciling entities against Wikidata via the [Wikifier](http://wikifier.org) service, orchestrated with [Prefect](https://prefect.io).

## Setup

```bash
uv sync
cp .env.example .env  # set WIKIFIER_API_KEY
```

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
```

## API

### Submit a reconciliation job

```
POST /reconcile
```

Accepts a list of entities and returns a job ID immediately. Reconciliation runs in the background.

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
  "status": "SCHEDULED"
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

1. Create `reconciliation/algorithms/<name>.py` and implement:
   ```python
   from reconciliation.models import Entity, ReconciliationMatch, ReconciliationResult

   def reconcile(entity: Entity) -> ReconciliationResult:
       ...
   ```

2. Swap the import in [reconciliation/flows.py](reconciliation/flows.py):
   ```python
   from reconciliation.algorithms.<name> import reconcile
   ```

That's it — the Prefect flow, API, and models are all algorithm-agnostic.

## curl examples

```bash
# Submit
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

1. Create `.env` if needed and set the Wikifier key:

```bash
cp .env.example .env
```

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
  -X POST http://localhost:5000/reconcile \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  --data @examples/single_entity.json
```

To run the image without Compose:

```bash
docker build -t echolot-wp4-api-service .
docker run --env-file .env -p 5000:5000 echolot-wp4-api-service
```

### Enrich a Wikidata item from a Wikipedia page

`POST /enrich` takes a Wikidata item and a Wikipedia article about the same subject. It reads the item's current statements and the properties Wikidata expects for its type (`instance of` → `properties for this type`), then asks an LLM to propose values found in the article. Nothing is written to Wikidata; the response is a list of suggestions for a person to review.

Set the server's LLM key in `.env` (only Anthropic is implemented so far). Callers can also send their own, as described below; leave `ANTHROPIC_API_KEY` unset to require that.

```
ANTHROPIC_API_KEY=...       # optional if every caller brings their own key
LLM_PROVIDER=anthropic      # optional, default
LLM_MODEL=claude-opus-5     # optional, default
```

Optional: `LLM_EFFORT` (`low`, `medium`, `high`, `xhigh` or `max`; default `high`), `LLM_REFUSAL_FALLBACK` (default on: on models that support it, a request the model declines on safety grounds is re-run server-side on Anthropic's recommended fallback model; set to `off` to get a plain `502` instead) and `WIKIMEDIA_USER_AGENT` (sent to the Wikimedia APIs; set it to something that identifies your deployment).

```bash
curl --fail-with-body --silent --show-error \
  -X POST http://localhost:5000/enrich \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  --data @examples/enrich_request.json
```

**Request** — `wikidata_id` may be a bare id or a full entity URL; the Wikipedia URL supplies the language and title. `provider` and `model` are optional.
```json
{
  "wikidata_id": "Q937",
  "wikipedia_url": "https://en.wikipedia.org/wiki/Albert_Einstein",
  "provider": "anthropic",
  "model": "claude-opus-5-5"
}
```

**Using your own LLM key** — send it in the `X-LLM-API-Key` header, alongside `X-API-Key`:

```bash
curl --fail-with-body --silent --show-error \
  -X POST https://your-host/enrich \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -H "X-LLM-API-Key: $ANTHROPIC_API_KEY" \
  --data @examples/enrich_request.json
```

- Choosing a `model` requires your own key. Requests using the server's key always run on the server's `LLM_MODEL`.
- The key is used for that request only. It is not stored, logged or returned.
- Only send a real key over HTTPS, and never in the request body or URL. The service itself serves plain HTTP, so put a reverse proxy that provides TLS in front of it unless the caller is on the same machine.

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

`model` in the response is the model that answered, which can differ from the one requested if a safety fallback ran.

Errors:

| Status | When |
|---|---|
| `401` | The service key is invalid, or Anthropic rejected the key in `X-LLM-API-Key` |
| `404` | The item or page does not exist |
| `422` | A malformed id or URL, an unknown `provider`, a `model` sent without your own key, or a request using your own key that Anthropic rejected (for example an unknown model) |
| `429` | Anthropic's rate limit was reached |
| `502` | Wikidata, Wikipedia or the LLM failed, including Anthropic rejecting the server's own key |
| `503` | No LLM key is available from either the header or the server |

To add another LLM provider, implement the `LLMProvider` protocol in [reconciliation/enrichment/llm.py](reconciliation/enrichment/llm.py) and register it in `get_provider`.
