"""Stdio MCP server exposing one tool: web_search.

Runs as its own process (the mcpo service launches `python -m gateway.mcp_server`
and re-exposes it as OpenAPI). It reuses the SAME rotation engine code as the
HTTP routes — Tor, cache, circuit breaker, and privacy logging all apply.
"""

from __future__ import annotations

import asyncio

from mcp.server.fastmcp import FastMCP

from gateway.cache import ResultCache, make_redis
from gateway.config import get_settings
from gateway.logging import configure_logging
from gateway.models import SearchRequest
from gateway.rotation import AllProvidersFailed, RotationEngine, build_provider

mcp = FastMCP("private-search-gateway")

_engine: RotationEngine | None = None
_engine_lock = asyncio.Lock()


async def _get_engine() -> RotationEngine:
    global _engine
    if _engine is not None:
        return _engine
    async with _engine_lock:
        if _engine is None:
            settings = get_settings()
            configure_logging(settings.log_level)
            redis_client = make_redis(settings.redis_url)
            cache = ResultCache(redis_client, settings.cache_ttl_seconds)
            providers = [
                build_provider(
                    name, settings.searxng_url, settings.request_timeout_seconds
                )
                for name in settings.provider_order
            ]
            _engine = RotationEngine(
                providers,
                cache,
                redis_client,
                failure_threshold=settings.circuit_failure_threshold,
                cooldown_seconds=settings.circuit_cooldown_seconds,
                log_queries=settings.log_queries,
            )
    return _engine


@mcp.tool()
async def web_search(
    query: str,
    max_results: int = 10,
    time_range: str | None = None,
) -> str:
    """Privacy-respecting web search via SearXNG over Tor.

    Returns the normalized SearchResponse as JSON.
    """
    tr = time_range if time_range in ("day", "week", "month", "year") else None
    req = SearchRequest(
        query=query,
        max_results=max(1, min(max_results, 50)),
        time_range=tr,  # type: ignore[arg-type]  # validated above
    )
    engine = await _get_engine()
    try:
        resp = await engine.search(req)
    except AllProvidersFailed:
        return '{"error": "all search providers are unavailable", "results": []}'
    return resp.model_dump_json()


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
