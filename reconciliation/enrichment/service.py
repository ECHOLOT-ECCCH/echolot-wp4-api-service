"""Enrichment: suggest Wikidata statements from a Wikipedia article with an LLM."""

from reconciliation.enrichment import wikidata, wikipedia
from reconciliation.enrichment.llm import LLMProvider
from reconciliation.enrichment.models import (
    CandidateProperty,
    EnrichmentRequest,
    EnrichmentResult,
    LabeledId,
    LLMSuggestion,
    LLMSuggestionList,
    Suggestion,
)

# Identifiers in other databases: prose articles do not carry them reliably.
SKIPPED_DATATYPES = {"external-id"}

STATUS_ORDER = {"new": 0, "conflict": 1, "confirms": 2}

Labels = dict[str, tuple[str | None, str | None]]

SYSTEM_PROMPT = """You help curate Wikidata. You receive (1) a Wikidata item: its label, description, \
aliases, current statements and the list of properties Wikidata expects for items of its type, \
and (2) the plain text of a Wikipedia article about the same subject. Assume they describe the \
same subject.

Propose values from the article for Wikidata properties.

Rules:
- Use only information stated in the article. Never use outside knowledge.
- Start with candidate properties that are missing. Then, for properties that already have \
values, report whether the article confirms or contradicts them, and add further values for \
properties that can hold several (occupation, award received, child, ...).
- When a candidate list is given, only propose properties from it. Otherwise propose any Wikidata \
property you are certain exists, referenced by its P-id.
- status: "new" = the property has no value yet, or this is an additional value not yet present; \
"confirms" = matches an existing value; "conflict" = contradicts an existing value.
- Value format by datatype: time -> ISO date (YYYY-MM-DD, YYYY-MM or YYYY, at the precision the \
article supports); wikibase-item -> the name exactly as written in the article; quantity -> number \
followed by the unit; monolingualtext, string, url -> as written.
- evidence: a short verbatim quote from the article that supports the value.
- confidence: 0 to 1.
- One suggestion per (property, value). Skip anything the article does not support. Do not repeat \
an existing value except to report confirms or conflict.
- Use note for ambiguity, precision caveats, or when a name could refer to several things."""


def enrich(request: EnrichmentRequest, provider: LLMProvider) -> EnrichmentResult:
    qid = wikidata.parse_qid(request.wikidata_id)
    lang, title = wikipedia.parse_url(request.wikipedia_url)

    item = wikidata.fetch_item(qid, lang)
    claims = wikidata.item_claims(item)
    class_ids = wikidata.instance_of_ids(claims)
    candidate_ids = wikidata.properties_for_type(class_ids) if class_ids else []

    labels = wikidata.resolve(
        [*claims, *candidate_ids, *class_ids, *wikidata.value_entity_ids(claims)], lang
    )

    existing = {
        pid: [wikidata.format_datavalue(dv, labels) for dv in values]
        for pid, values in claims.items()
    }
    shown_statements = {
        pid: values for pid, values in existing.items() if _datatype(labels, pid) not in SKIPPED_DATATYPES
    }
    candidates = [
        CandidateProperty(
            id=pid, label=_label(labels, pid), datatype=_datatype(labels, pid), has_value=pid in existing
        )
        for pid in candidate_ids
        if _datatype(labels, pid) not in SKIPPED_DATATYPES
    ]
    candidate_source = "properties_for_this_type" if candidates else "free"

    page_title, page_text = wikipedia.fetch_extract(lang, title)

    label = wikidata.pick_label(item, lang) or qid
    description = wikidata.pick_label(item, lang, "descriptions")
    instance_of = [LabeledId(id=cid, label=_label(labels, cid)) for cid in class_ids]

    user_prompt = build_user_prompt(
        qid=qid,
        label=label,
        description=description,
        aliases=wikidata.pick_aliases(item, lang),
        instance_of=instance_of,
        statements=shown_statements,
        candidates=candidates,
        labels=labels,
        page_title=page_title,
        lang=lang,
        page_text=page_text,
    )
    parsed, usage = provider.complete_structured(
        system=SYSTEM_PROMPT, user=user_prompt, schema=LLMSuggestionList
    )
    suggestions = finalize_suggestions(parsed.suggestions, existing, labels, lang)

    return EnrichmentResult(
        wikidata_id=qid,
        label=label,
        description=description,
        wikipedia_url=request.wikipedia_url,
        wikipedia_title=page_title,
        instance_of=instance_of,
        candidate_source=candidate_source,
        candidate_properties=candidates,
        suggestions=suggestions,
        model=provider.model,
        usage=usage,
    )


