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
    def __init__(
        self, base_url: str, bot_token: str, ws_url: str = "", site_url: str = ""
    ) -> None:
        self.base = base_url.rstrip("/")
        self.token = bot_token
        # OPERATOR-FACING URL (the tailnet serve) for building clickable permalinks — distinct from
        # `base` (the internal address the bridge connects to). Empty → permalink() returns None.
        self._site_url = site_url.rstrip("/")
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
        self._team_name: str | None = None   # the team's URL slug — permalinks use the NAME, not the id
        self._me: str | None = None
        self.username: str | None = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        me = (await self._client.get("/users/me")).json()
        self._me = me.get("id")
        self.username = me.get("username")
        # Best-effort at boot; retried lazily so the bridge self-heals once the operator
        # adds the bot to a TEAM (channel membership alone is not enough in Mattermost).
        await self._resolve_team()
        log.info(
            "mattermost adapter connected as %s (@%s, team %s)",
            self._me, self.username, self._team_id or "UNRESOLVED — add the bot to a team",
        )

    async def _resolve_team(self) -> str | None:
        try:
            teams = (await self._client.get("/users/me/teams")).json()
        except Exception:  # noqa: BLE001
            teams = []
        if isinstance(teams, list) and teams:
            self._team_id = teams[0]["id"]
            self._team_name = teams[0].get("name")   # URL slug for permalinks
        return self._team_id

    def permalink(self, post_id: str) -> str | None:
        """A clickable Mattermost permalink to a post/thread: `<site_url>/<team-name>/pl/<post_id>`.
        Returns None (caller degrades to a plain id) when the operator-facing site URL isn't
        configured or the team hasn't resolved yet — never raises, never blocks the dispatch."""
        if not self._site_url or not self._team_name or not post_id:
            return None
        return f"{self._site_url}/{self._team_name}/pl/{post_id}"

    async def _ensure_team(self) -> str:
        """Return the team id, re-resolving if it wasn't available at boot. Raises a clear,
        actionable error (not an AssertionError) if the bot still isn't on any team."""
        if self._team_id:
            return self._team_id
        await self._resolve_team()
        if not self._team_id:
            raise RuntimeError(
                "the bot account is not a member of any Mattermost TEAM yet — add the bot to "
                "your team (System Console -> User Management, or the team's Manage Members -> "
                "Add), not just to the #mgmt channel. The bridge retries automatically."
            )
        return self._team_id

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

    async def update_post(self, post_id: str, message: str) -> dict[str, Any]:
        # Mattermost PUT /posts/{id} full-updates; message + id is enough to edit text (CM.6).
        r = await self._client.put(f"/posts/{post_id}", json={"id": post_id, "message": message})
        r.raise_for_status()
        return r.json()

    async def ensure_channel(self, name: str) -> str:
        team_id = await self._ensure_team()
        # 1) Exact URL-slug match.
        r = await self._client.get(f"/teams/{team_id}/channels/name/{name}")
        if r.status_code == 200:
            return r.json()["id"]
        # 2) Fallback: match by DISPLAY name among the channels the bot is a member of. The
        #    operator usually types a display name (e.g. "mgmt") whose URL slug differs
        #    (e.g. "management") — resolve to the existing channel rather than creating a dup.
        mine = await self._client.get(f"/users/me/teams/{team_id}/channels")
        if mine.status_code == 200:
            for ch in mine.json():
                if name.lower() in (ch.get("display_name", "").lower(), ch.get("name", "").lower()):
                    return ch["id"]
        # 3) Last resort: create it (used for #effort-<name> channels the bridge owns).
        r = await self._client.post(
            "/channels",
            json={
                "team_id": team_id,
                "name": name,
                "display_name": name.replace("-", " ").title(),
                "type": "O",
            },
        )
        if r.status_code >= 400:
            raise RuntimeError(
                f"could not find or create channel {name!r} on team {team_id}: "
                f"HTTP {r.status_code} {r.text[:200]}"
            )
        return r.json()["id"]

    async def add_member(self, channel_id: str, user_id: str) -> None:
        try:
            await self._client.post(
                f"/channels/{channel_id}/members", json={"user_id": user_id}
            )
        except Exception as exc:  # noqa: BLE001 - already a member / benign
            log.debug("add_member(%s,%s): %s", channel_id, user_id, exc)

    async def posts_since(self, channel_id: str, since_ms: int) -> list[dict[str, Any]]:
        # Mattermost's `?since=0` returns nothing (it expects a real ms timestamp), so on the
        # first connect (no cursor yet) fetch the recent page instead — the idempotency ledger
        # keeps this from re-processing anything already handled.
        params: dict[str, Any] = {"since": since_ms} if since_ms and since_ms > 0 else {"per_page": 30}
        r = await self._client.get(f"/channels/{channel_id}/posts", params=params)
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
            # System posts (joins/adds/etc.) carry a non-empty `type` like "system_join_channel"
            # — the bridge skips them so they don't trigger a PO/worker call.
            "type": post.get("type", ""),
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
