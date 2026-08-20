"""Connection-leak self-healing (registry §10.3.3).

Regression for the observed wedge: Starlette abandons a disconnected StreamingResponse without
running the body_iterator's `finally`, so `release_connection` never fires and the held slot leaks.
Accumulated leaks pin the hard cap → every request is shed with 503 while the GPU sits idle.

These prove the held set is (a) idempotent under release and (b) self-healing via the reaper, so a
leaked slot is always reclaimed and the cap can never permanently wedge.
"""

import asyncio

import pytest

from llm_queue.config import Settings
from llm_queue.models import Rejected
from llm_queue.registry import Registry


def _settings(**over) -> Settings:
    s = Settings()
    for k, v in over.items():
        setattr(s, k, v)
    return s


async def test_reserve_counts_and_release_is_idempotent():
    reg = Registry(_settings(max_total_connections=5))
    await reg.reserve_connection("a", "qwen36-27b")
    await reg.reserve_connection("b", "qwen36-27b")
    assert reg.held_total == 2
    # releasing the same rid twice decrements exactly once (idempotent — a double/late release,
    # e.g. a generator finally that runs AFTER the reaper already reclaimed it, can't corrupt count)
    await reg.release_connection("a")
    await reg.release_connection("a")
    assert reg.held_total == 1
    # releasing an unknown rid is a harmless no-op (never drives the count negative)
    await reg.release_connection("never-reserved")
    assert reg.held_total == 1


async def test_cap_rejects_then_recovers_after_release():
    reg = Registry(_settings(max_total_connections=2))
    await reg.reserve_connection("a", "qwen36-27b")
    await reg.reserve_connection("b", "qwen36-27b")
    with pytest.raises(Rejected) as ei:
        await reg.reserve_connection("c", "qwen36-27b")
    assert ei.value.type == "queue_connections_exhausted"
    # freeing one slot lets the next caller in — the cap tracks real occupancy, not a stuck counter
    await reg.release_connection("a")
    await reg.reserve_connection("c", "qwen36-27b")
    assert reg.held_total == 2


async def test_reaper_reclaims_leaked_but_spares_fresh():
    reg = Registry(_settings(max_total_connections=3))
    # two connections that will never be released (the leak the reaper exists to reclaim)
    await reg.reserve_connection("leaked-1", "qwen36-27b")
    await reg.reserve_connection("leaked-2", "qwen36-27b")
    await asyncio.sleep(0.05)
    # a fresh, still-legitimate connection
    await reg.reserve_connection("live", "qwen36-27b")
    assert reg.held_total == 3
    # sweep with a TTL between the two ages: the 0.05s-old leaks are reclaimed, the fresh one spared
    reclaimed = await reg.reap_stale_connections(ttl_s=0.02)
    assert set(reclaimed) == {"leaked-1", "leaked-2"}
    assert reg.held_total == 1  # only the live connection remains


async def test_releasing_response_runs_generator_finally_on_disconnect():
    """Leak PREVENTION at the source: when the client disconnects mid-stream (the transport's send()
    raises), _ReleasingStreamingResponse must aclose the body iterator so its `finally` — where the
    held slot is released — runs IMMEDIATELY, not deferred to GC (Starlette 1.3.1 skips this)."""
    from llm_queue.routes.data import _ReleasingStreamingResponse

    released = {"ran": False}

    async def body():
        try:
            yield b"chunk-1"
            yield b"chunk-2"
        finally:
            released["ran"] = True  # stands in for `await release_connection(rid)`

    resp = _ReleasingStreamingResponse(body())

    async def send(msg):
        # Simulate a disconnected client: the ASGI transport raises on the first body write.
        if msg["type"] == "http.response.body" and msg.get("body"):
            raise OSError("client disconnected")

    with pytest.raises(OSError):
        await resp.stream_response(send)
    assert released["ran"] is True  # slot freed at disconnect, not left leaking until the reaper


async def test_full_cap_of_leaks_self_heals_via_reaper():
    """The exact wedge: the cap fills entirely with LEAKED connections (never released) so every new
    request is shed — then the reaper reclaims them all and admissions flow again."""
    reg = Registry(_settings(max_total_connections=4))
    for i in range(4):
        await reg.reserve_connection(f"leak-{i}", "qwen36-27b")
    with pytest.raises(Rejected):  # wedged — shedding while nothing is really running
        await reg.reserve_connection("new", "qwen36-27b")
    await asyncio.sleep(0.03)
    reclaimed = await reg.reap_stale_connections(ttl_s=0.01)
    assert len(reclaimed) == 4
    assert reg.held_total == 0
    # recovered: a new request is admitted again
    await reg.reserve_connection("new", "qwen36-27b")
    assert reg.held_total == 1
