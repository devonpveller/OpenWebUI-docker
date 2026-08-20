"""event-gateway — reliable wake bus (PLAN §3.1.1, P1.0).

The whole system hinges on "wake on @mention," so this is built for at-least-once
delivery WITH idempotency, not best-effort:
  - Idempotency: dedupe on the post id; a redelivered event never double-wakes/spawns.
  - Reconnect catch-up: Mattermost's WS does NOT replay missed events. On (re)connect or
    after a bridge restart, poll the REST API for posts since the last-processed timestamp
    per active channel, replay through the idempotent path, THEN resume the WS.
  - A wake that still can't be delivered past a bound is a §3 trigger (handled upstream),
    not a silent stall.

The gateway is transport-agnostic (works with any ChatAdapter) and delegates business
logic to an injected async `handler(event)`.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import select

from ..db import Database
from ..models import ChannelCursor, ProcessedEvent

log = logging.getLogger("agent_bridge.event_gateway")

Handler = Callable[[dict[str, Any]], Awaitable[None]]


class EventGateway:
    def __init__(self, db: Database, chat, handler: Handler, *, max_attempts: int = 5) -> None:
        self.db = db
        self.chat = chat
        self.handler = handler
        self._task: asyncio.Task | None = None
        self._active_channels: set[str] = set()
        # A poison event (handler always throws) is kept unprocessed and replays on every catch-up —
        # an infinite loop (the wedged-worker stuck-event bug). Cap the retries: after `max_attempts`
        # handler failures, DEAD-LETTER it (mark processed so it stops replaying) + log loudly. In-
        # memory per bridge session (a restart is a fresh, legitimate retry).
        self._max_attempts = max(1, max_attempts)
        self._attempts: dict[str, int] = {}

    def track_channel(self, channel_id: str) -> None:
        self._active_channels.add(channel_id)

    async def _already_processed(self, event_id: str) -> bool:
        async with self.db.session_factory() as s:
            return (await s.get(ProcessedEvent, event_id)) is not None

    async def _mark_processed(self, event_id: str, channel_id: str | None, ts: int) -> None:
        async with self.db.session_factory() as s:
            if await s.get(ProcessedEvent, event_id) is None:
                s.add(ProcessedEvent(event_id=event_id))
            if channel_id:
                cur = await s.get(ChannelCursor, channel_id)
                if cur is None:
                    s.add(ChannelCursor(channel_id=channel_id, last_ts=ts))
                elif ts > cur.last_ts:
                    cur.last_ts = ts
            await s.commit()

    async def dispatch(self, event: dict[str, Any]) -> bool:
        """Idempotent dispatch. Returns True if handled, False if a duplicate.
        Bot's own posts are ignored (no self-wake loops)."""
        event_id = event.get("id")
        if not event_id:
            return False
        if event.get("is_bot"):
            return False
        if await self._already_processed(event_id):
            log.debug("duplicate event %s ignored", event_id)
            return False
        try:
            await self.handler(event)
        except Exception as exc:  # noqa: BLE001
            n = self._attempts.get(event_id, 0) + 1
            self._attempts[event_id] = n
            if n >= self._max_attempts:
                # DEAD-LETTER: mark processed so this poison event stops replaying forever. Loud log
                # (an operator-visible escalation would be ideal; the handler owns that surface).
                log.error("event %s DEAD-LETTERED after %d failed handler attempts: %s",
                          event_id, n, exc)
                await self._mark_processed(event_id, event.get("channel_id"), int(event.get("ts", 0)))
                self._attempts.pop(event_id, None)
                return False
            # keep unprocessed → replays on the next catch-up (bounded by the cap above)
            raise
        await self._mark_processed(event_id, event.get("channel_id"), int(event.get("ts", 0)))
        self._attempts.pop(event_id, None)
        return True

    async def catch_up(self) -> int:
        """Replay missed posts per active channel since the last-processed ts. Each event is
        isolated: one bad event must not abort the whole catch-up (mirrors the live loop)."""
        replayed = 0
        channels = list(self._active_channels)
        log.info("catch-up: scanning %d channel(s): %s", len(channels), channels)
        for channel_id in channels:
            async with self.db.session_factory() as s:
                cur = await s.get(ChannelCursor, channel_id)
                since = cur.last_ts if cur else 0
            try:
                posts = await self.chat.posts_since(channel_id, since)
            except Exception as exc:  # noqa: BLE001
                log.warning("catch-up for %s failed: %s", channel_id, exc)
                continue
            log.info("catch-up: %s -> %d post(s) since %s", channel_id, len(posts), since)
            for post in posts:
                try:
                    if await self.dispatch(post):
                        replayed += 1
                except Exception as exc:  # noqa: BLE001 - one bad event can't abort catch-up
                    log.exception("catch-up dispatch error on %s: %s", post.get("id"), exc)
        log.info("catch-up replayed %d event(s)", replayed)
        return replayed

    async def run(self) -> None:
        """Catch up, then consume the live WS stream through the idempotent path. A failure in
        catch-up must NOT prevent the live WS loop from starting."""
        try:
            await self.catch_up()
        except Exception as exc:  # noqa: BLE001
            log.exception("catch-up failed (continuing to live WS): %s", exc)
        log.info("event-gateway: entering live WS loop")
        async for event in self.chat.events():
            try:
                await self.dispatch(event)
            except Exception as exc:  # noqa: BLE001 - never let one bad event kill the loop
                log.exception("event handler error (event kept unprocessed for retry): %s", exc)

    def start(self) -> asyncio.Task:
        self._task = asyncio.create_task(self.run(), name="event-gateway")
        return self._task

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
