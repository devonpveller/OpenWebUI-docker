"""Liveness — no model load, like llama-swap's router liveness (design §4.2).

Deliberately does NOT probe the upstream (a probe would force a llama-swap model
load → swap thrash, the exact failure background_health_checks:false avoids).
This is process liveness only; the queue being up is what matters for the
fail-closed contract (§8a)."""

from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz(request: Request) -> dict[str, object]:
    state = request.app.state.app
    return {
        "status": "ok",
        "held_total": state.registry.held_total,
        "models": list(state.registry.queues().keys()),
        # ao-queue: the shedding episode state the host watchdog pages on (shedding.py).
        "shedding": state.shed.snapshot(state.registry.held_total),
        # ao-queue round 2: model permits in use and permits the backstop had to reclaim.
        "running_total": sum(
            len(mq.snapshot()["running"]) for mq in state.registry.queues().values()
        ),
        "permits_reaped_total": state.permits_reaped_total,
    }
