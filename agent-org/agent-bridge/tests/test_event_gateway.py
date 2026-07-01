"""P1.0 reliable event delivery — idempotency + catch-up."""

from __future__ import annotations

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


async def _noop():
    return None
