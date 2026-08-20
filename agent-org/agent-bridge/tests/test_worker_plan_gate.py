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
# P13.3: a plan turn must return an actual PLAN. The FakeHarness default output is "ok", and a
# 2-char reply is now treated as a TRUNCATED turn (one re-ask) rather than adjudicated as a bad
# plan — gym-011 rejected finished work because the gate judged the narration line "Final test run
# and commit:" as though it were the worker's plan.
_PLAN = "\n".join([
    "UNDERSTANDING: port the callers to the new API.",
    "PLAN:",
    "1. update the call sites in parser.py",
    "2. run the test suite",
    "WON'T DO: no unrelated refactors",
    "RISKS: none known",
])


async def test_aligned_plan_is_reviewed_then_executed(db_url):
    orch, chat, harness, db = await _orch(db_url)
    try:
        await orch.projects.add("app", "https://github.com/acme/app.git")
        eid, chan, root = await orch.router.open_effort("feat", project="app")
        harness.output_queue.append(_PLAN)
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


# -- the lens rejects twice: stop before any execution, with the task named ------
async def test_lens_rejecting_both_plans_stops_before_any_execution(db_url):
    orch, chat, harness, db = await _orch(db_url)
    try:
        await orch.projects.add("app", "https://github.com/acme/app.git")
        await orch.projects.set_standing_intent(
            "app", "vendored engine only — never reintroduce `NuGet` package references")
        eid, chan, root = await orch.router.open_effort("drifty", project="app")
        harness.output_queue += [
            "PLAN: 1. revert the engine wiring back to NuGet packages",
            "PLAN: 1. keep using NuGet, it's easier",
        ]
        for why in ("the plan reverts to the forbidden NuGet wiring",
                    "the revision still keeps the forbidden NuGet dependency"):
            orch.models._client.queue_structured(
                MonitorVerdict(deviates=True, trigger="deviation", level="steering",
                               rationale=why))
        await orch.delegate(eid, chan, root, "fix the build errors")
        assert len(harness.wakes) == 2                         # plan + revision, NO execution
        assert all(w["plan_only"] for w in harness.wakes)
        assert "NOT aligned" in harness.wakes[1]["prompt"]
        assert await orch._event_count(eid, "worker_plan_rejected") == 2
        assert any("couldn't produce an aligned plan" in p["message"] for p in chat.posted)
        # the stop names the TASK, not just the effort id (operator 2026-07-14, twice)
        assert any("fix the build errors" in p["message"] and "⛔" in p["message"]
                   for p in chat.posted)
    finally:
        await _shutdown(orch, db)


# -- LLM lens flags the first plan; the revision passes and work proceeds -------
async def test_llm_lens_steers_once_then_the_revision_executes(db_url):
    orch, chat, harness, db = await _orch(db_url)
    try:
        await orch.projects.add("app", "https://github.com/acme/app.git")
        eid, chan, root = await orch.router.open_effort("steer", project="app")
        harness.output_queue.append(_PLAN)          # first plan turn
        harness.output_queue.append(_PLAN)          # the steered revision
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


# -- an EMPTY plan reply is a session symptom: fresh-session retry, then stop ----
async def test_empty_plan_reply_retries_once_in_a_fresh_session(db_url):
    """2026-07-14 live: a 593KB base session made the model return EMPTY on every plan turn —
    the gate stopped with 'plan missing'. An empty reply must rotate to a FRESH session (the
    worker_plan_empty event bumps _session_for's generation) and re-ask the same plan request."""
    orch, chat, harness, db = await _orch(db_url)
    try:
        await orch.projects.add("app", "https://github.com/acme/app.git")
        eid, chan, root = await orch.router.open_effort("hollow", project="app")
        harness.output_queue.append("   ")                    # empty plan turn (rotted session)
        harness.output_queue.append(_PLAN)                    # the fresh-session retry answers
        orch.models._client.queue_structured(MonitorVerdict(deviates=False))
        await orch.delegate(eid, chan, root, "port the parser")
        # wake 1 = plan turn; wake 2 = the SAME plan request in a FRESH session (generation bump).
        # P17 F16 — plan turns run in their own `~plan` session, derived from `_session_for` so
        # the empty-reply rotation below still works. What matters here is the GENERATION moving,
        # not the literal id.
        assert harness.wakes[0]["session_id"] == f"{eid}~plan"
        assert harness.wakes[1]["session_id"] == f"{eid}~r1~plan"   # rotated
        assert harness.wakes[1]["session_id"] != harness.wakes[0]["session_id"]
        assert "PLAN FIRST" in harness.wakes[1]["prompt"]      # re-ask, not a revision
        assert harness.wakes[2]["plan_only"] is False          # then execution proceeds
        assert await orch._event_count(eid, "worker_plan_empty") == 1
    finally:
        await _shutdown(orch, db)


