"""Fix 1 (PO progress visibility) + Fix 2 (effort-list hygiene).

Fix 1: the router records each worker's streamed command activity per effort, and the PO
surfaces it — so "what's going on?" is answered from FACTS, not "I have no visibility".
Fix 2: efforts carry a lifecycle (open|done|aborted); the default `/status` + PO status view
show only what's still in play, so finished test efforts don't drown the live signal.
Fakes only — no network, no model, no GPU.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from app.adapters.chat import FakeChatAdapter
from app.config import Settings
from app.db import Database
from app.modules.model_router import FakeModelClient
from app.orchestrator import Orchestrator
from app.schemas import Decision, OperatorIntent, Trigger
from app.worker.harness import FakeHarness

ROOT = Path(__file__).resolve().parents[1]


async def _orch(db_url, *, harness=None):
    settings = Settings(
        _env_file=None, chat_adapter="fake",
        profiles_dir=str(ROOT / "profiles"), charters_dir=str(ROOT / "charters"),
        floor_dir=str(ROOT / "floor"), worker_instance_urls="http://w1:8090",
        max_concurrent_workers=1, database_url=db_url, project_survey_enabled=False,
        review_mode="off", plan_approval="off",
    )
    db = Database(db_url)
    orch = Orchestrator(settings, db, FakeChatAdapter(),
                        model_client=FakeModelClient(), harness=harness or FakeHarness())
    await orch.setup()
    return orch, orch.chat, orch.harness, db


async def _drain(orch):
    if orch._bg_tasks:
        await asyncio.gather(*orch._bg_tasks)


def _concern(effort_id: str):
    from app.schemas import Concern
    return Concern(
        intent_thread="x", what_surfaced="drift", intent_of_change="matters",
        pm_recommendation="hold", blocked_efforts=[effort_id],
    )


# ── Fix 1: streamed commands are recorded + queryable per effort ─────────────
async def test_router_records_streamed_command_activity(db_url):
    cmds = ["git clone https://github.com/acme/x", "npm install", "npm test"]
    orch, chat, harness, db = await _orch(db_url, harness=FakeHarness(stream_commands=cmds))
    try:
        eid, chan, root = await orch.router.open_effort("build-x")
        await orch.delegate(eid, chan, root, "build x")          # routine → light path → done
        act = orch.router.recent_activity(eid)
        # every streamed command is captured (newest last), plus the answer line
        assert any("git clone" in a for a in act)
        assert any("npm test" in a for a in act)
        assert act[-1].startswith("💬")                          # the answer is the last activity
    finally:
        await db.dispose()


# ── Fix 1: the PO surfaces real activity for a status question (not "no visibility") ─
async def test_po_status_reply_surfaces_worker_activity(db_url):
    # result_status="error" keeps the effort OPEN (no done → still in the open /status view),
    # while the commands still stream through and get recorded.
    harness = FakeHarness(result_status="error", stream_commands=["cargo build --release"])
    orch, chat, harness, db = await _orch(db_url, harness=harness)
    try:
        eid, chan, root = await orch.router.open_effort("compiler")
        await orch.delegate(eid, chan, root, "compile it")       # streams the command, stays open
        assert any("cargo build" in a for a in orch.router.recent_activity(eid))

        # Operator asks "what's going on?" → PO status path folds the activity into the reply.
        orch.models._client.queue_structured(
            OperatorIntent(kind="status", reply="Here's where things stand:"))
        mgmt = await orch.mgmt_channel_id()
        await orch.nl_intake("what's going on?", mgmt, user_id="op", thread_id="t1")
        await _drain(orch)
        # the PO's status reply names the effort AND shows the real command it ran
        status_posts = [p["message"] for p in chat.posted if "where things stand" in p["message"]]
        assert status_posts and "cargo build" in status_posts[-1]
    finally:
        await db.dispose()


async def test_worker_activity_ctx_only_lists_efforts_with_activity(db_url):
    orch, chat, harness, db = await _orch(db_url)
    try:
        eid, _c, _r = await orch.router.open_effort("quiet")
        # no activity yet → context is the explicit "none yet" sentinel, not a fabricated line
        efforts = await orch.gate.snapshot(open_only=True)
        assert "none yet" in orch._worker_activity_ctx(efforts)
        orch.router._record_activity(eid, "✅ git status")
        assert "git status" in orch._worker_activity_ctx(efforts)
    finally:
        await db.dispose()


# ── Fix 2: a finished effort drops out of the default (open) status view ──────
async def test_finished_effort_marked_done_and_hidden_from_open_status(db_url):
    orch, chat, harness, db = await _orch(db_url)
    try:
        eid, chan, root = await orch.router.open_effort("ship-it")
        await orch.delegate(eid, chan, root, "ship it")          # routine → done
        # lifecycle flipped to done
        full = await orch.gate.snapshot()
        assert next(e for e in full if e["id"] == eid)["lifecycle"] == "done"
        # hidden from the open-only view (what the PO + default /status reason over)
        opens = await orch.gate.snapshot(open_only=True)
        assert all(e["id"] != eid for e in opens)

        mgmt = await orch.mgmt_channel_id()
        await orch._handle_command("/status", mgmt, "t1")
        assert any("no open efforts" in p["message"] for p in chat.posted)
        # …but `/status all` still shows it, tagged with its lifecycle
        chat.posted.clear()
        await orch._handle_command("/status all", mgmt, "t1")
        allpost = "\n".join(p["message"] for p in chat.posted)
        assert eid in allpost and "[done]" in allpost
    finally:
        await db.dispose()


# ── Fix 2: `/status <id>` targets one effort regardless of lifecycle + shows activity ─
async def test_status_by_id_shows_done_effort_with_activity(db_url):
    orch, chat, harness, db = await _orch(db_url, harness=FakeHarness(stream_commands=["make"]))
    try:
        eid, chan, root = await orch.router.open_effort("targeted")
        await orch.delegate(eid, chan, root, "do it")            # done + recorded "make"
        mgmt = await orch.mgmt_channel_id()
        chat.posted.clear()                                      # isolate the /status output
        await orch._handle_command(f"/status {eid}", mgmt, "t1")
        post = next(p["message"] for p in chat.posted if eid in p["message"])
        assert "make" in post                                    # activity line present
        assert "[done]" in post                                  # lifecycle tag shown on targeted view
    finally:
        await db.dispose()


# ── Fix 2: aborting an effort's concern marks it aborted (drops from open view) ─
async def test_abort_sets_aborted_lifecycle(db_url):
    orch, chat, harness, db = await _orch(db_url)
    try:
        await orch.gate.ensure_effort("e-abort", "e-abort")
        await orch.gate.freeze("e-abort", Trigger.deviation, _concern("e-abort"))
        await orch.gate.clear("e-abort", Decision(decision="abort"), actor_role="human")
        full = await orch.gate.snapshot()
        assert next(e for e in full if e["id"] == "e-abort")["lifecycle"] == "aborted"
        opens = await orch.gate.snapshot(open_only=True)
        assert all(e["id"] != "e-abort" for e in opens)
    finally:
        await db.dispose()


async def test_set_lifecycle_is_idempotent(db_url):
    orch, chat, harness, db = await _orch(db_url)
    try:
        await orch.gate.ensure_effort("e-idem", "e-idem")
        await orch.gate.set_lifecycle("e-idem", "done")
        await orch.gate.set_lifecycle("e-idem", "done")          # no-op, no raise
        full = await orch.gate.snapshot()
        assert next(e for e in full if e["id"] == "e-idem")["lifecycle"] == "done"
    finally:
        await db.dispose()
