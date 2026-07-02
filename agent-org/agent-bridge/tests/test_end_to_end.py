"""End-to-end P0-P2 loop through the orchestrator with fakes (no infra):
prove the loop *and* that we can stop it (governance §9 build order)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.adapters.chat import FakeChatAdapter
from app.config import Settings
from app.db import Database
from app.modules.model_router import FakeModelClient
from app.orchestrator import Orchestrator
from app.schemas import Concern, Trigger
from app.worker.harness import FakeHarness

ROOT = Path(__file__).resolve().parents[1]


async def _orch(db_url):
    settings = Settings(
        _env_file=None,
        chat_adapter="fake",
        profiles_dir=str(ROOT / "profiles"),
        charters_dir=str(ROOT / "charters"),
        floor_dir=str(ROOT / "floor"),
        worker_instance_urls="http://w1:8090",
        max_concurrent_workers=1,
        database_url=db_url,
    )
    db = Database(db_url)
    chat = FakeChatAdapter()
    harness = FakeHarness()
    orch = Orchestrator(settings, db, chat, model_client=FakeModelClient(), harness=harness)
    await orch.setup()
    return orch, chat, harness, db


async def test_wake_in_effort_thread_posts_reply(db_url):
    orch, chat, harness, db = await _orch(db_url)
    try:
        # An effort is a THREAD in its project channel (COMMS-MODEL §4 / CM.1).
        effort_id, channel_id, root = await orch.router.open_effort("demo")
        orch.events.track_channel(channel_id)
        # A reply IN the effort thread (root_id = the effort-card post id) wakes the worker.
        await orch.handle_event(
            {"id": "p1", "channel_id": channel_id, "thread_id": root,
             "message": "@worker please start", "is_bot": False, "ts": 1}
        )
        assert len(harness.wakes) == 1
        # --session continuity: the session id is the effort id (stable across replies), not the
        # individual reply post id.
        assert harness.wakes[0]["session_id"] == effort_id
        # the worker's reply streamed back into the SAME thread (bus-only comms, threaded).
        assert any(
            p["channel_id"] == channel_id and p.get("thread_id") == root for p in chat.posted
        )
    finally:
        await db.dispose()


async def test_two_efforts_share_one_project_channel(db_url):
    """CM.1 done-when: two efforts in the same project appear as two THREADS in one
    #proj-<slug> channel — no per-effort channel, so the sidebar count is stable."""
    orch, chat, harness, db = await _orch(db_url)
    try:
        e1, ch1, root1 = await orch.router.open_effort("alpha")
        e2, ch2, root2 = await orch.router.open_effort("beta")
        assert ch1 == ch2                 # one shared project channel
        assert root1 != root2             # two distinct effort threads
        assert e1 != e2
        # exactly one project channel was created (plus #mgmt/#incidents/#suggestions).
        assert list(chat.channels).count("proj-sandbox") == 1
    finally:
        await db.dispose()


async def test_concern_freezes_escalates_and_closure_comes_back_down(db_url):
    orch, chat, harness, db = await _orch(db_url)
    try:
        effort_id, channel_id, root = await orch.router.open_effort("demo")
        # PM raises a hard-gate CONCERN -> effort frozen + CONCERN posted to #mgmt + escalation
        # raised into the effort thread (CM.3 escalation ladder).
        concern = Concern(
            intent_thread="ship X aligned",
            what_surfaced="worker refused an unsafe step",
            intent_of_change="refusal must block, never be routed around (F3)",
            pm_recommendation="hold for human",
            blocked_efforts=[effort_id],
        )
        await orch.raise_concern(effort_id, Trigger.refusal, concern)
        assert await orch.gate.can_dispatch(effort_id) is False
        mgmt = await orch.mgmt_channel_id()
        assert any("CONCERN" in p["message"] and p["channel_id"] == mgmt for p in chat.posted)
        # CM.3: the up-signal is visible IN the effort thread (not just #mgmt).
        assert any(
            p.get("thread_id") == root and "Escalated" in p["message"] for p in chat.posted
        )

        # While frozen, a thread reply is refused (composition rule — no compute while frozen).
        pre = len(harness.wakes)
        await orch.handle_event(
            {"id": "p2", "channel_id": channel_id, "thread_id": root,
             "message": "keep going", "is_bot": False, "ts": 2}
        )
        assert len(harness.wakes) == pre  # no wake happened

        # The Human Operator replies in #mgmt to clear the hard-gate.
        await orch.handle_event(
            {"id": "p3", "channel_id": mgmt, "thread_id": None,
             "message": f"approve {effort_id} looks fine", "is_bot": False, "ts": 3}
        )
        assert await orch.gate.can_dispatch(effort_id) is True
        # ⭐ CM.4 "bring the audience back down": the resolution is echoed into the effort thread.
        assert any(
            p.get("thread_id") == root and "resuming" in p["message"].lower()
            for p in chat.posted
        )
    finally:
        await db.dispose()


