"""POST /wikidata/statements against canned Wikidata replies.

Runs the real endpoint and the real Wikidata helpers; only wikimedia.get_json is replaced, so
batching, label lookups and redirect handling are exercised as they run against Wikidata.
"""

from urllib.error import URLError

import pytest
from fastapi.testclient import TestClient

from reconciliation.enrichment import wikidata
from reconciliation.enrichment.wikimedia import UpstreamError
from reconciliation.standalone_api import app

SERVICE_KEY = "test-service-key"
AUTH = {"X-API-Key": SERVICE_KEY}
GREGORIAN = "http://www.wikidata.org/entity/Q1985727"
JULIAN = "http://www.wikidata.org/entity/Q1985786"


def item(qid: str) -> dict:
    return {
        "type": "wikibase-entityid",
        "value": {"entity-type": "item", "numeric-id": int(qid[1:]), "id": qid},
    }


def time(value: str, precision: int, calendar: str = GREGORIAN) -> dict:
    return {
        "type": "time",
        "value": {"time": value, "precision": precision, "calendarmodel": calendar},
    }


def statement(pid: str, datavalue: dict | None, datatype: str, rank: str = "normal") -> dict:
    snak = {"snaktype": "value", "property": pid, "datatype": datatype, "datavalue": datavalue}
    if datavalue is None:
        snak = {"snaktype": "somevalue", "property": pid, "datatype": datatype}
    return {"mainsnak": snak, "rank": rank, "type": "statement"}


def labels(**by_language: str) -> dict:
    return {code: {"language": code, "value": text} for code, text in by_language.items()}


PICASSO = {
    "id": "Q5593",
    "labels": labels(en="Pablo Picasso"),
    "descriptions": labels(en="Spanish painter (1881–1973)"),
    "claims": {
        "P31": [statement("P31", item("Q5"), "wikibase-item")],
        "P569": [statement("P569", time("+1881-10-25T00:00:00Z", 11), "time")],
        "P19": [statement("P19", item("Q8851"), "wikibase-item")],
        "P106": [
            statement("P106", item("Q1028181"), "wikibase-item"),
            statement("P106", item("Q1281618"), "wikibase-item", rank="preferred"),
            statement("P106", item("Q33231"), "wikibase-item", rank="deprecated"),
        ],
        "P570": [statement("P570", None, "time")],
        "P214": [statement("P214", {"type": "string", "value": "15873"}, "external-id")],
        "P2048": [
            statement(
                "P2048",
                {
                    "type": "quantity",
                    "value": {"amount": "+1.63", "unit": "http://www.wikidata.org/entity/Q11573"},
                },
                "quantity",
            )
        ],
        "P1559": [
            statement(
                "P1559",
                {"type": "monolingualtext", "value": {"text": "Pablo Ruiz Picasso", "language": "es"}},
                "monolingualtext",
            )
        ],
    },
}

CHILLIDA = {
    "id": "Q156736",
    "labels": labels(mul="Eduardo Chillida"),
    "descriptions": labels(en="Spanish sculptor"),
    "claims": {
        "P31": [statement("P31", item("Q5"), "wikibase-item")],
        "P569": [statement("P569", time("+1924-00-00T00:00:00Z", 9), "time")],
        "P19": [statement("P19", item("Q10313"), "wikibase-item")],
    },
}

PROPERTIES = {
    "P31": ("instance of", "wikibase-item"),
    "P569": ("date of birth", "time"),
    "P570": ("date of death", "time"),
    "P19": ("place of birth", "wikibase-item"),
    "P27": ("country of citizenship", "wikibase-item"),
    "P106": ("occupation", "wikibase-item"),
    "P214": ("VIAF ID", "external-id"),
    "P2048": ("height", "quantity"),
    "P1559": ("name in native language", "monolingualtext"),
}

