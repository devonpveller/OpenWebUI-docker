"""DELIVERY-PIPELINE D2 (autonomous test series red-gating the merge) + D6 (human-testing handoff).
D2: the project's check command runs on the delivered PR branch BEFORE the merge gate is presented;
red routes back to the owning effort ONCE (fix on the same branch → re-check); still red → the merge
gate is WITHDRAWN — a red never travels forward. No configured check → skipped with an honest note.
D6: every successful merge hands the operator the human-testing step; a failure report becomes a new
effort through ordinary intake. Fakes + mocked GitHub."""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from app.adapters.chat import FakeChatAdapter
from app.config import Settings
from app.db import Database
from app.modules.model_router import FakeModelClient
from app.orchestrator import Orchestrator
from app.worker.harness import FakeHarness

ROOT = Path(__file__).resolve().parents[1]


async def _orch(db_url, tmp_path):
    key = tmp_path / "app.pem"
    key.write_text("dummy")
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
    return orch, orch.chat, orch.harness, db


def _remote(state: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        if "/branches/" in p:
            handler.reads = getattr(handler, "reads", 0) + 1
            sha = "prehead000000" if handler.reads == 1 else "cafe1234beef"
            return httpx.Response(200, json={"commit": {"sha": sha}})
        if "/compare/" in p:
            return httpx.Response(200, json={"ahead_by": 1, "commits": [],
                "files": [{"filename": "src/x.py", "additions": 1, "deletions": 0}]})
        if p.endswith("/pulls") and request.method == "POST":
            return httpx.Response(201, json={"number": 5, "html_url": "https://github.com/devonpveller/Docker-Game/pull/5"})
        if "/merge" in p and request.method == "PUT":
            state["merged"] = True
            return httpx.Response(200, json={"merged": True})
        if p.count("/") == 3:
            return httpx.Response(200, json={"default_branch": "main"})
        return httpx.Response(404)
    return handler


async def _game(orch, check_cmd=""):
    await orch.projects.add("game", "https://github.com/devonpveller/Docker-Game")
    if check_cmd:
        assert await orch.projects.set_check("game", check_cmd)
    return await orch.router.open_effort("wire", project="game")


async def test_d2_pass_presents_merge_gate(db_url, tmp_path):
    orch, chat, harness, db = await _orch(db_url, tmp_path)
    try:
        eid, chan, root = await _game(orch, "dotnet build Build.sln")
        orch._gh_transport = httpx.MockTransport(_remote({}))
        harness.output_queue = ["did the work", "pushed", "CHECK: PASS"]   # step, publish, check
        await orch.delegate(eid, chan, root, "wire", plan_steps=["work"])
        assert len(harness.wakes) == 3                                     # step + publish + check
        assert "RUN THE PROJECT CHECK" in harness.wakes[2]["prompt"]
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "D2 checks passed" in msgs and "merge it" in msgs
        assert f"merge-{eid}" in orch._pending_merge                       # gate presented
    finally:
        await db.dispose()


async def test_d2_red_routes_back_then_green(db_url, tmp_path):
    orch, chat, harness, db = await _orch(db_url, tmp_path)
    try:
        eid, chan, root = await _game(orch, "dotnet build")
        orch._gh_transport = httpx.MockTransport(_remote({}))
        # step, publish, check FAIL, fix wake, re-check PASS
        harness.output_queue = ["work", "pushed", "CHECK: FAIL\nerror CS1002", "fixed it", "CHECK: PASS"]
        await orch.delegate(eid, chan, root, "wire", plan_steps=["work"])
        assert len(harness.wakes) == 5
        assert "THE PROJECT CHECK FAILED" in harness.wakes[3]["prompt"]     # routed back to the effort
        assert "error CS1002" in harness.wakes[3]["prompt"]                 # with the failing output
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "D2 check failed" in msgs                                    # surfaced in-thread
        assert "passed after one fix round" in msgs
        assert f"merge-{eid}" in orch._pending_merge                        # gate presented after green
    finally:
        await db.dispose()


async def test_d2_still_red_withdraws_the_merge_gate(db_url, tmp_path):
    orch, chat, harness, db = await _orch(db_url, tmp_path)
    try:
        eid, chan, root = await _game(orch, "dotnet build")
        orch._gh_transport = httpx.MockTransport(_remote({}))
        harness.output_queue = ["work", "pushed", "CHECK: FAIL\nboom", "tried", "CHECK: FAIL\nboom"]
        await orch.delegate(eid, chan, root, "wire", plan_steps=["work"])
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "merge gate" in msgs.lower() and "withdrawn" in msgs.lower() # red never forward
        assert f"merge-{eid}" not in orch._pending_merge                    # gate NOT presented
        assert "still failing" in msgs
    finally:
        await db.dispose()


async def test_d2_skipped_honestly_when_no_check_configured(db_url, tmp_path):
    orch, chat, harness, db = await _orch(db_url, tmp_path)
    try:
        eid, chan, root = await _game(orch)                                 # no check_cmd
        orch._gh_transport = httpx.MockTransport(_remote({}))
        await orch.delegate(eid, chan, root, "wire", plan_steps=["work"])
        assert len(harness.wakes) == 2                                      # NO check wake
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "D2 checks skipped" in msgs and "/project check" in msgs     # honest + actionable
        assert f"merge-{eid}" in orch._pending_merge
    finally:
        await db.dispose()


async def test_d6_handoff_on_merge(db_url, tmp_path):
    orch, chat, harness, db = await _orch(db_url, tmp_path)
    try:
        eid, chan, root = await _game(orch, "dotnet build")
        state: dict = {}
        orch._gh_transport = httpx.MockTransport(_remote(state))
        harness.output_queue = ["work", "pushed", "CHECK: PASS"]
        await orch.delegate(eid, chan, root, "wire", plan_steps=["work"])
        mgmt = await orch.mgmt_channel_id()
        await orch.nl_intake("merge it", mgmt, thread_id="t")
        assert state.get("merged") is True
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "Human testing (D6)" in msgs and "dotnet build" in msgs      # the handoff + local check
        assert "fix effort" in msgs                                         # the loop back to intake
    finally:
        await db.dispose()


async def test_project_check_command_sets_and_lists(db_url, tmp_path):
    orch, chat, harness, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("game", "https://github.com/devonpveller/Docker-Game")
        mgmt = await orch.mgmt_channel_id()
        await orch.handle_event({"id": "c1", "channel_id": mgmt, "is_bot": False, "ts": 1,
                                 "message": '/project check game "dotnet build Build.sln"'})
        p = await orch.projects.get("game")
        assert p["check_cmd"] == "dotnet build Build.sln"
        await orch.handle_event({"id": "c2", "channel_id": mgmt, "is_bot": False, "ts": 2,
                                 "message": "/project list"})
        msgs = " ".join(m["message"] for m in chat.posted)
        assert "🧪" in msgs and "dotnet build Build.sln" in msgs
    finally:
        await db.dispose()
