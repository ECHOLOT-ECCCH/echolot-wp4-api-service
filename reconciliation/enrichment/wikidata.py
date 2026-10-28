"""Read-only access to Wikidata: items, labels, datatypes and type-specific property lists."""

import json
import re

from reconciliation.enrichment.wikimedia import NotFound, UpstreamError, get_json

API_URL = "https://www.wikidata.org/w/api.php"
BATCH_SIZE = 50  # wbgetentities accepts at most 50 ids per request

INSTANCE_OF = "P31"
PROPERTIES_FOR_THIS_TYPE = "P1963"

QID_RE = re.compile(r"\bQ\d+\b", re.IGNORECASE)
PID_RE = re.compile(r"^P\d+$")

# (id, lang) -> (label, datatype). Datatype is only set for properties.
_label_cache: dict[tuple[str, str], tuple[str | None, str | None]] = {}
# class id -> ordered list of P1963 property ids
_candidates_cache: dict[str, list[str]] = {}


def parse_qid(value: str) -> str:
    """Accept 'Q937' or any URL containing the id, e.g. http://www.wikidata.org/entity/Q937."""
    match = QID_RE.search(value)
    if not match:
        raise ValueError(f"Not a Wikidata item id or entity URL: {value!r}")
    return match.group(0).upper()


def label_languages(lang: str) -> list[str]:
    """Languages to request, in fallback order.

    Wikidata stores a label that is identical across languages once under 'mul' and
    drops the per-language copies, so 'mul' must always be requested.
    """
    return list(dict.fromkeys([lang, "en", "mul"]))


def get_entities(ids: list[str], props: str, languages: list[str]) -> dict[str, dict]:
    """Fetch entities in batches. Returns id -> entity; ids that do not exist are absent.

    A redirected id is stored under both the requested and the target id.
    """
    entities: dict[str, dict] = {}
    unique_ids = list(dict.fromkeys(ids))
    for start in range(0, len(unique_ids), BATCH_SIZE):
        chunk = unique_ids[start : start + BATCH_SIZE]
        for entity_id, entity in _get_entities_chunk(chunk, props, languages).items():
            if "missing" in entity:
                continue
            entities[entity_id] = entity
            redirected_from = (entity.get("redirects") or {}).get("from")
            if redirected_from:
                entities[redirected_from] = entity
    return entities


def _get_entities_chunk(chunk: list[str], props: str, languages: list[str]) -> dict[str, dict]:
    """One wbgetentities call.

    Wikidata reports most unknown ids with a 'missing' marker, but for some (well-formed
    ids that were never assigned) it fails the whole request with 'no-such-entity'. In
    that case retry the ids one by one so the rest of the batch still resolves.
    """
    try:
        data = get_json(
            API_URL,
            {
                "action": "wbgetentities",
                "ids": "|".join(chunk),
                "props": props,
                "languages": "|".join(languages),
            },
        )
    except UpstreamError as exc:
        if exc.code != "no-such-entity":
            raise
        if len(chunk) == 1:
            return {}
        merged: dict[str, dict] = {}
        for entity_id in chunk:
            merged.update(_get_entities_chunk([entity_id], props, languages))
        return merged
    return data.get("entities", {})


def fetch_item(qid: str, lang: str) -> dict:
    entities = get_entities(
        [qid], "labels|descriptions|aliases|claims|sitelinks", label_languages(lang)
    )
    if qid not in entities:
        raise NotFound(f"Wikidata item {qid} does not exist")
    return entities[qid]


def pick_label(entity: dict, lang: str, field: str = "labels") -> str | None:
    values = entity.get(field) or {}
    for code in label_languages(lang):
        if code in values:
            return values[code]["value"]
    return None


def pick_aliases(entity: dict, lang: str) -> list[str]:
    aliases = entity.get("aliases") or {}
    for code in label_languages(lang):
        if code in aliases:
            return [alias["value"] for alias in aliases[code]]
    return []


def item_claims(entity: dict) -> dict[str, list[dict]]:
    """Property id -> main values of its non-deprecated statements.

    Each value is the snak's datavalue ({'type', 'value'}) plus the snak 'datatype'.
    'somevalue' and 'novalue' snaks carry no value and are skipped.
    """
    claims: dict[str, list[dict]] = {}
    for pid, statements in (entity.get("claims") or {}).items():
        values = []
        for statement in statements:
            if statement.get("rank") == "deprecated":
                continue
            snak = statement.get("mainsnak") or {}
            if snak.get("snaktype") != "value" or "datavalue" not in snak:
                continue
            values.append({**snak["datavalue"], "datatype": snak.get("datatype")})
        if values:
            claims[pid] = values
    return claims


