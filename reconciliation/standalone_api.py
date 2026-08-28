import json
import logging
import os
import secrets
from urllib.error import URLError

from fastapi import FastAPI, HTTPException, Security, status
from fastapi.security import APIKeyHeader
from pydantic import BaseModel

from reconciliation.algorithms.wikifier import reconcile
from reconciliation.models import Entity, ReconciliationMatch

logger = logging.getLogger(__name__)
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

app = FastAPI(title="ECHOLOT Standalone Reconciliation API", version="0.1.0")


class EntityResult(BaseModel):
    uri: str
    matches: list[ReconciliationMatch]


def require_api_key(provided_key: str | None = Security(api_key_header)) -> None:
    expected_key = os.getenv("API_KEY")
    if not expected_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="API key is not configured",
        )
    if provided_key is None or not secrets.compare_digest(provided_key, expected_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )


@app.get("/health", include_in_schema=False)
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post(
    "/reconcile",
    response_model=EntityResult,
    dependencies=[Security(require_api_key)],
)
def reconcile_entity(entity: Entity) -> EntityResult:
    if not os.getenv("WIKIFIER_API_KEY"):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Wikifier API key is not configured",
        )

    try:
        result = reconcile(entity)
    except (TimeoutError, URLError, json.JSONDecodeError, KeyError) as exc:
        logger.exception("Wikifier request failed for %s", entity.uri)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Reconciliation service request failed",
        ) from exc

    return EntityResult(uri=entity.uri, matches=result.matches)
