"""FastAPI app entry. Builds the rotation engine and the gate's runtime once at startup (spec §11:
no global mutable state beyond what the lifespan puts on app.state).

Start-up refuses (raises) on a malformed caller-keys file, an unknown engine name or an unreadable
secret file: a misconfigured gate never serves."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from gateway.cache import ResultCache, make_redis, redis_password_from
from gateway.callers import CallerRegistry, parse_caller_keys
from gateway.config import Settings, get_settings, read_secret_file
from gateway.deps import GateRuntime
from gateway.egress.fetch import Fetcher, FetchPolicy
from gateway.egress.resolve import DohResolver, Resolver, SystemResolver
from gateway.logging import configure_logging, get_logger
from gateway.rotation import RotationEngine, build_provider
from gateway.routes import gate, health, native, searxng_compat, tavily_shim
from gateway.screen.connector import Connector, make_connector
from gateway.screen.gate import Screen


def build_gate(settings: Settings) -> tuple[GateRuntime, Resolver, Connector]:
    callers = CallerRegistry(parse_caller_keys(read_secret_file(settings.caller_keys_file)),
                             settings.gateway_api_key)
    resolver: Resolver
    if settings.fetch_proxy_url:
        resolver = DohResolver(settings.doh_url, settings.fetch_proxy_url,
                               settings.fetch_timeout_seconds)
    else:
        resolver = SystemResolver()
    fetcher = Fetcher(
        FetchPolicy(
            proxy_url=settings.fetch_proxy_url,
            max_bytes=settings.fetch_max_bytes,
            timeout_s=settings.fetch_timeout_seconds,
            max_redirects=settings.fetch_max_redirects,
            ports=settings.fetch_port_set,
            content_types=settings.fetch_type_set,
        ),
        resolver,
    )
    connector: Connector
    connector, screening = make_connector(settings.intake_url, settings.intake_key_file,
                                          settings.intake_timeout_seconds,
                                          settings.intake_concurrency)
    runtime = GateRuntime(callers=callers, fetcher=fetcher, screen=Screen(connector),
                          screening_configured=screening)
    return runtime, resolver, connector


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    log = get_logger("gateway.main")

    password = redis_password_from(read_secret_file(settings.redis_password_file))
    redis_client = make_redis(settings.redis_url, password or None)
    cache_key = read_secret_file(settings.cache_key_file).encode("utf-8") or None
    cache = ResultCache(redis_client, settings.cache_ttl_seconds, cache_key)
    providers = [
        build_provider(name, settings.search_engine_url, settings.request_timeout_seconds)
        for name in settings.engine_order
    ]
    engine = RotationEngine(
        providers,
        cache,
        redis_client,
        failure_threshold=settings.circuit_failure_threshold,
        cooldown_seconds=settings.circuit_cooldown_seconds,
        log_queries=settings.log_queries,
    )
    runtime, resolver, connector = build_gate(settings)
    app.state.engine = engine
    app.state.gate = runtime
    log.info(
        "gateway_started",
        providers=[p.name for p in providers],
        log_queries=settings.log_queries,
        screened_callers=runtime.callers.screened_names,
        screening=runtime.screening_configured,
        fetch_mode="proxy" if settings.fetch_proxy_url else "direct",
        anonymous_legacy=settings.search_anonymous_legacy,
        redis_auth=bool(password),
    )
    try:
        yield
    finally:
        await engine.aclose()
        await resolver.aclose()
        await connector.aclose()
        await redis_client.aclose()
        log.info("gateway_stopped")


app = FastAPI(
    title="Search Gateway",
    version="0.2.0",
    description="The search plane's gate: web search and page fetches, guarded and screened.",
    lifespan=lifespan,
)

app.include_router(health.router)
app.include_router(native.router)
app.include_router(tavily_shim.router)
app.include_router(searxng_compat.router)
app.include_router(gate.router)
