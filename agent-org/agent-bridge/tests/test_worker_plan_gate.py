"""Worker-side PLAN GATE (operator 2026-07-14: "plan mode could be used to ensure alignment to
the task ... save wasted time working on the wrong thing, additional steering and ultimately
start over"). Before touching any code the worker plans in a READ-ONLY turn (plan_only=True ->
edit/write tools excluded on the daemon - headless plan mode) in its OWN session; the PM checks
the plan against the goal: forbidden standing-intent terms and declared delete-to-pass are
deterministic rejects, then an LLM off-goal lens. Misaligned -> one steered revision -> still
misaligned -> honest stop BEFORE any wasted work. Fakes only."""

from __future__ import annotations

import asyncio
from pathlib import Path

from app.adapters.chat import FakeChatAdapter
from app.config import Settings
from app.db import Database
from app.modules.model_router import FakeModelClient
from app.orchestrator import Orchestrator
from app.schemas import MonitorVerdict
from app.worker.harness import FakeHarness

ROOT = Path(__file__).resolve().parents[1]


async def _orch(db_url, *, plan_gate="all"):
    settings = Settings(
        _env_file=None, chat_adapter="fake",
        profiles_dir=str(ROOT / "profiles"), charters_dir=str(ROOT / "charters"),
        floor_dir=str(ROOT / "floor"), worker_instance_urls="http://w1:8090",
        max_concurrent_workers=1, database_url=db_url, project_survey_enabled=False,
        review_mode="off", plan_approval="off", worker_plan_gate=plan_gate,
    )
    db = Database(db_url)
    orch = Orchestrator(settings, db, FakeChatAdapter(),
                        model_client=FakeModelClient(), harness=FakeHarness())
    await orch.setup()
    return orch, orch.chat, orch.harness, db


async def _drain(orch, rounds: int = 3):
    for _ in range(rounds):
        if orch._bg_tasks:
            await asyncio.gather(*list(orch._bg_tasks))


async def _shutdown(orch, db):
    """Cancel the periodic loops setup() started BEFORE disposing the DB (a late tick against a
    disposed DB throws in aiosqlite's worker thread and lands on whatever test runs next)."""
    await _drain(orch)
    for t in (orch._capacity_task, orch._stall_task, orch._reaper_task):
        if t is not None:
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
    await db.dispose()


# -- aligned plan: read-only plan wake, PM pass, then execution in-session ------
async def test_aligned_plan_is_reviewed_then_executed(db_url):
    orch, chat, harness, db = await _orch(db_url)
    try:
        await orch.projects.add("app", "https://github.com/acme/app.git")
        eid, chan, root = await orch.router.open_effort("feat", project="app")
        orch.models._client.queue_structured(MonitorVerdict(deviates=False))
        await orch.delegate(eid, chan, root, "port the parser to the new API")
        # wake 1 = the READ-ONLY plan turn; wake 2 = execution with the approval note
        assert harness.wakes[0]["plan_only"] is True
        assert "PLAN FIRST" in harness.wakes[0]["prompt"]
        assert harness.wakes[1]["plan_only"] is False
        assert "REVIEWED and APPROVED" in harness.wakes[1]["prompt"]
        assert await orch._event_count(eid, "worker_plan_approved") == 1
        assert any("finished" in p["message"] for p in chat.posted)
    finally:
        await _shutdown(orch, db)


# -- forbidden standing-intent term in the plan: deterministic reject, no model --
async def test_forbidden_term_in_plan_rejects_before_any_execution(db_url):
    orch, chat, harness, db = await _orch(db_url)
    try:
        await orch.projects.add("app", "https://github.com/acme/app.git")
        await orch.projects.set_standing_intent(
            "app", "vendored engine only — never reintroduce `NuGet` package references")
        eid, chan, root = await orch.router.open_effort("drifty", project="app")
        harness.output_queue += [
            "PLAN: 1. revert the engine wiring back to NuGet packages",
            "PLAN: 1. still going with NuGet, it's easier",
        ]
        await orch.delegate(eid, chan, root, "fix the build errors")
        assert len(harness.wakes) == 2                         # plan + revision, NO execution
        assert all(w["plan_only"] for w in harness.wakes)
        assert "NOT aligned" in harness.wakes[1]["prompt"]
        assert await orch._event_count(eid, "worker_plan_rejected") == 2
        assert any("stayed misaligned" in p["message"] for p in chat.posted)
    finally:
        await _shutdown(orch, db)


# -- LLM lens flags the first plan; the revision passes and work proceeds -------
async def test_llm_lens_steers_once_then_the_revision_executes(db_url):
    orch, chat, harness, db = await _orch(db_url)
    try:
        await orch.projects.add("app", "https://github.com/acme/app.git")
        eid, chan, root = await orch.router.open_effort("steer", project="app")
        orch.models._client.queue_structured(
            MonitorVerdict(deviates=True, trigger="deviation", level="steering",
                           rationale="it renames the API instead of porting the callers"))
        orch.models._client.queue_structured(MonitorVerdict(deviates=False))
        await orch.delegate(eid, chan, root, "port the callers to the new API")
        kinds = [(w["plan_only"], "NOT aligned" in w["prompt"]) for w in harness.wakes[:3]]
        assert kinds[0] == (True, False)                       # plan
        assert kinds[1] == (True, True)                        # steered revision
        assert kinds[2][0] is False                            # execution
        assert await orch._event_count(eid, "worker_plan_rejected") == 1
        assert await orch._event_count(eid, "worker_plan_approved") == 1
    finally:
        await _shutdown(orch, db)


# -- risky-only mode: a routine effort skips the gate entirely -------------------
async def test_routine_effort_skips_the_gate_in_risky_mode(db_url):
    orch, chat, harness, db = await _orch(db_url, plan_gate="risky")
    try:
        await orch.projects.add("app", "https://github.com/acme/app.git")
        eid, chan, root = await orch.router.open_effort("routine", project="app")
        await orch.delegate(eid, chan, root, "tidy the readme")
        assert harness.wakes[0]["plan_only"] is False
        assert "PLAN FIRST" not in harness.wakes[0]["prompt"]
    finally:
        await _shutdown(orch, db)


# -- the deterministic delete-to-pass check, straight on the checker -------------
async def test_plan_misalignment_flags_declared_deletion_on_a_non_removal_goal(db_url):
    orch, chat, harness, db = await _orch(db_url)
    try:
        await orch.projects.add("app", "https://github.com/acme/app.git")
        eid, _chan, _root = await orch.router.open_effort("del", project="app")
        reason = await orch._plan_misalignment(
            eid, "port the editor to MonoGame",
            "PLAN: 1. delete the MouseCursor.Sdl.cs file to clear the FNA errors")
        assert reason is not None and "delet" in reason.lower()
        # ...but a REMOVAL goal is allowed to plan removals (the LLM lens fails open unqueued)
        assert await orch._plan_misalignment(
            eid, "remove the legacy cursor feature",
            "PLAN: 1. delete the MouseCursor.Sdl.cs file") is None
    finally:
        await _shutdown(orch, db)
