"""LLM-based reconciliation: the draft/llm_based pipeline, behind this API.

Searches Wikidata for the entity's name, describes the top five hits and asks a language
model (Latxa) which candidate, if any, is the same person. The model makes a yes/no
choice, so a match always has score 1.0.

Two ways to run it. With HITZ_API_KEY set, the entity goes to HiTZ's hosted service
(POST {HITZ_API_URL}/reconciliation, default https://zerbitzuak.hitz.eus/api), which runs
the same pipeline and answers with the same fields. Otherwise the draft runs here against
the OpenAI-compatible endpoint in the OPENAI_* settings.

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
import requests
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

HITZ_API_URL = "https://zerbitzuak.hitz.eus/api"
# HiTZ takes 6 to 12 s per entity
HITZ_TIMEOUT_S = 120


class HitzError(Exception):
    """HiTZ's reconciliation service failed or did not answer."""


# wikidata_tools retries Wikidata requests and returns no results when they keep
# failing, so only the model call raises; with HiTZ, any failure of its service.
UPSTREAM_ERRORS = (openai.APIError, HitzError)

_lock = threading.Lock()
_latxa = None


def check_configured() -> None:
    if os.getenv("HITZ_API_KEY"):
        return
    missing = [name for name in REQUIRED_ENV if not os.getenv(name)]
    if missing:
        raise AlgorithmNotConfigured(f"HITZ_API_KEY, or {', '.join(missing)}, not set")


def reconcile(entity: Entity) -> ReconciliationResult:
    check_configured()
    output = _with_hitz(entity) if os.getenv("HITZ_API_KEY") else _here(entity)
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


def _here(entity: Entity) -> dict:
    # Imported here rather than at the top: the draft modules import config.py, which
    # raises a bare KeyError when a variable is missing.
    from .inguma_recon import obtain_correct_qid_lexical

    return obtain_correct_qid_lexical(
        _get_latxa(),
        entity.name,
        information=_information(entity),
        context=entity.context,
    )


def _with_hitz(entity: Entity) -> dict:
    """The draft's output, from HiTZ's service. A refused key means "not configured" (503),
    so callers do not retry it; any other failure is a HitzError (502)."""
    url = os.getenv("HITZ_API_URL", HITZ_API_URL).rstrip("/") + "/reconciliation"
    body = {"name": entity.name, "info": _information_text(entity), "context": entity.context}
    try:
        response = requests.post(
            url, json=body, headers={"apikey": os.environ["HITZ_API_KEY"]}, timeout=HITZ_TIMEOUT_S
        )
    except requests.RequestException as exc:
        raise HitzError(f"HiTZ did not answer: {type(exc).__name__}") from exc
    if response.status_code in (401, 403):
        raise AlgorithmNotConfigured("HiTZ refused HITZ_API_KEY")
    if response.status_code >= 400:
        raise HitzError(f"HiTZ answered {response.status_code}")
    try:
        output = response.json()
    except ValueError as exc:
        raise HitzError("HiTZ's reply is not JSON") from exc
    if not isinstance(output, dict) or "desambiguation_result" not in output:
        raise HitzError("HiTZ's reply has no desambiguation_result")
    return output


def _information_text(entity: Entity) -> str:
    """HiTZ takes the information as one text: "gender: male; publication: A, B"."""
    parts = []
    for key, value in _information(entity).items():
        text = ", ".join(value) if isinstance(value, list) else value
        if text:
            parts.append(f"{key}: {text}")
    return "; ".join(parts)


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
