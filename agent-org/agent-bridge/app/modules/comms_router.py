"""comms-router — the deterministic *intent → destination* primitive (COMMS-MODEL §2).

Every message the bridge emits is routed by **audience × intent**, not by vibe. This module
owns the §2 routing table so no other module chooses a channel inline — "coordination lives in
the bridge" (governance §3.5) taken to its literal conclusion: *where* each message lands is a
pure function of its intent.

The table (COMMS-MODEL §2):

    intent            → destination
    ────────────────────────────────────────────────────────────
    operator_reply    → #mgmt                      (you ⇄ PO steering)
    concern           → #mgmt                      (decisions are MADE in private)
    decision          → #mgmt                      (the decision RECORD)
    effort_dispatch   → effort thread              (in #proj-<slug>)
    worker_activity   → effort thread
    escalation        → effort thread (@mention PM) (lateral raise, routed up, §4.8)
    closure           → effort thread              ("bring the audience back down", §3 rule 3)
    suggestion        → #suggestions               (learning loop, §6)
    incident          → #incidents                 (wake-storm/undeliverable/crash, §5 caps)

`resolve()` returns the destination; `post()` resolves-and-posts. Intents that ALSO echo
elsewhere (a concern that links its thread; a decision that brings the audience back down) are
composed by the caller issuing a second `post()` with the `closure` intent — keeping `resolve`
a pure single-destination function (its unit-test contract).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from enum import Enum

log = logging.getLogger("agent_bridge.comms")

# Resolver seams (injected) so this module doesn't import the orchestrator/router (no cycles).
MgmtResolver = Callable[[], Awaitable[str | None]]
EffortThreadResolver = Callable[[str], Awaitable[tuple[str, str] | None]]
ChannelTracker = Callable[[str], None]


class Intent(str, Enum):
    operator_reply = "operator_reply"     # PO's conversational reply / plan present → #mgmt
    effort_dispatch = "effort_dispatch"   # "worker dispatched…" → effort thread
    worker_activity = "worker_activity"   # command stream / answer → effort thread
    escalation = "escalation"             # lateral block/concern raised, routed up → effort thread
    concern = "concern"                   # a CONCERN needing a decision → #mgmt
    decision = "decision"                 # operator approve/modify/abort record → #mgmt
    suggestion = "suggestion"             # worker suggestion → #suggestions
    incident = "incident"                 # operational event → #incidents
    closure = "closure"                   # decision echoed back down → effort thread


# Which surface each intent lands on (the §2 table, as sets).
_MGMT_INTENTS = {Intent.operator_reply, Intent.concern, Intent.decision}
_THREAD_INTENTS = {
    Intent.effort_dispatch,
    Intent.worker_activity,
    Intent.escalation,
    Intent.closure,
}


class CommsRouter:
    def __init__(
        self,
        chat,  # ChatAdapter
        settings,
        *,
        mgmt_resolver: MgmtResolver,
        effort_thread_resolver: EffortThreadResolver,
        on_channel: ChannelTracker | None = None,
    ) -> None:
        self.chat = chat
        self.s = settings
        self._mgmt = mgmt_resolver
        self._effort_thread = effort_thread_resolver
        self._on_channel = on_channel
        self._fn_cache: dict[str, str] = {}

    # ── the routing table (§2) ───────────────────────────────────────────────
    async def resolve(
        self, intent: Intent, *, effort_id: str | None = None
    ) -> tuple[str | None, str | None]:
        """(channel_id, thread_id|None) per the §2 table. Pure single-destination — a caller
        that wants the secondary echo (e.g. closure after a decision) issues a second call."""
        if intent in _MGMT_INTENTS:
            return await self._mgmt(), None
        if intent in _THREAD_INTENTS:
            if not effort_id:
                raise ValueError(f"intent {intent.value} requires effort_id")
            loc = await self._effort_thread(effort_id)
            if loc is None:
                # Thread unknown (effort opened before the taxonomy change / race): fail toward
                # #mgmt so the message is SEEN, never silently dropped (observability = safety).
                log.warning("comms: no thread for effort %s — routing %s to #mgmt", effort_id, intent.value)
                return await self._mgmt(), None
            channel_id, root_post_id = loc
            return channel_id, root_post_id
        if intent is Intent.suggestion:
            return await self._function_channel(self.s.suggestions_channel), None
        if intent is Intent.incident:
            return await self._function_channel(self.s.incidents_channel), None
        raise ValueError(f"unknown intent {intent!r}")

    async def post(
        self, intent: Intent, message: str, *, effort_id: str | None = None,
        thread_id: str | None = None,
    ) -> dict | None:
        """Resolve + post. Returns the created post (or None if no destination is reachable —
        e.g. #mgmt not yet resolvable because the bot isn't on a team). `thread_id` overrides the
        resolved thread — used to keep #mgmt replies/summaries IN the operator's conversation
        thread instead of scattering them across new top-level posts."""
        channel_id, resolved_thread = await self.resolve(intent, effort_id=effort_id)
        thread = thread_id if thread_id is not None else resolved_thread
        if not channel_id:
            log.warning("comms: no destination for intent=%s effort=%s — dropped", intent.value, effort_id)
            return None
        return await self.chat.post(channel_id, message, thread_id=thread)

    # ── function channels (#incidents / #suggestions) ────────────────────────
    async def ensure_function_channels(self) -> list[str]:
        """Create-or-get the permanent function channels at boot (CM.5). Returns their ids."""
        return [
            await self._function_channel(self.s.incidents_channel),
            await self._function_channel(self.s.suggestions_channel),
        ]

    async def _function_channel(self, name: str) -> str | None:
        cid = self._fn_cache.get(name)
        if cid is not None:
            return cid
        try:
            cid = await self.chat.ensure_channel(name)
        except Exception as exc:  # noqa: BLE001 - platform not ready (no team yet); retry later
            log.warning("comms: function channel %r not ready: %s", name, exc)
            return None
        self._fn_cache[name] = cid
        if self._on_channel:
            self._on_channel(cid)
        return cid
