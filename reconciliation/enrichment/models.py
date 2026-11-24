from typing import Literal

from pydantic import BaseModel, Field

SuggestionStatus = Literal["new", "confirms", "conflict"]


class EnrichmentRequest(BaseModel):
    wikidata_id: str = Field(
        description="Item id such as 'Q937' or an entity URL such as http://www.wikidata.org/entity/Q937"
    )
    wikipedia_url: str = Field(description="Article URL such as https://en.wikipedia.org/wiki/Albert_Einstein")


class LabeledId(BaseModel):
    id: str
    label: str


class CandidateProperty(BaseModel):
    id: str
    label: str
    datatype: str | None = None
    has_value: bool


class Suggestion(BaseModel):
    property_id: str
    property_label: str
    datatype: str | None = None
    value: str
    status: SuggestionStatus
    existing_values: list[str] = []
    evidence: str
    confidence: float
    note: str | None = None


class EnrichmentResult(BaseModel):
    wikidata_id: str
    label: str
    description: str | None = None
    wikipedia_url: str
    wikipedia_title: str
    instance_of: list[LabeledId]
    candidate_source: Literal["properties_for_this_type", "free"]
    candidate_properties: list[CandidateProperty]
    suggestions: list[Suggestion]
    model: str
    usage: dict[str, int] = {}


# --- What the LLM fills in. The service validates it and adds labels/datatypes/existing values.


class LLMSuggestion(BaseModel):
    property_id: str = Field(description="Wikidata property id, e.g. 'P569'")
    value: str = Field(description="Proposed value, formatted according to the property's datatype")
    status: SuggestionStatus = Field(
        description="'new' if the property is empty or this is an additional value; "
        "'confirms' if it matches an existing value; 'conflict' if it contradicts one"
    )
    evidence: str = Field(description="Short verbatim quote from the article that supports the value")
    confidence: float = Field(description="Between 0 and 1")
    note: str | None = Field(
        default=None, description="Ambiguity, precision caveats or anything a reviewer should know"
    )


class LLMSuggestionList(BaseModel):
    suggestions: list[LLMSuggestion]


# --- POST /wikidata/statements: what Wikidata holds for items, for scripts that enrich a dataset.


class StatementsRequest(BaseModel):
    ids: list[str] = Field(
        min_length=1,
        max_length=50,
        description="Item ids such as 'Q937' or entity URLs such as http://www.wikidata.org/entity/Q937",
    )
    properties: list[str] | None = Field(
        default=None,
        description="Property ids such as 'P569'. Omitted or empty: every property of each item, "
        "with a count and one example value",
    )
    lang: str = Field(
        default="en",
        pattern=r"^[a-z]{2,3}(-[a-z0-9]+)*$",
        description="Language of labels; falls back to English, then to labels shared by all languages",
    )


class StatementValue(BaseModel):
    text: str = Field(description="The value as short readable text")
    rank: Literal["preferred", "normal"]
    id: str | None = Field(default=None, description="The QID (or other entity id) of an item value")
    label: str | None = Field(default=None, description="The label of an item value")
    time: str | None = Field(default=None, description="Wikidata's time string, e.g. '+1881-10-25T00:00:00Z'")
    precision: int | None = Field(
        default=None, description="Of a time: 11 day, 10 month, 9 year, 8 decade, 7 century"
    )
    calendar: Literal["gregorian", "julian"] | None = None
    amount: str | None = Field(default=None, description="Of a quantity, without a leading '+'")
    unit: str | None = Field(default=None, description="The QID of a quantity's unit")
    unit_label: str | None = None
    language: str | None = Field(default=None, description="Of a monolingual text")
    latitude: float | None = None
    longitude: float | None = None


class PropertyStatements(BaseModel):
    property: str
    label: str | None = None
    datatype: str | None = None
    count: int = Field(description="Number of non-deprecated values")
    values: list[StatementValue] = Field(
        description="Preferred values first. All of them when properties were asked for, "
        "otherwise the first one only"
    )


class ItemStatements(BaseModel):
    requested: str = Field(description="The id or URL as sent")
    found: bool
    id: str | None = Field(default=None, description="The item's QID, after following a redirect")
    label: str | None = None
    description: str | None = None
    statements: list[PropertyStatements] = []


class StatementsResult(BaseModel):
    lang: str
    summary: bool = Field(description="True when no properties were asked for")
    items: list[ItemStatements]
