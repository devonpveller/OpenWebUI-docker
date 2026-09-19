"""Read-only observability namespace — SAFE to bridge to llm-net.

The §3.3/§10.3.1 resolution: read-only queue state may be surfaced to llm-net
consumers (the §9 `llm-traffic` OWUI pipe, a future dashboard) via a LiteLLM
pass-through, but the MUTATING control verbs must NOT be. The mutating routes
live under ``/queue/{id}/...`` and ``/keys/...`` (see control.py) — bridging the
bare ``/queue`` prefix would expose ``POST /queue/{id}/priority`` too.

So the read endpoints get their OWN ``/observe/*`` prefix that contains GET
verbs ONLY. LiteLLM passes through exactly ``/observe/*`` → no mutation can leak
onto llm-net even if the pass-through prefix-matches. The canonical ``/queue``
routes remain for direct operator (`docker exec`) use.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from . import control

router = APIRouter(tags=["observe"])


@router.get("/observe/queue")
async def observe_queue(request: Request) -> dict[str, object]:
    return await control.get_queue(request)


@router.get("/observe/queue/stats")
async def observe_stats(request: Request) -> dict[str, object]:
    return await control.get_stats(request)


@router.get("/observe/queue/estimate")
async def observe_estimate(
    request: Request, key: str | None = None, model: str | None = None
) -> dict[str, object]:
    return await control.get_estimate(request, key=key, model=model)
