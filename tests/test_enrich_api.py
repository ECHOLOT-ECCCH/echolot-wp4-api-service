"""POST /enrich: caller-supplied model and LLM key.

Runs the real endpoint, service and Anthropic SDK. Only the network is replaced: Wikimedia
responses are canned and the SDK's HTTP transport is a mock, so what reaches Anthropic can be
inspected directly.
"""

import json
import logging

import anthropic
import httpx2
import pytest
from fastapi.testclient import TestClient

from reconciliation.enrichment import wikidata, wikipedia
from reconciliation.standalone_api import app

SERVICE_KEY = "test-service-key"
SERVER_LLM_KEY = "sk-test-server-key"
CALLER_LLM_KEY = "sk-test-caller-key-must-not-leak"
SERVER_MODEL = "claude-opus-5"
BODY = {
    "wikidata_id": "Q937",
    "wikipedia_url": "https://en.wikipedia.org/wiki/Albert_Einstein",
}
AUTH = {"X-API-Key": SERVICE_KEY}
CALLER_AUTH = {**AUTH, "X-LLM-API-Key": CALLER_LLM_KEY}


def fake_get_json(base_url: str, params: dict) -> dict:
    if params.get("action") == "wbgetentities":
        ids = params["ids"].split("|")
        return {"entities": {i: {"id": i, "labels": {"en": {"value": i}}, "claims": {}} for i in ids}}
    return {"query": {"pages": [{"title": "Albert Einstein", "extract": "Article text."}]}}


