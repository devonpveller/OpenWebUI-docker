"""End-to-end P0-P2 loop through the orchestrator with fakes (no infra):
prove the loop *and* that we can stop it (governance §9 build order)."""

from __future__ import annotations

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


async def test_wake_on_mention_posts_reply(db_url):
    orch, chat, harness, db = await _orch(db_url)
    try:
        effort_id, channel_id = await orch.router.ensure_effort_channel("demo")
        orch.events.track_channel(channel_id)
        # An inbound @mention in the effort channel wakes a worker (P0.4/P1.3).
        await orch.handle_event(
            {"id": "p1", "channel_id": channel_id, "thread_id": "t1",
             "message": "@worker please start", "is_bot": False, "ts": 1}
        )
        assert len(harness.wakes) == 1
        assert harness.wakes[0]["session_id"] == "t1"
        # the worker's reply was posted back on the bus (bus-only comms).
        assert any(p["channel_id"] == channel_id for p in chat.posted)
    finally:
        await db.dispose()


async def test_concern_freezes_and_operator_clears(db_url):
    orch, chat, harness, db = await _orch(db_url)
    try:
        effort_id, channel_id = await orch.router.ensure_effort_channel("demo")
        # PM raises a hard-gate CONCERN -> effort frozen + CONCERN posted to #mgmt.
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

        # While frozen, a wake is refused (composition rule — no compute while frozen).
        pre = len(harness.wakes)
        await orch.handle_event(
            {"id": "p2", "channel_id": channel_id, "thread_id": "t1",
             "message": "keep going", "is_bot": False, "ts": 2}
        )
        assert len(harness.wakes) == pre  # no wake happened

        # The Human Operator replies in #mgmt to clear the hard-gate.
        await orch.handle_event(
            {"id": "p3", "channel_id": mgmt, "thread_id": None,
             "message": f"approve {effort_id} looks fine", "is_bot": False, "ts": 3}
        )
        assert await orch.gate.can_dispatch(effort_id) is True
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
