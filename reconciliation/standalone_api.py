import json
import logging
import os
import secrets
from typing import Annotated
from urllib.error import URLError

from fastapi import FastAPI, HTTPException, Query, Security, status
from fastapi.security import APIKeyHeader
from pydantic import BaseModel

from reconciliation.algorithms import (
    DEFAULT_ALGORITHM,
    AlgorithmName,
    AlgorithmNotConfigured,
    get_algorithm,
)
from reconciliation.enrichment.llm import LLMError, LLMNotConfigured, get_provider
from reconciliation.enrichment.models import (
    EnrichmentRequest,
    EnrichmentResult,
    StatementsRequest,
    StatementsResult,
)
from reconciliation.enrichment.service import enrich
from reconciliation.enrichment.statements import statements
from reconciliation.enrichment.wikimedia import NotFound, UpstreamError
from reconciliation.models import Entity, ReconciliationMatch

logger = logging.getLogger(__name__)
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

app = FastAPI(title="ECHOLOT Standalone Reconciliation API", version="0.1.0")


class EntityResult(BaseModel):
    uri: str
    algorithm: AlgorithmName
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
def reconcile_entity(
    entity: Entity,
    algorithm: Annotated[
        AlgorithmName, Query(description="Reconciliation approach to run")
    ] = DEFAULT_ALGORITHM,
) -> EntityResult:
    implementation = get_algorithm(algorithm)
    try:
        implementation.check_configured()
    except AlgorithmNotConfigured as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Algorithm {algorithm.value} is not configured: {exc}",
        ) from exc

    try:
        result = implementation.reconcile(entity)
    except AlgorithmNotConfigured as exc:  # found only when calling, e.g. a refused key
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Algorithm {algorithm.value} is not configured: {exc}",
        ) from exc
    except implementation.UPSTREAM_ERRORS as exc:
        logger.exception("%s request failed for %s", algorithm.value, entity.uri)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Reconciliation service request failed",
        ) from exc

    return EntityResult(uri=entity.uri, algorithm=algorithm, matches=result.matches)


@app.post(
    "/enrich",
    response_model=EnrichmentResult,
    dependencies=[Security(require_api_key)],
)
def enrich_entity(request: EnrichmentRequest) -> EnrichmentResult:
    try:
        provider = get_provider()
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


@app.post(
    "/wikidata/statements",
    response_model=StatementsResult,
    response_model_exclude_none=True,
    dependencies=[Security(require_api_key)],
)
def wikidata_statements(request: StatementsRequest) -> StatementsResult:
    try:
        return statements(request)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    except (TimeoutError, URLError, UpstreamError, json.JSONDecodeError, KeyError) as exc:
        logger.exception("Wikidata request failed for %s", request.ids)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Wikidata request failed",
        ) from exc
