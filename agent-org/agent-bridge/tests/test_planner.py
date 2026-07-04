"""The PLANNER (P-APL.2) + executor (P-APL.3). The operator describes a multi-step architecture in
plain language; the planner drafts a CONCRETE, reviewable plan of primitives (fork / add_submodule) +
worker tasks; nothing runs until the operator approves the WHOLE plan; then the executor dispatches
each step to its governed primitive or a worker. This is the GENERAL mechanism (any project/
architecture) that replaced the hardcoded `compose` recipe. Fakes + a mocked GitHub."""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

from app.adapters.chat import FakeChatAdapter
from app.config import Settings
from app.db import Database
from app.modules.model_router import FakeModelClient
from app.orchestrator import Orchestrator
from app.schemas import LifecyclePlan, LifecycleStep, OperatorIntent
from app.worker.harness import FakeHarness

ROOT = Path(__file__).resolve().parents[1]


async def _orch(db_url, tmp_path):
    key = tmp_path / "app.pem"
    key.write_text("dummy")  # so github_app_enabled is True (FakeGitHubApp never reads it)
    settings = Settings(
        _env_file=None, chat_adapter="fake",
        profiles_dir=str(ROOT / "profiles"), charters_dir=str(ROOT / "charters"),
        floor_dir=str(ROOT / "floor"), worker_instance_urls="http://w1:8090",
        max_concurrent_workers=1, database_url=db_url, project_survey_enabled=False,
        review_mode="off", plan_approval="off",
        github_app_id="1", github_app_owner="devonpveller",
        github_app_private_key_path=str(key),
    )
    db = Database(db_url)
    orch = Orchestrator(settings, db, FakeChatAdapter(),
                        model_client=FakeModelClient(), harness=FakeHarness())
    await orch.setup()
    return orch, orch.chat, db


async def _drain(orch):
    for _ in range(12):
        if not orch._bg_tasks:
            return
        await asyncio.gather(*list(orch._bg_tasks), return_exceptions=True)


async def test_plan_drafts_presents_then_executes_on_approve(db_url, tmp_path):
    orch, chat, db = await _orch(db_url, tmp_path)
    try:
        # existing state the planner reasons over: the engine repo + one fork already registered
        await orch.projects.add("monogame-engine", "https://github.com/devonpveller/MonoGame-Engine")
        await orch.projects.add("murder", "https://github.com/devonpveller/murder",
                                upstream_url="https://github.com/isadorasophia/murder")
        orch._gh_transport = httpx.MockTransport(lambda r: httpx.Response(
            202, json={"full_name": "devonpveller/MonoGame",
                       "html_url": "https://github.com/devonpveller/MonoGame"}))
        # 1st structured call = intent classification (kind=plan); 2nd = the planner's drafted plan
        orch.models._client.queue_structured(OperatorIntent(kind="plan", reply="Let me draft that."))
        orch.models._client.queue_structured(LifecyclePlan(goal="engine vendoring my forks", steps=[
            LifecycleStep(kind="fork", source="MonoGame/MonoGame", summary="fork monogame"),
            LifecycleStep(kind="add_submodule", source="murder", target="monogame-engine",
                          path="murder", summary="add murder submodule"),
            LifecycleStep(kind="worker_task", target="monogame-engine",
                          task="wire murder to build against the monogame submodule source",
                          summary="wire the build"),
        ]))
        mgmt = await orch.mgmt_channel_id()
        await orch.nl_intake(
            "set up monogame-engine to vendor my forks as submodules and wire the build",
            mgmt, thread_id="t")

        # PRESENTED + held — nothing ran
        assert orch._pending_lifecycle, "a plan should be pending approval"
        pid = next(iter(orch._pending_lifecycle))
        assert any("Plan" in p["message"] and "fork" in p["message"].lower()
                   and "submodule" in p["message"].lower() for p in chat.posted)
        assert not orch.harness.submodules and not orch.harness.wakes   # NOT executed on draft

        # APPROVE → executor runs every step
        await orch.handle_event({"id": "d1", "channel_id": mgmt, "message": f"approve {pid}",
                                 "is_bot": False, "ts": 2})
        await _drain(orch)
        assert pid not in orch._pending_lifecycle
        # the submodule was added (via the operator-plane git executor)
        assert any(path == "murder" for (_b, _u, path) in orch.harness.submodules)
        # the fork ran (monogame fork now registered as a project)
        assert await orch.projects.resolve("monogame") is not None
        # the worker task was dispatched
        assert len(orch.harness.wakes) >= 1
        assert any("Plan run" in m["message"] for m in chat.posted)
    finally:
        await db.dispose()


async def test_plan_abort_runs_nothing(db_url, tmp_path):
    orch, chat, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("monogame-engine", "https://github.com/devonpveller/MonoGame-Engine")
        orch.models._client.queue_structured(OperatorIntent(kind="plan", reply="Drafting."))
        orch.models._client.queue_structured(LifecyclePlan(goal="x", steps=[
            LifecycleStep(kind="worker_task", target="monogame-engine", task="do a thing", summary="t")]))
        mgmt = await orch.mgmt_channel_id()
        await orch.nl_intake("build something multi-step in monogame-engine", mgmt, thread_id="t")
        pid = next(iter(orch._pending_lifecycle))
        await orch.handle_event({"id": "d1", "channel_id": mgmt, "message": f"abort {pid}",
                                 "is_bot": False, "ts": 2})
        assert pid not in orch._pending_lifecycle
        assert not orch.harness.wakes                                   # nothing dispatched
        assert any("dropped" in m["message"].lower() for m in chat.posted)
    finally:
        await db.dispose()


async def test_planner_empty_plan_asks_for_more(db_url, tmp_path):
    orch, chat, db = await _orch(db_url, tmp_path)
    try:
        orch.models._client.queue_structured(OperatorIntent(kind="plan", reply="Hmm."))
        orch.models._client.queue_structured(LifecyclePlan(goal="", steps=[]))   # model gave nothing
        mgmt = await orch.mgmt_channel_id()
        await orch.nl_intake("do the architecture thing", mgmt, thread_id="t")
        assert not orch._pending_lifecycle
        assert any("concrete steps" in m["message"] for m in chat.posted)
    finally:
        await db.dispose()
