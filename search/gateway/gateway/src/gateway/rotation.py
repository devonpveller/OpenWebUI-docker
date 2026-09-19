"""Provider rotation + Redis-backed circuit breaker (spec §4.4.6).

Per request:
  1. Cache lookup (return immediately on hit, cached=true).
  2. Walk providers in PROVIDER_PRIORITY order; skip any with an open circuit.
  3. On TransientProviderError: bump failure counter; open the circuit for
     CIRCUIT_COOLDOWN_SECONDS once it reaches CIRCUIT_FAILURE_THRESHOLD.
  4. On success: reset the circuit, cache, return.
  5. All providers exhausted -> AllProvidersFailed (route maps to 503).

Circuit state lives in Redis with TTLs so it survives restarts and is shared
across uvicorn workers.
"""

from __future__ import annotations

from collections.abc import Callable

import redis.asyncio as redis

from gateway.cache import ResultCache
from gateway.logging import get_logger, query_fingerprint
from gateway.models import SearchRequest, SearchResponse
from gateway.providers._stubs import (
    BraveProvider,
    KagiProvider,
    MojeekProvider,
    TavilyProvider,
)
from gateway.providers.base import (
    ProviderConfigError,
    SearchProvider,
    TransientProviderError,
)
from gateway.providers.searxng import SearxngProvider

log = get_logger("gateway.rotation")

_CIRCUIT_OPEN = "psg:circuit:open:"
_CIRCUIT_FAIL = "psg:circuit:fail:"


class AllProvidersFailed(Exception):
    """Every configured provider failed or was circuit-broken for this request."""


def build_provider(name: str, searxng_url: str, timeout: float) -> SearchProvider:
    factories: dict[str, Callable[[], SearchProvider]] = {
        "searxng": lambda: SearxngProvider(searxng_url, timeout),
        "kagi": KagiProvider,
        "mojeek": MojeekProvider,
        "brave": BraveProvider,
        "tavily": TavilyProvider,
    }
    if name not in factories:
        raise ValueError(f"Unknown provider in PROVIDER_PRIORITY: {name!r}")
    return factories[name]()


class RotationEngine:
    def __init__(
        self,
        providers: list[SearchProvider],
        cache: ResultCache,
        redis_client: redis.Redis,
        *,
        failure_threshold: int,
        cooldown_seconds: int,
        log_queries: bool,
    ) -> None:
        self._providers = providers
        self._cache = cache
        self._r = redis_client
        self._threshold = failure_threshold
        self._cooldown = cooldown_seconds
        self._log_queries = log_queries

    @property
    def providers(self) -> list[SearchProvider]:
        return self._providers

    # --- circuit breaker -------------------------------------------------
    async def _circuit_open(self, name: str) -> bool:
        return bool(await self._r.exists(f"{_CIRCUIT_OPEN}{name}"))

    async def _record_failure(self, name: str) -> None:
        key = f"{_CIRCUIT_FAIL}{name}"
        count = await self._r.incr(key)
        if count == 1:
            # Failure-counting window: same length as the cooldown.
            await self._r.expire(key, self._cooldown)
        if count >= self._threshold:
            await self._r.set(
                f"{_CIRCUIT_OPEN}{name}", "1", ex=self._cooldown
            )
            await self._r.delete(key)
            log.warning("circuit_opened", provider=name, cooldown_s=self._cooldown)

    async def _reset_circuit(self, name: str) -> None:
        await self._r.delete(f"{_CIRCUIT_FAIL}{name}", f"{_CIRCUIT_OPEN}{name}")

    # --- main entrypoint -------------------------------------------------
    async def search(self, req: SearchRequest) -> SearchResponse:
        qfp = query_fingerprint(req.query) if self._log_queries else None

        cached = await self._cache.get(req)
        if cached is not None:
            log.info("cache_hit", provider=cached.provider_used, qfp=qfp)
            return cached

        for provider in self._providers:
            if await self._circuit_open(provider.name):
                log.info("circuit_skip", provider=provider.name, qfp=qfp)
                continue
            try:
                results = await provider.search(req)
            except TransientProviderError as exc:
                log.warning(
                    "provider_transient", provider=provider.name, error=str(exc), qfp=qfp
                )
                await self._record_failure(provider.name)
                continue
            except ProviderConfigError as exc:
                # Permanent: don't count toward the circuit, just skip it.
                log.error(
                    "provider_config_error",
                    provider=provider.name,
                    error=str(exc),
                    qfp=qfp,
                )
                continue

            await self._reset_circuit(provider.name)
            resp = SearchResponse(
                query=req.query,
                provider_used=provider.name,
                results=results,
                cached=False,
            )
            # Do NOT cache empty results: an engine hiccup / Tor blip would
            # otherwise be frozen in for the full TTL. Only successful,
            # non-empty responses are worth caching.
            if results:
                await self._cache.set(req, resp)
            log.info(
                "search_ok",
                provider=provider.name,
                result_count=len(results),
                qfp=qfp,
            )
            return resp

        log.error("all_providers_failed", qfp=qfp)
        raise AllProvidersFailed

    # --- readiness -------------------------------------------------------
    async def redis_ok(self) -> bool:
        return await self._cache.ping()

    async def any_provider_healthy(self) -> bool:
        for provider in self._providers:
            if await provider.health():
                return True
        return False

    async def aclose(self) -> None:
        for provider in self._providers:
            await provider.aclose()
