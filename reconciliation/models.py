from pydantic import BaseModel


class Entity(BaseModel):
    uri: str
    name: str
    entity_type: str
    identifiers: dict[str, str] = {}
    information: dict[str, str] = {}
    context: str = ""


class ReconciliationMatch(BaseModel):
    url: str
    wikidata_item_id: str | None = None
    title: str
    score: float


class ReconciliationResult(BaseModel):
    matches: list[ReconciliationMatch]