class FakeAnthropic:
    """Records requests to Anthropic and answers them, by default successfully."""

    def __init__(self) -> None:
        self.requests: list[httpx2.Request] = []
        self.status = 200
        self.answering_model: str | None = None

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if self.status != 200:
            error = {"type": "error", "error": {"type": "some_error", "message": "upstream says no"}}
            return httpx2.Response(self.status, json=error)
        return httpx2.Response(
            200,
            json={
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "model": self.answering_model or self.sent_body()["model"],
                "content": [{"type": "text", "text": '{"suggestions": []}'}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )

    def sent_body(self) -> dict:
        return json.loads(self.requests[-1].content)

    def sent_key(self) -> str:
        return self.requests[-1].headers["x-api-key"]


@pytest.fixture
def upstream(monkeypatch: pytest.MonkeyPatch) -> FakeAnthropic:
    fake = FakeAnthropic()
    real_client = anthropic.Anthropic

    def client_with_mock_transport(**kwargs):
        http_client = anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(fake))
        return real_client(**kwargs, http_client=http_client, max_retries=0)

    monkeypatch.setattr(anthropic, "Anthropic", client_with_mock_transport)
    monkeypatch.setattr(wikidata, "get_json", fake_get_json)
    monkeypatch.setattr(wikipedia, "get_json", fake_get_json)
    monkeypatch.setenv("API_KEY", SERVICE_KEY)
    monkeypatch.setenv("ANTHROPIC_API_KEY", SERVER_LLM_KEY)
    monkeypatch.setenv("LLM_MODEL", SERVER_MODEL)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("LLM_REFUSAL_FALLBACK", raising=False)
    return fake


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_caller_key_and_model_reach_anthropic(upstream: FakeAnthropic, client: TestClient) -> None:
    response = client.post(
        "/enrich",
        json={**BODY, "provider": "anthropic", "model": "claude-sonnet-5-5"},
        headers=CALLER_AUTH,
    )

    assert response.status_code == 200
    assert upstream.sent_key() == CALLER_LLM_KEY
    assert upstream.sent_body()["model"] == "claude-sonnet-5-5"
    assert response.json()["model"] == "claude-sonnet-5-5"


def test_caller_key_without_model_uses_server_model(upstream: FakeAnthropic, client: TestClient) -> None:
    response = client.post("/enrich", json=BODY, headers=CALLER_AUTH)

    assert response.status_code == 200
    assert upstream.sent_key() == CALLER_LLM_KEY
    assert upstream.sent_body()["model"] == SERVER_MODEL


def test_existing_request_shape_uses_server_key_and_model(upstream: FakeAnthropic, client: TestClient) -> None:
    response = client.post("/enrich", json=BODY, headers=AUTH)

    assert response.status_code == 200
    assert upstream.sent_key() == SERVER_LLM_KEY
    assert upstream.sent_body()["model"] == SERVER_MODEL


def test_service_key_is_still_required(upstream: FakeAnthropic, client: TestClient) -> None:
    response = client.post("/enrich", json=BODY, headers={"X-LLM-API-Key": CALLER_LLM_KEY})

    assert response.status_code == 401
    assert upstream.requests == []


def test_model_without_caller_key_is_rejected(upstream: FakeAnthropic, client: TestClient) -> None:
    response = client.post("/enrich", json={**BODY, "model": "claude-fable-5-1"}, headers=AUTH)

    assert response.status_code == 422
    assert "X-LLM-API-Key" in response.json()["detail"]
    assert upstream.requests == []


def test_unknown_provider_is_rejected(upstream: FakeAnthropic, client: TestClient) -> None:
    response = client.post("/enrich", json={**BODY, "provider": "other"}, headers=CALLER_AUTH)

    assert response.status_code == 422
    assert upstream.requests == []


def test_non_ascii_key_is_rejected(upstream: FakeAnthropic, client: TestClient) -> None:
    headers = {**AUTH, "X-LLM-API-Key": "sk-clé".encode("latin-1")}

    response = client.post("/enrich", json=BODY, headers=headers)

    assert response.status_code == 401
    assert upstream.requests == []


def test_no_key_anywhere_names_the_header(
    upstream: FakeAnthropic, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY")

    response = client.post("/enrich", json=BODY, headers=AUTH)

    assert response.status_code == 503
    assert "X-LLM-API-Key" in response.json()["detail"]


@pytest.mark.parametrize(
    ("upstream_status", "expected"),
    [(400, 422), (401, 401), (403, 401), (404, 422), (429, 429), (500, 502)],
)
def test_upstream_errors_with_caller_key(
    upstream: FakeAnthropic, client: TestClient, upstream_status: int, expected: int
) -> None:
    upstream.status = upstream_status

    response = client.post("/enrich", json=BODY, headers=CALLER_AUTH)

    assert response.status_code == expected
    assert "upstream says no" in response.json()["detail"]


@pytest.mark.parametrize(
    ("upstream_status", "expected"),
    [(400, 502), (401, 502), (403, 502), (404, 502), (429, 429), (500, 502)],
)
def test_upstream_errors_with_server_key(
    upstream: FakeAnthropic, client: TestClient, upstream_status: int, expected: int
) -> None:
    upstream.status = upstream_status

    response = client.post("/enrich", json=BODY, headers=AUTH)

    assert response.status_code == expected


@pytest.mark.parametrize(
    "model", ["claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"]
)
def test_refusal_fallback_sent_for_supported_models(
    upstream: FakeAnthropic, client: TestClient, model: str
) -> None:
    client.post("/enrich", json={**BODY, "model": model}, headers=CALLER_AUTH)

    assert upstream.sent_body()["fallbacks"] == "default"


@pytest.mark.parametrize("model", ["claude-haiku-5-5", "claude-opus-4-8", "claude-future-9"])
def test_refusal_fallback_not_sent_for_other_models(
    upstream: FakeAnthropic, client: TestClient, model: str
) -> None:
    response = client.post("/enrich", json={**BODY, "model": model}, headers=CALLER_AUTH)

    assert response.status_code == 200
    assert "fallbacks" not in upstream.sent_body()


def test_response_reports_the_model_that_answered(upstream: FakeAnthropic, client: TestClient) -> None:
    upstream.answering_model = "claude-opus-4-8"

    response = client.post("/enrich", json={**BODY, "model": "claude-opus-5-5"}, headers=CALLER_AUTH)

    assert response.json()["model"] == "claude-opus-4-8"


@pytest.mark.parametrize("upstream_status", [200, 400, 401, 403, 404, 429, 500])
def test_caller_key_never_in_response_or_logs(
    upstream: FakeAnthropic,
    client: TestClient,
    caplog: pytest.LogCaptureFixture,
    upstream_status: int,
) -> None:
    upstream.status = upstream_status

    with caplog.at_level(logging.DEBUG):
        response = client.post("/enrich", json=BODY, headers=CALLER_AUTH)

    assert CALLER_LLM_KEY not in response.text
    assert CALLER_LLM_KEY not in caplog.text


def test_caller_key_not_echoed_by_validation_errors(upstream: FakeAnthropic, client: TestClient) -> None:
    response = client.post("/enrich", json={"wikidata_id": "Q937"}, headers=CALLER_AUTH)

    assert response.status_code == 422
    assert CALLER_LLM_KEY not in response.text


def test_both_keys_appear_in_api_docs() -> None:
    schemes = app.openapi()["components"]["securitySchemes"]

    assert {scheme["name"] for scheme in schemes.values()} == {"X-API-Key", "X-LLM-API-Key"}
