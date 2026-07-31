"""F33 (gym-036) — the worker starts a turn on the DELIVERY-BRANCH HEAD, not a stale base clone.

`set_project` clones the DEFAULT branch (base). gym-036's drain worker got that base clone, re-did
already-done work, and its push bounced (non-fast-forward) → stale-workspace thrash. The fix: before the
turn, the bridge MECHANICALLY runs `git fetch origin <branch> && git checkout -f -B <branch> FETCH_HEAD`
on the acquired worker (not a prompt instruction). Best-effort: on the first delivery the branch doesn't
exist yet, the fetch fails, and the worker stays on base to create it. Fakes only.
"""

from __future__ import annotations

from pathlib import Path

from app.adapters.chat import FakeChatAdapter
from app.config import Settings
from app.db import Database
from app.modules.model_router import FakeModelClient
from app.orchestrator import Orchestrator
from app.worker.harness import FakeHarness

ROOT = Path(__file__).resolve().parents[1]
REPO = "https://github.com/acme/gym.git"


async def _orch(db_url, **over):
    settings = Settings(
        _env_file=None, chat_adapter="fake",
        profiles_dir=str(ROOT / "profiles"), charters_dir=str(ROOT / "charters"),
        floor_dir=str(ROOT / "floor"), worker_instance_urls="http://w1:8090",
        max_concurrent_workers=1, database_url=db_url, project_survey_enabled=False,
        review_mode="off", plan_approval="off", **over,
    )
    db = Database(db_url)
    orch = Orchestrator(settings, db, FakeChatAdapter(),
                        model_client=FakeModelClient(), harness=FakeHarness(result_status="done"))
    await orch.setup()
    return orch, db


async def _effort(orch):
    await orch.projects.add("gym", REPO)
    eid, chan, root = await orch.router.open_effort("feat", project="gym")
    await orch.charters.set_goal(eid, "do it", created_by="po")
    return eid, chan, root


async def test_wake_hard_syncs_to_the_delivery_branch(db_url):
    orch, db = await _orch(db_url)
    try:
        eid, chan, root = await _effort(orch)
        branch = orch._effort_branch(eid)                       # agent/<effort_id>
        orch.harness.check_queue = [(0, f"Switched to branch {branch}", False)]
        res = await orch.router.wake(eid, role="worker-default", thread_id=root, channel_id=chan,
                                     instruction="do it", repo=REPO, checkout_branch=branch)
        # the mechanical fetch + force-checkout ran on the worker, BEFORE the turn
        cmds = [c["command"] for c in orch.harness.checks]
        assert any(f"git fetch origin {branch}" in c and f"checkout -f -B {branch} FETCH_HEAD" in c
                   for c in cmds)
        assert await orch._event_count(eid, "worker_synced_to_delivery") == 1
        assert res is not None and res.ok                       # the turn still ran after the sync
    finally:
        await db.dispose()


async def test_no_sync_when_checkout_branch_absent(db_url):
    orch, db = await _orch(db_url)
    try:
        eid, chan, root = await _effort(orch)
        res = await orch.router.wake(eid, role="worker-default", thread_id=root, channel_id=chan,
                                     instruction="do it", repo=REPO)   # no checkout_branch
        assert not any("git fetch origin" in c["command"] for c in orch.harness.checks)
        assert await orch._event_count(eid, "worker_synced_to_delivery") == 0
        assert res is not None and res.ok
    finally:
        await db.dispose()


async def test_first_delivery_missing_branch_is_best_effort(db_url):
    """First delivery: the branch doesn't exist yet, the fetch fails — the worker stays on base to
    create it, and the turn STILL runs (the sync never blocks a dispatch)."""
    orch, db = await _orch(db_url)
    try:
        eid, chan, root = await _effort(orch)
        branch = orch._effort_branch(eid)
        orch.harness.check_queue = [(128, f"fatal: couldn't find remote ref {branch}", False)]
        res = await orch.router.wake(eid, role="worker-default", thread_id=root, channel_id=chan,
                                     instruction="do it", repo=REPO, checkout_branch=branch)
        assert await orch._event_count(eid, "worker_synced_to_delivery") == 1   # attempted (synced=False)
        assert res is not None and res.ok                                       # turn ran regardless
    finally:
        await db.dispose()


async def test_sync_survives_a_daemon_without_check_route(db_url):
    """An older daemon with no /check route makes run_check raise — the sync is best-effort and must
    swallow it, never breaking the dispatch."""
    orch, db = await _orch(db_url)
    try:
        eid, chan, root = await _effort(orch)
        branch = orch._effort_branch(eid)
        orch.harness.check_queue = []          # FakeHarness.run_check raises when the queue is empty
        res = await orch.router.wake(eid, role="worker-default", thread_id=root, channel_id=chan,
                                     instruction="do it", repo=REPO, checkout_branch=branch)
        assert res is not None and res.ok      # the raise was swallowed; the turn still ran
    finally:
        await db.dispose()
