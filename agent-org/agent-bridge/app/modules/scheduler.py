"""scheduler — the worker pool + concurrency + idle-wait FSM (machine B, PLAN §3.6).

Implements {computing, waiting, suspended} — the SCHEDULING states, explicitly NOT the
governance gate (`frozen` is machine A and lives in governance_gate). The bounded GPU
budget only works because a blocked agent RELEASES its slot (waiting), so a ~1-2-slot
budget runs a multi-agent org.

Concurrency is a STATIC, conservatively-sized semaphore (`MAX_CONCURRENT_WORKERS`): there
is no live GPU-occupancy signal (`/slots` is dead on llama-swap — C6), so the interactive
reserve is held by CONFIG, not by probing. We count `computing` instances against the cap.

Composition rule (governance §3.0): a `frozen` effort's agents may NEVER be moved to
`computing`. `acquire()` consults the gate's `can_dispatch` first and refuses — clearing
the gate (machine A) is what re-admits an effort to the scheduler (machine B).
"""

from __future__ import annotations

import logging

from sqlalchemy import func, select

from ..db import Database
from ..models import (
    SCHED_COMPUTING,
    SCHED_IDLE,
    SCHED_SUSPENDED,
    SCHED_WAITING,
    WorkerInstance,
)
from .audit_sink import AuditSink
from .governance_gate import GovernanceGate

log = logging.getLogger("agent_bridge.scheduler")


class FrozenEffortError(Exception):
    """Raised when the scheduler is asked to dispatch a frozen effort (composition rule)."""


class NoCapacityError(Exception):
    """Raised when the semaphore cap or the pool is exhausted (caller should queue)."""


