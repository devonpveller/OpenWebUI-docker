"""Cross-effort A->B DEBUG HANDOFF (operator 2026-07-14: workers "work with each other by
providing debug logs for errors they've run into outside of their current workspace ... engages/
wakes the other worker to fix the bug and push. Worker is told the bug was fixed and wakes again
to continue"). BRIDGE-MEDIATED, never peer-to-peer (floor #3/#7): a blocked worker reports
`HANDOFF: <target> :: <summary>` + its debug log; the org wakes the OWNING project's worker as a
normal gated effort, pauses the reporter, and re-engages it when the fix finishes clean. Depth 1
(no chains), capped per effort, unresolvable targets route to the human. Fakes only."""

from __future__ import annotations

import asyncio
from pathlib import Path

from app.adapters.chat import FakeChatAdapter
from app.config import Settings
from app.db import Database
from app.models import Effort
from app.modules.model_router import FakeModelClient
from app.orchestrator import Orchestrator, _parse_handoff
from app.worker.harness import FakeHarness

ROOT = Path(__file__).resolve().parents[1]

_HANDOFF_OUT = (
    "I ported the parser but the build is blocked by a crash inside the vendored lib.\n"
    "HANDOFF: vendor/libx/src/Parser.cs :: Parse() throws NullReferenceException on empty input\n"
    "Unhandled exception. System.NullReferenceException: Object reference not set\n"
    "   at LibX.Parser.Parse(String s) in /workspace/vendor/libx/src/Parser.cs:line 42\n"
)


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


async def _drain(orch, rounds: int = 3):
    # resolution/resume spawn NEW background tasks from inside earlier ones - drain in rounds
    for _ in range(rounds):
        if orch._bg_tasks:
            await asyncio.gather(*list(orch._bg_tasks))


async def _shutdown(orch, db):
    """Stop the orchestrator's periodic loops (capacity drain / stall watchdog / branch reaper)
    BEFORE disposing the DB - a timer that ticks after dispose hits aiosqlite's worker thread and
    the resulting thread exception is blamed on whatever test runs next (live in this suite:
    41 phantom ERRORs sprayed across later files). The orchestrator has no shutdown API yet, so
    tests that call setup() must cancel what setup() started."""
    await _drain(orch)
    for t in (orch._capacity_task, orch._stall_task, orch._reaper_task):
        if t is not None:
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
    await db.dispose()


# -- the marker parser ---------------------------------------------------------
def test_parse_handoff_extracts_target_summary_and_log():
    ho = _parse_handoff(_HANDOFF_OUT)
    assert ho is not None
    assert ho["target"] == "vendor/libx/src/Parser.cs"
    assert "NullReferenceException" in ho["summary"]
    assert "line 42" in ho["log"]


def test_parse_handoff_ignores_the_echoed_template_line():
    # A worker that parrots the protocol clause (angle brackets) must not open a handoff.
    echoed = "HANDOFF: <path or project where the bug lives> :: <one-line summary>"
    assert _parse_handoff(echoed) is None
    assert _parse_handoff("all good, build passes") is None


def test_parse_handoff_without_summary_still_parses():
    ho = _parse_handoff("HANDOFF: libx\nerror: boom")
    assert ho is not None and ho["target"] == "libx" and ho["summary"] == ""


# -- the full loop: A blocked -> B fixes -> A resumes ----------------------------
async def test_handoff_wakes_owner_with_the_log_then_resumes_the_reporter(db_url):
    orch, chat, harness, db = await _orch(db_url)
    try:
        await orch.projects.add("app", "https://github.com/acme/app.git")
        await orch.projects.add("libx", "https://github.com/acme/libx.git")
        eid, chan, root = await orch.router.open_effort("feat", project="app")
        harness.output_queue.append(_HANDOFF_OUT)   # A's first step ends blocked
        await orch.delegate(eid, chan, root, "port the parser to the new API")
        # A is paused; a fix effort exists on libx with the debug log as its goal
        assert eid in orch._handoff_waiting
        fix_eid = next(iter(orch._handoff_by_fix))
        assert fix_eid.startswith("effort-hx-") and orch._handoff_by_fix[fix_eid]["from"] == eid
        async with orch.db.session_factory() as s:
            fix = await s.get(Effort, fix_eid)
        assert fix is not None and fix.project == "libx"
        assert await orch._event_count(eid, "handoff_opened") == 1

        await _drain(orch)   # B runs (fix + publish) -> resolution -> A resumes (step + publish)
        # the owner's worker got the debug log
        assert any("CROSS-PROJECT BUG HANDOFF" in w["prompt"] and "line 42" in w["prompt"]
                   for w in harness.wakes)
        # A was re-engaged on its ORIGINAL goal with the resolution note
        assert any("HANDOFF RESOLVED" in w["prompt"] and "port the parser" in w["prompt"]
                   for w in harness.wakes)
        assert eid not in orch._handoff_waiting and not orch._handoff_by_fix
        assert await orch._event_count(eid, "handoff_resolved") == 1
        # both closed honestly through the normal finish path
        assert sum("finished" in p["message"] for p in chat.posted) >= 2
    finally:
        await _shutdown(orch, db)


