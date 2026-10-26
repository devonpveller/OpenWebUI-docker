"""Redis-backed result cache, authenticated (audit A2).

Before: key = sha256 of the request JSON (predictable from the public code) and the value served
as stored, so anyone who could write to redis chose every caller's results. Now, with a key only
the gateway holds (GATE_CACHE_KEY_FILE, else a random key per process):

  key    psg:cache:v2:<HMAC-SHA256(k, "key\\0" + request JSON)>   - cannot be predicted
  value  <HMAC-SHA256(k, "val\\0" + redis key + "\\0" + json) hex>.<json>
         - bound to its own key, so neither a forged value nor one copied from another key verifies

A value that does not verify is a MISS (and is deleted), never served. Redis also gets a password
(REDIS_PASSWORD_FILE; compose `requirepass`): the HMAC is what holds if that is bypassed.

The same Redis connection is reused for circuit-breaker state (rotation.py), so this module owns
the single async client.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

import redis.asyncio as redis

from gateway.models import SearchRequest, SearchResponse

_CACHE_PREFIX = "psg:cache:v2:"
# The key used when none is configured: random, per process (a restart empties the cache).
_PROCESS_KEY = secrets.token_bytes(32)


def _mac(key: bytes, *parts: str) -> str:
    return hmac.new(key, "\0".join(parts).encode("utf-8"), hashlib.sha256).hexdigest()


def cache_key(req: SearchRequest, hmac_key: bytes | None = None) -> str:
    k = hmac_key or _PROCESS_KEY
    return f"{_CACHE_PREFIX}{_mac(k, 'key', req.cache_key_payload())}"


class ResultCache:
    """Thin async cache over Redis. Stores MAC'd serialized SearchResponse JSON."""

    def __init__(self, client: redis.Redis, ttl_seconds: int,
                 hmac_key: bytes | None = None) -> None:
        self._r = client
        self._ttl = ttl_seconds
        self._k = hmac_key or _PROCESS_KEY

    @property
    def client(self) -> redis.Redis:
        return self._r

    async def get(self, req: SearchRequest) -> SearchResponse | None:
        if self._ttl <= 0:
            return None
        key = cache_key(req, self._k)
        raw = await self._r.get(key)
        if raw is None:
            return None
        tag, sep, payload = str(raw).partition(".")
        if not sep or not hmac.compare_digest(tag, _mac(self._k, "val", key, payload)):
            await self._r.delete(key)  # forged or corrupt: drop it, answer as a miss
            return None
        try:
            resp = SearchResponse.model_validate_json(payload)
        except ValueError:
            await self._r.delete(key)
            return None
        resp.cached = True
        return resp

    async def set(self, req: SearchRequest, resp: SearchResponse) -> None:
        if self._ttl <= 0:
            return
        key = cache_key(req, self._k)
        # Persist with cached=false; get() flips it on read.
        payload = resp.model_copy(update={"cached": False}).model_dump_json()
        await self._r.set(key, f"{_mac(self._k, 'val', key, payload)}.{payload}", ex=self._ttl)

    async def ping(self) -> bool:
        try:
            return bool(await self._r.ping())
        except Exception:
            return False


def redis_password_from(text: str) -> str:
    """The password in a REDIS_PASSWORD_FILE: a bare password, or a redis.conf whose
    `requirepass <pw>` line carries it (one file then serves redis and the gateway)."""
    for line in text.splitlines():
        words = line.strip().split(None, 1)
        if len(words) == 2 and words[0].lower() == "requirepass":
            return words[1].strip().strip('"')
    return text.strip()


def make_redis(redis_url: str, password: str | None = None) -> redis.Redis:
    """Single decode-responses client shared by cache + circuit breaker."""
    return redis.from_url(redis_url, decode_responses=True, password=password or None)
