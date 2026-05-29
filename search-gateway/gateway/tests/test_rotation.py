"""Rotation + circuit breaker: failover, circuit open/cooldown, cache hit."""

from __future__ import annotations

import pytest

from gateway.cache import ResultCache, cache_key
from gateway.models import SearchRequest, SearchResult
from gateway.providers.base import SearchProvider, TransientProviderError
from gateway.rotation import AllProvidersFailed, RotationEngine

from conftest import FakeRedis


class FakeProvider(SearchProvider):
    def __init__(self, name: str, *, fail: bool = False, rank: int = 0) -> None:
        self.name = name
        self.privacy_rank = rank
        self.fail = fail
        self.calls = 0

    async def search(self, req: SearchRequest) -> list[SearchResult]:
        self.calls += 1
        if self.fail:
            raise TransientProviderError(f"{self.name} down")
        return [
            SearchResult(
                title=f"{self.name} result",
                url="https://example.com",
                provider=self.name,
            )
        ]

    async def health(self) -> bool:
        return not self.fail


def make_engine(providers: list[SearchProvider], redis: FakeRedis, **kw: int) -> RotationEngine:
    cache = ResultCache(redis, kw.pop("ttl", 900))
    return RotationEngine(
        providers,
        cache,
        redis,
        failure_threshold=kw.get("failure_threshold", 3),
        cooldown_seconds=kw.get("cooldown_seconds", 120),
        log_queries=False,
    )


REQ = SearchRequest(query="anthropic claude")


async def test_failover_to_next_provider(fake_redis: FakeRedis) -> None:
    primary = FakeProvider("primary", fail=True)
    backup = FakeProvider("backup")
    engine = make_engine([primary, backup], fake_redis)

    resp = await engine.search(REQ)
    assert resp.provider_used == "backup"
    assert primary.calls == 1 and backup.calls == 1


async def test_cache_hit_short_circuits(fake_redis: FakeRedis) -> None:
    provider = FakeProvider("p")
    engine = make_engine([provider], fake_redis)

    first = await engine.search(REQ)
    assert first.cached is False
    second = await engine.search(REQ)
    assert second.cached is True
    assert provider.calls == 1  # provider not hit again


async def test_circuit_opens_after_threshold_and_skips(fake_redis: FakeRedis) -> None:
    bad = FakeProvider("bad", fail=True)
    good = FakeProvider("good")
    engine = make_engine([bad, good], fake_redis, failure_threshold=2)

    # Two distinct queries so cache never masks the bad provider.
    await engine.search(SearchRequest(query="q1"))
    await engine.search(SearchRequest(query="q2"))
    # Circuit for "bad" should now be open.
    assert await fake_redis.exists("psg:circuit:open:bad") == 1

    calls_before = bad.calls
    await engine.search(SearchRequest(query="q3"))
    assert bad.calls == calls_before  # skipped while circuit open


async def test_circuit_cooldown_expiry_reallows_provider(fake_redis: FakeRedis) -> None:
    bad = FakeProvider("bad", fail=True)
    good = FakeProvider("good")
    engine = make_engine([bad, good], fake_redis, failure_threshold=1)

    await engine.search(SearchRequest(query="q1"))
    assert await fake_redis.exists("psg:circuit:open:bad") == 1

    # Simulate TTL elapsing.
    fake_redis.force_expire("psg:circuit:open:bad")
    bad.fail = False
    resp = await engine.search(SearchRequest(query="q2"))
    assert resp.provider_used == "bad"  # tried again after cooldown


class EmptyProvider(SearchProvider):
    name = "empty"
    privacy_rank = 0

    async def search(self, req: SearchRequest) -> list[SearchResult]:
        return []

    async def health(self) -> bool:
        return True


async def test_empty_results_are_not_cached(fake_redis: FakeRedis) -> None:
    provider = EmptyProvider()
    engine = make_engine([provider], fake_redis)

    first = await engine.search(REQ)
    assert first.results == [] and first.cached is False
    second = await engine.search(REQ)
    # Not served from cache — provider re-queried (so a Tor blip can recover).
    assert second.cached is False


async def test_all_providers_failed_raises(fake_redis: FakeRedis) -> None:
    engine = make_engine([FakeProvider("a", fail=True)], fake_redis)
    with pytest.raises(AllProvidersFailed):
        await engine.search(SearchRequest(query="nothing works"))


async def test_cache_key_is_stable_and_request_specific() -> None:
    assert cache_key(SearchRequest(query="x")) == cache_key(SearchRequest(query="x"))
    assert cache_key(SearchRequest(query="x")) != cache_key(SearchRequest(query="y"))
