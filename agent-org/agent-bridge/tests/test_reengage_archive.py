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


# ── a SCOPED reengage must never widen to unrelated efforts ──────────────────
async def test_scoped_reengage_does_not_dispatch_unrelated_efforts(db_url):
    """Regression for the transcript: 'get the workers working on monogame-engine' re-dispatched stale
    CALCULATOR efforts (against the monogame workspace) because a filter that matched nothing fell
    back to ALL idle efforts. A scoped request must dispatch nothing unrelated + say so."""
    orch, chat, harness, db = await _orch(db_url)
    try:
        await _idle_effort(orch, "calculator-percent-function")     # unrelated, idle, in sandbox
        await _idle_effort(orch, "add-hello-function")              # unrelated, idle
        orch.models._client.queue_structured(
            OperatorIntent(kind="reengage", target_filter="monogame-engine", reply="On it —"))
        mgmt = await orch.mgmt_channel_id()
        await orch.nl_intake("get the workers working on monogame-engine", mgmt, thread_id="t")
        await _drain(orch)
        assert len(harness.wakes) == 0                              # NOTHING unrelated dispatched
        posts = " ".join(p["message"] for p in chat.posted)
        assert "no idle effort" in posts and "monogame-engine" in posts   # helpful, offers to start new
        assert "Dispatching workers now" not in posts              # did NOT grab the calculators
    finally:
        await db.dispose()


async def test_scoped_reengage_matches_by_project(db_url):
    """A named project scopes to efforts in THAT project — even when the effort id doesn't contain the
    project name — and leaves other projects' idle efforts alone."""
    orch, chat, harness, db = await _orch(db_url)
    try:
        await orch.projects.add("monogame-engine", "https://github.com/me/mono.git")
        eid, _c, _r = await orch.router.open_effort("port-shader", project="monogame-engine")
        await orch.charters.set_goal(eid, "port the shader", created_by="po")
        await _idle_effort(orch, "calculator-percent-function")     # different project (sandbox)
        orch.models._client.queue_structured(
            OperatorIntent(kind="reengage", target_filter="monogame-engine", reply="On it —"))
        mgmt = await orch.mgmt_channel_id()
        await orch.nl_intake("get the workers working on monogame-engine", mgmt, thread_id="t")
        await _drain(orch)
        # ONLY the monogame-engine effort ran (delegate may wake it more than once for its steps);
        # the sandbox calculator effort was never touched.
        assert len(harness.wakes) >= 1
        assert all("port-shader" in w["session_id"] for w in harness.wakes)
        assert not any("calculator" in w["prompt"].lower() for w in harness.wakes)
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


# ── LIVE 2026-07-05 15:48: "continue its previous task" fanned out to ALL 5 idle efforts ──
async def test_reengage_previous_task_singular_resumes_only_most_recent(db_url):
    """An unscoped re-engage whose words say ONE task ("continue its previous task") must resume
    only the most recently touched idle effort — not re-run every stale goal in the backlog."""
    orch, chat, harness, db = await _orch(db_url)
    try:
        old_eid, _c1, _r1 = await _idle_effort(orch, "stale-yesterday")
        new_eid, _c2, _r2 = await _idle_effort(orch, "fresh-interrupted")
        # make recency unambiguous: touch the fresh effort LAST
        from app.models import Effort
        async with orch.db.session_factory() as s:
            older = await s.get(Effort, old_eid)
            older.updated_at = "2026-07-04T00:00:00+00:00"
            newer = await s.get(Effort, new_eid)
            newer.updated_at = "2026-07-05T15:00:00+00:00"
            await s.commit()
        orch.models._client.queue_structured(OperatorIntent(
            kind="reengage", reply="Resuming the interrupted work."))
        mgmt = await orch.mgmt_channel_id()
        await orch.nl_intake("there was a disruption, please have the worker continue its "
                             "previous task.", mgmt, thread_id="t")
        await _drain(orch)
        woken = {w["session_id"] for w in harness.wakes}
        assert new_eid in woken, "the most recent effort was not resumed"
        assert old_eid not in woken, "a stale effort was fanned out despite the SINGULAR ask"
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "most recent effort" in msgs          # transparent about the narrowed scope
    finally:
        await db.dispose()


