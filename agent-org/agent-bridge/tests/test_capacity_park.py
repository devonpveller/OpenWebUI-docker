"""Capacity park-and-resume: an orchestration step shed by inference backpressure is PARKED
(machine B suspended, DB-backed) instead of failed, and auto-resumed when capacity returns —
so a research/ingestion batch saturating the GPU can't stall the whole sequence. Fakes only."""

from __future__ import annotations

import asyncio
from pathlib import Path

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


async def _orch(db_url, *, harness=None, **over):
    settings = Settings(
        _env_file=None, chat_adapter="fake",
        profiles_dir=str(ROOT / "profiles"), charters_dir=str(ROOT / "charters"),
        floor_dir=str(ROOT / "floor"), worker_instance_urls="http://w1:8090",
        max_concurrent_workers=1, database_url=db_url, project_survey_enabled=False,
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
