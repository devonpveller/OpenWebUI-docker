"""Stall watchdog (operator 2026-07-10: "there hasn't been an update in 2 hours"). Root cause: a
clone/focus failure only flipped the effort card to 'error' — no escalation, no audit, no recovery —
so an effort sat SILENT for ~2h. Fix: (1) clone_failed escalates loudly + audits `focus_failed`;
(2) a watchdog sweeps for efforts wedged MID-DISPATCH (silent past a threshold, not delegating/parked,
last event not a resolution) and auto-re-engages them (bounded), escalating past the cap. Fakes only."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from app.adapters.chat import FakeChatAdapter
from app.config import Settings
from app.db import Database
from app.models import Effort, Event, GoalVersion
from app.modules.model_router import FakeModelClient
from app.orchestrator import Orchestrator
from app.worker.harness import FakeHarness

ROOT = Path(__file__).resolve().parents[1]


async def _orch(db_url):
    settings = Settings(
        _env_file=None, chat_adapter="fake",
        profiles_dir=str(ROOT / "profiles"), charters_dir=str(ROOT / "charters"),
        floor_dir=str(ROOT / "floor"), worker_instance_urls="http://w1:8090",
        max_concurrent_workers=1, database_url=db_url, project_survey_enabled=False,
        review_mode="off", plan_approval="off",
        stall_threshold_s=900, stall_max_recoveries=2,
    )
    db = Database(db_url)
    orch = Orchestrator(settings, db, FakeChatAdapter(),
                        model_client=FakeModelClient(), harness=FakeHarness())
    await orch.setup()
    return orch, orch.chat, db


async def _seed(orch, eid, *, last_kind, age_min=120, recoveries=0, extra_kinds=()):
    """Insert an effort whose LATEST event is `last_kind` at `age_min` ago (bypasses the fresh
    goal/open events that set_goal/open_effort would stamp NOW)."""
    base = datetime.now(timezone.utc) - timedelta(minutes=age_min)
    # stagger timestamps so `_last_event` deterministically returns `last_kind` (newest), while all
    # events still sit past the stall threshold.
    def _ts(offset_s):  # earlier events get more-negative offsets
        return (base + timedelta(seconds=offset_s)).isoformat()
    chan = await orch.mgmt_channel_id()
    async with orch.db.session_factory() as s:
        s.add(Effort(id=eid, name=eid, channel_id=chan, root_post_id=f"root-{eid}",
                     state="active", lifecycle="open"))
        s.add(GoalVersion(effort_id=eid, version=1, objective="fix the thing", created_by="po"))
        s.add(Event(kind="worker_acquire", effort_id=eid, ts=_ts(0)))
        t = 1
        for k in extra_kinds:
            s.add(Event(kind=k, effort_id=eid, ts=_ts(t))); t += 1
        for _ in range(recoveries):
            s.add(Event(kind="stall_recovered", effort_id=eid, ts=_ts(t))); t += 1
        s.add(Event(kind=last_kind, effort_id=eid, ts=_ts(t)))     # the newest event
        await s.commit()


async def test_watchdog_recovers_a_mid_dispatch_wedge(db_url):
    """An effort last seen at `worker_release` (dispatched, then silent 2h) is auto-re-engaged."""
    orch, chat, db = await _orch(db_url)
    try:
        await _seed(orch, "effort-wedged", last_kind="worker_release", age_min=120)
        await orch._sweep_stalled_efforts()
        assert await orch._event_count("effort-wedged", "stall_recovered") == 1
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "went quiet" in msgs and "effort-wedged" in msgs      # loud, not silent
    finally:
        await db.dispose()


async def test_watchdog_skips_efforts_awaiting_the_operator(db_url):
    """An effort whose last event is a RESOLUTION (a PR opened, awaiting merge) is correctly waiting
    on the operator — the watchdog must NOT re-dispatch it."""
    orch, chat, db = await _orch(db_url)
    try:
        await _seed(orch, "effort-pr", last_kind="delivery_pr_opened", age_min=200)
        await orch._sweep_stalled_efforts()
        assert await orch._event_count("effort-pr", "stall_recovered") == 0
    finally:
        await db.dispose()


async def test_watchdog_skips_a_fresh_dispatch(db_url):
    """A just-dispatched effort (well within the threshold) is left alone — no premature re-engage."""
    orch, chat, db = await _orch(db_url)
    try:
        await _seed(orch, "effort-fresh", last_kind="worker_project_set", age_min=2)
        await orch._sweep_stalled_efforts()
        assert await orch._event_count("effort-fresh", "stall_recovered") == 0
    finally:
        await db.dispose()


async def test_watchdog_escalates_after_the_recovery_cap(db_url):
    """Past `stall_max_recoveries` auto-re-engages, the watchdog stops looping and escalates loudly."""
    orch, chat, db = await _orch(db_url)
    try:
        await _seed(orch, "effort-stuck", last_kind="worker_release", age_min=120, recoveries=2)
        await orch._sweep_stalled_efforts()
        assert await orch._event_count("effort-stuck", "stall_escalated") == 1
        assert await orch._event_count("effort-stuck", "stall_recovered") == 2   # no new recovery
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "stalled mid-dispatch" in msgs and "stopped auto-retrying" in msgs
    finally:
        await db.dispose()


async def test_clone_failure_escalates_loudly_and_audits(db_url):
    """A clone/focus failure is surfaced to the operator + audited `focus_failed` (never the old
    silent card-flip) so the watchdog can pick it up."""
    orch, chat, db = await _orch(db_url)
    try:
        await _seed(orch, "effort-clone", last_kind="worker_acquire", age_min=1)
        res = SimpleNamespace(status="clone_failed", ok=False,
                              output="fatal: could not read from remote; clone failed", signal=None)
        await orch._handle_clone_failure("effort-clone", res)
        assert await orch._event_count("effort-clone", "focus_failed") == 1
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "couldn't set up its workspace" in msgs and "nothing ran" in msgs.lower()
        assert "not your code" in msgs.lower()                      # honest attribution
    finally:
        await db.dispose()


async def test_sweep_defers_while_a_worker_daemon_is_actually_running(db_url):
    """Restart-safe (live 2026-07-11): a bridge redeploy mid-task wipes the in-memory 'executing'
    marker, so the watchdog must ask the DAEMON — if a worker reports a RUNNING task, work IS
    happening; defer the sweep (re-dispatching would 409 the still-running worker). When workers are
    free again, the genuinely-wedged effort recovers."""
    orch, chat, db = await _orch(db_url)
    try:
        await _seed(orch, "effort-wedged", last_kind="worker_release", age_min=120)
        orch.harness.busy_urls = {"http://w1:8090"}          # a worker is still working
        await orch._sweep_stalled_efforts()
        assert await orch._event_count("effort-wedged", "stall_recovered") == 0   # deferred
        orch.harness.busy_urls = set()                        # workers free now
        await orch._sweep_stalled_efforts()
        assert await orch._event_count("effort-wedged", "stall_recovered") == 1   # recovered
    finally:
        await db.dispose()


async def test_watchdog_recovers_a_plan_drafted_strand(db_url):
    """An effort that drafted a plan and then sat at `plan_drafted` with no dispatch (2026-07-16 gym:
    30+ min silent, GPU idle, no posts) must be re-engaged — the planning/dry-run mid-pipeline
    AUTO-ADVANCES to dispatch, so a stall there is a wedge, not a state awaiting the operator."""
    orch, chat, db = await _orch(db_url)
    try:
        await _seed(orch, "effort-planned", last_kind="plan_drafted", age_min=40)
        await orch._sweep_stalled_efforts()
        assert await orch._event_count("effort-planned", "stall_recovered") == 1
    finally:
        await db.dispose()


async def test_watchdog_recovers_a_dry_run_strand(db_url):
    """Same coverage gap, one step later: an effort whose dry-run passed but never dispatched is a
    wedge the watchdog must recover (not an operator-hold)."""
    orch, chat, db = await _orch(db_url)
    try:
        await _seed(orch, "effort-dry", last_kind="dry_run_recorded", age_min=40)
        await orch._sweep_stalled_efforts()
        assert await orch._event_count("effort-dry", "stall_recovered") == 1
    finally:
        await db.dispose()


async def test_watchdog_recovers_a_post_publish_stall(db_url):
    """A delivery that PUBLISHED a branch but whose verify→PR→closure then STALLED (silent, no
    worker running) is a wedge the watchdog must recover — publishing is not the finish line (live
    2026-07-11: an auto-iteration re-published then went silent 20 min, both workers idle)."""
    orch, chat, db = await _orch(db_url)
    try:
        await _seed(orch, "effort-pub", last_kind="effort_published", age_min=30)
        await orch._sweep_stalled_efforts()
        assert await orch._event_count("effort-pub", "stall_recovered") == 1
    finally:
        await db.dispose()