async def test_reengage_plural_still_dispatches_all_idle(db_url):
    """"get the workers working" (no singular phrasing) keeps the documented fan-out."""
    settings = Settings(
        _env_file=None, chat_adapter="fake",
        profiles_dir=str(ROOT / "profiles"), charters_dir=str(ROOT / "charters"),
        floor_dir=str(ROOT / "floor"),
        worker_instance_urls="http://w1:8090,http://w2:8090",   # room for BOTH efforts
        max_concurrent_workers=2, database_url=db_url, project_survey_enabled=False,
        review_mode="off", plan_approval="off",
    )
    db = Database(db_url)
    orch = Orchestrator(settings, db, FakeChatAdapter(),
                        model_client=FakeModelClient(), harness=FakeHarness())
    await orch.setup()
    chat, harness = orch.chat, orch.harness
    try:
        e1, _c1, _r1 = await _idle_effort(orch, "one")
        e2, _c2, _r2 = await _idle_effort(orch, "two")
        orch.models._client.queue_structured(OperatorIntent(
            kind="reengage", reply="Dispatching all idle work."))
        mgmt = await orch.mgmt_channel_id()
        await orch.nl_intake("get the workers working", mgmt, thread_id="t")
        await _drain(orch)
        woken = {w["session_id"] for w in harness.wakes}
        assert {e1, e2} <= woken, f"fan-out lost efforts: woke only {woken}"
    finally:
        await db.dispose()


# ── LIVE 2026-07-06: "re-run it" (the escalation's own invited reply) re-fanned the backlog ──
async def test_rerun_it_in_effort_thread_resumes_that_effort_only(db_url):
    """The undelivered escalation says "Reply to re-run it" — a reply in that effort's #mgmt
    conversation must resume THAT effort, never fan out over every stale idle effort."""
    orch, chat, harness, db = await _orch(db_url)
    try:
        stale, _c1, _r1 = await _idle_effort(orch, "stale-june-leftover")
        target, _c2, _r2 = await _idle_effort(orch, "escalated-fix")
        orch._effort_mgmt_thread[target] = "mgmt-thread-42"     # the escalation's conversation
        orch.models._client.queue_structured(OperatorIntent(
            kind="reengage", reply="Re-running it."))
        mgmt = await orch.mgmt_channel_id()
        await orch.nl_intake("re-run it", mgmt, thread_id="mgmt-thread-42")
        await _drain(orch)
        woken = {w["session_id"] for w in harness.wakes}
        assert target in woken, "the escalated effort was not resumed"
        assert stale not in woken, "the stale backlog was fanned out AGAIN"
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "this conversation is about" in msgs
    finally:
        await db.dispose()


async def test_bare_rerun_it_outside_thread_resumes_most_recent(db_url):
    orch, chat, harness, db = await _orch(db_url)
    try:
        old, _c1, _r1 = await _idle_effort(orch, "old-one")
        new, _c2, _r2 = await _idle_effort(orch, "fresh-one")
        from app.models import Effort
        async with orch.db.session_factory() as s:
            (await s.get(Effort, old)).updated_at = "2026-07-01T00:00:00+00:00"
            (await s.get(Effort, new)).updated_at = "2026-07-06T00:00:00+00:00"
            await s.commit()
        orch.models._client.queue_structured(OperatorIntent(
            kind="reengage", reply="On it."))
        mgmt = await orch.mgmt_channel_id()
        await orch.nl_intake("re-run it", mgmt, thread_id="unmapped-thread")
        await _drain(orch)
        woken = {w["session_id"] for w in harness.wakes}
        assert new in woken and old not in woken
    finally:
        await db.dispose()


