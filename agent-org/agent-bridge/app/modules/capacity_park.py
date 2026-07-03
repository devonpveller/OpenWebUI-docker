"""capacity-park — the DB-backed store of efforts parked on inference backpressure.

When the shared single-GPU llm-queue sheds an orchestration step (429/503), the effort is PARKED
here instead of failed, and auto-resumed when capacity returns (the orchestrator owns the resume
action + the capacity-recovered event; this module owns only the durable storage). Pure CRUD, so it
is trivially testable and the resume policy stays in one place.

This is machine B (`suspended`, reason=inference_backpressure) — NOT a governance freeze (machine A).
It mirrors the scheduler's existing park/resume shape (`to_waiting`/`wake_finished`) for a second
blocker type: waiting on GPU capacity instead of on a dependency effort's finish.
"""

from __future__ import annotations

import json
import logging

from sqlalchemy import select

from ..db import Database, now_iso
from ..models import ParkedEffort
from .audit_sink import AuditSink

log = logging.getLogger("agent_bridge.capacity_park")


class ParkStore:
    def __init__(self, db: Database, audit: AuditSink) -> None:
        self.db = db
        self.audit = audit

    async def park(
        self, effort_id: str, *, stage: str, channel_id: str | None, root_post_id: str | None,
        request: str, plan_steps: list[str] | None, from_step: int, mgmt_thread: str | None,
    ) -> None:
        """Park (or refresh) an effort with its resume token. PRESERVES the existing attempt count
        (the drain loop owns bumping it) so a re-park after a failed resume doesn't reset the
        starvation clock; a fresh park after real progress (unpark cleared the row) starts at 0."""
        async with self.db.session_factory() as s:
            row = await s.get(ParkedEffort, effort_id)
            steps_json = json.dumps(plan_steps) if plan_steps else None
            first = row is None
            if row is None:
                row = ParkedEffort(
                    effort_id=effort_id, stage=stage, channel_id=channel_id,
                    root_post_id=root_post_id, request=request, plan_steps_json=steps_json,
                    from_step=from_step, mgmt_thread=mgmt_thread, attempts=0, parked_at=now_iso(),
                )
                s.add(row)
            else:  # re-park at (possibly) a new resume point; keep attempts + original parked_at
                row.stage, row.channel_id, row.root_post_id = stage, channel_id, root_post_id
                row.request, row.plan_steps_json, row.from_step = request, steps_json, from_step
                row.mgmt_thread = mgmt_thread
            await s.commit()
        if first:
            await self.audit.log(
                "effort_parked_backpressure", effort_id=effort_id,
                payload={"stage": stage, "from_step": from_step},
            )

    async def bump_attempts(self, effort_id: str) -> int:
        """Increment + return the resume-attempt count (called once per drain cycle). 0 if gone."""
        async with self.db.session_factory() as s:
            row = await s.get(ParkedEffort, effort_id)
            if row is None:
                return 0
            row.attempts += 1
            n = row.attempts
            await s.commit()
        return n

    async def unpark(self, effort_id: str) -> None:
        async with self.db.session_factory() as s:
            row = await s.get(ParkedEffort, effort_id)
            if row is not None:
                await s.delete(row)
                await s.commit()

    async def is_parked(self, effort_id: str) -> bool:
        async with self.db.session_factory() as s:
            return (await s.get(ParkedEffort, effort_id)) is not None

    async def count(self) -> int:
        async with self.db.session_factory() as s:
            return len((await s.execute(select(ParkedEffort.effort_id))).scalars().all())

    async def oldest(self) -> dict | None:
        """The longest-parked effort's resume token (FIFO drain), or None if the park is empty."""
        async with self.db.session_factory() as s:
            row = (
                await s.execute(select(ParkedEffort).order_by(ParkedEffort.parked_at).limit(1))
            ).scalar_one_or_none()
            return _token(row) if row else None

    async def all(self) -> list[dict]:
        async with self.db.session_factory() as s:
            rows = (
                await s.execute(select(ParkedEffort).order_by(ParkedEffort.parked_at))
            ).scalars().all()
        return [_token(r) for r in rows]


def _token(r: ParkedEffort) -> dict:
    return {
        "effort_id": r.effort_id, "stage": r.stage, "channel_id": r.channel_id,
        "root_post_id": r.root_post_id, "request": r.request,
        "plan_steps": json.loads(r.plan_steps_json) if r.plan_steps_json else None,
        "from_step": r.from_step, "mgmt_thread": r.mgmt_thread, "attempts": r.attempts,
        "parked_at": r.parked_at,
    }
