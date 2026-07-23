"""P25 — the drain loop must survive a TRANSIENT unverifiable delivery (evidence: gym-023, 2026-07-23).

`_verify_delivery` collapses ANY transient GitHub read failure to `landed=False, verifiable=False`,
and the whole drain loop used to live only inside `_finish_effort`'s `elif delivery.landed:` branch.
So one transient read blip on a mid-drain round routed that round to the unverifiable branch, which
closed the effort with every task still queued (gym-023: 5 of 16 tasks, no re-sweep, no
`scope_completed`, 11 stranded). The fix hoists the pending-queue drain into a shared,
verification-independent step (`_drain_next_pending`) that BOTH the landed and the unverifiable branch
call — the queue is the org's OWN memory (§5) and drains against the workspace, not the remote.

Fakes only.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from app.adapters.chat import FakeChatAdapter
from app.config import Settings
from app.db import Database
from app.models import Effort
from app.modules.capabilities import BranchDelivery
from app.modules.model_router import FakeModelClient
from app.orchestrator import Orchestrator
from app.worker.harness import FakeHarness

ROOT = Path(__file__).resolve().parents[1]
REPO = "https://github.com/acme/gym.git"


async def _orch(db_url, **overrides):
    kwargs = dict(
        _env_file=None, chat_adapter="fake",
        profiles_dir=str(ROOT / "profiles"), charters_dir=str(ROOT / "charters"),
        floor_dir=str(ROOT / "floor"), worker_instance_urls="http://w1:8090",
        max_concurrent_workers=1, database_url=db_url, project_survey_enabled=False,
        review_mode="off", plan_approval="off", qa_gate="report", drain_loop=True,
        drain_tier_walk=True, drain_plan_split=True,
    )
    kwargs.update(overrides)
    settings = Settings(**kwargs)
    db = Database(db_url)
    orch = Orchestrator(settings, db, FakeChatAdapter(),
                        model_client=FakeModelClient(), harness=FakeHarness())
    await orch.setup()
    return orch, db


async def _shutdown(orch, db):
    if orch._bg_tasks:
        await asyncio.gather(*list(orch._bg_tasks))
    for t in (orch._capacity_task, orch._stall_task, orch._reaper_task):
        if t is not None:
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
    await db.dispose()


async def _effort(orch, name="todo-product"):
    await orch.projects.add("gym", REPO)
    eid, _c, _r = await orch.router.open_effort(name, project="gym")
    await orch.charters.set_goal(eid, "a todo CLI that adds and lists todos", created_by="po")
    return eid


async def _lifecycle(orch, eid):
    async with orch.db.session_factory() as s:
        e = await s.get(Effort, eid)
    return e.lifecycle


# ── the helper's contract ─────────────────────────────────────────────────────
async def test_drain_next_pending_dispatches_one_task_and_returns_true(db_url):
    """The queue is the org's memory; the helper hands over the NEXT single task and returns True so
    the caller re-enters (never closes) while work remains."""
    orch, db = await _orch(db_url)
    try:
        eid = await _effort(orch)
        for body in ("add a delete command", "add an edit command", "validate the due date"):
            await orch.add_task(body, project_slug="gym", effort_id=eid, round_no=1)
        orch._delegating.add(eid)                               # model reentrancy: queue, don't spawn
        assert await orch._drain_next_pending(eid) is True      # dispatched exactly ONE
        assert len(await orch.list_open_tasks(effort_id=eid)) == 2   # the other two stay queued
        assert await orch._event_count(eid, "drain_dispatch") == 1
    finally:
        await _shutdown(orch, db)


async def test_drain_next_pending_returns_false_on_an_empty_queue(db_url):
    """An empty queue → False, so the caller proceeds to its honest close/re-sweep (no hang)."""
    orch, db = await _orch(db_url)
    try:
        eid = await _effort(orch)
        assert await orch._drain_next_pending(eid) is False
    finally:
        await _shutdown(orch, db)


async def test_drain_next_pending_is_a_noop_when_the_drain_loop_is_off(db_url):
    """Drain off → the legacy QA path is untouched; the helper never drains."""
    orch, db = await _orch(db_url, drain_loop=False)
    try:
        eid = await _effort(orch)
        await orch.add_task("add a delete command", project_slug="gym", effort_id=eid, round_no=1)
        assert await orch._drain_next_pending(eid) is False
        assert len(await orch.list_open_tasks(effort_id=eid)) == 1   # untouched
    finally:
        await _shutdown(orch, db)


# ── the integration: an UNVERIFIABLE delivery drains instead of abandoning ─────
async def test_unverifiable_delivery_drains_the_queue_instead_of_closing(db_url):
    """THE gym-023 bug. A transient verify failure (landed=False, verifiable=False) routed the round
    to the unverifiable branch, which closed the effort with tasks still queued. It must now dispatch
    the next queued task and stay open."""
    orch, db = await _orch(db_url)
    try:
        eid = await _effort(orch)
        for body in ("add a delete command", "validate the due date"):
            await orch.add_task(body, project_slug="gym", effort_id=eid, round_no=1)
        orch._published_branch[eid] = f"agent/{eid}"           # the worker self-reported a push
        orch._delegating.add(eid)                              # model reentrancy: queue, don't spawn
        deliv = BranchDelivery(verifiable=False, branch=f"agent/{eid}")   # transient: couldn't verify
        res = SimpleNamespace(status="done", output="Done. delete command added.")
        await orch._finish_effort(eid, res, delivery=deliv)
        assert await orch._event_count(eid, "drain_dispatch") == 1   # dispatched the next task
        assert await _lifecycle(orch, eid) != "done"                 # NOT prematurely closed
        assert len(await orch.list_open_tasks(effort_id=eid)) == 1   # one handed over, one queued
    finally:
        await _shutdown(orch, db)


async def test_unverifiable_delivery_with_an_empty_queue_still_closes_honestly(db_url):
    """No regression: with the queue EMPTY, an unverifiable delivery still closes with the honest
    'could not independently verify' self-report — it must not hang open, and it must not pretend the
    push is verified fact."""
    orch, db = await _orch(db_url)
    try:
        eid = await _effort(orch)
        orch._published_branch[eid] = f"agent/{eid}"
        deliv = BranchDelivery(verifiable=False, branch=f"agent/{eid}")
        res = SimpleNamespace(status="done", output="Done.")
        await orch._finish_effort(eid, res, delivery=deliv)
        assert await orch._event_count(eid, "drain_dispatch") == 0
        assert await _lifecycle(orch, eid) == "done"
        msgs = " ".join(p["message"] for p in orch.chat.posted)
        assert "independently verify" in msgs
    finally:
        await _shutdown(orch, db)
