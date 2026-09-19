"""Shared test fixtures: a dependency-free in-memory async Redis double."""

from __future__ import annotations

import os

import pytest

# Settings require GATEWAY_API_KEY at import time of any module using config.
os.environ.setdefault("GATEWAY_API_KEY", "test-key")


class FakeRedis:
    """Minimal async Redis stand-in implementing only what the gateway uses.

    TTLs are recorded but not auto-expired; tests that need expiry simulate it
    by calling ``force_expire``.
    """

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> bool:
        self.store[key] = str(value)
        if ex is not None:
            self.ttls[key] = ex
        return True

    async def incr(self, key: str) -> int:
        new = int(self.store.get(key, "0")) + 1
        self.store[key] = str(new)
        return new

    async def expire(self, key: str, seconds: int) -> bool:
        if key in self.store:
            self.ttls[key] = seconds
            return True
        return False

    async def exists(self, key: str) -> int:
        return 1 if key in self.store else 0

    async def delete(self, *keys: str) -> int:
        removed = 0
        for key in keys:
            if key in self.store:
                del self.store[key]
                self.ttls.pop(key, None)
                removed += 1
        return removed

    async def ping(self) -> bool:
        return True

    async def aclose(self) -> None:
        return None

    # test helper
    def force_expire(self, key: str) -> None:
        self.store.pop(key, None)
        self.ttls.pop(key, None)


@pytest.fixture
def fake_redis() -> FakeRedis:
    return FakeRedis()
