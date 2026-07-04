"""The PM can ACT, not just narrate. Idle efforts don't auto-run; the PM re-dispatches them
(reengage) and cancels stale ones (archive) FOR REAL — no phantom 'queued, will proceed as
resources free up'. Regression for the transcript where the PM made empty promises. Fakes only."""

from __future__ import annotations

import asyncio
from pathlib import Path

from app.adapters.chat import FakeChatAdapter
from app.config import Settings
from app.db import Database
from app.modules.model_router import FakeModelClient
from app.orchestrator import Orchestrator
from app.schemas import OperatorIntent
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
    for _ in range(12):
        if not orch._bg_tasks:
            return
        await asyncio.gather(*list(orch._bg_tasks), return_exceptions=True)


async def _idle_effort(orch, name, goal="do the thing"):
    """An OPEN effort with a goal recorded but no worker running — the 'idle, won't auto-start' case."""
    eid, chan, root = await orch.router.open_effort(name)
    await orch.charters.set_goal(eid, goal, created_by="po")
    return eid, chan, root


# ── honest status: an open effort with nothing running is 'idle', not 'active' ──
async def test_status_map_reports_idle_not_active(db_url):
    orch, chat, harness, db = await _orch(db_url)
    try:
        eid, _c, _r = await _idle_effort(orch, "stuck")
        efforts = await orch.gate.snapshot(open_only=True)
        smap = await orch._effort_status_map(efforts)
        assert smap[eid] == "idle"                              # NOT "active"
        rendered = orch._render_status(efforts, smap)
        assert "idle" in rendered and "Nothing is running" in rendered
    finally:
        await db.dispose()


# ── reengage: idle effort is actually RE-DISPATCHED (a worker runs) ──────────
async def test_reengage_dispatches_idle_effort(db_url):
    orch, chat, harness, db = await _orch(db_url)
    try:
        eid, _c, _r = await _idle_effort(orch, "port-shader", goal="port the shader")
        assert len(harness.wakes) == 0                          # idle: nothing ran
        mgmt = await orch.mgmt_channel_id()
        await orch._reengage([eid], mgmt_channel=mgmt)
        await _drain(orch)
        assert len(harness.wakes) == 1                          # ACTUALLY dispatched a worker
        assert any("Dispatching workers now" in p["message"] for p in chat.posted)
    finally:
        await db.dispose()


# ── the dispatch message LINKS to the live effort thread (observability = safety) ──
async def test_reengage_links_effort_thread_when_permalink_available(db_url):
    """The work streams to the effort THREAD (#proj-<slug>), not #mgmt — so the dispatch message
    must link straight to it, or the operator is left hunting ('the pm says see the project thread,
    but there's nothing there'). When the adapter can't build a permalink it degrades to a plain id."""
    orch, chat, harness, db = await _orch(db_url)
    try:
        eid, _c, root = await _idle_effort(orch, "port-shader", goal="port the shader")
        mgmt = await orch.mgmt_channel_id()
        # permalink unavailable (no site url / team) → degrades to a plain `id`, never a broken link
        await orch._reengage([eid], mgmt_channel=mgmt)
        msg = next(p["message"] for p in chat.posted if "Dispatching workers now" in p["message"])
        assert f"`{eid}`" in msg and "](" not in msg               # plain id, no link
        # now the adapter CAN build a permalink → the id becomes a clickable markdown link to the thread
        chat.posted.clear()
        chat.permalink_base = "https://mm.example/team"
        eid2, _c2, root2 = await _idle_effort(orch, "port-audio", goal="port the audio")
        await orch._reengage([eid2], mgmt_channel=mgmt)
        msg2 = next(p["message"] for p in chat.posted if "Dispatching workers now" in p["message"])
        assert f"[`{eid2}`](https://mm.example/team/pl/{root2})" in msg2   # clickable → live thread
    finally:
        await db.dispose()


