import json
import logging
import os
import secrets
from urllib.error import URLError

from fastapi import FastAPI, HTTPException, Security, status
from fastapi.security import APIKeyHeader
from pydantic import BaseModel

from reconciliation.algorithms.wikifier import reconcile
from reconciliation.enrichment.llm import (
    LLMError,
    LLMKeyRejected,
    LLMNotConfigured,
    LLMRateLimited,
    LLMRequestRejected,
    get_provider,
)
from reconciliation.enrichment.models import EnrichmentRequest, EnrichmentResult
from reconciliation.enrichment.service import enrich
from reconciliation.enrichment.wikimedia import NotFound, UpstreamError
from reconciliation.models import Entity, ReconciliationMatch

logger = logging.getLogger(__name__)
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
# A header rather than a body field: FastAPI echoes the request body in its 422 responses.
# The scheme name is explicit because two schemes with the default name collide in the API docs.
llm_key_header = APIKeyHeader(name="X-LLM-API-Key", scheme_name="LLMAPIKeyHeader", auto_error=False)

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


@app.post(
    "/enrich",
    response_model=EnrichmentResult,
    dependencies=[Security(require_api_key)],
)
def enrich_entity(
    request: EnrichmentRequest,
    llm_key: str | None = Security(llm_key_header),
) -> EnrichmentResult:
    if llm_key and not llm_key.isascii():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid LLM API key",
        )
    if request.model and not llm_key:
        # Otherwise anyone with the shared service key could pick pricier models on the server's bill.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Choosing a model requires your own key in the X-LLM-API-Key header",
        )

    try:
        provider = get_provider(provider=request.provider, api_key=llm_key, model=request.model)
    except LLMNotConfigured as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"LLM is not configured: {exc}",
        ) from exc

    try:
        return enrich(request, provider)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    except NotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except LLMRateLimited as exc:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)) from exc
    except LLMKeyRejected as exc:
        if llm_key:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
        logger.exception("LLM request failed for %s", request.wikidata_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"LLM request failed: {exc}",
        ) from exc
    except LLMRequestRejected as exc:
        if llm_key:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
        logger.exception("LLM request failed for %s", request.wikidata_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"LLM request failed: {exc}",
        ) from exc
    except LLMError as exc:
        logger.exception("LLM request failed for %s", request.wikidata_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"LLM request failed: {exc}",
        ) from exc
    except (TimeoutError, URLError, UpstreamError, json.JSONDecodeError, KeyError) as exc:
        logger.exception("Wikimedia request failed for %s", request.wikidata_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Wikidata or Wikipedia request failed",
        ) from exc