def entity_id(datavalue: dict) -> str | None:
    """Return the entity id referenced by a wikibase-entityid datavalue, else None."""
    if datavalue.get("type") != "wikibase-entityid":
        return None
    value = datavalue["value"]
    if "id" in value:
        return value["id"]
    prefix = {"item": "Q", "property": "P", "lexeme": "L"}.get(value.get("entity-type"), "Q")
    return f"{prefix}{value['numeric-id']}"


def instance_of_ids(claims: dict[str, list[dict]]) -> list[str]:
    return [eid for dv in claims.get(INSTANCE_OF, []) if (eid := entity_id(dv))]


def value_entity_ids(claims: dict[str, list[dict]]) -> list[str]:
    """Every entity id used as a value or as a quantity unit; needed to render labels."""
    ids: list[str] = []
    for values in claims.values():
        for dv in values:
            if eid := entity_id(dv):
                ids.append(eid)
            elif dv.get("type") == "quantity":
                unit = dv["value"].get("unit") or "1"
                if unit != "1":
                    ids.append(unit.rsplit("/", 1)[-1])
    return ids


def properties_for_type(class_ids: list[str]) -> list[str]:
    """Ordered union of the 'properties for this type' (P1963) lists of the given classes."""
    uncached = [cid for cid in class_ids if cid not in _candidates_cache]
    if uncached:
        entities = get_entities(uncached, "claims", ["en"])
        for cid in uncached:
            claims = item_claims(entities.get(cid, {}))
            _candidates_cache[cid] = [
                eid
                for dv in claims.get(PROPERTIES_FOR_THIS_TYPE, [])
                if (eid := entity_id(dv))
            ]
    ordered: list[str] = []
    for cid in class_ids:
        for pid in _candidates_cache[cid]:
            if pid not in ordered:
                ordered.append(pid)
    return ordered


def resolve(ids: list[str], lang: str) -> dict[str, tuple[str | None, str | None]]:
    """id -> (label, datatype). Datatype is only set for properties; unknown ids map to (None, None)."""
    unique_ids = list(dict.fromkeys(ids))
    uncached = [i for i in unique_ids if (i, lang) not in _label_cache]
    if uncached:
        entities = get_entities(uncached, "labels|datatype", label_languages(lang))
        for i in uncached:
            entity = entities.get(i)
            _label_cache[(i, lang)] = (
                (pick_label(entity, lang), entity.get("datatype")) if entity else (None, None)
            )
    return {i: _label_cache[(i, lang)] for i in unique_ids}


def format_datavalue(datavalue: dict, labels: dict[str, tuple[str | None, str | None]]) -> str:
    """Render a datavalue as short human-readable text, using resolved labels for entities."""
    kind = datavalue.get("type")
    value = datavalue.get("value")
    if kind == "wikibase-entityid":
        eid = entity_id(datavalue)
        label = labels.get(eid, (None, None))[0]
        return f"{label} ({eid})" if label else eid
    if kind == "time":
        return format_time(value)
    if kind == "string":
        return value
    if kind == "monolingualtext":
        return f"{value['text']} [{value['language']}]"
    if kind == "quantity":
        amount = value["amount"].lstrip("+")
        unit = value.get("unit") or "1"
        if unit == "1":
            return amount
        unit_id = unit.rsplit("/", 1)[-1]
        unit_label = labels.get(unit_id, (None, None))[0]
        return f"{amount} {unit_label or unit_id}"
    if kind == "globecoordinate":
        return f"{value['latitude']}, {value['longitude']}"
    return json.dumps(value, ensure_ascii=False)


def format_time(value: dict) -> str:
    """Render a Wikidata time at its stated precision (11 = day, 10 = month, 9 = year, ...)."""
    raw = value.get("time", "")
    precision = value.get("precision", 11)
    sign = "-" if raw.startswith("-") else ""
    year, month, day = raw.lstrip("+-").split("T")[0].split("-")
    year = str(int(year))
    if precision >= 11:
        return f"{sign}{year}-{month}-{day}"
    if precision == 10:
        return f"{sign}{year}-{month}"
    if precision == 9:
        return f"{sign}{year}"
    if precision == 8:
        return f"{sign}{year[:-1]}0s"
    if precision == 7:
        return f"{sign}{year} (century precision)"
    return f"{sign}{year} (approximate)"
