"""P5 scheduler FSM (machine B) tests — semaphore, idle-wait, composition with the gate."""

from __future__ import annotations

import pytest

from app.config import Settings
from app.modules.audit_sink import AuditSink
from app.modules.governance_gate import GovernanceGate
from app.modules.scheduler import FrozenEffortError, NoCapacityError, Scheduler
from app.schemas import Concern, Trigger


async def _sched(db, cap=1):
    audit = AuditSink(db, Settings(_env_file=None))
    gate = GovernanceGate(db, audit)
    sched = Scheduler(db, gate, audit, max_concurrent=cap)
    return gate, sched


def _c(e):
    return Concern(intent_thread="i", what_surfaced="s", intent_of_change="w", blocked_efforts=[e])


async def test_acquire_and_release(db):
    gate, sched = await _sched(db, cap=1)
    await gate.ensure_effort("e1", "e1")
    await sched.register("w1", "http://w1")
    inst = await sched.acquire("e1", "worker", "sess1")
    assert inst.id == "w1" and inst.sched_state == "computing"
    await sched.release("w1")
    snap = {i["id"]: i for i in await sched.snapshot()}
    assert snap["w1"]["state"] == "suspended"


async def test_acquire_prefers_session_affine_worker(db):
    """Workspace stickiness: a follow-up wake for the SAME session (next step / publish / re-engage)
    returns to the SAME worker that holds its workspace + parked `--session` — not a different, empty
    one. This is the fix for the publish landing on the wrong worker → 'finished but pushed no branch'
    (or a 409). A brand-new session still takes another free worker."""
    gate, sched = await _sched(db, cap=2)
    await gate.ensure_effort("e1", "e1")
    await gate.ensure_effort("e2", "e2")
    await sched.register("w1", "u1")
    await sched.register("w2", "u2")
    first = await sched.acquire("e1", "worker", "e1")     # binds session_id=e1 to some worker
    await sched.release(first.id)                          # suspended, KEEPS session_id=e1
    again = await sched.acquire("e1", "worker", "e1")      # affinity → the SAME worker
    assert again.id == first.id
    other = await sched.acquire("e2", "worker", "e2")      # different session → the other free worker
    assert other.id != first.id


async def test_cap_enforced_then_queue(db):
    gate, sched = await _sched(db, cap=1)
    await gate.ensure_effort("e1", "e1")
    await gate.ensure_effort("e2", "e2")
    await sched.register("w1", "u1")
    await sched.register("w2", "u2")
    await sched.acquire("e1", "worker", "s1")
    with pytest.raises(NoCapacityError):
        await sched.acquire("e2", "worker", "s2")  # cap=1 -> queue


async def test_frozen_effort_refused(db):
    gate, sched = await _sched(db, cap=2)
    await gate.ensure_effort("e1", "e1")
    await sched.register("w1", "u1")
    await gate.freeze("e1", Trigger.refusal, _c("e1"))
    with pytest.raises(FrozenEffortError):
        await sched.acquire("e1", "worker", "s1")  # composition rule: no compute while frozen


async def test_waiting_releases_slot_and_resumes(db):
    gate, sched = await _sched(db, cap=1)
    await gate.ensure_effort("e1", "e1")
    await gate.ensure_effort("e2", "e2")
    await sched.register("w1", "u1")
    await sched.register("w2", "u2")
    # w1 computes on e1, then goes waiting on e2's finish -> frees the single slot.
    await sched.acquire("e1", "worker", "s1")
    await sched.to_waiting("w1", waiting_on_effort="e2")
    # slot is free now: a second acquire succeeds under cap=1.
    inst = await sched.acquire("e2", "worker", "s2")
    assert inst.id == "w2"
    await sched.release("w2", suspend=False)  # free a slot again
    resumed = await sched.wake_finished("e2")
    assert "w1" in resumed


