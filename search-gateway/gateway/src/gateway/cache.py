"""Redis-backed result cache. Key = sha256 of the normalized request JSON.

The same Redis connection is reused for circuit-breaker state (rotation.py),
so this module owns the single async client.
"""

from __future__ import annotations

import hashlib

import redis.asyncio as redis

from gateway.models import SearchRequest, SearchResponse

_CACHE_PREFIX = "psg:cache:"


def cache_key(req: SearchRequest) -> str:
    digest = hashlib.sha256(req.cache_key_payload().encode("utf-8")).hexdigest()
    return f"{_CACHE_PREFIX}{digest}"


class ResultCache:
    """Thin async cache over Redis. Stores serialized SearchResponse JSON."""

    def __init__(self, client: redis.Redis, ttl_seconds: int) -> None:
        self._r = client
        self._ttl = ttl_seconds

    @property
    def client(self) -> redis.Redis:
        return self._r

    async def get(self, req: SearchRequest) -> SearchResponse | None:
        if self._ttl <= 0:
            return None
        raw = await self._r.get(cache_key(req))
        if raw is None:
            return None
        resp = SearchResponse.model_validate_json(raw)
        resp.cached = True
        return resp

    async def set(self, req: SearchRequest, resp: SearchResponse) -> None:
        if self._ttl <= 0:
            return
        # Persist with cached=false; get() flips it on read.
        to_store = resp.model_copy(update={"cached": False})
        await self._r.set(cache_key(req), to_store.model_dump_json(), ex=self._ttl)

    async def ping(self) -> bool:
        try:
            return bool(await self._r.ping())
        except Exception:
            return False


def make_redis(redis_url: str) -> redis.Redis:
    """Single decode-responses client shared by cache + circuit breaker."""
    return redis.from_url(redis_url, decode_responses=True)
