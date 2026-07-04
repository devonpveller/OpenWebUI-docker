"""P1.0 reliable event delivery — idempotency + catch-up."""

from __future__ import annotations

import pytest

from app.adapters.chat import FakeChatAdapter
from app.modules.event_gateway import EventGateway


class RecordingChat(FakeChatAdapter):
    def __init__(self, catchup=None):
        super().__init__()
        self._catchup = catchup or []

    async def posts_since(self, channel_id, since_ms):
        return [p for p in self._catchup if p["channel_id"] == channel_id]


async def test_idempotent_dispatch(db):
    seen = []
    chat = FakeChatAdapter()
    gw = EventGateway(db, chat, lambda e: seen.append(e["id"]) or _noop())
    ev = {"id": "p1", "channel_id": "c1", "message": "hi", "is_bot": False, "ts": 5}
    assert await gw.dispatch(ev) is True
    assert await gw.dispatch(ev) is False   # duplicate -> no double-wake
    assert seen == ["p1"]


async def test_bot_posts_ignored(db):
    seen = []
    chat = FakeChatAdapter()
    gw = EventGateway(db, chat, lambda e: seen.append(e) or _noop())
    assert await gw.dispatch({"id": "b1", "is_bot": True, "ts": 1}) is False
    assert seen == []


async def test_catch_up_replays_missed(db):
    seen = []
    catchup = [{"id": "m1", "channel_id": "c1", "message": "missed", "is_bot": False, "ts": 9}]
    chat = RecordingChat(catchup)
    gw = EventGateway(db, chat, lambda e: seen.append(e["id"]) or _noop())
    gw.track_channel("c1")
    n = await gw.catch_up()
    assert n == 1 and seen == ["m1"]
    # a second catch-up does not replay the same event (cursor advanced + idempotent).
    assert await gw.catch_up() == 0


async def test_poison_event_dead_lettered_after_max_attempts(db):
    """A handler that always throws must not replay forever (the wedged-worker stuck-event loop). It
    is retried up to max_attempts, then DEAD-LETTERED: marked processed so future catch-ups dedupe
    it — the loop is bounded."""
    calls = []

    async def boom(e):
        calls.append(e["id"])
        raise RuntimeError("handler always fails")

    gw = EventGateway(db, FakeChatAdapter(), boom, max_attempts=3)
    ev = {"id": "poison", "channel_id": "c1", "message": "x", "is_bot": False, "ts": 1}
    for _ in range(2):                       # attempts 1-2 keep it unprocessed (re-raise → replay)
        with pytest.raises(RuntimeError):
            await gw.dispatch(ev)
    assert await gw.dispatch(ev) is False    # attempt 3 hits the cap → dead-lettered, no raise
    assert calls == ["poison", "poison", "poison"]
    assert await gw.dispatch(ev) is False    # now processed → deduped, handler not called again
    assert calls == ["poison", "poison", "poison"]


async def _noop():
    return None