async def test_quarantine_excludes_worker_and_reroutes(db):
    """A wedged worker (409'd dispatch) is quarantined: freed to idle, its session/effort binding
    dropped, and NOT pickable — so a re-acquire routes the effort to a healthy worker instead of
    looping on the stuck one (the idle-GPU stuck-loop fix)."""
    from app.models import WorkerInstance
    gate, sched = await _sched(db, cap=2)
    await gate.ensure_effort("e1", "e1")
    await sched.register("w1", "u1")
    await sched.register("w2", "u2")
    first = await sched.acquire("e1", "worker", "e1")
    await sched.quarantine(first.id, seconds=300, reason="409 busy")
    async with sched.db.session_factory() as s:
        q = await s.get(WorkerInstance, first.id)
    assert q.sched_state == "idle" and q.session_id is None and q.quarantined_until is not None
    again = await sched.acquire("e1", "worker", "e1")     # skips the quarantined one
    assert again.id != first.id


async def test_reset_stale_lifts_quarantine_on_boot(db):
    """Boot is a clean slate: reset_stale lifts a stale quarantine so a bridge restart re-admits every
    worker (a genuinely-wedged one just re-quarantines on its next failed dispatch)."""
    gate, sched = await _sched(db, cap=1)
    await gate.ensure_effort("e1", "e1")
    await sched.register("w1", "u1")
    inst = await sched.acquire("e1", "worker", "e1")
    await sched.quarantine(inst.id, seconds=999, reason="x")
    with pytest.raises(NoCapacityError):                  # quarantined → no free worker
        await sched.acquire("e1", "worker", "e1")
    await sched.reset_stale()                             # boot lifts it
    resumed = await sched.acquire("e1", "worker", "e1")
    assert resumed.id == "w1"


async def test_enforce_freeze_forces_out_of_computing(db):
    gate, sched = await _sched(db, cap=2)
    await gate.ensure_effort("e1", "e1")
    await sched.register("w1", "u1")
    await sched.acquire("e1", "worker", "s1")
    await gate.freeze("e1", Trigger.deviation, _c("e1"))
    await sched.enforce_freeze("e1")
    snap = {i["id"]: i for i in await sched.snapshot()}
    assert snap["w1"]["state"] != "computing"


async def test_retire_leaves_no_assignment(db):
    gate, sched = await _sched(db, cap=1)
    await sched.register("w1", "u1")
    await sched.retire("w1")
    snap = {i["id"]: i for i in await sched.snapshot()}
    assert snap["w1"]["retired"] is True
    # a retired instance is not acquirable
    await gate.ensure_effort("e1", "e1")
    with pytest.raises(NoCapacityError):
        await sched.acquire("e1", "worker", "s1")


# ── LIVE 2026-07-05 15:48: a 5-effort re-engage burst double-booked BOTH workers ──
# acquire() was SELECT-then-UPDATE with awaits between; concurrent acquires all saw the same
# "idle" snapshot and last-write-wins bound one worker to several efforts — worker-1 accepted
# TWO tasks into ONE workspace (same repo), risking cross-effort `git add -A` contamination.
async def test_concurrent_acquires_never_double_book_a_worker(db):
    import asyncio

    gate, sched = await _sched(db, cap=4)                  # cap is NOT the constraint here
    for e in ("e1", "e2", "e3", "e4"):
        await gate.ensure_effort(e, e)
    await sched.register("w1", "u1")                       # ONE worker, four contenders

    async def grab(e):
        try:
            return (await sched.acquire(e, "worker", e)).id
        except NoCapacityError:
            return None

    got = await asyncio.gather(*(grab(e) for e in ("e1", "e2", "e3", "e4")))
    winners = [g for g in got if g]
    assert winners == ["w1"] , f"exactly one effort may hold w1, got {got}"
    snap = {i["id"]: i for i in await sched.snapshot()}
    assert snap["w1"]["state"] == "computing"


async def test_concurrent_acquires_spread_across_distinct_workers(db):
    import asyncio

    gate, sched = await _sched(db, cap=4)
    for e in ("e1", "e2", "e3", "e4", "e5"):
        await gate.ensure_effort(e, e)
    await sched.register("w1", "u1")
    await sched.register("w2", "u2")

    async def grab(e):
        try:
            return (await sched.acquire(e, "worker", e)).id
        except NoCapacityError:
            return None

    got = await asyncio.gather(*(grab(e) for e in ("e1", "e2", "e3", "e4", "e5")))
    winners = [g for g in got if g]
    assert sorted(winners) == ["w1", "w2"], \
        f"each free worker must be bound EXACTLY once, got {got}"
