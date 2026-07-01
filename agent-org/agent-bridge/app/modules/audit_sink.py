"""audit-sink — append-only event log + Open Brain mirror (governance §5/§6, P6.1/P6.2).

Every wake, hand-off, CONCERN, decision, goal/rule change and review verdict is
persisted here with versions so the log *replays* who-woke-whom and every gate decision
(P6.1 done-when). The safety-critical subset is mirrored to Open Brain for durable,
queryable provenance (P6.2). This module is write-only from everyone else's view.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from sqlalchemy import select, update

from ..config import Settings
from ..db import Database
from ..models import Event

log = logging.getLogger("agent_bridge.audit")

# Event kinds that get mirrored to Open Brain (critical hand-offs + decisions, §5).
_MIRROR_KINDS = {
    "concern_posted",
    "operator_decision",
    "effort_frozen",
    "effort_cleared",
    "kill_switch",
    "floor_change",
    "role_type_approved",
    "pattern_proposed",
}


class AuditSink:
    def __init__(self, db: Database, settings: Settings) -> None:
        self.db = db
        self.s = settings

    async def log(
        self,
        kind: str,
        *,
        effort_id: str | None = None,
        actor: str | None = None,
        rule_version: int | None = None,
        goal_version: int | None = None,
        payload: dict[str, Any] | None = None,
    ) -> int:
        async with self.db.session_factory() as s:
            ev = Event(
                kind=kind,
                effort_id=effort_id,
                actor=actor,
                rule_version=rule_version,
                goal_version=goal_version,
                payload=payload or {},
            )
            s.add(ev)
            await s.commit()
            await s.refresh(ev)
            event_id = ev.id
        if self.s.openbrain_mirror_enabled and kind in _MIRROR_KINDS:
            await self._mirror(event_id, kind, effort_id, payload or {})
        return event_id

    async def _mirror(
        self, event_id: int, kind: str, effort_id: str | None, payload: dict[str, Any]
    ) -> None:
        """Capture to Open Brain via the cloud gateway's capture tool (best-effort)."""
        try:
            text = f"[agent-org:{kind}] effort={effort_id} :: {payload}"
            async with httpx.AsyncClient(timeout=15.0) as c:
                await c.post(
                    f"{self.s.openbrain_url.rstrip('/')}/capture_thought",
                    headers={"x-brain-key": self.s.openbrain_key},
                    json={"content": text, "metadata": {"source": "agent-org", "kind": kind}},
                )
            async with self.db.session_factory() as s:
                await s.execute(
                    update(Event).where(Event.id == event_id).values(mirrored=True)
                )
                await s.commit()
        except Exception as exc:  # noqa: BLE001 - mirror is best-effort, never blocks
            log.warning("open-brain mirror failed for event %s: %s", event_id, exc)

    async def replay(self, effort_id: str | None = None) -> list[dict[str, Any]]:
        async with self.db.session_factory() as s:
            q = select(Event).order_by(Event.id)
            if effort_id:
                q = q.where(Event.effort_id == effort_id)
            rows = (await s.execute(q)).scalars().all()
        return [
            {
                "id": r.id,
                "ts": str(r.ts),
                "kind": r.kind,
                "effort_id": r.effort_id,
                "actor": r.actor,
                "rule_version": r.rule_version,
                "goal_version": r.goal_version,
                "payload": r.payload,
            }
            for r in rows
        ]
