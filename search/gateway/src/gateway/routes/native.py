"""Native REST surface: POST /v1/search (spec §4.4.7)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from gateway.deps import get_engine, require_bearer
from gateway.models import SearchRequest, SearchResponse
from gateway.rotation import AllProvidersFailed, RotationEngine

router = APIRouter(tags=["native"])


@router.post(
    "/v1/search",
    response_model=SearchResponse,
    dependencies=[Depends(require_bearer)],
)
async def search(
    req: SearchRequest,
    engine: RotationEngine = Depends(get_engine),
) -> SearchResponse:
    try:
        return await engine.search(req)
    except AllProvidersFailed:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="all search providers are unavailable",
        ) from None