# ── reengage via NL: "get the workers working" fires, no empty promise ───────
async def test_nl_reengage_fires_not_promises(db_url):
    orch, chat, harness, db = await _orch(db_url)
    try:
        eid, _c, _r = await _idle_effort(orch, "monogame-setup", goal="clone + upstream")
        orch.models._client.queue_structured(
            OperatorIntent(kind="reengage", target_filter="monogame", reply="On it —"))
        mgmt = await orch.mgmt_channel_id()
        await orch.nl_intake("get the workers working on the monogame tasks", mgmt, thread_id="t1")
        await _drain(orch)
        assert len(harness.wakes) == 1                          # dispatched, not promised
        posts = " ".join(p["message"] for p in chat.posted)
        assert "Dispatching workers now" in posts
        assert "resources become available" not in posts       # the phantom-queue lie is gone
    finally:
        await db.dispose()


async def test_reengage_skips_already_running(db_url):
    orch, chat, harness, db = await _orch(db_url)
    try:
        eid, _c, _r = await _idle_effort(orch, "busy")
        orch._delegating.add(eid)                               # pretend it's executing now
        mgmt = await orch.mgmt_channel_id()
        await orch._reengage([eid], mgmt_channel=mgmt)
        await _drain(orch)
        assert len(harness.wakes) == 0                          # not double-dispatched
        assert any("already running" in p["message"] for p in chat.posted)
    finally:
        await db.dispose()


# ── archive: 'yes, abort the calculators' actually cancels them ──────────────
async def test_archive_cancels_open_efforts_by_filter(db_url):
    orch, chat, harness, db = await _orch(db_url)
    try:
        for n in ("calculator-add", "calculator-divide", "monogame-setup"):
            await _idle_effort(orch, n)
        mgmt = await orch.mgmt_channel_id()
        efforts = await orch.gate.snapshot(open_only=True)
        targets = orch._select_efforts(
            OperatorIntent(kind="archive", target_filter="calculator"), efforts)
        assert set(targets) == {"effort-calculator-add", "effort-calculator-divide"}
        await orch._archive_efforts(targets, mgmt_channel=mgmt)
        # the two calculators are now aborted (gone from the open view); monogame stays
        opens = {e["id"] for e in await orch.gate.snapshot(open_only=True)}
        assert "effort-calculator-add" not in opens and "effort-calculator-divide" not in opens
        assert "effort-monogame-setup" in opens
        assert any("Archived" in p["message"] for p in chat.posted)
    finally:
        await db.dispose()


async def test_nl_abort_confirmation_actually_archives(db_url):
    orch, chat, harness, db = await _orch(db_url)
    try:
        await _idle_effort(orch, "calculator-x")
        orch.models._client.queue_structured(
            OperatorIntent(kind="archive", target_filter="calculator", reply="Sure —"))
        mgmt = await orch.mgmt_channel_id()
        await orch.nl_intake("yes, abort the calculator tasks", mgmt, thread_id="t1")
        opens = {e["id"] for e in await orch.gate.snapshot(open_only=True)}
        assert "effort-calculator-x" not in opens              # actually cancelled, not "flagged"
        assert any("Archived" in p["message"] for p in chat.posted)
        assert not any("flag" in p["message"].lower() for p in chat.posted)
    finally:
        await db.dispose()


# ── /retry and /archive commands ────────────────────────────────────────────
async def test_retry_and_archive_commands(db_url):
    orch, chat, harness, db = await _orch(db_url)
    try:
        await _idle_effort(orch, "calc-1")
        eid2, _c, _r = await _idle_effort(orch, "mono-1", goal="do mono")
        mgmt = await orch.mgmt_channel_id()
        await orch._handle_command("/archive calc", mgmt, "t1")
        assert "effort-calc-1" not in {e["id"] for e in await orch.gate.snapshot(open_only=True)}
        await orch._handle_command("/retry mono", mgmt, "t1")
        await _drain(orch)
        assert any(w for w in harness.wakes)                    # /retry dispatched mono-1
    finally:
        await db.dispose()
