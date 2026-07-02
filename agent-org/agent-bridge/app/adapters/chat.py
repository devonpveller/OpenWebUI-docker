"""ChatAdapter — the platform seam (OD-3).

The bridge is built against this small interface so Mattermost is *not* load-bearing
in our code (Matrix/Zulip stay swappable — OUTLINE). The FakeChatAdapter makes the
whole gate/wake loop testable with zero infra.

An "event" is normalized to a dict with at least:
  {id, channel_id, thread_id, user_id, message, is_bot, ts}
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, Protocol


class ChatAdapter(Protocol):
    async def start(self) -> None: ...
    async def stop(self) -> None: ...

    async def post(
        self, channel_id: str, message: str, thread_id: str | None = None
    ) -> dict[str, Any]:
        """Post a message; returns the created post (incl. its id)."""
        ...

    async def ensure_channel(self, name: str) -> str:
        """Create-or-get a channel by name; returns its id."""
        ...

    async def posts_since(self, channel_id: str, since_ms: int) -> list[dict[str, Any]]:
        """REST catch-up: posts in a channel after `since_ms` (PLAN §3.1.1)."""
        ...

    def events(self) -> AsyncIterator[dict[str, Any]]:
        """Async stream of normalized inbound events (WebSocket)."""
        ...


class FakeChatAdapter:
    """In-memory adapter for tests and the `chat_adapter=fake` dev mode.

    Deterministic: no network, no clock skew. `inject()` pushes an inbound event;
    `posted` records everything the bridge posts so tests can assert on it.
    """

    def __init__(self) -> None:
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.posted: list[dict[str, Any]] = []
        self.channels: dict[str, str] = {}
        self.username = "bot-pm"
        self._post_seq = 0
        self._closed = False

    async def start(self) -> None:  # pragma: no cover - trivial
        self._closed = False

    async def stop(self) -> None:  # pragma: no cover - trivial
        self._closed = True

    async def post(
        self, channel_id: str, message: str, thread_id: str | None = None
    ) -> dict[str, Any]:
        self._post_seq += 1
        post = {
            "id": f"post-{self._post_seq}",
            "channel_id": channel_id,
            "thread_id": thread_id,
            "message": message,
            "is_bot": True,
        }
        self.posted.append(post)
        return post

    async def ensure_channel(self, name: str) -> str:
        cid = self.channels.get(name)
        if cid is None:
            cid = f"chan-{name}"
            self.channels[name] = cid
        return cid

    async def posts_since(self, channel_id: str, since_ms: int) -> list[dict[str, Any]]:
        return []  # tests drive delivery via inject()

    async def inject(self, event: dict[str, Any]) -> None:
        await self._queue.put(event)

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        while not self._closed:
            ev = await self._queue.get()
            yield ev


# Type alias for the bridge's event handler.
EventHandler = Callable[[dict[str, Any]], Awaitable[None]]
