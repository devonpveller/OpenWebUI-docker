"""P31 Slice 1 — an ENVIRONMENT failure is not a WORKER failure.

When EVERY worker is health-quarantined (the fleet is unreachable, or inference is shedding 502/503),
a silent turn is a stalled ENVIRONMENT, not a hung worker or a code gate. The stall watchdog must HOLD
(audit `env_wait`, post one honest "waiting on the environment" note) instead of escalating a worker/
code gate — gym-030 fired THREE false `stall_escalated` during the llm-queue shed. A genuine hang with
a HEALTHY env still escalates (gym-033). The signal is deterministic (all-workers-quarantined), not an
LLM verdict. The hold is not terminal — it auto-resumes the moment a worker is reachable again. Fakes.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select

from app.adapters.chat import FakeChatAdapter
from app.config import Settings
from app.db import Database
from app.models import Effort, Event, GoalVersion, WorkerInstance
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


async def _seed(orch, eid, *, last_kind, age_min=120, recoveries=0):
    base = datetime.now(timezone.utc) - timedelta(minutes=age_min)
    def _ts(o):
        return (base + timedelta(seconds=o)).isoformat()
    chan = await orch.mgmt_channel_id()
    async with orch.db.session_factory() as s:
        s.add(Effort(id=eid, name=eid, channel_id=chan, root_post_id=f"root-{eid}",
                     state="active", lifecycle="open"))
        s.add(GoalVersion(effort_id=eid, version=1, objective="fix it", created_by="po"))
        s.add(Event(kind="worker_acquire", effort_id=eid, ts=_ts(0)))
        t = 1
        for _ in range(recoveries):
            s.add(Event(kind="stall_recovered", effort_id=eid, ts=_ts(t))); t += 1
        s.add(Event(kind=last_kind, effort_id=eid, ts=_ts(t)))
        await s.commit()


async def _set_quarantine(orch, *, until):
    async with orch.db.session_factory() as s:
        for wi in (await s.execute(select(WorkerInstance))).scalars().all():
            wi.quarantined_until = until
        await s.commit()


async def _quarantine_all(orch, seconds=600):
    await _set_quarantine(orch, until=(datetime.now(timezone.utc)
                                       + timedelta(seconds=seconds)).isoformat())


# ── the deterministic signal ──────────────────────────────────────────────────
async def test_environment_down_is_exactly_all_workers_quarantined(db_url):
    orch, _chat, db = await _orch(db_url)
    try:
        assert await orch.scheduler.environment_down() is False   # a reachable worker exists
        await _quarantine_all(orch)
        assert await orch.scheduler.environment_down() is True     # whole fleet unreachable
        await _set_quarantine(orch, until=None)                    # lifted
        assert await orch.scheduler.environment_down() is False
    finally:
        await db.dispose()


# ── the watchdog HOLDS instead of escalating (gym-030) ────────────────────────
async def test_env_down_holds_instead_of_escalating(db_url):
    orch, chat, db = await _orch(db_url)
    try:
        await _seed(orch, "effort-envdown", last_kind="worker_release", age_min=120, recoveries=2)
        await _quarantine_all(orch)
        await orch._sweep_stalled_efforts()
        assert await orch._event_count("effort-envdown", "stall_escalated") == 0   # not a worker fault
        assert await orch._event_count("effort-envdown", "stall_recovered") == 2   # no re-engage into a down env
        assert await orch._event_count("effort-envdown", "env_wait") == 1          # held on the environment
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "waiting on the environment" in msgs.lower()
    finally:
        await db.dispose()


# ── gym-033 regression guard: a genuine hang on a HEALTHY env still escalates ──
async def test_env_healthy_still_escalates_a_genuine_hang(db_url):
    orch, chat, db = await _orch(db_url)
    try:
        await _seed(orch, "effort-hang", last_kind="worker_release", age_min=120, recoveries=2)
        await orch._sweep_stalled_efforts()                        # env healthy (worker reachable)
        assert await orch._event_count("effort-hang", "stall_escalated") == 1
        assert await orch._event_count("effort-hang", "env_wait") == 0
    finally:
        await db.dispose()


# ── the hold is not terminal: it auto-resumes when the environment heals ──────
async def test_env_down_auto_resumes_when_it_heals(db_url):
    orch, chat, db = await _orch(db_url)
    try:
        await _seed(orch, "effort-heal", last_kind="worker_release", age_min=120, recoveries=0)
        await _quarantine_all(orch)
        await orch._sweep_stalled_efforts()
        assert await orch._event_count("effort-heal", "env_wait") == 1
        assert await orch._event_count("effort-heal", "stall_recovered") == 0      # held, not recovered
        await _set_quarantine(orch, until=None)                                    # environment heals
        await orch._sweep_stalled_efforts()
        assert await orch._event_count("effort-heal", "stall_recovered") == 1      # auto-resumed, no human re-run
    finally:
        await db.dispose()


# ── the honest note is posted once per outage, not every sweep ────────────────
async def test_env_wait_note_is_throttled(db_url):
    orch, chat, db = await _orch(db_url)
    try:
        await _seed(orch, "effort-quiet", last_kind="worker_release", age_min=120, recoveries=0)
        await _quarantine_all(orch)
        await orch._sweep_stalled_efforts()
        await orch._sweep_stalled_efforts()
        assert await orch._event_count("effort-quiet", "env_wait") == 2            # audited each sweep
        notes = [p for p in chat.posted if "waiting on the environment" in p["message"].lower()]
        assert len(notes) == 1                                                     # but noted only once
    finally:
        await db.dispose()
