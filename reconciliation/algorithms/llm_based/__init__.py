"""LLM-based reconciliation: the draft/llm_based implementation, behind this API.

Searches Wikidata for the entity's name, describes the top five hits with SPARQL and
asks an OpenAI-compatible chat model (Latxa in the draft) which candidate, if any, is
the same person. The model makes a yes/no choice, so a match always has score 1.0.

config.py, latxa_recon.py, inguma_recon.py and wikidata_tools.py are copies of the
draft files. The only change is that their imports of each other are relative, which
they need to work inside this package. main.py (the draft's CLI) is not included.

Each entity goes through the draft's obtain_correct_qid_lexical, with the same
preprocessing as its batch driver (obtain_ids_from_wikidata). The driver itself is not
called, because it sleeps 6 s per entity and appends to a JSONL file.
"""

import os
import threading

import openai
from dotenv import load_dotenv

from reconciliation.algorithms import AlgorithmNotConfigured, wikidata_entity_url
from reconciliation.models import Entity, ReconciliationMatch, ReconciliationResult

load_dotenv()

# config.py reads every one of these with os.environ[...] when it is imported.
REQUIRED_ENV = (
    "OPENAI_BASE_URL",
    "OPENAI_API_KEY",
    "OPENAI_MODEL",
    "OPENAI_MAX_TOKENS",
    "OPENAI_TEMPERATURE",
    "OPENAI_TOP_P",
    "OPENAI_STREAM",
)

# wikidata_tools retries Wikidata requests and returns no results when they keep
# failing, so only the model call raises.
UPSTREAM_ERRORS = (openai.APIError,)

_lock = threading.Lock()
_latxa = None


def check_configured() -> None:
    missing = [name for name in REQUIRED_ENV if not os.getenv(name)]
    if missing:
        raise AlgorithmNotConfigured(f"{', '.join(missing)} not set")


def reconcile(entity: Entity) -> ReconciliationResult:
    check_configured()
    # Imported here rather than at the top: the draft modules import config.py, which
    # raises a bare KeyError when a variable is missing.
    from .inguma_recon import obtain_correct_qid_lexical

    output = obtain_correct_qid_lexical(
        _get_latxa(),
        entity.name,
        information=_information(entity),
        context=entity.context,
    )
    qid = output["desambiguation_result"]
    if qid == "None":
        return ReconciliationResult(matches=[])

    candidate = next((r for r in output["full_results"] if r["QID"] == qid), {})
    label = (candidate.get("description") or {}).get("name")
    match = ReconciliationMatch(
        url=wikidata_entity_url(qid),
        wikidata_item_id=qid,
        title=label or qid,
        score=1.0,
    )
    return ReconciliationResult(matches=[match])


def _information(entity: Entity) -> dict:
    # As in obtain_ids_from_wikidata: keep at most five publications.
    information = dict(entity.information)
    publication = information.get("publication", [])
    information["publication"] = (
        publication[:5] if isinstance(publication, list) else publication
    )
    return information


def _get_latxa():
    global _latxa
    with _lock:
        if _latxa is None:
            from .latxa_recon import Latxa

            _latxa = Latxa()
        return _latxa