def build_user_prompt(
    *,
    qid: str,
    label: str,
    description: str | None,
    aliases: list[str],
    instance_of: list[LabeledId],
    statements: dict[str, list[str]],
    candidates: list[CandidateProperty],
    labels: Labels,
    page_title: str,
    lang: str,
    page_text: str,
) -> str:
    lines = [f"# Wikidata item {qid}", f"Label: {label}"]
    if description:
        lines.append(f"Description: {description}")
    if aliases:
        lines.append(f"Aliases: {', '.join(aliases)}")
    if instance_of:
        lines.append("Instance of: " + ", ".join(f"{c.label} ({c.id})" for c in instance_of))

    lines += ["", "## Current statements (external identifiers omitted)"]
    if statements:
        for pid, values in statements.items():
            lines.append(f"{pid} {_label(labels, pid)} [{_datatype(labels, pid) or '?'}]: " + "; ".join(values))
    else:
        lines.append("(none)")

    lines += ["", "## Candidate properties"]
    if candidates:
        lines.append("Properties Wikidata expects for items of this type.")
        lines.append("Missing:")
        lines += [_candidate_line(c) for c in candidates if not c.has_value] or ["- (none)"]
        lines.append("Already filled (check for confirmation, conflicts or additional values):")
        lines += [_candidate_line(c) for c in candidates if c.has_value] or ["- (none)"]
    else:
        lines.append("No type-specific list is available. Propose any relevant Wikidata property by its P-id.")

    lines += ["", f"# Wikipedia article: {page_title} ({lang}.wikipedia.org)", "", page_text]
    return "\n".join(lines)


def finalize_suggestions(
    raw: list[LLMSuggestion], existing: dict[str, list[str]], labels: Labels, lang: str
) -> list[Suggestion]:
    """Validate LLM output against Wikidata and attach labels, datatypes and existing values."""
    ids = [s.property_id.strip().upper() for s in raw]
    unknown = [pid for pid in ids if wikidata.PID_RE.match(pid) and pid not in labels]
    if unknown:
        labels.update(wikidata.resolve(unknown, lang))

    suggestions: list[Suggestion] = []
    seen: set[tuple[str, str]] = set()
    for pid, s in zip(ids, raw):
        datatype = _datatype(labels, pid)
        if not wikidata.PID_RE.match(pid) or datatype is None or datatype in SKIPPED_DATATYPES:
            continue  # malformed, non-existent, or an external identifier
        value = s.value.strip()
        if not value or (pid, value) in seen:
            continue
        seen.add((pid, value))
        existing_values = existing.get(pid, [])
        suggestions.append(
            Suggestion(
                property_id=pid,
                property_label=_label(labels, pid),
                datatype=datatype,
                value=value,
                status="new" if not existing_values else s.status,
                existing_values=existing_values,
                evidence=s.evidence.strip(),
                confidence=max(0.0, min(1.0, s.confidence)),
                note=s.note,
            )
        )
    suggestions.sort(key=lambda s: (STATUS_ORDER[s.status], -s.confidence))
    return suggestions


def _label(labels: Labels, eid: str) -> str:
    return labels.get(eid, (None, None))[0] or eid


def _datatype(labels: Labels, pid: str) -> str | None:
    return labels.get(pid, (None, None))[1]


def _candidate_line(c: CandidateProperty) -> str:
    return f"- {c.id} {c.label} [{c.datatype or '?'}]"
