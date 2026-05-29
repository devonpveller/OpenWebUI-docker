"""Tavily-compatible shim: POST /tavily/search (spec §4.4.7).

Accepts the bearer token OR an ``api_key`` field in the body (Tavily clients
send the latter). Maps search_depth=advanced -> more results. Never
synthesizes an ``answer`` — returns null to keep the shim honest.
"""

from __future__ import annotations

import secrets
import time
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field

from gateway.config import Settings, get_settings
from gateway.deps import _bearer_token, get_engine
from gateway.models import SearchRequest
from gateway.rotation import AllProvidersFailed, RotationEngine

router = APIRouter(tags=["tavily"])

_ADVANCED_MAX_RESULTS = 20


class TavilyRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    api_key: str | None = None
    max_results: int | None = Field(default=None, ge=1, le=50)
    search_depth: Literal["basic", "advanced"] = "basic"


class TavilyResult(BaseModel):
    title: str
    url: str
    content: str
    score: float
    published_date: str | None = None


class TavilyResponse(BaseModel):
    query: str
    results: list[TavilyResult]
    answer: None = None
    response_time: float


@router.post("/tavily/search", response_model=TavilyResponse)
async def tavily_search(
    body: TavilyRequest,
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
    engine: RotationEngine = Depends(get_engine),
) -> TavilyResponse:
    supplied = _bearer_token(authorization) or body.api_key
    if supplied is None or not secrets.compare_digest(
        supplied, settings.gateway_api_key
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing or invalid API key",
        )

    if body.max_results is not None:
        max_results = body.max_results
    elif body.search_depth == "advanced":
        max_results = _ADVANCED_MAX_RESULTS
    else:
        max_results = 10

    started = time.perf_counter()
    try:
        resp = await engine.search(
            SearchRequest(query=body.query, max_results=max_results)
        )
    except AllProvidersFailed:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="all search providers are unavailable",
        ) from None
    elapsed = round(time.perf_counter() - started, 4)

    results = [
        TavilyResult(
            title=r.title,
            url=str(r.url),
            content=r.snippet or "",
            score=r.score if r.score is not None else 0.0,
            published_date=r.published_at.isoformat() if r.published_at else None,
        )
        for r in resp.results
    ]
    return TavilyResponse(query=body.query, results=results, response_time=elapsed)
