"""Liveness and readiness (spec §4.4.7). No auth on these."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status

from gateway.deps import get_engine
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
