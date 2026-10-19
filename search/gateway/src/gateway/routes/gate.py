"""The gate's own routes: per-caller keys only, everything screened (PLAN-connectors step 3).

  POST /v1/fetch        {"url"} -> FetchResponse: the page fetched under the guard and the caps,
                        handed to intake, returned ONLY if intake labelled it clean (fenced +
                        labels); else ``withheld`` / ``refused`` / ``error`` with no page text
  POST /v1/web-loader   Open WebUI's external web loader (WEB_LOADER_ENGINE=external,
                        EXTERNAL_WEB_LOADER_URL=<gate>/v1/web-loader): {"urls": [...]} ->
                        [{page_content, metadata: {source, title, labels}}] for the clean pages
                        only; a page that is not clean is simply absent (OWUI drops it the same
                        way it drops a failed load)
  POST /v1/owui/search  Open WebUI's external web SEARCH engine (WEB_SEARCH_ENGINE=external), the
                        header-capable replacement for its keyless `searxng` engine:
                        {"query", "count"} -> [{link, title, snippet}] for clean results only

Nothing here logs a URL or a query: counts and outcomes only (T0-d / audit A3).
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from gateway.callers import Caller
from gateway.config import Settings, get_settings
from gateway.deps import GateRuntime, get_engine, get_gate, require_screened
from gateway.egress.fetch import FetchError, media_type
from gateway.egress.guard import REASONS, Refused
from gateway.logging import get_logger
from gateway.models import (
    FetchRequest,
    FetchResponse,
    LoaderRequest,
    OwuiSearchRequest,
    SearchRequest,
)
from gateway.rotation import AllProvidersFailed, RotationEngine
from gateway.routes.common import require_screening, screened_search

router = APIRouter(tags=["gate"])
log = get_logger("gateway.gate")


# Fixed, gate-authored texts for the non-ok answers that are not refusals.
ERROR_NETWORK = "error: the page could not be fetched (network)"
ERROR_HTTP = "error: the site answered with an HTTP error status"
WITHHELD = "withheld: intake did not label this page clean"


async def fetch_and_screen(gate: GateRuntime, url: str) -> FetchResponse:
    """A NON-ok answer carries only the caller's own url, the status, intake's label, an int HTTP
    status and a fixed reason - never a string the site chose (final URL, redirect hops, header
    values; review T-2). An ok answer carries the allow-listed media type without parameters."""
    try:
        f = await gate.fetcher.fetch(url)
    except Refused as e:
        return FetchResponse(status="refused", url=url, reason=REASONS[e.kind])
    except FetchError:
        return FetchResponse(status="error", url=url, reason=ERROR_NETWORK)
    if f.status >= 300:
        return FetchResponse(status="error", url=url, http_status=int(f.status), reason=ERROR_HTTP)
    outcome, screened = await gate.screen.page(f)
    if screened is None:
        return FetchResponse(status="withheld", url=url, label=outcome.label,
                             http_status=int(f.status), reason=WITHHELD)
    return FetchResponse(status="ok", url=url, final_url=f.final_url, redirects=f.redirects,
                         http_status=int(f.status), content_type=media_type(f.content_type),
                         label=outcome.label, title=screened.title or None, text=screened.text,
                         fenced=screened.fenced, labels=screened.labels)


@router.post("/v1/fetch", response_model=FetchResponse)
async def fetch(
    body: FetchRequest,
    caller: Caller = Depends(require_screened),
    gate: GateRuntime = Depends(get_gate),
) -> FetchResponse:
    require_screening(gate)
    out = await fetch_and_screen(gate, body.url)
    log.info("fetch_done", caller=caller.name, status=out.status, label=out.label)
    return out


@router.post("/v1/web-loader")
async def web_loader(
    body: LoaderRequest,
    caller: Caller = Depends(require_screened),
    gate: GateRuntime = Depends(get_gate),
    settings: Settings = Depends(get_settings),
) -> list[dict[str, Any]]:
    require_screening(gate)
    if len(body.urls) > settings.loader_max_urls:
        raise HTTPException(413,
                            f"at most {settings.loader_max_urls} urls per request")
    sem = asyncio.Semaphore(settings.fetch_concurrency)

    async def one(u: str) -> FetchResponse:
        async with sem:
            return await fetch_and_screen(gate, u)

    outs = await asyncio.gather(*(one(u) for u in body.urls))
    docs = [
        {"page_content": o.fenced or "",
         "metadata": {"source": o.final_url or o.url, "title": o.title or "",
                      "labels": o.labels or {}}}
        for o in outs if o.status == "ok"
    ]
    counts: dict[str, int] = {}
    for o in outs:
        counts[o.status] = counts.get(o.status, 0) + 1
    log.info("web_loader_done", caller=caller.name, **counts)
    return docs


@router.post("/v1/owui/search")
async def owui_search(
    body: OwuiSearchRequest,
    caller: Caller = Depends(require_screened),
    engine: RotationEngine = Depends(get_engine),
    gate: GateRuntime = Depends(get_gate),
) -> list[dict[str, str]]:
    try:
        resp = await screened_search(engine, gate,
                                     SearchRequest(query=body.query, max_results=body.count))
    except AllProvidersFailed:
        return []
    return [{"link": str(r.url), "title": r.title, "snippet": r.fenced} for r in resp.results]