VALUE_ITEMS = {
    "Q5": labels(en="human"),
    "Q8851": labels(en="Málaga", eu="Malaga"),
    "Q10313": labels(en="San Sebastián", eu="Donostia"),
    "Q1028181": labels(en="painter", eu="margolari"),
    "Q1281618": labels(en="sculptor", eu="eskultore"),
    "Q11573": labels(en="metre"),
}


class FakeWikidata:
    """Answers wbgetentities like Wikidata does, and records what was asked."""

    def __init__(self) -> None:
        self.items = {"Q5593": PICASSO, "Q156736": CHILLIDA}
        self.calls: list[list[str]] = []
        self.failure: Exception | None = None

    def __call__(self, base_url: str, params: dict) -> dict:
        assert params["action"] == "wbgetentities"
        if self.failure is not None:
            raise self.failure
        ids = params["ids"].split("|")
        self.calls.append(ids)
        entities = {}
        for i in ids:
            if i == "Q999":  # redirected to Picasso
                entities["Q5593"] = {**PICASSO, "redirects": {"from": "Q999", "to": "Q5593"}}
            elif i in self.items:
                entities[i] = self.items[i]
            elif i in PROPERTIES:
                label, datatype = PROPERTIES[i]
                entities[i] = {"id": i, "labels": labels(en=label), "datatype": datatype}
            elif i in VALUE_ITEMS:
                entities[i] = {"id": i, "labels": VALUE_ITEMS[i]}
            else:
                entities[i] = {"id": i, "missing": ""}
        return {"entities": entities}


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeWikidata:
    fake = FakeWikidata()
    monkeypatch.setenv("API_KEY", SERVICE_KEY)
    monkeypatch.setattr(wikidata, "get_json", fake)
    monkeypatch.setattr(wikidata, "_label_cache", {})
    return fake


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def post(client: TestClient, body: dict, headers: dict = AUTH):
    return client.post("/wikidata/statements", json=body, headers=headers)


def by_property(item: dict) -> dict[str, dict]:
    return {s["property"]: s for s in item["statements"]}


def test_without_properties_every_property_with_a_count_and_one_example(fake, client):
    response = post(client, {"ids": ["Q5593"]})

    assert response.status_code == 200
    body = response.json()
    assert body["summary"] is True and body["lang"] == "en"
    [picasso] = body["items"]
    assert picasso["found"] is True
    assert picasso["id"] == "Q5593"
    assert picasso["label"] == "Pablo Picasso"
    assert picasso["description"] == "Spanish painter (1881–1973)"
    statements = by_property(picasso)
    # 'unknown value' (P570 here) carries no value and is left out
    assert list(statements) == ["P31", "P569", "P19", "P106", "P214", "P2048", "P1559"]
    occupation = statements["P106"]
    assert occupation["label"] == "occupation"
    assert occupation["datatype"] == "wikibase-item"
    assert occupation["count"] == 2  # the deprecated one does not count
    assert occupation["values"] == [
        {"text": "sculptor", "rank": "preferred", "id": "Q1281618", "label": "sculptor"}
    ]
    assert statements["P214"]["values"] == [{"text": "15873", "rank": "normal"}]


def test_with_properties_every_value_in_the_order_asked(fake, client):
    response = post(client, {"ids": ["Q5593"], "properties": ["P106", "p569", "P27", "P106"]})

    assert response.status_code == 200
    body = response.json()
    assert body["summary"] is False
    statements = body["items"][0]["statements"]
    assert [s["property"] for s in statements] == ["P106", "P569", "P27"]
    occupation, birth, citizenship = statements
    assert [v["id"] for v in occupation["values"]] == ["Q1281618", "Q1028181"]
    assert [v["rank"] for v in occupation["values"]] == ["preferred", "normal"]
    assert birth["values"] == [
        {
            "text": "1881-10-25",
            "rank": "normal",
            "time": "+1881-10-25T00:00:00Z",
            "precision": 11,
            "calendar": "gregorian",
        }
    ]
    # absent from the item: listed, with its label, so a script can tell "none" from "not asked"
    assert citizenship == {
        "property": "P27",
        "label": "country of citizenship",
        "datatype": "wikibase-item",
        "count": 0,
        "values": [],
    }