# ── LIVE 2026-07-06: "re-run effort-X" → "Nothing to re-engage" (lifecycle was still done) ──
async def test_named_rerun_reopens_a_done_effort(db_url):
    orch, chat, harness, db = await _orch(db_url)
    try:
        eid, _c, _r = await _idle_effort(orch, "delivered-once")
        await orch.gate.set_lifecycle(eid, "done")            # a prior delivery closed it
        orch.models._client.queue_structured(OperatorIntent(
            kind="reengage", reply="Re-running it.", effort_id=eid))
        mgmt = await orch.mgmt_channel_id()
        await orch.nl_intake(f"re-run {eid}", mgmt, thread_id="t")
        await _drain(orch)
        woken = {w["session_id"].split("~")[0] for w in harness.wakes}
        assert eid in woken, "the named done effort was not reopened + dispatched"
        from app.models import Effort
        async with orch.db.session_factory() as s:
            e = await s.get(Effort, eid)
        assert e.lifecycle in ("open", "done")   # reopened for the run (done again if it finished)
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "Nothing to re-engage" not in msgs
    finally:
        await db.dispose()


async def test_error_report_reuse_reopens_closed_effort(db_url):
    orch, chat, harness, db = await _orch(db_url)
    try:
        eid, _c, _r = await _idle_effort(orch, "fix-thing-errors")
        await orch.gate.set_lifecycle(eid, "done")
        # a re-reported error reuses the slug → open_effort must flip lifecycle back to open
        eid2, _chan, _root = await orch.router.open_effort("fix-thing-errors")
        assert eid2 == eid
        from app.models import Effort
        async with orch.db.session_factory() as s:
            e = await s.get(Effort, eid)
        assert e.lifecycle == "open"
    finally:
        await db.dispose()


async def test_named_rerun_survives_model_dropping_the_effort_id(db_url):
    """LIVE 2026-07-06 (second miss): the model classified "re-run effort-X" as reengage but
    returned effort_id=None → "Nothing to re-engage" with an EMPTY open set. An effort id in the
    operator's own words must resolve deterministically."""
    orch, chat, harness, db = await _orch(db_url)
    try:
        eid, _c, _r = await _idle_effort(orch, "named-in-words")
        await orch.gate.set_lifecycle(eid, "done")            # closed AND the open set is empty
        orch.models._client.queue_structured(OperatorIntent(
            kind="reengage", reply="Re-running."))            # model DROPPED the id
        mgmt = await orch.mgmt_channel_id()
        await orch.nl_intake(f"re-run {eid}", mgmt, thread_id="t")
        await _drain(orch)
        woken = {w["session_id"].split("~")[0] for w in harness.wakes}
        assert eid in woken, "the literally-named effort was not resolved from the message"
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "Nothing to re-engage" not in msgs
    finally:
        await db.dispose()


# ── LIVE 2026-07-06: "frozen (a concern or the kill switch)" gave the operator no context ──
async def test_kill_refusal_names_the_switch_and_release_resumes_automatically(db_url):
    orch, chat, harness, db = await _orch(db_url)
    try:
        eid, chan, root = await _idle_effort(orch, "blocked-by-stop")
        await orch.gate.kill_switch(on=True, actor="human")     # the operator's "stop"
        await orch.delegate(eid, chan, root, "do the thing", plan_steps=["work"])
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "kill switch" in msgs.lower() and "resume" in msgs, \
            "the refusal must NAME the blocker and the release word"
        assert "a concern or the kill switch" not in msgs, "still the ambiguous riddle"
        assert len(harness.wakes) == 0
        # the release re-dispatches automatically — no re-asking
        orch.models._client.queue_structured(OperatorIntent(
            kind="unkill", reply="Releasing."))
        mgmt = await orch.mgmt_channel_id()
        await orch.nl_intake("resume", mgmt, thread_id="t")
        await _drain(orch)
        woken = {w["session_id"].split("~")[0] for w in harness.wakes}
        assert eid in woken, "the freeze-blocked effort did not auto-resume on release"
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "Re-dispatching" in msgs
    finally:
        await db.dispose()
