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
