"""Liveness and readiness (spec §4.4.7). No auth on these."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status

from gateway.deps import get_engine
from gateway.engine_health import health_verdict
from gateway.rotation import RotationEngine

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Process liveness — always 200 if the event loop is serving."""
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(
    response: Response,
    engine: RotationEngine = Depends(get_engine),
) -> dict[str, object]:
    """Ready only if Redis answers AND at least one provider is healthy
    (i.e. SearXNG reachable through the Tor chain)."""
    redis_ok = await engine.redis_ok()
    provider_ok = await engine.any_provider_healthy()
    ready = redis_ok and provider_ok
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"ready": ready, "redis": redis_ok, "providers": provider_ok}


@router.get("/health")
async def health(engine: RotationEngine = Depends(get_engine)) -> dict[str, object]:
    """Detail, not just up/down (research-trust 2026-09-11).

    /readyz answers "SearXNG returned 200". Through the whole audited outage it
    said yes: bing answered every query with HTTP 200 and ten results for the
    first word of the query. This reports which engines actually put results in
    the recent payloads, which ones SearXNG reported as failing, and a verdict
    that says DEGRADED when fewer than two engines are answering.

    Unauthenticated like /healthz and /readyz, and it exposes no query text -
    engine names and counts only.
    """
    redis_ok = await engine.redis_ok()
    engines: dict[str, object] = {}
    for provider in getattr(engine, "providers", []) or []:
        health_window = getattr(provider, "engine_health", None)
        if health_window is None:
            continue
        snap = health_window.snapshot()
        engines[getattr(provider, "name", "provider")] = {
            **snap,
            "verdict": health_verdict(snap),
        }
    verdicts = [str(v.get("verdict")) for v in engines.values() if isinstance(v, dict)]
    overall = "unknown"
    if verdicts:
        overall = "DEGRADED" if "DEGRADED" in verdicts else (
            "ok" if "ok" in verdicts else "unknown"
        )
    return {"status": "ok", "redis": redis_ok, "search": overall, "providers": engines}
