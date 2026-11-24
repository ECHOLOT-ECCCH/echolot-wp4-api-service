"""API_DOCS turns the documentation pages off; they answer without a key."""

import importlib

import pytest
from fastapi.testclient import TestClient

import reconciliation.standalone_api as api


@pytest.fixture
def reload_api(monkeypatch: pytest.MonkeyPatch):
    def load(value: str | None):
        if value is None:
            monkeypatch.delenv("API_DOCS", raising=False)
        else:
            monkeypatch.setenv("API_DOCS", value)
        return TestClient(importlib.reload(api).app)

    yield load
    monkeypatch.delenv("API_DOCS", raising=False)
    importlib.reload(api)


@pytest.mark.parametrize(("value", "status"), [(None, 200), ("true", 200), ("false", 404)])
def test_the_docs_pages(reload_api, value: str | None, status: int) -> None:
    client = reload_api(value)
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == status, path
    assert client.get("/health").json() == {"status": "ok"}
