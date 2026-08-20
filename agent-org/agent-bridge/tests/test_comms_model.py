"""COMMS-MODEL (CM.1–CM.6) — deterministic intent→destination routing, project-channel/
effort-thread taxonomy, function channels, suggestion + incident surfacing, effort-card status.
Fakes only — no infra, no GPU."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.adapters.chat import FakeChatAdapter
from app.config import Settings
from app.db import Database
from app.modules.comms_router import Intent
from app.modules.model_router import FakeModelClient
from app.orchestrator import Orchestrator
from app.schemas import Concern, Trigger
from app.worker.harness import FakeHarness

ROOT = Path(__file__).resolve().parents[1]


async def _orch(db_url):
    settings = Settings(
        _env_file=None, chat_adapter="fake",
        profiles_dir=str(ROOT / "profiles"), charters_dir=str(ROOT / "charters"),
        floor_dir=str(ROOT / "floor"), worker_instance_urls="http://w1:8090",
        max_concurrent_workers=1, database_url=db_url,
    )
    db = Database(db_url)
    orch = Orchestrator(
        settings, db, FakeChatAdapter(), model_client=FakeModelClient(), harness=FakeHarness()
    )
    await orch.setup()
    return orch, orch.chat, db


# ── CM.2: the §2 routing table is a pure, total function ─────────────────────
async def test_routing_table_resolves_each_intent(db_url):
    orch, chat, db = await _orch(db_url)
    try:
        eid, proj_chan, root = await orch.router.open_effort("demo")
        mgmt = await orch.mgmt_channel_id()
        comms = orch.comms
        # #mgmt intents (steering surface): decisions are MADE + RECORDED here.
        for it in (Intent.operator_reply, Intent.concern, Intent.decision):
            assert await comms.resolve(it) == (mgmt, None)
        # effort-thread intents land in (project_channel, effort_thread_root).
        for it in (Intent.effort_dispatch, Intent.worker_activity, Intent.escalation, Intent.closure):
            assert await comms.resolve(it, effort_id=eid) == (proj_chan, root)
        # function channels (top-level, no thread).
        inc_chan, inc_thread = await comms.resolve(Intent.incident)
        sug_chan, sug_thread = await comms.resolve(Intent.suggestion)
        assert inc_thread is None and sug_thread is None
        assert inc_chan == chat.channels["incidents"]
        assert sug_chan == chat.channels["suggestions"]
    finally:
        await db.dispose()


async def test_thread_intent_requires_effort_id(db_url):
    orch, chat, db = await _orch(db_url)
    try:
        with pytest.raises(ValueError):
            await orch.comms.resolve(Intent.worker_activity)  # thread intent, no effort_id
    finally:
        await db.dispose()


# ── CM.5: the permanent function channels exist from boot ────────────────────
async def test_function_channels_ensured_on_boot(db_url):
    orch, chat, db = await _orch(db_url)
    try:
        assert "incidents" in chat.channels and "suggestions" in chat.channels
    finally:
        await db.dispose()


async def test_suggestion_surfaces_in_suggestions_channel(db_url):
    orch, chat, db = await _orch(db_url)
    try:
        await orch.record_suggestion(
            "worker-1", "grant read access to shared config", effort_id="effort-x"
        )
        sug = chat.channels["suggestions"]
        assert any(
            p["channel_id"] == sug and "suggestion" in p["message"].lower() for p in chat.posted
        )
    finally:
        await db.dispose()


async def test_wake_storm_posts_incident_and_freezes(db_url):
    orch, chat, db = await _orch(db_url)
    try:
        eid, proj_chan, root = await orch.router.open_effort("noisy")
        orch.s.wake_storm_max = 1  # trip fast
        for i in range(2):
            await orch.handle_event(
                {"id": f"w{i}", "channel_id": proj_chan, "thread_id": root,
                 "message": "go", "is_bot": False, "ts": i + 1}
            )
        inc = chat.channels["incidents"]
        assert any(
            p["channel_id"] == inc and "wake-storm" in p["message"].lower() for p in chat.posted
        )
        assert await orch.gate.can_dispatch(eid) is False  # still freezes per §3
    finally:
        await db.dispose()


# ── CM.6: the effort-card root post reflects live status ─────────────────────
async def test_effort_card_status_updates_on_freeze(db_url):
    orch, chat, db = await _orch(db_url)
    try:
        eid, proj_chan, root = await orch.router.open_effort("cardy")
        await orch.raise_concern(
            eid, Trigger.refusal,
            Concern(intent_thread="i", what_surfaced="refusal", intent_of_change="blocks"),
        )
        # the effort-card root post was EDITED (not re-posted) to a frozen status.
        assert any(e["id"] == root and "frozen" in e["message"].lower() for e in chat.edited)
    finally:
        await db.dispose()


# ── CM.1: worker-activity streaming batches successful commands (CM.6) ───────
async def test_activity_stream_batches_successful_commands(db_url):
    """A command-heavy task coalesces successful commands into fewer thread posts; a failed
    command flushes the batch and posts immediately with context (notification discipline)."""
    orch, chat, db = await _orch(db_url)
    try:
        eid, proj_chan, root = await orch.router.open_effort("busy")

        # A harness that streams several successful commands then one failure.
        class _Chatty(FakeHarness):
            async def wake(self, base_url, session_id, prompt, *, channel="batch", on_update=None):
                self.wakes.append({"base_url": base_url, "session_id": session_id, "prompt": prompt})
                if on_update:
                    for i in range(5):
                        await on_update("command", {"command": f"ls {i}", "ok": True})
                    await on_update("command", {"command": "git push", "ok": False,
                                                "stderr_tail": "denied by floor"})
                    await on_update("answer", {"status": "done", "answer": "done"})
                from app.worker.harness import WorkResult
                return WorkResult("done", "t1", "done")

        orch.router.harness = _Chatty()
        orch.s.activity_batch = 3  # flush every 3 successful commands
        await orch.router.wake(
            eid, role="worker-default", thread_id=root, channel_id=proj_chan,
            session_id=eid, instruction="do lots",
        )
        thread_posts = [p for p in chat.posted if p.get("thread_id") == root]
        # the failed command is surfaced on its own, with its stderr context.
        assert any("git push" in p["message"] and "denied by floor" in p["message"] for p in thread_posts)
        # 5 successful commands did NOT become 5 separate posts (they were batched).
        cmd_posts = [p for p in thread_posts if "`$ ls" in p["message"]]
        assert 0 < len(cmd_posts) < 5
    finally:
        await db.dispose()
