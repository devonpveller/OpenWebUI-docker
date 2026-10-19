"""SearXNG-compatible surface: GET /search (Approach B).

Open WebUI v0.8.10's `searxng` engine GETs SEARXNG_QUERY_URL with the
`<query>` placeholder, strips the URL's own query string, then sends params:
  q, format=json, pageno, language(=all), safesearch, time_range, categories
and reads response.results[].{url,title,content,score}.

This endpoint speaks exactly that contract while internally routing through
the rotation engine (VPN + cache + circuit breaker + privacy logging).

WHO MAY CALL (search-gate, audit A4: this route was keyless on the published port):
  * a per-caller key (Authorization: Bearer) -> the SCREENED answer: only results intake labelled
    clean; `content` is the nonce-fenced text, `labels` rides beside it, `withheld` counts the rest
  * GATEWAY_API_KEY -> today's unscreened answer
  * no key -> 401, UNLESS SEARCH_ANONYMOUS_LEGACY=true. That switch exists only because OWUI's
    `searxng` engine cannot send a header and the research engine calls this path keyless today;
    the plane's compose sets it until step 5 moves both callers (OWUI to the header-capable
    POST /v1/owui/search, research to a per-caller key), then it goes.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse

from gateway.callers import Caller
from gateway.config import Settings, get_settings
from gateway.deps import GateRuntime, get_engine, get_gate, optional_caller
from gateway.models import SearchRequest
from gateway.rotation import AllProvidersFailed, RotationEngine
from gateway.routes.common import screened_search

router = APIRouter(tags=["searxng-compat"])

# OWUI trims to its own RAG result count; give it a healthy pool to trim from.
_DEFAULT_RESULTS = 20
_VALID_TIME_RANGES = {"day", "week", "month", "year"}


@router.get("/search")
async def searxng_compat_search(
    q: str = Query(..., min_length=1, max_length=2000),
    format: str = Query("json"),
    pageno: int = Query(1, ge=1),
    language: str = Query("all"),
    safesearch: int = Query(0, ge=0, le=2),
    time_range: str = Query(""),
    categories: str = Query(""),
    count: int = Query(_DEFAULT_RESULTS, ge=1, le=50),
    engine: RotationEngine = Depends(get_engine),
    caller: Caller | None = Depends(optional_caller),
    settings: Settings = Depends(get_settings),
    gate: GateRuntime = Depends(get_gate),
) -> JSONResponse:
    if caller is None and not settings.search_anonymous_legacy:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing or invalid bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    tr = time_range if time_range in _VALID_TIME_RANGES else None
    lang = None if language in ("", "all") else language

    req = SearchRequest(
        query=q,
        max_results=count,
        safe_search=safesearch,
        language=lang,
        time_range=tr,  # type: ignore[arg-type]  # validated against the set above
    )

    if caller is not None and caller.screened:
        try:
            screened = await screened_search(engine, gate, req)
        except AllProvidersFailed:
            return JSONResponse({"query": q, "number_of_results": 0, "results": []},
                                status_code=200)
        rows = [
            {
                "url": str(r.url),
                "title": r.title,
                "content": r.fenced,
                "engine": r.source_engine or screened.provider_used,
                "score": r.score if r.score is not None else 0.0,
                "publishedDate": r.published_at.isoformat() if r.published_at else None,
                "category": "general",
                "labels": r.labels,
            }
            for r in screened.results
        ]
        return JSONResponse({"query": q, "number_of_results": len(rows), "results": rows,
                             "withheld": screened.withheld, "answers": [], "infoboxes": [],
                             "suggestions": []})

    try:
        resp = await engine.search(req)
    except AllProvidersFailed:
        # SearXNG-shaped empty body; OWUI treats this as "no results".
        return JSONResponse(
            {"query": q, "number_of_results": 0, "results": []},
            status_code=200,
        )

    results: list[dict[str, Any]] = [
        {
            "url": str(r.url),
            "title": r.title,
            "content": r.snippet or "",
            "engine": r.source_engine or resp.provider_used,
            "score": r.score if r.score is not None else 0.0,
            "publishedDate": r.published_at.isoformat() if r.published_at else None,
            "category": "general",
        }
        for r in resp.results
    ]
    return JSONResponse(
        {
            "query": q,
            "number_of_results": len(results),
            "results": results,
            "answers": [],
            "infoboxes": [],
            "suggestions": [],
        }
    )