async def test_slash_effort_command_creates_and_replies(db_url):
    orch, chat, harness, db = await _orch(db_url)
    try:
        mgmt = await orch.mgmt_channel_id()
        await orch.handle_event(
            {"id": "c1", "channel_id": mgmt, "message": "@bot-pm /effort demo",
             "is_bot": False, "ts": 1}
        )
        assert await orch.gate.state_of("effort-demo") == "active"
        assert any("opened effort" in p["message"].lower() for p in chat.posted)
    finally:
        await db.dispose()


async def test_nl_message_routes_to_po(db_url):
    from app.schemas import OperatorIntent

    orch, chat, harness, db = await _orch(db_url)
    try:
        mgmt = await orch.mgmt_channel_id()
        # The PO agent (FakeModelClient) returns a conversational reply for plain language.
        orch.models._client.queue_structured(
            OperatorIntent(kind="chitchat", reply="Hi — I'm your PO. What would you like built?")
        )
        await orch.handle_event(
            {"id": "h1", "channel_id": mgmt, "message": "hey, are you there?",
             "is_bot": False, "ts": 1}
        )
        assert any("I'm your PO" in p["message"] for p in chat.posted)
    finally:
        await db.dispose()


async def test_nl_request_opens_effort(db_url):
    from app.schemas import OperatorIntent

    orch, chat, harness, db = await _orch(db_url)
    try:
        mgmt = await orch.mgmt_channel_id()
        orch.models._client.queue_structured(
            OperatorIntent(kind="request", effort_name="dark-mode",
                           reply="On it — I'll scope a dark-mode effort.")
        )
        await orch.handle_event(
            {"id": "r1", "channel_id": mgmt, "message": "can you add a dark mode toggle?",
             "is_bot": False, "ts": 1}
        )
        assert await orch.gate.state_of("effort-dark-mode") == "active"  # effort opened from NL
        assert any("dark-mode" in p["message"] for p in chat.posted)
        # a background delegation was spawned; let it finish (FakeHarness completes instantly).
        if orch._bg_tasks:
            await asyncio.gather(*orch._bg_tasks)
        assert len(harness.wakes) == 1  # the worker was dispatched
    finally:
        await db.dispose()


async def test_nl_decision_requires_explicit_confirmation(db_url):
    from app.schemas import OperatorIntent

    orch, chat, harness, db = await _orch(db_url)
    try:
        eid, _ = await orch.router.ensure_effort_channel("demo")
        # freeze it (hard-gate) so a "decision" is pending
        from app.schemas import Concern, Trigger
        await orch.raise_concern(
            eid, Trigger.refusal,
            Concern(intent_thread="i", what_surfaced="refusal", intent_of_change="blocks"),
        )
        mgmt = await orch.mgmt_channel_id()
        orch.models._client.queue_structured(
            OperatorIntent(kind="decision", effort_id=eid, decision="approve",
                           reply="Sounds good.")
        )
        await orch.handle_event(
            {"id": "d1", "channel_id": mgmt, "message": "yeah that's fine, go ahead",
             "is_bot": False, "ts": 1}
        )
        # NL must NOT auto-clear a hard-gate — it asks for the explicit command.
        assert await orch.gate.can_dispatch(eid) is False
        assert any(f"approve {eid}" in p["message"] for p in chat.posted)
    finally:
        await db.dispose()


async def test_status_command_lists_efforts(db_url):
    orch, chat, harness, db = await _orch(db_url)
    try:
        mgmt = await orch.mgmt_channel_id()
        await orch.router.ensure_effort_channel("demo")
        await orch.handle_event(
            {"id": "s1", "channel_id": mgmt, "message": "/status", "is_bot": False, "ts": 1}
        )
        assert any("effort-demo" in p["message"] for p in chat.posted)
    finally:
        await db.dispose()


async def test_kill_switch_from_mgmt(db_url):
    orch, chat, harness, db = await _orch(db_url)
    try:
        effort_id, channel_id = await orch.router.ensure_effort_channel("demo")
        mgmt = await orch.mgmt_channel_id()
        await orch.handle_event(
            {"id": "k1", "channel_id": mgmt, "message": "kill", "is_bot": False, "ts": 1}
        )
        assert await orch.gate.can_dispatch(effort_id) is False  # everything frozen
        await orch.handle_event(
            {"id": "k2", "channel_id": mgmt, "message": "unkill", "is_bot": False, "ts": 2}
        )
        assert await orch.gate.can_dispatch(effort_id) is True
    finally:
        await db.dispose()
