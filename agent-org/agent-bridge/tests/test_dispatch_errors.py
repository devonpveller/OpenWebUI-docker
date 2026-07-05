"""Dispatch-failure SURFACING. A worker that can't run (no repo focused → daemon 409, or any
delegation error) must (a) be reported in readable, actionable language and (b) surface UP to the
operator's conversation — never a raw 409 buried in the effort thread while the system sits idle.
Fakes only."""

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
    return orch, orch.chat, orch.harness, db


# ── raw worker errors are translated to readable, actionable lines ──────────
def test_friendly_dispatch_error_translates():
    e409nofocus = Exception("Client error '409 Conflict' for url 'http://ao-worker-2:8090/tasks': "
                            "no project focused - run /project first")
    m = Orchestrator._friendly_dispatch_error(e409nofocus)
    assert "409" in m and "repo focused" in m and "archive" in m
    ebusy = Exception("Client error '409 Conflict' for url 'http://ao-worker-1:8090/tasks'")
    assert "busy" in Orchestrator._friendly_dispatch_error(ebusy)
    econn = Exception("ConnectError: [Errno 111] Connection refused")
    assert "unreachable" in Orchestrator._friendly_dispatch_error(econn)


# ── a delegation error reaches the OPERATOR'S conversation, not just the effort thread ─
async def test_delegation_error_reaches_operator(db_url):
    orch, chat, harness, db = await _orch(db_url)
    try:
        await orch.projects.add("mono", "https://github.com/me/mono.git")
        eid, chan, root = await orch.router.open_effort("boom", project="mono")
        orch._effort_mgmt_thread[eid] = "op-thread"                 # the operator's conversation

        async def _boom(*a, **k):   # the worker rejects the task (409), like a lost focus
            raise Exception("Client error '409 Conflict' for url '.../tasks': no project focused")
        orch.router.wake = _boom

        await orch.delegate(eid, chan, root, "do it")
        # threaded UP to the operator (operator_reply → their thread), not only the project thread
        assert any(p.get("thread_id") == "op-thread" and eid in p["message"] for p in chat.posted)
        # and it's the friendly text, not a raw stack/URL dump
        opmsg = next(p["message"] for p in chat.posted if p.get("thread_id") == "op-thread")
        assert "couldn't run" in opmsg and "repo focused" in opmsg
        # the effort is marked errored (not left looking active)
        full = await orch.gate.snapshot()
        assert eid not in orch._delegating
    finally:
        await db.dispose()


# ── single-flight: a duplicate concurrent dispatch is refused (no 409 double-wake) ─
async def test_delegate_single_flight_refuses_duplicate(db_url):
    orch, chat, harness, db = await _orch(db_url)
    try:
        await orch.projects.add("mono", "https://github.com/me/mono.git")
        eid, chan, root = await orch.router.open_effort("solo", project="mono")
        await orch.charters.set_goal(eid, "do it", created_by="po")
        orch._delegating.add(eid)                       # pretend a delegate is already in flight
        await orch.delegate(eid, chan, root, "do it")   # the duplicate must be refused
        assert len(harness.wakes) == 0                  # NOT dispatched again (would 409 a busy worker)
    finally:
        await db.dispose()


# ── re-engage threads each effort's failures back to the operator's conversation ─
async def test_reengage_associates_operator_thread(db_url):
    orch, chat, harness, db = await _orch(db_url)
    try:
        await orch.projects.add("mono", "https://github.com/me/mono.git")
        eid, chan, root = await orch.router.open_effort("work", project="mono")
        await orch.charters.set_goal(eid, "do it", created_by="po")
        mgmt = await orch.mgmt_channel_id()
        await orch._reengage([eid], mgmt_channel=mgmt, mgmt_thread="conv-1")
        # the effort is now bound to the operator's thread so summaries/errors land there
        assert orch._effort_mgmt_thread.get(eid) == "conv-1"
    finally:
        await db.dispose()


# ── LIVE 2026-07-05: a workspace collision must NOT be blamed on repo/token ────────
async def test_workspace_collision_is_not_misdiagnosed_as_private_repo(db_url):
    """clone failed (exit 128): "destination path '/workspace' already exists and is not an empty
    directory" = another effort holds this worker's checkout (a dispatch race). The live message
    said "private or missing … deploy token" — a token rabbit hole. It must say collision."""
    orch, chat, harness, db = await _orch(db_url)
    try:
        await orch.projects.add("mono", "https://github.com/me/mono.git")
        eid, chan, root = await orch.router.open_effort("collide", project="mono")
        harness.set_project_fails = ("clone failed (exit 128): fatal: destination path "
                                     "'/workspace' already exists and is not an empty directory.")
        await orch.delegate(eid, chan, root, "do it")
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "busy with another effort" in msgs, "collision not named"
        assert "private or missing" not in msgs, "still misdiagnosed as a repo/token problem"
    finally:
        await db.dispose()


async def test_genuine_auth_clone_failure_still_says_private_or_missing(db_url):
    orch, chat, harness, db = await _orch(db_url)
    try:
        await orch.projects.add("mono", "https://github.com/me/mono.git")
        eid, chan, root = await orch.router.open_effort("denied", project="mono")
        harness.set_project_fails = ("clone failed (exit 128): fatal: Authentication failed for "
                                     "'https://github.com/me/mono.git'")
        await orch.delegate(eid, chan, root, "do it")
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "private or missing" in msgs
    finally:
        await db.dispose()
