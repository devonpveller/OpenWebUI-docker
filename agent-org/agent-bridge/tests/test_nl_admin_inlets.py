"""Operator preference: EVERY user-facing command has a natural-language inlet (slash commands are
just a power-user fallback). These cover the admin inlets — list/remove projects, widen egress,
kill/unkill — driven purely by NL intent. Fakes only."""

from __future__ import annotations

from pathlib import Path

from app.adapters.chat import FakeChatAdapter
from app.config import Settings
from app.db import Database
from app.modules.model_router import FakeModelClient
from app.orchestrator import Orchestrator
from app.schemas import OperatorIntent
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
    return orch, orch.chat, db


async def test_nl_project_list(db_url):
    orch, chat, db = await _orch(db_url)
    try:
        await orch.projects.add("acme", "https://github.com/acme/app.git")
        orch.models._client.queue_structured(OperatorIntent(kind="project_list", reply="Here:"))
        mgmt = await orch.mgmt_channel_id()
        await orch.nl_intake("what projects do we have?", mgmt, thread_id="t1")
        assert any("acme" in p["message"] and "Projects:" in p["message"] for p in chat.posted)
    finally:
        await db.dispose()


async def test_nl_project_remove(db_url):
    orch, chat, db = await _orch(db_url)
    try:
        await orch.projects.add("acme", "https://github.com/acme/app.git")
        orch.models._client.queue_structured(
            OperatorIntent(kind="project_remove", project="acme", reply="Okay —"))
        mgmt = await orch.mgmt_channel_id()
        await orch.nl_intake("forget the acme project", mgmt, thread_id="t1")
        assert await orch.projects.resolve("acme") is None          # actually removed
        assert any("Removed project" in p["message"] for p in chat.posted)
    finally:
        await db.dispose()


async def test_nl_egress_allow(db_url):
    orch, chat, db = await _orch(db_url)
    try:
        orch.models._client.queue_structured(
            OperatorIntent(kind="egress_allow", host="gitlab.example.com", reply="Sure —"))
        mgmt = await orch.mgmt_channel_id()
        await orch.nl_intake("let the workers reach gitlab.example.com", mgmt, thread_id="t1")
        assert await orch.egress.is_allowed("gitlab.example.com")
        assert any("can now reach" in p["message"] for p in chat.posted)
    finally:
        await db.dispose()


async def test_start_effort_prose_is_not_tidied(db_url):
    """Regression (2026-07-16 gym): "start a new effort <multi-word> on the <proj> project. <goal>"
    whose goal text describes a "clear-completed" FEATURE was swallowed by _nl_tidy_up (the word
    "effort" + "clear…completed" tripped the board-tidy classifier) and silently dropped — no effort,
    just a "Tidied up" reply. The deterministic new-effort idiom must catch the natural phrasing
    FIRST and open the effort, slugifying the multi-word name."""
    orch, chat, db = await _orch(db_url)
    try:
        await orch.projects.add("gym", "https://github.com/acme/gym.git")
        mgmt = await orch.mgmt_channel_id()
        msg = ("start a new effort gym-004 todo-product on the gym project. Take the todo CLI and "
               "add delete, priority levels, and a clear-completed action, with per-command help. "
               "Deliver as a PR.")
        await orch.nl_intake(msg, mgmt, thread_id="t1")
        posts = " || ".join(p["message"] for p in chat.posted)
        assert "On it — opened" in posts and "todo-product" in posts   # routed to new-effort
        assert "Tidied up" not in posts                                # NOT the board-tidy path
        snap = await orch.gate.snapshot(open_only=True)
        assert any("gym-004-todo-product" in (e.get("id") or "") for e in snap)   # slugified name
    finally:
        await db.dispose()


async def test_tidy_guard_skips_start_effort(db_url):
    """The _nl_tidy_up guard in isolation: a start-effort directive is never a board-tidy, even
    though its goal text mentions clear/completed as feature words."""
    orch, chat, db = await _orch(db_url)
    try:
        mgmt = await orch.mgmt_channel_id()
        handled = await orch._nl_tidy_up(
            "start a new effort gym-004 todo-product on the gym project. add a clear-completed "
            "action and clear out finished todos.", mgmt, thread_id="t1")
        assert handled is False                                        # not treated as tidy
        assert not any("Tidied up" in p["message"] for p in chat.posted)
    finally:
        await db.dispose()


def test_tidy_regex_ignores_feature_words():
    """_TIDY_RE must not fire on a hyphenated FEATURE compound ("clear-completed") in a goal or
    steering message, but must still catch real board-cleanup phrasing (2026-07-16 gym: the feature
    word swallowed a start-effort fire AND a clarification answer as "Tidied up")."""
    from app.orchestrator import Orchestrator
    r = Orchestrator._TIDY_RE
    assert not r.search("add a clear-completed action to the todo app")
    assert not r.search("for effort-gym-004-todo-product: keep clear-completed and search")
    assert r.search("tidy up")
    assert r.search("clean up the finished efforts")
    assert r.search("clear out the old branches")


async def test_nl_kill_and_unkill(db_url):
    orch, chat, db = await _orch(db_url)
    try:
        mgmt = await orch.mgmt_channel_id()
        orch.models._client.queue_structured(OperatorIntent(kind="kill", reply="Stopping —"))
        await orch.nl_intake("emergency stop, freeze everything", mgmt, thread_id="t1")
        from app.models import GlobalState
        async with orch.db.session_factory() as s:
            assert (await s.get(GlobalState, 1)).kill_switch is True   # actually engaged
        assert any("Kill switch ENGAGED" in p["message"] for p in chat.posted)

        orch.models._client.queue_structured(OperatorIntent(kind="unkill", reply="Resuming —"))
        await orch.nl_intake("resume, let them run", mgmt, thread_id="t2")
        async with orch.db.session_factory() as s:
            assert (await s.get(GlobalState, 1)).kill_switch is False  # released
        assert any("released" in p["message"] for p in chat.posted)
    finally:
        await db.dispose()
