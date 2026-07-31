"""Capacity park-and-resume: an orchestration step shed by inference backpressure is PARKED
(machine B suspended, DB-backed) instead of failed, and auto-resumed when capacity returns —
so a research/ingestion batch saturating the GPU can't stall the whole sequence. Fakes only."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select

from app.models import WorkerInstance
from app.adapters.chat import FakeChatAdapter
from app.config import Settings
from app.db import Database
from app.modules.audit_sink import AuditSink
from app.modules.capacity_park import ParkStore
from app.modules.model_router import FakeModelClient
from app.orchestrator import Orchestrator
from app.schemas import OperatorIntent, ReadinessVerdict
from app.worker.harness import FakeHarness

ROOT = Path(__file__).resolve().parents[1]

SHED = "ServiceUnavailableError: llm-queue at hard connection cap (128); queue_connections_exhausted"


async def _orch(db_url, *, harness=None, worker_urls="http://w1:8090", max_workers=1, **over):
    settings = Settings(
        _env_file=None, chat_adapter="fake",
        profiles_dir=str(ROOT / "profiles"), charters_dir=str(ROOT / "charters"),
        floor_dir=str(ROOT / "floor"), worker_instance_urls=worker_urls,
        max_concurrent_workers=max_workers, database_url=db_url, project_survey_enabled=False,
        review_mode="off", plan_approval="off",
        model_backpressure_retries=2, model_backpressure_base_delay_s=0.0,
        model_backpressure_max_delay_s=0.0, **over,
    )
    db = Database(db_url)
    orch = Orchestrator(settings, db, FakeChatAdapter(),
                        model_client=FakeModelClient(), harness=harness or FakeHarness())
    await orch.setup()
    return orch, orch.chat, orch.harness, db


async def _drain_bg(orch):
    # Resume spawns nested background tasks (drain → _intake_or_dispatch → delegate), so keep
    # gathering until the tree is empty (a single gather misses tasks spawned mid-await).
    for _ in range(12):
        if not orch._bg_tasks:
            return
        await asyncio.gather(*list(orch._bg_tasks), return_exceptions=True)


# ── ParkStore unit ───────────────────────────────────────────────────────────
async def test_parkstore_crud_and_attempts(db, settings):
    store = ParkStore(db, AuditSink(db, settings))
    await store.park("e1", stage="delegate", channel_id="c", root_post_id="r", request="do x",
                     plan_steps=["a", "b"], from_step=2, mgmt_thread="t")
    assert await store.is_parked("e1")
    assert await store.count() == 1
    tok = await store.oldest()
    assert tok["effort_id"] == "e1" and tok["stage"] == "delegate" and tok["from_step"] == 2
    assert tok["plan_steps"] == ["a", "b"]
    # re-park preserves attempts; the drain loop owns bumping
    assert await store.bump_attempts("e1") == 1
    await store.park("e1", stage="delegate", channel_id="c", root_post_id="r", request="do x",
                     plan_steps=["a", "b"], from_step=2, mgmt_thread="t")
    assert (await store.oldest())["attempts"] == 1
    await store.unpark("e1")
    assert not await store.is_parked("e1")


# ── intake shed → park; capacity back → resume + dispatch ────────────────────
async def test_intake_shed_parks_then_resumes(db_url):
    orch, chat, harness, db = await _orch(db_url)
    try:
        eid, chan, root = await orch.router.open_effort("feat")
        orch.models._client.queue_raise(Exception(SHED), times=10)   # readiness gate sheds
        await orch._intake_or_dispatch(eid, chan, root, "add a thing", reply_prefix="ok",
                                       mgmt_channel=await orch.mgmt_channel_id())
        assert await orch.parks.is_parked(eid)                       # parked, not failed
        assert (await orch.parks.oldest())["stage"] == "intake"
        assert len(harness.wakes) == 0                               # nothing dispatched
        assert any("Paused" in p["message"] for p in chat.posted)

        # capacity returns: stop shedding, readiness now succeeds → drain resumes → dispatch
        orch.models._client._raises.clear()
        orch.models._client.queue_structured(ReadinessVerdict(clear_and_safe=True, blast_radius="routine"))
        await orch._drain_parked_once()
        await _drain_bg(orch)
        assert not await orch.parks.is_parked(eid)                   # unparked (progressed)
        assert len(harness.wakes) == 1                               # worker dispatched on resume
    finally:
        await db.dispose()


# ── worker-inference shed → delegate parks; capacity back → resume ───────────
async def test_worker_shed_parks_delegate_then_resumes(db_url):
    harness = FakeHarness(result_status="error", output=SHED)         # worker's own inference shed
    orch, chat, harness, db = await _orch(db_url, harness=harness)
    try:
        eid, chan, root = await orch.router.open_effort("build")
        await orch.delegate(eid, chan, root, "build it")
        assert await orch.parks.is_parked(eid)                        # parked, NOT escalated as failure
        assert (await orch.parks.oldest())["stage"] == "delegate"
        assert not any("ended **error**" in p["message"] for p in chat.posted)  # not a worker failure

        # capacity returns — the worker succeeds now
        orch.harness.result_status, orch.harness.output = "done", "ok"
        await orch._drain_parked_once()
        await _drain_bg(orch)
        assert not await orch.parks.is_parked(eid)
        assert any("finished (**done**)" in p["message"] for p in chat.posted)
    finally:
        await db.dispose()


# ── a successful model call self-clocks the capacity signal ──────────────────
async def test_successful_call_fires_capacity_signal(db_url):
    orch, chat, harness, db = await _orch(db_url)
    try:
        assert orch.models.on_capacity_signal is not None             # wired
        orch._capacity_event.clear()
        orch.models._client.queue_structured(OperatorIntent(reply="x"))
        await orch.models.structured("po", "s", "u", OperatorIntent)
        assert orch._capacity_event.is_set()                          # success = capacity signal
    finally:
        await db.dispose()


# ── starvation: after the attempt cap, escalate + stop retrying ──────────────
async def test_starvation_escalates_and_unparks(db_url):
    harness = FakeHarness(result_status="error", output=SHED)         # never recovers
    orch, chat, harness, db = await _orch(db_url, harness=harness, capacity_max_attempts=2)
    try:
        eid, chan, root = await orch.router.open_effort("stuck")
        await orch.delegate(eid, chan, root, "do it")                 # parks (attempts 0)
        for _ in range(5):
            if not await orch.parks.is_parked(eid):
                break
            await orch._drain_parked_once()   # bump → resume → re-shed → re-park
            await _drain_bg(orch)
        assert not await orch.parks.is_parked(eid)                    # gave up (unparked)
        assert any("waiting on GPU capacity" in p["message"] for p in chat.posted)
    finally:
        await db.dispose()


# ── DB-backed durability: a parked effort survives a bridge "restart" ────────
async def test_parked_effort_survives_restart(db_url):
    harness = FakeHarness(result_status="error", output=SHED)
    orch, chat, harness, db = await _orch(db_url, harness=harness)
    try:
        eid, chan, root = await orch.router.open_effort("durable")
        await orch.delegate(eid, chan, root, "x")
        assert await orch.parks.is_parked(eid)
        await orch.aclose()
    finally:
        await db.dispose()
    # a fresh bridge on the SAME db sees the parked effort (resume-on-boot would pick it up)
    db2 = Database(db_url)
    orch2 = Orchestrator(
        Settings(_env_file=None, chat_adapter="fake", profiles_dir=str(ROOT / "profiles"),
                 charters_dir=str(ROOT / "charters"), floor_dir=str(ROOT / "floor"),
                 worker_instance_urls="http://w1:8090", max_concurrent_workers=1,
                 database_url=db_url, project_survey_enabled=False),
        db2, FakeChatAdapter(), model_client=FakeModelClient(), harness=FakeHarness())
    try:
        await orch2.setup()
        assert await orch2.parks.is_parked("effort-durable")          # persisted across the restart
    finally:
        await orch2.aclose()
        await db2.dispose()


# ── source guard: grounding is skipped while backpressure is recent ──────────
async def test_source_guard_skips_grounding_under_backpressure(db_url):
    orch, chat, harness, db = await _orch(db_url, grounding_enabled=True)
    try:
        eid, chan, root = await orch.router.open_effort("risky")
        orch._note_backpressure()                                     # a shed just happened
        await orch.prepare_execution(eid, "do the risky thing", risk="cross_effort")
        assert any("skipped grounding" in p["message"] for p in chat.posted)
    finally:
        await db.dispose()


# ── the drain loop's timer tick resumes without a signal ─────────────────────
async def test_drain_loop_timer_resumes(db_url):
    harness = FakeHarness(result_status="error", output=SHED)
    orch, chat, harness, db = await _orch(db_url, harness=harness, capacity_timer_s=0.1)
    try:
        eid, chan, root = await orch.router.open_effort("timed")
        await orch.delegate(eid, chan, root, "x")
        assert await orch.parks.is_parked(eid)
        orch.harness.result_status, orch.harness.output = "done", "ok"
        # start the loop manually (fake adapter doesn't auto-start it) — the TIMER tick drains it
        orch._capacity_task = asyncio.create_task(orch._capacity_drain_loop())
        for _ in range(20):
            await asyncio.sleep(0.1)
            await _drain_bg(orch)
            if not await orch.parks.is_parked(eid):
                break
        assert not await orch.parks.is_parked(eid)                    # timer fallback resumed it
    finally:
        await orch.aclose()
        await db.dispose()


# ── no worker slot → PARK (not "couldn't dispatch") → auto-run when a worker frees ──
async def test_no_worker_slot_parks_then_resumes_on_release(db_url):
    orch, chat, harness, db = await _orch(db_url)          # max_concurrent_workers=1
    try:
        # Occupy the only worker slot so the next dispatch hits NoCapacity.
        held, _hc, _hr = await orch.router.open_effort("holder")
        holder = await orch.scheduler.acquire(held, "worker-default", "sid")
        eid, chan, root = await orch.router.open_effort("waiter")
        await orch.charters.set_goal(eid, "do it", created_by="po")
        await orch.delegate(eid, chan, root, "do it")
        assert await orch.parks.is_parked(eid)                          # parked, NOT dead-ended
        assert (await orch.parks.oldest())["reason"] == "no_worker_slot"
        assert any("Waiting for a free worker" in p["message"] for p in chat.posted)
        assert len(harness.wakes) == 0                                  # didn't run yet

        # Free the worker → the release drain resumes the waiter and it runs.
        await orch.scheduler.release(holder.id)
        assert orch._capacity_event.is_set()                           # on_release fired the signal
        await orch._drain_parked_once()
        await _drain_bg(orch)
        assert not await orch.parks.is_parked(eid)                      # resumed
        assert len(harness.wakes) >= 1                                  # actually ran
    finally:
        await db.dispose()


async def test_no_worker_slot_does_not_escalate_on_attempts(db_url):
    # Slot contention is normal → a slot-parked effort must NOT hit the GPU starvation cap.
    orch, chat, harness, db = await _orch(db_url, capacity_max_attempts=1)
    try:
        held, _hc, _hr = await orch.router.open_effort("holder")
        await orch.scheduler.acquire(held, "worker-default", "sid")   # occupy the slot
        eid, chan, root = await orch.router.open_effort("patient")
        await orch.charters.set_goal(eid, "do it", created_by="po")
        await orch.delegate(eid, chan, root, "do it")                  # parks (no_worker_slot)
        for _ in range(4):                                             # drains while still no slot
            await orch._drain_parked_once()
            await _drain_bg(orch)
        assert await orch.parks.is_parked(eid)                         # still WAITING, never escalated
        assert not any("waiting on GPU capacity" in p["message"] for p in chat.posted)
    finally:
        await db.dispose()


# ── P31 F31.2 — fairness: a stuck FIFO head no longer starves the tail ────────
async def _park_delegate(orch, eid, chan, root):
    await orch.charters.set_goal(eid, "do it", created_by="po")
    await orch.parks.park(eid, stage="delegate", channel_id=chan, root_post_id=root,
                          request="do it", plan_steps=["do it"], from_step=1, mgmt_thread=None,
                          reason="no_worker_slot")


async def test_drain_resumes_all_dispatchable_not_just_the_head(db_url):
    """gym-030→031/032 regression: a re-park PRESERVES `parked_at`, so a head that keeps re-parking
    stayed pinned at the front of the FIFO and the old one-per-tick drain resumed only IT — starving
    every newer park (gym-031/032 got 0 acquires behind gym-030). The drain now attempts EVERY
    dispatchable park per tick, so the tail resumes even while the head churns."""
    orch, chat, harness, db = await _orch(db_url, worker_urls="http://w1:8090,http://w2:8090",
                                          max_workers=2)
    try:
        e1, c1, r1 = await orch.router.open_effort("head")
        e2, c2, r2 = await orch.router.open_effort("tail")
        await _park_delegate(orch, e1, c1, r1)     # older (front of FIFO)
        await _park_delegate(orch, e2, c2, r2)     # newer (would starve behind the head)
        assert await orch.parks.is_parked(e1) and await orch.parks.is_parked(e2)
        await orch._drain_parked_once()            # ONE tick
        await _drain_bg(orch)
        assert not await orch.parks.is_parked(e1)  # head resumed
        assert not await orch.parks.is_parked(e2)  # AND the tail — not blocked behind the head
    finally:
        await db.dispose()


# ── P31 F31.2 — the drain HOLDS when the whole fleet is unreachable ───────────
async def test_drain_holds_all_parks_when_environment_down(db_url):
    """When every worker is health-quarantined (inference/fleet down), resuming just re-parks — and the
    re-parking head is exactly what monopolised the drain during gym-030's outage. So the drain holds
    entirely while the environment is down, and resumes the instant it's back."""
    orch, chat, harness, db = await _orch(db_url)
    try:
        eid, chan, root = await orch.router.open_effort("held")
        await _park_delegate(orch, eid, chan, root)
        # quarantine every worker → environment_down()
        until = (datetime.now(timezone.utc) + timedelta(seconds=600)).isoformat()
        async with orch.db.session_factory() as s:
            for wi in (await s.execute(select(WorkerInstance))).scalars().all():
                wi.quarantined_until = until
            await s.commit()
        assert await orch.scheduler.environment_down() is True
        await orch._drain_parked_once()
        await _drain_bg(orch)
        assert await orch.parks.is_parked(eid)                 # HELD — not resumed into a dead env
        assert len(harness.wakes) == 0
        # environment heals → the same drain resumes it
        async with orch.db.session_factory() as s:
            for wi in (await s.execute(select(WorkerInstance))).scalars().all():
                wi.quarantined_until = None
            await s.commit()
        await orch._drain_parked_once()
        await _drain_bg(orch)
        assert not await orch.parks.is_parked(eid)             # auto-resumed once reachable
    finally:
        await db.dispose()


async def test_drain_unparks_a_terminal_zombie_and_never_resumes_it(db_url):
    """gym-030 zombie (surfaced live by F31.2's resume-all): an ABORTED effort keeps state=active, so
    `can_dispatch` (the GOVERNANCE gate, which does not read lifecycle) stays True — the drain resumed
    its stale park FOREVER, burning inference on a dead run and starving the live one. The drain now
    recognises a terminal (aborted/done) effort, UNPARKS the zombie, and never resumes it."""
    orch, chat, harness, db = await _orch(db_url)
    try:
        eid, chan, root = await orch.router.open_effort("zombie")
        await _park_delegate(orch, eid, chan, root)             # parked (no_worker_slot)
        await orch.gate.set_lifecycle(eid, "aborted")          # operator aborted it — state stays active
        assert await orch.gate.can_dispatch(eid) is True       # the trap: gate reads state, not lifecycle
        await orch._drain_parked_once()
        await _drain_bg(orch)
        assert not await orch.parks.is_parked(eid)             # zombie unparked (cleaned up)...
        assert len(harness.wakes) == 0                         # ...and NEVER resumed a dead run
    finally:
        await db.dispose()
