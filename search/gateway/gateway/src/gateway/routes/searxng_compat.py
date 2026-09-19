"""SearXNG-compatible surface: GET /search (Approach B).

Open WebUI v0.8.10's `searxng` engine GETs SEARXNG_QUERY_URL with the
`<query>` placeholder, strips the URL's own query string, then sends params:
  q, format=json, pageno, language(=all), safesearch, time_range, categories
and reads response.results[].{url,title,content,score}.

This endpoint speaks exactly that contract while internally routing through
the rotation engine (Tor + cache + circuit breaker + privacy logging).

NO bearer auth: OWUI's searxng engine cannot send an Authorization header.
This is safe because the gateway's SearXNG-compat path is reachable only on
the internal docker network (not host-published) and called solely by OWUI —
the same trust model the stack already uses for internal services.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from gateway.deps import get_engine
from gateway.models import SearchRequest
from gateway.rotation import AllProvidersFailed, RotationEngine

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
) -> JSONResponse:
    tr = time_range if time_range in _VALID_TIME_RANGES else None
    lang = None if language in ("", "all") else language

    req = SearchRequest(
        query=q,
        max_results=count,
        safe_search=safesearch,
        language=lang,
        time_range=tr,  # type: ignore[arg-type]  # validated against the set above
    )

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
