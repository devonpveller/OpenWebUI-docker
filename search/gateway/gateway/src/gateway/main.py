"""FastAPI app entry. Builds the rotation engine once at startup (spec §11:
no global mutable state beyond the engine on app.state)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from gateway.cache import ResultCache, make_redis
from gateway.config import get_settings
from gateway.logging import configure_logging, get_logger
from gateway.rotation import RotationEngine, build_provider
from gateway.routes import health, native, searxng_compat, tavily_shim


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    log = get_logger("gateway.main")

    redis_client = make_redis(settings.redis_url)
    cache = ResultCache(redis_client, settings.cache_ttl_seconds)
    providers = [
        build_provider(name, settings.searxng_url, settings.request_timeout_seconds)
        for name in settings.provider_order
    ]
    engine = RotationEngine(
        providers,
        cache,
        redis_client,
        failure_threshold=settings.circuit_failure_threshold,
        cooldown_seconds=settings.circuit_cooldown_seconds,
        log_queries=settings.log_queries,
    )
    app.state.engine = engine
    log.info(
        "gateway_started",
        providers=[p.name for p in providers],
        log_queries=settings.log_queries,
    )
    try:
        yield
    finally:
        await engine.aclose()
        await redis_client.aclose()
        log.info("gateway_stopped")


app = FastAPI(
    title="Private Search Gateway",
    version="0.1.0",
    description="Privacy-first web search fronting SearXNG over Tor.",
    lifespan=lifespan,
)

app.include_router(health.router)
app.include_router(native.router)
app.include_router(tavily_shim.router)
app.include_router(searxng_compat.router)
