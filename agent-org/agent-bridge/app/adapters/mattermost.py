"""Mattermost ChatAdapter — WebSocket event bus + REST poster (PLAN §3.1, TOOLING §3.1).

Uses the raw Mattermost REST v4 API + the `/api/v4/websocket` event stream directly
(no heavyweight driver dependency). Reconnects with backoff; the event-gateway layer
above handles idempotency + REST catch-up, so this class only needs to (re)connect and
normalize events — it is NOT responsible for at-least-once delivery.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx
import websockets

log = logging.getLogger("agent_bridge.mattermost")


class MattermostAdapter:
    def __init__(self, base_url: str, bot_token: str, ws_url: str = "") -> None:
        self.base = base_url.rstrip("/")
        self.token = bot_token
        self.ws_url = ws_url or (
            self.base.replace("http://", "ws://").replace("https://", "wss://")
            + "/api/v4/websocket"
        )
        self._client = httpx.AsyncClient(
            base_url=f"{self.base}/api/v4",
            headers={"Authorization": f"Bearer {bot_token}"},
            timeout=30.0,
        )
        self._team_id: str | None = None
        self._me: str | None = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        me = (await self._client.get("/users/me")).json()
        self._me = me.get("id")
        teams = (await self._client.get("/users/me/teams")).json()
        if teams:
            self._team_id = teams[0]["id"]
        log.info("mattermost adapter connected as %s (team %s)", self._me, self._team_id)

    async def stop(self) -> None:
        self._stop.set()
        await self._client.aclose()

    async def post(
        self, channel_id: str, message: str, thread_id: str | None = None
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"channel_id": channel_id, "message": message}
        if thread_id:
            body["root_id"] = thread_id
        r = await self._client.post("/posts", json=body)
        r.raise_for_status()
        return r.json()

    async def ensure_channel(self, name: str) -> str:
        assert self._team_id, "team not resolved — call start() first"
        # Try to fetch by name; create if missing.
        r = await self._client.get(f"/teams/{self._team_id}/channels/name/{name}")
        if r.status_code == 200:
            return r.json()["id"]
        r = await self._client.post(
            "/channels",
            json={
                "team_id": self._team_id,
                "name": name,
                "display_name": name.replace("-", " ").title(),
                "type": "O",
            },
        )
        r.raise_for_status()
        return r.json()["id"]

    async def posts_since(self, channel_id: str, since_ms: int) -> list[dict[str, Any]]:
        r = await self._client.get(
            f"/channels/{channel_id}/posts", params={"since": since_ms}
        )
        r.raise_for_status()
        data = r.json()
        order = data.get("order", [])
        posts = data.get("posts", {})
        return [self._normalize(posts[pid]) for pid in reversed(order) if pid in posts]

    def _normalize(self, post: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": post.get("id"),
            "channel_id": post.get("channel_id"),
            "thread_id": post.get("root_id") or post.get("id"),
            "user_id": post.get("user_id"),
            "message": post.get("message", ""),
            "is_bot": bool(post.get("props", {}).get("from_bot"))
            or post.get("user_id") == self._me,
            "ts": post.get("create_at", 0),
        }

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                async with websockets.connect(self.ws_url, max_size=2**22) as ws:
                    # Mattermost auth challenge over the socket.
                    await ws.send(
                        json.dumps(
                            {
                                "seq": 1,
                                "action": "authentication_challenge",
                                "data": {"token": self.token},
                            }
                        )
                    )
                    backoff = 1.0
                    async for raw in ws:
                        msg = json.loads(raw)
                        if msg.get("event") != "posted":
                            continue
                        post = json.loads(msg["data"]["post"])
                        yield self._normalize(post)
            except Exception as exc:  # noqa: BLE001 - resilience is the point
                if self._stop.is_set():
                    break
                log.warning("mattermost WS dropped (%s); reconnecting in %.0fs", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)
