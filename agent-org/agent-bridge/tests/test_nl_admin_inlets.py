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
