"""Native REST surface: POST /v1/search (spec §4.4.7).

A per-caller key gets the SCREENED answer (GateSearchResponse: only intake-clean results, fenced,
labelled, the rest counted in ``withheld``). GATEWAY_API_KEY (the legacy lane) gets the
SearchResponse it always got. No key: 401.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from gateway.callers import Caller
from gateway.deps import GateRuntime, get_engine, get_gate, require_caller
from gateway.models import GateSearchResponse, SearchRequest, SearchResponse
from gateway.rotation import AllProvidersFailed, RotationEngine
from gateway.routes.common import screened_search

router = APIRouter(tags=["native"])


@router.post("/v1/search", response_model=SearchResponse | GateSearchResponse)
async def search(
    req: SearchRequest,
    caller: Caller = Depends(require_caller),
    engine: RotationEngine = Depends(get_engine),
    gate: GateRuntime = Depends(get_gate),
) -> SearchResponse | GateSearchResponse:
    try:
        if caller.screened:
            return await screened_search(engine, gate, req)
        return await engine.search(req)
    except AllProvidersFailed:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="all search providers are unavailable",
        ) from None