class Scheduler:
    def __init__(
        self, db: Database, gate: GovernanceGate, audit: AuditSink, max_concurrent: int
    ) -> None:
        self.db = db
        self.gate = gate
        self.audit = audit
        self.max_concurrent = max_concurrent

    # ── pool registry ───────────────────────────────────────────────────────
    async def register(self, instance_id: str, base_url: str) -> None:
        async with self.db.session_factory() as s:
            row = await s.get(WorkerInstance, instance_id)
            if row is None:
                s.add(
                    WorkerInstance(
                        id=instance_id, base_url=base_url, sched_state=SCHED_IDLE
                    )
                )
            else:
                row.base_url = base_url
                row.retired = False
            await s.commit()

    async def register_from_urls(self, urls_csv: str) -> None:
        for i, url in enumerate(u.strip() for u in urls_csv.split(",") if u.strip()):
            await self.register(f"worker-{i + 1}", url)

    async def reset_stale(self) -> int:
        """On bridge startup, any `computing`/`waiting` instance is stale — the bridge lost its
        poll loop on the last shutdown, so nothing is actually driving that worker. Reset to
        idle so a crash/restart can't wedge the pool with a permanently-'busy' worker."""
        async with self.db.session_factory() as s:
            rows = (
                await s.execute(
                    select(WorkerInstance).where(
                        WorkerInstance.sched_state.in_([SCHED_COMPUTING, SCHED_WAITING])
                    )
                )
            ).scalars().all()
            for r in rows:
                r.sched_state = SCHED_IDLE
                r.effort_id = None
                r.waiting_on_effort = None
            await s.commit()
            return len(rows)

    async def _computing_count(self, s) -> int:
        return int(
            (
                await s.execute(
                    select(func.count()).where(
                        WorkerInstance.sched_state == SCHED_COMPUTING
                    )
                )
            ).scalar_one()
        )

    # ── acquire / release (the semaphore) ────────────────────────────────────
    async def acquire(self, effort_id: str, role: str, session_id: str) -> WorkerInstance:
        """Move a free instance to `computing` for an effort. Enforces the cap and
        the composition rule (no computing while frozen)."""
        if not await self.gate.can_dispatch(effort_id):
            raise FrozenEffortError(
                f"effort {effort_id} is frozen/killed — scheduler will not dispatch "
                f"(clear the gate first — governance §3.0 composition rule)"
            )
        async with self.db.session_factory() as s:
            if await self._computing_count(s) >= self.max_concurrent:
                raise NoCapacityError("MAX_CONCURRENT_WORKERS reached — queue the effort")
            inst = (
                await s.execute(
                    select(WorkerInstance)
                    .where(
                        WorkerInstance.retired.is_(False),
                        WorkerInstance.sched_state.in_([SCHED_IDLE, SCHED_SUSPENDED]),
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
            if inst is None:
                raise NoCapacityError("no free pool instance — queue the effort")
            inst.sched_state = SCHED_COMPUTING
            inst.effort_id = effort_id
            inst.role = role
            inst.session_id = session_id
            inst.waiting_on_effort = None
            await s.commit()
            await s.refresh(inst)
        await self.audit.log(
            "worker_acquire",
            effort_id=effort_id,
            actor=inst.id,
            payload={"role": role, "session_id": session_id},
        )
        return inst

    async def release(self, instance_id: str, *, suspend: bool = True) -> None:
        """Free a slot after computing. suspend=True parks the --session (no cost)."""
        async with self.db.session_factory() as s:
            inst = await s.get(WorkerInstance, instance_id)
            if inst is None:
                return
            inst.sched_state = SCHED_SUSPENDED if suspend else SCHED_IDLE
            inst.waiting_on_effort = None
            await s.commit()
        await self.audit.log("worker_release", actor=instance_id)

    # ── idle-wait (dependency DAG) ───────────────────────────────────────────
    async def to_waiting(self, instance_id: str, waiting_on_effort: str) -> None:
        """Yield the slot while blocked on another effort's `finish` (PLAN §3.6).
        This is ordinary idleness, NOT a safety pause."""
        async with self.db.session_factory() as s:
            inst = await s.get(WorkerInstance, instance_id)
            if inst is None:
                return
            inst.sched_state = SCHED_WAITING
            inst.waiting_on_effort = waiting_on_effort
            await s.commit()
        await self.audit.log(
            "worker_waiting", actor=instance_id, payload={"on": waiting_on_effort}
        )

    async def wake_finished(self, effort_id: str) -> list[str]:
        """A `finish` event on `effort_id`: return the ids of instances that were
        waiting on it and now have a free slot to resume. Slot-bounded."""
        resumed: list[str] = []
        async with self.db.session_factory() as s:
            waiters = (
                await s.execute(
                    select(WorkerInstance).where(
                        WorkerInstance.sched_state == SCHED_WAITING,
                        WorkerInstance.waiting_on_effort == effort_id,
                    )
                )
            ).scalars().all()
            for inst in waiters:
                if await self._computing_count(s) >= self.max_concurrent:
                    break  # slot-bounded: the rest stay waiting until a slot frees
                # Only resume if the waiter's own effort is still dispatchable.
                if inst.effort_id and not await self.gate.can_dispatch(inst.effort_id):
                    continue
                inst.sched_state = SCHED_COMPUTING
                inst.waiting_on_effort = None
                resumed.append(inst.id)
            await s.commit()
        for iid in resumed:
            await self.audit.log("worker_resumed", actor=iid, payload={"after": effort_id})
        return resumed

    # ── freeze composition: force frozen efforts' agents out of computing ────
    async def enforce_freeze(self, effort_id: str) -> None:
        """When the gate freezes an effort, its agents must release their slot
        (a frozen effort's agents are never `computing` — governance §3.0)."""
        async with self.db.session_factory() as s:
            insts = (
                await s.execute(
                    select(WorkerInstance).where(WorkerInstance.effort_id == effort_id)
                )
            ).scalars().all()
            for inst in insts:
                if inst.sched_state == SCHED_COMPUTING:
                    inst.sched_state = SCHED_SUSPENDED
            await s.commit()

    # ── retirement (§4.1/P5.8) ───────────────────────────────────────────────
    async def retire(self, instance_id: str, *, actor: str = "pm") -> None:
        async with self.db.session_factory() as s:
            inst = await s.get(WorkerInstance, instance_id)
            if inst is not None:
                inst.retired = True
                inst.sched_state = SCHED_IDLE
                inst.effort_id = None
                inst.session_id = None
                await s.commit()
        await self.audit.log("worker_retired", actor=actor, payload={"instance": instance_id})

    async def snapshot(self) -> list[dict]:
        async with self.db.session_factory() as s:
            rows = (await s.execute(select(WorkerInstance))).scalars().all()
        return [
            {
                "id": r.id,
                "state": r.sched_state,
                "effort_id": r.effort_id,
                "role": r.role,
                "waiting_on": r.waiting_on_effort,
                "retired": r.retired,
            }
            for r in rows
        ]