async def test_two_empty_plan_replies_stop_with_the_goal_named(db_url):
    orch, chat, harness, db = await _orch(db_url)
    try:
        await orch.projects.add("app", "https://github.com/acme/app.git")
        eid, chan, root = await orch.router.open_effort("husk", project="app")
        harness.output_queue += ["", ""]                       # empty in base AND fresh session
        await orch.delegate(eid, chan, root, "port the parser to the new API")
        assert len(harness.wakes) == 2                         # no execution wake
        assert await orch._event_count(eid, "worker_plan_stopped") == 1
        stop = next(p["message"] for p in chat.posted if "EMPTY twice" in p["message"])
        # the stop names the TASK, not just the effort id (operator 2026-07-14, twice)
        assert "port the parser to the new API" in stop
    finally:
        await _shutdown(orch, db)


async def test_plan_gate_stop_rotates_the_next_runs_session(db_url):
    orch, chat, harness, db = await _orch(db_url)
    try:
        await orch.projects.add("app", "https://github.com/acme/app.git")
        await orch.projects.set_standing_intent("app", "never reintroduce `NuGet` references")
        eid, chan, root = await orch.router.open_effort("rerun", project="app")
        harness.output_queue += ["PLAN: 1. use NuGet", "PLAN: 1. re-add NuGet anyway"]
        for _ in range(2):
            orch.models._client.queue_structured(
                MonitorVerdict(deviates=True, trigger="deviation", level="steering",
                               rationale="reintroduces the forbidden NuGet wiring"))
        await orch.delegate(eid, chan, root, "fix the build")   # -> misaligned twice -> stop
        n = len(harness.wakes)
        # the operator's re-run starts from a FRESH session (worker_plan_stopped rotates)
        harness.output_queue.append("PLAN: 1. port to the vendored engine")
        orch.models._client.queue_structured(MonitorVerdict(deviates=False))
        await orch.delegate(eid, chan, root, "fix the build")
        # the generation is what rotated; P17 F16 appends `~plan` to plan-turn sessions
        assert "~r1" in harness.wakes[n]["session_id"]
    finally:
        await _shutdown(orch, db)


# -- plan judgment is REASONING, not keywords (operator 2026-07-14) --------------
async def test_plan_judgment_is_lens_only_no_keyword_rejects(db_url):
    """Live false positives (operator: "the plan would naturally mention FNA if it still exists
    in the repo … the plan inclusion needs reasoning not determinism"): honest plans naming the
    forbidden term they were REMOVING were rejected by substring/context matching. There is NO
    deterministic reject on plan prose anymore — the lens is the sole judge (fed the standing
    intent + forbidden terms), and the delivery gates on the ACTUAL diff stay the backstop."""
    orch, chat, harness, db = await _orch(db_url)
    try:
        await orch.projects.add("app", "https://github.com/acme/app.git")
        await orch.projects.set_standing_intent(
            "app", "vendored engine only — never reintroduce `Murder.FNA`")
        eid, _c, _r = await orch.router.open_effort("porty", project="app")
        # With the lens failed-open (nothing queued), NO plan wording is rejected — not even
        # additive-sounding phrasing; judgment belongs to reasoning, never to a keyword list.
        for plan in (
            "PLAN: 1. remove the Murder.FNA NuGet package from Murder.csproj",
            "PLAN: 1. verify no Murder.FNA references remain anywhere",
            "PLAN: 1. revert back to Murder.FNA for now",
        ):
            assert await orch._plan_misalignment(eid, "port FNA to MonoGame", plan) is None, plan
        # The lens sees the constraints: its verdict (with rationale) is the reject path.
        orch.models._client.queue_structured(
            MonitorVerdict(deviates=True, trigger="deviation", level="steering",
                           rationale="the plan reverts to Murder.FNA instead of porting"))
        reason = await orch._plan_misalignment(
            eid, "port FNA to MonoGame", "PLAN: 1. revert back to Murder.FNA for now")
        assert reason is not None and "Murder.FNA" in reason
    finally:
        await _shutdown(orch, db)


# -- delete-to-pass in a plan is the LENS's call too (reasoning, not keywords) ---
async def test_declared_deletion_is_judged_by_the_lens_not_keywords(db_url):
    orch, chat, harness, db = await _orch(db_url)
    try:
        await orch.projects.add("app", "https://github.com/acme/app.git")
        eid, _chan, _root = await orch.router.open_effort("del", project="app")
        orch.models._client.queue_structured(
            MonitorVerdict(deviates=True, trigger="deviation", level="steering",
                           rationale="deleting the cursor is dropping a feature, not porting"))
        reason = await orch._plan_misalignment(
            eid, "port the editor to MonoGame",
            "PLAN: 1. delete the MouseCursor.Sdl.cs file to clear the FNA errors")
        assert reason is not None and "cursor" in reason.lower()
        # a REMOVAL goal's deletion plan passes when the lens agrees (fails open unqueued)
        assert await orch._plan_misalignment(
            eid, "remove the legacy cursor feature",
            "PLAN: 1. delete the MouseCursor.Sdl.cs file") is None
    finally:
        await _shutdown(orch, db)