# -- the step wake carries the protocol clause (so workers KNOW how to report) --
async def test_step_wake_carries_the_handoff_protocol_when_a_sibling_exists(db_url):
    orch, chat, harness, db = await _orch(db_url)
    try:
        await orch.projects.add("app", "https://github.com/acme/app.git")
        await orch.projects.add("libx", "https://github.com/acme/libx.git")
        eid, chan, root = await orch.router.open_effort("solo", project="app")
        await orch.delegate(eid, chan, root, "tidy the readme")
        assert "HANDOFF PROTOCOL" in harness.wakes[0]["prompt"]
    finally:
        await _shutdown(orch, db)


async def test_no_protocol_clause_without_a_sibling_project(db_url):
    orch, chat, harness, db = await _orch(db_url)
    try:
        await orch.projects.add("app", "https://github.com/acme/app.git")
        eid, chan, root = await orch.router.open_effort("lone", project="app")
        await orch.delegate(eid, chan, root, "tidy the readme")
        assert "HANDOFF PROTOCOL" not in harness.wakes[0]["prompt"]
    finally:
        await _shutdown(orch, db)


# -- depth 1: a fix effort may not hand off again - that reaches the human ------
async def test_a_fix_effort_cannot_chain_another_handoff(db_url):
    orch, chat, harness, db = await _orch(db_url)
    try:
        await orch.projects.add("app", "https://github.com/acme/app.git")
        await orch.projects.add("libx", "https://github.com/acme/libx.git")
        eid, chan, root = await orch.router.open_effort("hx-feat-1", project="libx")
        harness.output_queue.append(
            "HANDOFF: app/src/Main.cs :: the caller is broken too\nerror: nope")
        await orch.delegate(eid, chan, root, "fix the parser bug handed off to you")
        assert not orch._handoff_by_fix                       # no chained fix effort
        assert any("depth 1" in p["message"] for p in chat.posted)
    finally:
        await _shutdown(orch, db)


# -- the cap: a repeat offender escalates instead of looping ---------------------
async def test_handoff_cap_escalates_instead_of_opening_another_loop(db_url):
    orch, chat, harness, db = await _orch(db_url)
    try:
        await orch.projects.add("app", "https://github.com/acme/app.git")
        await orch.projects.add("libx", "https://github.com/acme/libx.git")
        eid, chan, root = await orch.router.open_effort("greedy", project="app")
        for _ in range(orch.s.handoff_cap):
            await orch.audit.log("handoff_opened", effort_id=eid, payload={})
        harness.output_queue.append(_HANDOFF_OUT)
        await orch.delegate(eid, chan, root, "port the parser")
        assert not orch._handoff_by_fix
        assert any("handoff cap" in p["message"] for p in chat.posted)
    finally:
        await _shutdown(orch, db)


# -- an unmappable target routes to the human, honestly --------------------------
async def test_unresolvable_owner_routes_to_the_operator(db_url):
    orch, chat, harness, db = await _orch(db_url)
    try:
        await orch.projects.add("app", "https://github.com/acme/app.git")
        eid, chan, root = await orch.router.open_effort("stuck", project="app")
        harness.output_queue.append("HANDOFF: some/alien/code.c :: no idea whose\nerror: boom")
        await orch.delegate(eid, chan, root, "port the parser")
        assert not orch._handoff_by_fix and eid not in orch._handoff_waiting
        assert any("can't map" in p["message"] for p in chat.posted)
    finally:
        await _shutdown(orch, db)


# -- a PARTIAL fix keeps the reporter paused and tells the operator once ---------
async def test_partial_fix_finish_keeps_the_reporter_paused(db_url):
    orch, chat, harness, db = await _orch(db_url)
    try:
        await orch.projects.add("app", "https://github.com/acme/app.git")
        eid, chan, root = await orch.router.open_effort("waity", project="app")
        orch._handoff_by_fix["effort-hx-waity-1"] = {
            "from": eid, "target": "libx", "escalated": False}
        orch._handoff_waiting.add(eid)
        await orch._resolve_handoff_if_any("effort-hx-waity-1", None, None, clean=False)
        assert orch._handoff_by_fix["effort-hx-waity-1"]["escalated"] is True
        assert eid not in orch._handoff_waiting               # watchdog may reclaim it
        assert any("stays paused" in p["message"] for p in chat.posted)
        # a second partial finish does not spam the operator again
        before = len(chat.posted)
        await orch._resolve_handoff_if_any("effort-hx-waity-1", None, None, clean=False)
        assert len(chat.posted) == before
    finally:
        await _shutdown(orch, db)
