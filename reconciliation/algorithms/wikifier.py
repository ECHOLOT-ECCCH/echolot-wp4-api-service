import json
import os
import re
import urllib.parse
import urllib.request
from urllib.error import URLError

from dotenv import load_dotenv

from reconciliation.algorithms import AlgorithmNotConfigured
from reconciliation.models import Entity, ReconciliationMatch, ReconciliationResult

load_dotenv()

# Network failures and malformed responses from wikifier.org.
UPSTREAM_ERRORS = (TimeoutError, URLError, json.JSONDecodeError, KeyError)


def check_configured() -> None:
    if not os.getenv("WIKIFIER_API_KEY"):
        raise AlgorithmNotConfigured("WIKIFIER_API_KEY is not set")


def reconcile(entity: Entity) -> ReconciliationResult:
    raw = entity.model_dump_json()
    ch_from, ch_to = _find_name_span(raw, entity.name)
    if ch_from is None:
        return ReconciliationResult(matches=[])

    wikifier_response = _wikify(raw)

    matches = []
    for ann in wikifier_response.get("annotations", []):
        overlapping = [
            s
            for s in ann.get("support", [])
            if _spans_overlap(s["chFrom"], s["chTo"], ch_from, ch_to)
        ]
        if not overlapping:
            continue
        best = max(overlapping, key=lambda s: s.get("prbConfidence", 0))
        matches.append(
            ReconciliationMatch(
                url=ann["url"],
                wikidata_item_id=ann.get("wikiDataItemId"),
                title=ann["title"],
                score=best["prbConfidence"],
            )
        )

    matches.sort(key=lambda m: m.score, reverse=True)
    return ReconciliationResult(matches=matches)


def _wikify(text: str, lang: str = "en", threshold: float = 0.8) -> dict:
    data = urllib.parse.urlencode(
        [
            ("text", text),
            ("lang", lang),
            ("userKey", os.getenv("WIKIFIER_API_KEY", "")),
            ("pageRankSqThreshold", "%g" % threshold),
            ("applyPageRankSqThreshold", "true"),
            ("nTopDfValuesToIgnore", "200"),
            ("nWordsToIgnoreFromList", "200"),
            ("wikiDataClasses", "true"),
            ("wikiDataClassIds", "false"),
            ("support", "true"),
            ("ranges", "false"),
            ("minLinkFrequency", "2"),
            ("includeCosines", "false"),
            ("maxMentionEntropy", "3"),
        ]
    )
    req = urllib.request.Request(
        "http://www.wikifier.org/annotate-article",
        data=data.encode("utf8"),
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as f:
        return json.loads(f.read().decode("utf8"))


def _find_name_span(raw: str, name: str) -> tuple[int, int] | tuple[None, None]:
    m = re.search(r'"name"\s*:\s*"', raw)
    if not m:
        return None, None
    ch_from = m.end()
    return ch_from, ch_from + len(name) - 1


def _spans_overlap(a_from: int, a_to: int, b_from: int, b_to: int) -> bool:
    return a_from <= b_to and b_from <= a_to
