"""POST /reconcile?algorithm=llm_based through HiTZ's hosted service (HITZ_API_KEY set).

Runs the real endpoint and adapter; only requests.post is replaced, so nothing goes over the
network. The replies have the fields HiTZ answered with on 2026-11-12.
"""

import pytest
import requests
from fastapi.testclient import TestClient

from reconciliation.algorithms import llm_based
from reconciliation.standalone_api import app

SERVICE_KEY = "test-service-key"
AUTH = {"X-API-Key": SERVICE_KEY}
ENTITY = {
    "uri": "u1",
    "name": "Eduardo Chillida",
    "entity_type": "person",
    "information": {"gender": "male", "publication": ["a", "b", "c", "d", "e", "f"]},
    "context": "Basque sculptor",
}
CHILLIDA = {
    "querystring": "Eduardo Chillida",
    "desambiguation_result": "Q156736",
    "correct_qid": "Q156736",
    "reasoning": "Same name, birth year, nationality and occupation.",
    "position": 0,
    "full_results": [
        {"QID": "Q156736", "snippet": "Spanish Basque sculptor (1924–2002)",
         "description": {"name": "Eduardo Chillida", "alias": ["Txillida"], "info": {}}},
        {"QID": "Q59286162", "snippet": "aquatint by Eduardo Chillida",
         "description": {"name": "Aquatint, ed 42/50", "alias": [], "info": {}}},
    ],
}
NOBODY = {"querystring": "Nobody", "desambiguation_result": "None", "correct_qid": "None",
          "reasoning": "", "position": -1, "full_results": []}


class FakeResponse:
    def __init__(self, status_code: int, body: object = None) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> object:
        if self._body is None:
            raise ValueError("not JSON")
        return self._body


class FakeHitz:
    """Stands in for requests.post: records what is sent, answers with `reply` or raises."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.reply: tuple[int, object] = (200, CHILLIDA)
        self.failure: Exception | None = None

    def __call__(self, url: str, json: dict, headers: dict, timeout: float) -> FakeResponse:
        self.calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        if self.failure is not None:
            raise self.failure
        return FakeResponse(*self.reply)


@pytest.fixture
def hitz(monkeypatch: pytest.MonkeyPatch) -> FakeHitz:
    for name in ("HITZ_API_KEY", "HITZ_API_URL", *llm_based.REQUIRED_ENV):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("API_KEY", SERVICE_KEY)
    monkeypatch.setenv("HITZ_API_KEY", "test-hitz-key")
    fake = FakeHitz()
    monkeypatch.setattr(llm_based.requests, "post", fake)
    return fake


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def reconcile(client: TestClient, entity: dict = ENTITY):
    return client.post("/reconcile", params={"algorithm": "llm_based"}, json=entity, headers=AUTH)


def test_a_match_from_hitz(hitz: FakeHitz, client: TestClient) -> None:
    response = reconcile(client)

    assert response.status_code == 200
    assert response.json()["matches"] == [
        {
            "url": "http://www.wikidata.org/entity/Q156736",
            "wikidata_item_id": "Q156736",
            "title": "Eduardo Chillida",
            "score": 1.0,
        }
    ]
    [call] = hitz.calls
    assert call["url"] == "https://zerbitzuak.hitz.eus/api/reconciliation"
    assert call["headers"] == {"apikey": "test-hitz-key"}
    # one text, with at most five publications, as the draft's batch driver keeps them
    assert call["json"] == {
        "name": "Eduardo Chillida",
        "info": "gender: male; publication: a, b, c, d, e",
        "context": "Basque sculptor",
    }


def test_none_is_no_match(hitz: FakeHitz, client: TestClient) -> None:
    hitz.reply = (200, NOBODY)

    response = reconcile(client)

    assert response.status_code == 200
    assert response.json()["matches"] == []


def test_the_service_address_can_be_set(
    hitz: FakeHitz, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HITZ_API_URL", "http://hitz.test/api/")

    reconcile(client)

    assert hitz.calls[0]["url"] == "http://hitz.test/api/reconciliation"


def test_a_refused_key_is_not_configured_not_a_retry(hitz: FakeHitz, client: TestClient) -> None:
    hitz.reply = (401, {"message": "Authorization Failed"})

    response = reconcile(client)

    assert response.status_code == 503
    assert response.json()["detail"] == (
        "Algorithm llm_based is not configured: HiTZ refused HITZ_API_KEY"
    )


@pytest.mark.parametrize(
    ("reply", "failure"),
    [
        ((500, {"detail": "boom"}), None),
        ((200, None), None),  # not JSON
        ((200, {"message": "something else"}), None),
        ((200, CHILLIDA), requests.Timeout()),
        ((200, CHILLIDA), requests.ConnectionError()),
    ],
    ids=["error status", "not JSON", "unexpected JSON", "timeout", "unreachable"],
)
def test_a_failing_service_is_502(
    hitz: FakeHitz, client: TestClient, reply: tuple[int, object], failure: Exception | None
) -> None:
    hitz.reply = reply
    hitz.failure = failure

    response = reconcile(client)

    assert response.status_code == 502
    assert response.json()["detail"] == "Reconciliation service request failed"


def test_without_hitz_or_a_local_model_it_is_not_configured(
    hitz: FakeHitz, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("HITZ_API_KEY")

    response = reconcile(client)

    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail.startswith("Algorithm llm_based is not configured: HITZ_API_KEY, or OPENAI_BASE_URL")
    assert hitz.calls == []
