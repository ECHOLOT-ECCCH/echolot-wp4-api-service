"""Rule-based reconciliation: the draft/rule_based implementation, behind this API.

Matches a person against a local datasheet, a TSV dump of Wikidata or Getty ULAN
people, using Soundex blocking, biographical rules and Jaro-Winkler name similarity.
A match's score is the draft's normalised score, between its score_threshold (0.9)
and 1.

ruleBasedMatcher.py is an unchanged copy of the draft file. Each entity goes through
the draft's own Echolot JSON path (RuleBasedMatcher.match_file), so the input mapping
is the draft's as well. Only people are matched, using the name, information.gender
and identifiers.wikidata.

RULE_BASED_TARGET_FILE names the datasheet. It is indexed once per process, on first
use.
"""

import json
import os
import re
import tempfile
import threading
from pathlib import Path

from dotenv import load_dotenv

from reconciliation.algorithms import AlgorithmNotConfigured, wikidata_entity_url
from reconciliation.models import Entity, ReconciliationMatch, ReconciliationResult

from .ruleBasedMatcher import RuleBasedMatcher

load_dotenv()

# The datasheet is a local file; nothing here calls a remote service.
UPSTREAM_ERRORS: tuple[type[Exception], ...] = ()

_ULAN_URL = "http://vocab.getty.edu/ulan/{}"

_lock = threading.Lock()
_matcher: RuleBasedMatcher | None = None


def check_configured() -> None:
    _target_file()


def reconcile(entity: Entity) -> ReconciliationResult:
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "entity.json"
        source.write_text(json.dumps([entity.model_dump()]), encoding="utf-8")
        results = _get_matcher().match_file(source)

    # match_file skips entities that are not people, so there may be no result.
    output = next(iter(results.values()), {"retrieval_results": []})
    matches = [
        match
        for result in output["retrieval_results"]
        if (match := _to_match(result)) is not None
    ]
    return ReconciliationResult(matches=matches)


def _to_match(result: dict) -> ReconciliationMatch | None:
    """Convert one PersonRecord.to_result_json() dict. Its QUID is the row's Wikidata
    QID when it has one, otherwise its numeric ULAN id."""
    quid = result.get("QUID")
    if not quid:
        return None  # the datasheet row has neither a Wikidata nor a ULAN id
    is_qid = re.fullmatch(r"Q\d+", quid) is not None
    return ReconciliationMatch(
        url=wikidata_entity_url(quid) if is_qid else _ULAN_URL.format(quid),
        wikidata_item_id=quid if is_qid else None,
        title=result["description"]["name"],
        score=result.get("probability", 0.0),
    )


def _target_file() -> Path:
    value = os.getenv("RULE_BASED_TARGET_FILE")
    if not value:
        raise AlgorithmNotConfigured("RULE_BASED_TARGET_FILE is not set")
    path = Path(value)
    if not path.is_file():
        raise AlgorithmNotConfigured(f"RULE_BASED_TARGET_FILE {path} is not a file")
    return path


def _get_matcher() -> RuleBasedMatcher:
    # Indexing is slow and memory-hungry. The lock makes concurrent first calls (the
    # API's thread pool, Prefect's task threads) wait for one load instead of each
    # starting their own.
    global _matcher
    with _lock:
        if _matcher is None:
            _matcher = RuleBasedMatcher(_target_file())
        return _matcher
