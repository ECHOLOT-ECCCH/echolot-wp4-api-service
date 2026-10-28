"""Shared HTTP helper for the Wikidata and Wikipedia APIs."""

import json
import os
import urllib.parse
import urllib.request

# Wikimedia asks API clients to identify themselves; override with WIKIMEDIA_USER_AGENT.
DEFAULT_USER_AGENT = (
    "echolot-wp4-api-service/0.1 "
    "(https://github.com/ECHOLOT-ECCCH/echolot-wp4-api-service)"
)
TIMEOUT_SECONDS = 30


class NotFound(Exception):
    """The requested Wikidata item or Wikipedia page does not exist."""


class UpstreamError(Exception):
    """A Wikimedia API answered with an error payload."""

    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.code = code


def get_json(base_url: str, params: dict) -> dict:
    query = urllib.parse.urlencode({**params, "format": "json"})
    req = urllib.request.Request(
        f"{base_url}?{query}",
        headers={"User-Agent": os.getenv("WIKIMEDIA_USER_AGENT", DEFAULT_USER_AGENT)},
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as f:
        data = json.loads(f.read().decode("utf8"))
    if "error" in data:
        error = data["error"]
        raise UpstreamError(f"{base_url}: {error.get('info') or error}", code=error.get("code"))
    return data
