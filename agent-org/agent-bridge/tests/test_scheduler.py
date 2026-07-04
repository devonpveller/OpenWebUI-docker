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
