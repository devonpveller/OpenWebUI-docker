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
