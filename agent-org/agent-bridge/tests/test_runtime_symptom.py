"""Runtime-symptom honesty (operator "no false done"; live 2026-07-10 atlas effort). The effort
"the editor throws this at runtime when clicked" built GREEN and closed 'done' + opened PRs — but
the actual interaction-triggered crash was never reproduced or verified, because the org's checks
are headless and can't click a UI. A green build is necessary but NOT sufficient to prove a
run-time/interaction/visual symptom is gone. A delivery for such a goal must be surfaced as
"delivered — needs your runtime check", kept visible, never a clean 'done'. Generic for any
project: the detector keys off the GOAL wording, not any MonoGame/murder specifics."""

from __future__ import annotations

from pathlib import Path

from app.adapters.chat import FakeChatAdapter
from app.config import Settings
from app.db import Database
from types import SimpleNamespace

from app.models import Effort, GoalVersion
from app.modules.capabilities import BranchDelivery
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
    )
    db = Database(db_url)
    orch = Orchestrator(settings, db, FakeChatAdapter(),
                        model_client=FakeModelClient(), harness=FakeHarness())
    await orch.setup()
    return orch, db


async def _seed_goal(orch, eid, objective):
    """A bare effort carrying `objective` as its current goal (enough for _no_changes_acceptable)."""
    chan = await orch.mgmt_channel_id()
    async with orch.db.session_factory() as s:
        s.add(Effort(id=eid, name=eid, channel_id=chan, root_post_id=f"root-{eid}",
                     state="active", lifecycle="open"))
        s.add(GoalVersion(effort_id=eid, version=1, objective=objective, created_by="po"))
        await s.commit()


async def test_interaction_and_visual_goals_are_flagged(db_url):
    orch, db = await _orch(db_url)
    try:
        f = orch._runtime_symptom_phrase
        # the real atlas goal + a spread of runtime/interaction/visual symptoms
        assert f("the editor throws this at runtime when clicked")
        assert f("the cursor is wrong and the toolbar doesn't render")
        assert f("app crashes on startup with an unhandled exception")
        assert f("clicking the Save button freezes the window")
        assert f("nothing happens when I press play")
        assert f("the sprite doesn't animate and the screen goes black")
        assert f("menu items misrender on resize")
    finally:
        await db.dispose()


async def test_build_only_goals_do_not_trip_it(db_url):
    orch, db = await _orch(db_url)
    try:
        f = orch._runtime_symptom_phrase
        # pure build/compile goals are fully verified by a green build — NO caveat, no false alarm
        assert f("fix the build errors") is None
        assert f("make it compile against the vendored engine") is None
        assert f("resolve the OnExiting override signature mismatch") is None
        assert f("update the API calls to the new namespace") is None
        assert f("bump the submodule and wire the composition") is None
        assert f("") is None
        assert f(None) is None
    finally:
        await db.dispose()


async def test_no_changes_on_a_behavioral_goal_needs_repro_proof(db_url):
    """The false-done the operator distrusts (live 2026-07-11): an auto-iteration of an
    already-unverified atlas fix returned NO CHANGES (read-only) and was closed 'done — verified'.
    A behavioral-symptom goal can NEVER be closed on a bare no-op — doing nothing can't fix a live
    symptom — unless the report proves the symptom no longer reproduces (`REPRO:` + `AFTER: PASS`)."""
    orch, db = await _orch(db_url)
    try:
        await _seed_goal(orch, "eff-behav",
                         "the editor throws this at runtime when Game Profile is clicked")
        # a bare 'read-only, nothing changed' claim — NOT acceptable for a live symptom
        assert not await orch._no_changes_acceptable(
            "eff-behav", "NO CHANGES: read-only, I only explored the atlas loader.")
        # even a green BUILD isn't proof a runtime symptom is gone
        assert not await orch._no_changes_acceptable(
            "eff-behav", "NO CHANGES: build succeeded, 0 errors, nothing to change.")
        # a reproduction that now PASSES IS proof (same bar _finish_effort's runtime gate uses)
        assert await orch._no_changes_acceptable(
            "eff-behav",
            "NO CHANGES: the symptom was already fixed upstream.\n"
            "REPRO: click Game Profile\nBEFORE: FAIL — atlas not loaded\nAFTER: PASS — atlas loads")
    finally:
        await db.dispose()


async def test_no_changes_on_a_readonly_goal_is_still_accepted(db_url):
    """Guard the other side: a genuine read-only/investigation goal (no runtime symptom, no
    check_cmd) still finishes cleanly on NO CHANGES — the answer IS the deliverable. The fix must
    not over-trigger and break legitimate investigation completions."""
    orch, db = await _orch(db_url)
    try:
        await _seed_goal(orch, "eff-read", "investigate and document the atlas loading structure")
        assert await orch._no_changes_acceptable(
            "eff-read", "NO CHANGES: read-only investigation, zero modifications.")
    finally:
        await db.dispose()


async def test_finish_effort_backstop_refuses_no_changes_done_on_behavioral_goal(db_url):
    """The single-chokepoint BACKSTOP: even if some upstream path leaks a no_changes delivery to
    _finish_effort for a BEHAVIORAL goal without repro proof, the closure itself must REFUSE the
    false done and surface honest needs-attention. Belt-and-suspenders for the 2026-07-11 false-done
    that recurred via a hard-to-trace path — the closure chokepoint makes the class impossible."""
    orch, db = await _orch(db_url)
    try:
        eid = "eff-backstop"
        await _seed_goal(orch, eid, "the editor throws at runtime when clicked; the cursor is missing")
        result = SimpleNamespace(output="NO CHANGES: already published, working tree clean, tests pass")
        deliv = BranchDelivery(no_changes=True, branch=f"agent/{eid}")
        await orch._finish_effort(eid, result, delivery=deliv)
        # NOT closed done — surfaced honest needs-attention
        async with orch.db.session_factory() as s:
            e = await s.get(Effort, eid)
        assert e.lifecycle != "done", "a behavioral no-op reached a false 'done' through the closure"
        msgs = " ".join(p["message"] for p in orch.chat.posted)
        assert "did **not** mark it done" in msgs and "runtime/interaction" in msgs
        assert "finished (**done**)" not in msgs
        assert await orch._event_count(eid, "delivery_runtime_unverified") == 1
    finally:
        await db.dispose()


async def test_finish_effort_backstop_allows_readonly_no_changes_done(db_url):
    """The backstop must NOT over-trigger: a genuine read-only goal (no runtime symptom) still
    finishes done cleanly on a no_changes delivery."""
    orch, db = await _orch(db_url)
    try:
        eid = "eff-readonly-finish"
        await _seed_goal(orch, eid, "investigate and document the atlas loader structure")
        result = SimpleNamespace(output="NO CHANGES: read-only investigation, nothing to modify")
        deliv = BranchDelivery(no_changes=True, branch=f"agent/{eid}")
        await orch._finish_effort(eid, result, delivery=deliv)
        msgs = " ".join(p["message"] for p in orch.chat.posted)
        assert "finished (**done**)" in msgs and "read-only task" in msgs
        assert "did **not** mark it done" not in msgs
    finally:
        await db.dispose()


async def test_phrase_snippet_is_bounded_and_readable(db_url):
    orch, db = await _orch(db_url)
    try:
        long_goal = ("the game runs fine for a while but then " * 4
                     + "it crashes when the level loads " + "and more text " * 5)
        phrase = orch._runtime_symptom_phrase(long_goal)
        assert phrase and "crash" in phrase.lower()
        assert len(phrase) < 120           # a short readable snippet, not the whole goal
        assert "\n" not in phrase
    finally:
        await db.dispose()
