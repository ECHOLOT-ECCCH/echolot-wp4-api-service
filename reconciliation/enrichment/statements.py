"""What Wikidata holds for items, as values a script can use (POST /wikidata/statements).

Two modes. Without properties, every property an item has, with its label, datatype, the
number of values and one example: enough to explore a sample and decide what to extract. With
properties, every value of those properties, and an absent one with count 0: what a script
extracts per record. Deprecated statements and statements without a value ('unknown value',
'no value') are left out.
"""

from reconciliation.enrichment import wikidata
from reconciliation.enrichment.models import (
    ItemStatements,
    PropertyStatements,
    StatementsRequest,
    StatementsResult,
    StatementValue,
)

CALENDARS = {
    "http://www.wikidata.org/entity/Q1985727": "gregorian",
    "http://www.wikidata.org/entity/Q1985786": "julian",
}

Labels = dict[str, tuple[str | None, str | None]]


def statements(request: StatementsRequest) -> StatementsResult:
    """Raises ValueError for a malformed item or property id."""
    lang = request.lang
    requested = [(raw, wikidata.parse_qid(raw)) for raw in request.ids]
    properties = _property_ids(request.properties or [])
    summary = not properties

    entities = wikidata.get_entities(
        [qid for _, qid in requested], "labels|descriptions|claims", wikidata.label_languages(lang)
    )
    values = {qid: item_values(entity) for qid, entity in entities.items()}

    shown: dict[str, dict[str, list[dict]]] = {}
    for qid, claims in values.items():
        if summary:
            shown[qid] = {pid: vals[:1] for pid, vals in claims.items()}
        else:
            shown[qid] = {pid: claims.get(pid, []) for pid in properties}

    pids = properties or [pid for claims in values.values() for pid in claims]
    labels = wikidata.resolve(pids + _referenced_ids(shown), lang)

    items = []
    for raw, qid in requested:
        entity = entities.get(qid)
        if entity is None:
            items.append(ItemStatements(requested=raw, found=False))
            continue
        items.append(
            ItemStatements(
                requested=raw,
                found=True,
                id=entity.get("id", qid),
                label=wikidata.pick_label(entity, lang),
                description=wikidata.pick_label(entity, lang, "descriptions"),
                statements=[
                    PropertyStatements(
                        property=pid,
                        label=labels.get(pid, (None, None))[0],
                        datatype=labels.get(pid, (None, None))[1]
                        or next((v.get("datatype") for v in vals), None),
                        count=len(values[qid].get(pid, [])),
                        values=[to_value(v, labels) for v in vals],
                    )
                    for pid, vals in shown[qid].items()
                ],
            )
        )
    return StatementsResult(lang=lang, summary=summary, items=items)


def item_values(entity: dict) -> dict[str, list[dict]]:
    """Property id -> the values of its non-deprecated statements, preferred ones first.

    Like wikidata.item_claims, but each value also carries its statement's rank.
    """
    claims: dict[str, list[dict]] = {}
    for pid, statements in (entity.get("claims") or {}).items():
        values = []
        for statement in statements:
            rank = statement.get("rank", "normal")
            if rank == "deprecated":
                continue
            snak = statement.get("mainsnak") or {}
            if snak.get("snaktype") != "value" or "datavalue" not in snak:
                continue
            values.append({**snak["datavalue"], "datatype": snak.get("datatype"), "rank": rank})
        if values:
            claims[pid] = sorted(values, key=lambda v: v["rank"] != "preferred")
    return claims


def to_value(datavalue: dict, labels: Labels) -> StatementValue:
    kind = datavalue.get("type")
    value = datavalue.get("value")
    rank = datavalue.get("rank", "normal")
    text = wikidata.format_datavalue(datavalue, labels)
    if kind == "wikibase-entityid":
        eid = wikidata.entity_id(datavalue)
        label = labels.get(eid, (None, None))[0]
        return StatementValue(text=label or eid or text, rank=rank, id=eid, label=label)
    if kind == "time":
        return StatementValue(
            text=text,
            rank=rank,
            time=value.get("time"),
            precision=value.get("precision"),
            calendar=CALENDARS.get(value.get("calendarmodel", "")),
        )
    if kind == "quantity":
        unit = _unit_id(value)
        return StatementValue(
            text=text,
            rank=rank,
            amount=value["amount"].lstrip("+"),
            unit=unit,
            unit_label=labels.get(unit, (None, None))[0] if unit else None,
        )
    if kind == "monolingualtext":
        return StatementValue(text=value["text"], rank=rank, language=value["language"])
    if kind == "globecoordinate":
        return StatementValue(
            text=text, rank=rank, latitude=value["latitude"], longitude=value["longitude"]
        )
    return StatementValue(text=text, rank=rank)


def _property_ids(properties: list[str]) -> list[str]:
    ids = []
    for raw in properties:
        pid = raw.strip().upper()
        if not wikidata.PID_RE.match(pid):
            raise ValueError(f"Not a Wikidata property id: {raw!r}")
        if pid not in ids:
            ids.append(pid)
    return ids


def _unit_id(quantity: dict) -> str | None:
    unit = quantity.get("unit") or "1"
    return None if unit == "1" else unit.rsplit("/", 1)[-1]


def _referenced_ids(shown: dict[str, dict[str, list[dict]]]) -> list[str]:
    """The entity ids whose labels the shown values need: item values and quantity units."""
    ids = []
    for claims in shown.values():
        for vals in claims.values():
            for dv in vals:
                if eid := wikidata.entity_id(dv):
                    ids.append(eid)
                elif dv.get("type") == "quantity" and (unit := _unit_id(dv["value"])):
                    ids.append(unit)
    return ids
