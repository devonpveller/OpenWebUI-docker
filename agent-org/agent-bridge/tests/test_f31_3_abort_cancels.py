"""F31.3 — abort / freeze / boot must CANCEL the in-flight worker DAEMON turn, not just the bridge
bookkeeping.

Every path that frees a worker SLOT was bookkeeping-only: abort (`_archive_efforts` → set_lifecycle),
freeze (`enforce_freeze` → SUSPENDED), and boot (`reset_stale` → IDLE) all flip the DB `sched_state`
but never tell the daemon to stop — so the orphaned turn grinds to its deadline holding capacity
(gym-030: an aborted effort's worker ground ~1h → downstream no_worker_slot parks; and every bridge
recreate re-orphaned both workers until a manual restart). The daemon-cancel already exists
(`harness.cancel_task`); F31.3 wires it, via `_cancel_worker_turns`, into those three paths. Ground
truth is the daemon's own task list (`running_task_progress`), so it is restart-safe. Fakes only.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from app.adapters.chat import FakeChatAdapter
from app.config import Settings
from app.db import Database
from app.models import SCHED_COMPUTING, WorkerInstance
from app.modules.model_router import FakeModelClient
from app.orchestrator import Orchestrator
from app.worker.harness import FakeHarness

ROOT = Path(__file__).resolve().parents[1]
REPO = "https://github.com/acme/gym.git"
W1, W2 = "http://w1:8090", "http://w2:8090"


async def _orch(db_url, **over):
    settings = Settings(
        _env_file=None, chat_adapter="fake",
        profiles_dir=str(ROOT / "profiles"), charters_dir=str(ROOT / "charters"),
        floor_dir=str(ROOT / "floor"), worker_instance_urls=f"{W1},{W2}",
        max_concurrent_workers=2, database_url=db_url, project_survey_enabled=False,
        review_mode="off", plan_approval="off", **over,
    )
    db = Database(db_url)
    orch = Orchestrator(settings, db, FakeChatAdapter(),
                        model_client=FakeModelClient(), harness=FakeHarness())
    await orch.setup()   # boot cancel runs here with no busy workers → no-op
    return orch, db


async def _bind(orch, base_url, effort_id):
    """Mark a worker as computing an effort in the bridge DB (what acquire does)."""
    async with orch.db.session_factory() as s:
        inst = (await s.execute(
            select(WorkerInstance).where(WorkerInstance.base_url == base_url))).scalar_one()
        inst.effort_id = effort_id
        inst.sched_state = SCHED_COMPUTING
        await s.commit()


def _busy(orch, mapping: dict[str, str]):
    """Make the daemon report a RUNNING task per url (url -> task_id)."""
    orch.harness.busy_urls = set(mapping)
    orch.harness.progress_task_ids = dict(mapping)
    orch.harness.progress_offsets = {u: 1 for u in mapping}


# ── the helper: abort/freeze TARGET only the effort's worker ───────────────────
async def test_cancel_targets_only_the_efforts_worker(db_url):
    orch, db = await _orch(db_url)
    try:
        await _bind(orch, W1, "eff-A")
        await _bind(orch, W2, "eff-B")
        _busy(orch, {W1: "task-A", W2: "task-B"})
        n = await orch._cancel_worker_turns(effort_id="eff-A", reason="abort")
        assert n == 1
        assert (W1, "task-A") in orch.harness.cancelled     # A's daemon turn cancelled
        assert all(u != W2 for u, _ in orch.harness.cancelled)   # B untouched
        assert await orch._event_count("eff-A", "worker_turn_cancelled") == 1
    finally:
        await db.dispose()


# ── boot: cancel EVERY running turn (all orphaned after a restart) ─────────────
async def test_boot_cancels_all_orphaned_turns(db_url):
    orch, db = await _orch(db_url)
    try:
        _busy(orch, {W1: "task-1", W2: "task-2"})
        n = await orch._cancel_worker_turns(effort_id=None, reason="boot-orphan")
        assert n == 2
        assert {u for u, _ in orch.harness.cancelled} == {W1, W2}
    finally:
        await db.dispose()


# ── best-effort: a bound-but-idle daemon (no running task) is never "cancelled" ─
async def test_no_running_task_is_a_noop(db_url):
    orch, db = await _orch(db_url)
    try:
        await _bind(orch, W1, "eff-A")
        # W1 bound to eff-A but the daemon reports NO running task (not in busy_urls)
        n = await orch._cancel_worker_turns(effort_id="eff-A", reason="abort")
        assert n == 0 and orch.harness.cancelled == []
    finally:
        await db.dispose()


# ── the abort WIRING end-to-end: _archive_efforts cancels the turn + aborts ────
async def test_archive_efforts_cancels_the_in_flight_turn(db_url):
    orch, db = await _orch(db_url)
    try:
        await orch.projects.add("gym", REPO)
        eid, chan, _root = await orch.router.open_effort("feat", project="gym")
        await orch.charters.set_goal(eid, "do it", created_by="po")
        await _bind(orch, W1, eid)
        _busy(orch, {W1: "task-live"})
        await orch._archive_efforts([eid], mgmt_channel=chan)
        # the daemon turn was cancelled (not left to grind to the deadline)…
        assert (W1, "task-live") in orch.harness.cancelled
        assert await orch._event_count(eid, "worker_turn_cancelled") == 1
        # …and the effort is archived
        assert await orch.gate.lifecycle_of(eid) == "aborted"
    finally:
        await db.dispose()