def test_typed_values_quantity_and_monolingual_text(fake, client):
    response = post(client, {"ids": ["Q5593"], "properties": ["P2048", "P1559"]})

    height, native_name = response.json()["items"][0]["statements"]
    assert height["values"] == [
        {"text": "1.63 metre", "rank": "normal", "amount": "1.63", "unit": "Q11573", "unit_label": "metre"}
    ]
    assert native_name["values"] == [
        {"text": "Pablo Ruiz Picasso", "rank": "normal", "language": "es"}
    ]


def test_dates_at_their_precision_and_labels_in_the_language_asked(fake, client):
    response = post(client, {"ids": ["Q156736"], "properties": ["P569", "P19"], "lang": "eu"})

    [chillida] = response.json()["items"]
    # no Basque or English label: the label shared by all languages
    assert chillida["label"] == "Eduardo Chillida"
    birth, place = chillida["statements"]
    assert birth["values"][0]["text"] == "1924"
    assert birth["values"][0]["precision"] == 9
    assert place["values"] == [{"text": "Donostia", "rank": "normal", "id": "Q10313", "label": "Donostia"}]


def test_julian_calendar_is_reported(fake, client):
    birth = statement("P569", time("+1452-04-15T00:00:00Z", 11, JULIAN), "time")
    fake.items["Q762"] = {"id": "Q762", "labels": labels(en="Leonardo da Vinci"), "claims": {"P569": [birth]}}

    response = post(client, {"ids": ["Q762"], "properties": ["P569"]})

    assert response.json()["items"][0]["statements"][0]["values"][0]["calendar"] == "julian"


def test_several_items_in_order_with_redirects_urls_and_unknown_ids(fake, client):
    ids = ["http://www.wikidata.org/entity/Q999", "Q404", "Q156736"]

    response = post(client, {"ids": ids, "properties": ["P569"]})

    assert response.status_code == 200
    redirected, unknown, chillida = response.json()["items"]
    assert redirected["requested"] == "http://www.wikidata.org/entity/Q999"
    assert redirected["found"] is True
    assert redirected["id"] == "Q5593"  # the item the redirect leads to
    assert redirected["label"] == "Pablo Picasso"
    assert unknown == {"requested": "Q404", "found": False, "statements": []}
    assert chillida["id"] == "Q156736"
    # the three items in one Wikidata call
    assert fake.calls[0] == ["Q999", "Q404", "Q156736"]


@pytest.mark.parametrize(
    "body",
    [
        {"ids": []},
        {"ids": [f"Q{i}" for i in range(1, 52)]},
        {"ids": ["Picasso"]},
        {"ids": ["Q5593"], "properties": ["date of birth"]},
        {"ids": ["Q5593"], "lang": "English"},
    ],
    ids=["no ids", "51 ids", "not an id", "not a property id", "bad language"],
)
def test_bad_input_is_422(fake, client, body):
    response = post(client, body)

    assert response.status_code == 422
    assert fake.calls == []


def test_needs_the_service_key(fake, client):
    assert post(client, {"ids": ["Q5593"]}, headers={}).status_code == 401
    assert post(client, {"ids": ["Q5593"]}, headers={"X-API-Key": "wrong"}).status_code == 401


@pytest.mark.parametrize(
    "failure",
    [UpstreamError("maxlag", code="maxlag"), TimeoutError(), URLError("connection refused")],
    ids=["Wikidata error", "timeout", "unreachable"],
)
def test_wikidata_failure_is_502(fake, client, failure):
    fake.failure = failure

    response = post(client, {"ids": ["Q5593"]})

    assert response.status_code == 502
    assert response.json()["detail"] == "Wikidata request failed"
