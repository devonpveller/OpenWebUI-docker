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

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select

from ..db import Database, now_iso
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
        # Fired when a worker slot frees (release) — the orchestrator drains efforts parked on
        # `no_worker_slot`, so a slot-starved effort auto-runs the moment a worker is free. No-op
        # if unset. Precise (a slot genuinely freed), so it can't tight-loop the just-parked effort.
        self.on_release = None
        # Serializes ALLOCATION (acquire / wake_finished). The critical section is
        # count→select-free→bind with awaits in between; without this, a burst of concurrent
        # acquires (a multi-effort re-engage) all see the same "idle" snapshot and last-write-wins
        # binds ONE worker to SEVERAL efforts (live 2026-07-05: both workers double-booked;
        # worker-1 accepted two tasks into one workspace). Single-process bridge ⇒ an asyncio
        # lock is sufficient and exact.
        self._alloc_lock = asyncio.Lock()

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
        """Legacy shape: a bare CSV of daemon URLs, every one assumed little-coder.

        Kept because it is the documented operator surface (`AO_WORKER_INSTANCE_URLS`) and
        the tests that predate the registry use it. Prefer `register_pool`, which carries
        each instance's runner KIND alongside its address (DFU U4).
        """
        for i, url in enumerate(u.strip() for u in urls_csv.split(",") if u.strip()):
            await self.register(f"worker-{i + 1}", url)

    async def register_pool(self, pool: list[tuple[str, str, str]]) -> None:
        """Register `(instance_id, base_url, runner_kind)` triples from the shared runner
        registry (`app/modules/runners.py`).

        The kind is NOT stored on the row on purpose: it is a property of the ADDRESS, and
        the registry is its single source of truth. Persisting a copy would create a second
        one that drifts the first time an operator re-points a URL - the same class of
        defect as a config value duplicated in two languages, which is what the shared
        registry exists to end.
        """
        for instance_id, base_url, _kind in pool:
            await self.register(instance_id, base_url)

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
            # Boot is a clean slate: lift any stale health-quarantine so a bridge restart re-admits
            # every worker (a genuinely-wedged one simply re-quarantines on its next failed dispatch).
            quarantined = (
                await s.execute(
                    select(WorkerInstance).where(WorkerInstance.quarantined_until.is_not(None))
                )
            ).scalars().all()
            for q in quarantined:
                q.quarantined_until = None
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

    async def environment_down(self) -> bool:
        """P31 — a DETERMINISTIC environment-health signal: True when there are workers and EVERY
        non-retired one is currently health-quarantined (unreachable / 502-503 shed / 409-wedged).
        That is an ENVIRONMENT outage — inference or the whole worker fleet is down — NOT worker-slot
        contention: a busy-but-reachable worker is `computing`, never quarantined, so it never trips
        this. Callers (the stall watchdog) use it to WAIT-for-health instead of escalating a silent
        turn as a hung worker (an environment failure is not a worker failure). Returns False when no
        workers are registered (nothing to conclude) so this never masks a real config problem."""
        async with self.db.session_factory() as s:
            total = int((await s.execute(
                select(func.count()).where(WorkerInstance.retired.is_(False))
            )).scalar_one())
            if total == 0:
                return False
            reachable = int((await s.execute(
                select(func.count()).where(
                    WorkerInstance.retired.is_(False),
                    or_(
                        WorkerInstance.quarantined_until.is_(None),
                        WorkerInstance.quarantined_until <= now_iso(),
                    ),
                )
            )).scalar_one())
            return reachable == 0

    # ── acquire / release (the semaphore) ────────────────────────────────────
    async def acquire(self, effort_id: str, role: str, session_id: str) -> WorkerInstance:
        """Move a free instance to `computing` for an effort. Enforces the cap and
        the composition rule (no computing while frozen)."""
        if not await self.gate.can_dispatch(effort_id):
            raise FrozenEffortError(
                f"effort {effort_id} is frozen/killed — scheduler will not dispatch "
                f"(clear the gate first — governance §3.0 composition rule)"
            )
        async with self._alloc_lock, self.db.session_factory() as s:
            if await self._computing_count(s) >= self.max_concurrent:
                raise NoCapacityError("MAX_CONCURRENT_WORKERS reached — queue the effort")
            base_q = select(WorkerInstance).where(
                WorkerInstance.retired.is_(False),
                WorkerInstance.sched_state.in_([SCHED_IDLE, SCHED_SUSPENDED]),
                # Skip a quarantined (wedged/unreachable) worker until its back-off lapses — a stuck
                # daemon must not keep being picked (the infinite-409-retry bug).
                or_(
                    WorkerInstance.quarantined_until.is_(None),
                    WorkerInstance.quarantined_until <= now_iso(),
                ),
            )
            # AFFINITY (workspace stickiness): a follow-up wake for a session — the next plan
            # step, the publish, a PM re-engage — MUST return to the SAME worker that holds this
            # effort's workspace + parked `--session`. release() suspends but keeps session_id, so
            # that worker is reusable. Picking *any* free worker instead runs the wake against a
            # DIFFERENT / empty workspace → "finished but pushed no branch" (nothing to commit) or a
            # 409 if that worker is busy. Prefer the affine instance; fall back to any free one for a
            # brand-new session.
            inst = (
                await s.execute(base_q.where(WorkerInstance.session_id == session_id).limit(1))
            ).scalar_one_or_none()
            if inst is None:
                inst = (await s.execute(base_q.limit(1))).scalar_one_or_none()
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

    async def quarantine(self, instance_id: str, *, seconds: float, reason: str = "") -> None:
        """Mark a worker un-pickable for `seconds` because a dispatch to it failed (409 busy /
        unreachable). Frees its slot (→ idle) and DROPS its session/effort binding — a wedged worker's
        workspace + affinity are worthless, so the effort re-clones on a healthy worker. Self-healing:
        after the back-off lapses the worker is eligible again (a transient wedge recovers on its own);
        a genuinely-stuck one just re-quarantines on the next failed dispatch. NOT a governance freeze
        (that's the effort's gate) — this is pool-health only."""
        until = (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()
        async with self.db.session_factory() as s:
            inst = await s.get(WorkerInstance, instance_id)
            if inst is None:
                return
            inst.sched_state = SCHED_IDLE
            inst.effort_id = None
            inst.session_id = None
            inst.waiting_on_effort = None
            inst.quarantined_until = until
            await s.commit()
        await self.audit.log(
            "worker_quarantined", actor=instance_id,
            payload={"until": until, "reason": reason[:160]},
        )
        if self.on_release is not None:   # a slot freed → let parked work drain onto a healthy worker
            try:
                self.on_release()
            except Exception:  # noqa: BLE001
                pass

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
        if self.on_release is not None:   # a slot freed → let the orchestrator drain slot-parked work
            try:
                self.on_release()
            except Exception:  # noqa: BLE001 - a signal hiccup must never break release
                pass

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
        async with self._alloc_lock, self.db.session_factory() as s:
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
