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
