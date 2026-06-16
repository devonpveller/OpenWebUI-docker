"""Control plane — the differentiator (design §4.2).

READ endpoints (GET /queue, /queue/stats, /queue/estimate) are what the §9
`llm-traffic` OWUI pipe and a future dashboard render, and what lets "LiteLLM
understand live model state". They are safe to surface to llm-net via a LiteLLM
read-only pass-through (§3.3).

MUTATING endpoints (POST /queue/{id}/priority, /cancel, /keys/{key}/policy) change
scheduling for ALL callers and are UNAUTHENTICATED. INVARIANT (§10.3.1): they must
NEVER be exposed on llm-net or a host port without auth — reachable solely from
llm-backend-net (operator via `docker exec`) until real virtual keys land.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..app_state import AppState
from ..policy import PriorityClass

router = APIRouter(tags=["control"])


def _state(request: Request) -> AppState:
    return request.app.state.app


@router.get("/queue")
async def get_queue(request: Request) -> dict[str, object]:
    """Authoritative live view: {running, waiting, avg_T_s, P, ...} per model."""
    state = _state(request)
    return {
        "models": {name: mq.snapshot() for name, mq in state.registry.queues().items()},
        "held_total": state.registry.held_total,
        "max_total_connections": state.settings.max_total_connections,
    }


@router.get("/queue/stats")
async def get_stats(request: Request) -> dict[str, object]:
    """Depth, per-key shares, avg T, slot occupancy. (Rate/percentile stats land
    with the analytics event store in P3 — joined from queue_events.)"""
    state = _state(request)
    out: dict[str, object] = {}
    for name, mq in state.registry.queues().items():
        snap = mq.snapshot()
        out[name] = {
            "depth_waiting": len(snap["waiting"]),  # type: ignore[arg-type]
            "running": len(snap["running"]),  # type: ignore[arg-type]
            "permits_free": snap["permits_free"],
            "avg_T_s": snap["avg_T_s"],
            "P": snap["P"],
            "inflight_by_key": snap["inflight_by_key"],
        }
    return {"models": out, "held_total": state.registry.held_total}


@router.get("/queue/estimate")
async def get_estimate(
    request: Request, key: str | None = None, model: str | None = None
) -> dict[str, object]:
    """Projected wait for a hypothetical NEW request from ``key`` (pre-flight)."""
    state = _state(request)
    cls = state.policy.classify(key)
    mq = state.registry.queue_for(model)
    est = mq.estimate_wait(cls.rank)
    return {
        "model": mq.model_key,
        "key": key,
        "priority_class": cls.name,
        "rank": cls.rank,
        "acceptable_wait_s": cls.acceptable_wait_s,
        "projected_wait_s": round(est, 1),
        "avg_T_s": round(mq.avg_t, 2),
        "P": mq.slots,
        "would_admit": (est <= cls.acceptable_wait_s) if state.settings.enforce_budget else True,
    }


class PriorityBody(BaseModel):
    rank: int


@router.post("/queue/{request_id}/priority")
async def set_priority(request: Request, request_id: str, body: PriorityBody) -> JSONResponse:
    state = _state(request)
    for mq in state.registry.queues().values():
        if await mq.set_priority(request_id, body.rank):
            return JSONResponse({"ok": True, "id": request_id, "rank": body.rank})
    return JSONResponse({"ok": False, "error": "not found or already dispatched"}, status_code=404)


@router.post("/queue/{request_id}/cancel")
async def cancel(request: Request, request_id: str) -> JSONResponse:
    state = _state(request)
    for mq in state.registry.queues().values():
        snap = mq.snapshot()
        if any(w["id"] == request_id for w in snap["waiting"]):  # type: ignore[index]
            # Look up the live Waiter via the queue's internal map.
            w = mq._waiters.get(request_id)  # noqa: SLF001 — same package, intentional
            if w is not None and await mq.cancel_waiting(w):
                return JSONResponse({"ok": True, "id": request_id})
    return JSONResponse({"ok": False, "error": "not found or already dispatched"}, status_code=404)


class PolicyBody(BaseModel):
    priority_class: str
    rank: int
    acceptable_wait_s: float
    max_concurrency: int | None = None


@router.post("/keys/{key}/policy")
async def set_key_policy(request: Request, key: str, body: PolicyBody) -> JSONResponse:
    state = _state(request)
    state.policy.set_key(
        key,
        PriorityClass(
            name=body.priority_class,
            rank=body.rank,
            acceptable_wait_s=body.acceptable_wait_s,
            max_concurrency=body.max_concurrency,
        ),
    )
    return JSONResponse({"ok": True, "key": key, "class": body.priority_class, "rank": body.rank})
