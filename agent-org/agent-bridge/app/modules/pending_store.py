"""pending-store — the DB-backed store of decisions awaiting the operator's `approve <id>`.

The orchestrator holds proposals (lifecycle plans, capability actions, Stage-3 effort plans) in
in-memory dicts so they resolve fast. But an in-memory-only proposal is LOST on a bridge restart —
the operator rebuilds/bounces the container and the hard gate they were about to clear silently
vanishes (`no id's in the chat`). That violates the fail-safe posture (§3): a pending human decision
must survive a bounce. This module owns only the durable mirror; the orchestrator owns the semantics
(propose → save; approve/abort → delete; boot → rehydrate). Pure CRUD, trivially testable.

Mirrors `ParkStore` (capacity_park) — same shape, second kind of durable orchestrator state."""

from __future__ import annotations

import logging

from sqlalchemy import select

from ..db import Database
from ..models import PendingApproval
from .audit_sink import AuditSink

log = logging.getLogger("agent_bridge.pending_store")


class PendingStore:
    def __init__(self, db: Database, audit: AuditSink) -> None:
        self.db = db
        self.audit = audit

    async def save(self, pid: str, kind: str, payload: dict) -> None:
        """Persist (or refresh) a pending proposal. Upsert so a re-draft under the same id replaces
        the stored payload. `payload` MUST already be JSON-safe (the caller dumps any pydantic plan)."""
        async with self.db.session_factory() as s:
            row = await s.get(PendingApproval, pid)
            if row is None:
                s.add(PendingApproval(id=pid, kind=kind, payload=payload))
            else:
                row.kind, row.payload = kind, payload
            await s.commit()

    async def delete(self, pid: str) -> None:
        """Remove a proposal the instant it's decided (approve/abort). Idempotent — no-op if absent."""
        async with self.db.session_factory() as s:
            row = await s.get(PendingApproval, pid)
            if row is not None:
                await s.delete(row)
                await s.commit()

    async def all(self) -> list[dict]:
        """Every persisted pending proposal (oldest first) for rehydration on boot."""
        async with self.db.session_factory() as s:
            rows = (
                await s.execute(select(PendingApproval).order_by(PendingApproval.created_at))
            ).scalars().all()
        return [{"id": r.id, "kind": r.kind, "payload": r.payload} for r in rows]
