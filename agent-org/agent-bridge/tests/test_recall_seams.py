"""Governed recall INTO A BRIEF - the four seams, proved with a FIXTURE (memory-plane §3).

WHY THIS FILE EXISTS SEPARATELY FROM `test_agent_memory_recall.py`. That file proves the
module: the block renders, the client bounds its request, a dead transport returns []. Every
one of its assertions still holds if `_agent_memory_context` is never called from anywhere -
which was the state the seams shipped in. They were written and nothing executed them.

THE FAILURE MODE THIS GUARDS. Recall's natural failure is silence: it returns nothing, the
block renders as "", the brief is unchanged, and every test that asserts "no crash" passes.
This repo has shipped that shape before. So each seam test below uses a fixture memory that
SHOULD match and asserts that it DID - the sentinel string has to be *in* the brief the
worker receives. The paired control (recall off -> sentinel absent) is what makes the
positive assertion mean something: together they distinguish "correctly returned nothing"
from "silently broken".

Fakes only - no Open Brain, no network, no GPU.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import httpx
import pytest

from app.adapters.chat import FakeChatAdapter
from app.config import Settings
from app.db import Database
from app.modules import openbrain_memory as om
from app.modules.model_router import FakeModelClient
from app.orchestrator import Orchestrator
from app.worker.harness import FakeHarness

ROOT = Path(__file__).resolve().parents[1]

# A memory that SHOULD come back for these goals. The sentinel is deliberately unlike any
# other string in the suite, so an assertion on it cannot be satisfied by accident.
SENTINEL = "llm-queue holds the lane budget - a 429 under fan-out is the per-model limit"
TRACE_ID = "trace-fixture-0001"
FIXTURE = {
    "memory_id": "mem-fixture-0001",
    "summary": SENTINEL,
    "can_use_as_instruction": False,
    "requires_user_confirmation": True,
}


def _brain(state: dict, *, items=None, usage_delay: float = 0.0, boom: bool = False,
           by_query=None):
    """A stand-in Open Brain: the recall REST twin plus the report-usage MCP tool.

    Records what it was ASKED, not only what it answered - the query text and the usage
    arguments are half of what these tests are about.
    """
    served = [FIXTURE] if items is None else items

    def _serve(query: str):
        """What the plane answers. `by_query` lets a test make the ANSWER depend on the
        QUESTION, which is the only way to prove a seam re-queried rather than inherited
        a block some earlier seam put in the text it was handed."""
        return by_query(query) if by_query is not None else served

    async def handler(request: httpx.Request) -> httpx.Response:
        if boom:
            raise httpx.ConnectError("open brain is down")
        body = json.loads(request.content.decode() or "{}")
        if request.url.path.endswith("/agent-memory/recall"):
            state.setdefault("recalls", []).append(body)
            return httpx.Response(
                200, json={"trace_id": TRACE_ID, "items": _serve(body.get("query") or "")})
        # JSON-RPC tools/call on the root path
        name = (body.get("params") or {}).get("name")
        args = (body.get("params") or {}).get("arguments") or {}
        if name == "agent_memory_report_usage":
            if usage_delay:
                await asyncio.sleep(usage_delay)
            state.setdefault("usage", []).append(args)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1,
                                         "result": {"content": [{"type": "text", "text": "ok"}]}})

    return httpx.MockTransport(handler)


async def _orch(db_url, tmp_path, *, recall: bool = True, transport=None):
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
    settings.memory_recall_enabled = recall
    settings.memory_writeback_enabled = False
    settings.openbrain_key = "test-key"
    db = Database(db_url)
    orch = Orchestrator(settings, db, FakeChatAdapter(),
                        model_client=FakeModelClient(), harness=FakeHarness())
    await orch.setup()
    if transport is not None:
        orch.memory.transport = transport
    return orch, orch.chat, orch.harness, db


def _remote():
    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        if "/compare/" in p:
            return httpx.Response(200, json={
                "ahead_by": 1, "behind_by": 0, "commits": [],
                "files": [{"filename": "a.py", "additions": 1, "deletions": 0, "patch": "+x\n"}]})
        if "/branches/" in p:
            return httpx.Response(200, json={"commit": {"sha": "cafe1234beef"}})
        if p.endswith("/pulls") and request.method == "POST":
            return httpx.Response(201, json={"number": 9, "html_url": "https://x/pull/9"})
        if p.endswith("/pulls") and request.method == "GET":
            return httpx.Response(200, json=[])
        if p.count("/") == 3:
            return httpx.Response(200, json={"default_branch": "main"})
        return httpx.Response(404)
    return httpx.MockTransport(handler)


async def _shutdown(orch, db):
    for t in (orch._capacity_task, orch._stall_task, orch._reaper_task):
        if t is not None:
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
    if orch._bg_tasks:
        await asyncio.gather(*list(orch._bg_tasks), return_exceptions=True)
    await db.dispose()


# -- seam 1: intake, and it must land INSIDE the versioned goal ---------------
async def test_seam_1_intake_puts_a_matching_memory_in_the_versioned_goal(db_url, tmp_path):
    """The block goes in BEFORE `set_goal`, so the record of what the work was asked to do
    contains what steered it. A memory injected after the freeze steers invisibly."""
    state: dict = {}
    orch, _c, _h, db = await _orch(db_url, tmp_path, transport=_brain(state))
    try:
        await orch.projects.add("app", "https://github.com/acme/app.git")
        orch._gh_transport = _remote()
        eid, chan, root = await orch.router.open_effort("fix", project="app")
        await orch._intake_or_dispatch(eid, chan, root, "fix the fan-out timeouts",
                                       reply_prefix="", mgmt_channel=chan)
        _v, goal, _s = await orch.charters.current_goal(eid)
        assert "RELEVANT MEMORIES" in goal
        assert SENTINEL in goal, "the fixture memory should have matched and did not"
        assert state.get("recalls"), "the seam never asked the plane anything"
    finally:
        await _shutdown(orch, db)


async def test_seam_1_control_recall_off_means_the_sentinel_is_absent(db_url, tmp_path):
    """The control that makes the assertion above mean something: same fixture, same goal,
    recall OFF. Without this pair a passing seam test cannot tell 'correctly returned
    nothing' from 'silently broken'."""
    state: dict = {}
    orch, _c, _h, db = await _orch(db_url, tmp_path, recall=False, transport=_brain(state))
    try:
        await orch.projects.add("app", "https://github.com/acme/app.git")
        orch._gh_transport = _remote()
        eid, chan, root = await orch.router.open_effort("fix", project="app")
        await orch._intake_or_dispatch(eid, chan, root, "fix the fan-out timeouts",
                                       reply_prefix="", mgmt_channel=chan)
        _v, goal, _s = await orch.charters.current_goal(eid)
        assert SENTINEL not in goal and "RELEVANT MEMORIES" not in goal
        assert not state.get("recalls"), "recall is off and it still called the plane"
    finally:
        await _shutdown(orch, db)


async def test_the_recall_query_is_the_REQUEST_not_the_assembled_brief(db_url, tmp_path):
    """WHAT IS EMBEDDED DECIDES WHAT COMES BACK. By the time the recall seam runs at intake,
    the request has already grown a STANDING INTENT block and an ACCEPTANCE CORPUS block.
    Embedding that composite searches the plane for the org's own boilerplate, which is
    identical on every effort - the one thing guaranteed not to discriminate between goals.
    With no similarity floor in the SQL (see the threshold note) nothing downstream repairs
    a badly-chosen query: whatever it ranks first is what the worker is told to weigh."""
    state: dict = {}
    orch, _c, _h, db = await _orch(db_url, tmp_path, transport=_brain(state))
    try:
        await orch.projects.add("app", "https://github.com/acme/app.git")
        await orch.projects.set_standing_intent("app", "app builds from the vendored source")
        await orch.projects.add_acceptance_check("app", "operator review: reopen must exist",
                                                 "python3 todo.py reopen --help")
        orch._gh_transport = _remote()
        eid, chan, root = await orch.router.open_effort("fix", project="app")
        await orch._intake_or_dispatch(eid, chan, root, "fix the fan-out timeouts",
                                       reply_prefix="", mgmt_channel=chan)
        q = state["recalls"][0]["query"]
        assert "fan-out timeouts" in q
        assert "STANDING INTENT" not in q, "the query carried the org's boilerplate"
        assert "ACCEPTANCE CORPUS" not in q, "the query carried the org's boilerplate"
    finally:
        await _shutdown(orch, db)


# -- seam 2: the first coding step -------------------------------------------
async def test_seam_2_the_first_coding_step_carries_it_to_the_worker(db_url, tmp_path):
    """The brief the worker actually reads. The goal is where the record lives; this is where
    the memory has to be for it to do anything."""
    state: dict = {}
    orch, _c, harness, db = await _orch(db_url, tmp_path, transport=_brain(state))
    try:
        await orch.projects.add("app", "https://github.com/acme/app.git")
        orch._gh_transport = _remote()
        eid, chan, root = await orch.router.open_effort("fix", project="app")
        harness.output_queue = ["did the work", "pushed"]
        harness.check_queue = [(0, "ok", False)]
        await orch.delegate(eid, chan, root, "fix the fan-out timeouts", plan_steps=["work"])
        prompts = " ".join(w["prompt"] for w in harness.wakes)
        assert "RELEVANT MEMORIES" in prompts and SENTINEL in prompts
    finally:
        await _shutdown(orch, db)


# -- seam 3: a burn-down round -----------------------------------------------
async def test_seam_3_a_burndown_round_carries_it(db_url, tmp_path):
    """Every burn-down round is a FRESH session. Besides the clause set this is the only
    thing carrying what the org already learned into the round."""
    state: dict = {}
    orch, _c, harness, db = await _orch(db_url, tmp_path, transport=_brain(state))
    try:
        await orch.projects.add("app", "https://github.com/acme/app.git")
        eid, chan, root = await orch.router.open_effort("fix", project="app")
        await orch.charters.set_goal(eid, "fix the fan-out timeouts", created_by="po")
        await orch._burndown_wake(eid, chan, root, 1, ["error CS0103: boom"],
                                  part=None, host=None, branch="agent/fix",
                                  repo="https://github.com/acme/app.git", branch_exists=True)
        prompts = " ".join(w["prompt"] for w in harness.wakes)
        assert "RELEVANT MEMORIES" in prompts and SENTINEL in prompts
    finally:
        await _shutdown(orch, db)


async def test_seam_3_asks_about_the_FAILURE_not_the_whole_round_brief(db_url, tmp_path):
    """A burn-down round's brief is mostly standing instructions about how to behave. What a
    round needs recalled is what is known about THIS failure, so the errors are the query."""
    state: dict = {}
    orch, _c, _h, db = await _orch(db_url, tmp_path, transport=_brain(state))
    try:
        await orch.projects.add("app", "https://github.com/acme/app.git")
        eid, chan, root = await orch.router.open_effort("fix", project="app")
        await orch.charters.set_goal(eid, "fix the fan-out timeouts", created_by="po")
        await orch._burndown_wake(eid, chan, root, 1, ["error CS0103: name Frobnicate missing"],
                                  part=None, host=None, branch="agent/fix",
                                  repo="https://github.com/acme/app.git", branch_exists=True)
        q = state["recalls"][0]["query"]
        assert "Frobnicate" in q, "the round's own errors were not part of the query"
        assert "LEARNED CONSTRAINTS" not in q and "Your workspace is" not in q
    finally:
        await _shutdown(orch, db)


# -- seam 4: the cross-project handoff resume --------------------------------
_HANDOFF_OUT = (
    "blocked by a crash inside the vendored lib.\n"
    "HANDOFF: vendor/libx/src/Parser.cs :: Parse() throws on empty input\n"
    "   at LibX.Parser.Parse(String s) in /workspace/vendor/libx/src/Parser.cs:line 42\n"
)


# THE OLD SEAM-4 TEST WAS REMOVED HERE, not weakened: it asserted "some resumed prompt
# contains the sentinel" while reaching the seam through `delegate()` directly, which is
# not how a resume is ever reached in production. With the seam-4 injection deleted it
# still passed - the resumed dispatch re-entered `_run_step` i==1 (seam 2) and re-injected
# there, so the assertion was satisfied by a different seam than the one it named. The
# real-intake tests below the guards section replace it and go red when seam 4 goes away.

# -- the guards, at the helper -----------------------------------------------
async def test_the_block_is_injected_once_when_the_request_already_carries_one(db_url, tmp_path):
    """The guard substring, and what it BOUNDS: the text it is applied to. A goal carrying
    two memory blocks would spend its budget twice and tell the worker the org said
    everything twice. It does NOT bound the worker prompt, which echoes the whole goal -
    see `test_the_guard_bounds_the_GOAL_and_the_prompt_echoes_the_goal` below."""
    state: dict = {}
    orch, _c, _h, db = await _orch(db_url, tmp_path, transport=_brain(state))
    try:
        await orch.projects.add("app", "https://github.com/acme/app.git")
        orch._gh_transport = _remote()
        eid, chan, root = await orch.router.open_effort("fix", project="app")
        pre = ("fix the fan-out timeouts\n\nRELEVANT MEMORIES - already carried.\n"
               "  - [evidence] something the org recorded earlier")
        await orch._intake_or_dispatch(eid, chan, root, pre, reply_prefix="", mgmt_channel=chan)
        _v, goal, _s = await orch.charters.current_goal(eid)
        assert goal.count("RELEVANT MEMORIES") == 1
        assert not state.get("recalls"), "the guard held the text but still paid for a recall"
    finally:
        await _shutdown(orch, db)


async def test_the_guard_bounds_the_GOAL_and_the_prompt_echoes_the_goal(db_url, tmp_path):
    """WHAT THE GUARD DOES AND DOES NOT PROMISE - measured, because the docstring above used
    to promise more than it can deliver.

    Measured after a real intake (2026-08-30): the versioned goal carries ONE block, and the
    first worker prompt carries TWO. That is not a second recall - exactly one call is made.
    `router.wake` composes `prompt = build_context(...) + instruction`, `build_context`
    injects the effort's current goal, and with no plan steps the instruction IS that goal
    (`delegate`: `steps = plan_steps or [goal]`). So the whole goal appears twice, and every
    block inside it doubles: STANDING INTENT, the request text and the memory block alike.

    A guard on a substring of ONE text cannot deduplicate two texts, and closing the echo
    means changing how every dispatch composes its prompt - outside this item, recorded in
    `documentation/notes/u6recall-findings.md`. So the property asserted here is the one that
    is actually recall's to hold: THE MEMORY BLOCK IS ECHOED NO MORE OFTEN THAN ANY OTHER
    BLOCK IN THE GOAL. That stays true if the echo is ever fixed, and fails if recall alone
    starts injecting twice - which is the failure the guard exists for."""
    state: dict = {}
    orch, _c, harness, db = await _orch(db_url, tmp_path, transport=_brain(state))
    try:
        await orch.projects.add("app", "https://github.com/acme/app.git")
        await orch.projects.set_standing_intent("app", "app builds from the vendored source")
        orch._gh_transport = _remote()
        eid, chan, root = await orch.router.open_effort("fix", project="app")
        harness.output_queue = ["did the work", "pushed"]
        harness.check_queue = [(0, "ok", False)]
        await orch._intake_or_dispatch(eid, chan, root, "fix the fan-out timeouts",
                                       reply_prefix="", mgmt_channel=chan)
        for _ in range(4):
            if orch._bg_tasks:
                await asyncio.gather(*list(orch._bg_tasks), return_exceptions=True)
        _v, goal, _s = await orch.charters.current_goal(eid)
        assert len(state["recalls"]) == 1, "one dispatch must cost exactly one recall"
        assert goal.count("RELEVANT MEMORIES") == 1
        first = harness.wakes[0]["prompt"]
        assert SENTINEL in first
        assert first.count("RELEVANT MEMORIES") == first.count("STANDING INTENT"), (
            "the memory block is echoed more often than the rest of the goal")
        assert first.count("RELEVANT MEMORIES") == first.count("fix the fan-out timeouts")
    finally:
        await _shutdown(orch, db)


async def test_usage_is_reported_against_the_trace_that_returned_it(db_url, tmp_path):
    """CLOSING THE LOOP means the report is attributable. `agent_memory_audit_events` has a
    `trace_id` column for exactly this; a usage report without it records that *a* memory was
    used and loses which recall surfaced it - the question the trace exists to answer."""
    state: dict = {}
    orch, _c, _h, db = await _orch(db_url, tmp_path, transport=_brain(state))
    try:
        block = await orch._agent_memory_context("app", "fix the fan-out timeouts")
        assert SENTINEL in block
        assert state.get("usage"), "nothing was reported back to the plane"
        assert all(u.get("trace_id") == TRACE_ID for u in state["usage"])
    finally:
        await _shutdown(orch, db)


async def test_a_memory_dropped_from_the_block_is_reported_UNUSED(db_url, tmp_path):
    """The NEGATIVE case is the whole point of `used`. The block bounds itself, so some
    recalled memories never reach the worker at all. Reporting those as used tells the plane
    its recall is working when the brief never showed them - it poisons the one signal that
    can detect bad recall."""
    state: dict = {}
    many = [{"memory_id": f"mem-{i}", "summary": f"memory number {i} " + "x" * 280,
             "can_use_as_instruction": False, "requires_user_confirmation": False}
            for i in range(20)]
    orch, _c, _h, db = await _orch(db_url, tmp_path, transport=_brain(state, items=many))
    try:
        block = await orch._agent_memory_context("app", "anything")
        rendered = {m["memory_id"] for m in many if f"memory number {m['memory_id'][4:]} " in block}
        reported_used = {u["memory_id"] for u in state.get("usage", []) if u["used"]}
        reported_unused = {u["memory_id"] for u in state.get("usage", []) if not u["used"]}
        assert rendered and len(rendered) < len(many), "the fixture must overflow the block"
        assert reported_used == rendered
        assert reported_unused == {m["memory_id"] for m in many} - rendered
    finally:
        await _shutdown(orch, db)


async def test_usage_reporting_cannot_stall_a_dispatch(db_url, tmp_path, monkeypatch):
    """Context enrichment must never block dispatch, and this one talks to a network service
    N+1 times. Reported one at a time with the client's own timeout, a slow plane adds
    limit x timeout to every dispatch - minutes, on the path that freezes the goal."""
    state: dict = {}
    many = [{"memory_id": f"mem-{i}", "summary": f"memory number {i}",
             "can_use_as_instruction": False, "requires_user_confirmation": False}
            for i in range(8)]
    orch, _c, _h, db = await _orch(
        db_url, tmp_path, transport=_brain(state, items=many, usage_delay=3.0))
    try:
        monkeypatch.setattr(om, "USAGE_REPORT_BUDGET_S", 0.25, raising=False)
        t0 = time.monotonic()
        block = await orch._agent_memory_context("app", "anything")
        elapsed = time.monotonic() - t0
        assert "memory number 0" in block, "the block must still be produced"
        assert elapsed < 2.0, f"the seam stalled for {elapsed:.1f}s on usage reporting"
    finally:
        await _shutdown(orch, db)


async def test_a_dead_plane_never_blocks_the_seam(db_url, tmp_path):
    """The fail-soft law at the SEAM, not just in the module: intake completes and the goal is
    still frozen when Open Brain is unreachable."""
    state: dict = {}
    orch, _c, _h, db = await _orch(db_url, tmp_path, transport=_brain(state, boom=True))
    try:
        await orch.projects.add("app", "https://github.com/acme/app.git")
        orch._gh_transport = _remote()
        eid, chan, root = await orch.router.open_effort("fix", project="app")
        await orch._intake_or_dispatch(eid, chan, root, "fix the fan-out timeouts",
                                       reply_prefix="", mgmt_channel=chan)
        _v, goal, _s = await orch.charters.current_goal(eid)
        assert "fix the fan-out timeouts" in goal and "RELEVANT MEMORIES" not in goal
    finally:
        await _shutdown(orch, db)


@pytest.mark.parametrize("bad", [[{"summary": "  "}], []])
async def test_an_empty_recall_leaves_the_brief_untouched(db_url, tmp_path, bad):
    """Correctly-nothing. The brief must be BYTE-IDENTICAL to the un-enriched one - not a
    header with no items, which tells a worker the org knows nothing."""
    state: dict = {}
    orch, _c, _h, db = await _orch(db_url, tmp_path, transport=_brain(state, items=bad))
    try:
        assert await orch._agent_memory_context("app", "anything") == ""
    finally:
        await _shutdown(orch, db)


# -- seam 4, rebuilt: it must RE-QUERY, and the test must be able to fail ------
#
# WHAT THE OLD TEST PROVED, AND WHY THAT WAS NOTHING. It reached the seam by calling
# `delegate()` directly, so the versioned goal it created carried no memory block and the
# guard `"RELEVANT MEMORIES" not in resume_goal` happened to pass. On the REAL path intake
# runs first and puts the block INSIDE the versioned goal on purpose, so `resume_goal`
# always carried it, the guard was always false, and the seam could only ever fire when
# seam 1 had produced nothing. The old test still passed with the injection deleted, because
# the resumed dispatch re-entered `_run_step` i==1 - seam 2 - which re-injected.
#
# The tests below run through REAL INTAKE and assert a memory that ONLY THE HANDOFF QUERY
# can return. Deleting seam 4 makes them red: the resume then carries the stale intake
# block, and seam 2 is skipped precisely because that block is present.

HANDOFF_SENTINEL = "libx Parser.Parse rejects empty input - the org already recorded this"
MEM_HANDOFF = {
    "memory_id": "mem-handoff-0001",
    "summary": HANDOFF_SENTINEL,
    "can_use_as_instruction": False,
    "requires_user_confirmation": True,
}


def _by_handoff(query: str):
    """The plane answers the HANDOFF question differently from the intake question. Nothing
    else in this suite can distinguish a re-query from an inherited block."""
    return [MEM_HANDOFF] if "HANDOFF RESOLVED" in query else [FIXTURE]


async def _intake_then_handoff(orch, harness):
    """Real intake -> a worker that hands off -> the owning project's fix -> the resume."""
    await orch.projects.add("app", "https://github.com/acme/app.git")
    await orch.projects.add("libx", "https://github.com/acme/libx.git")
    orch._gh_transport = _remote()
    eid, chan, root = await orch.router.open_effort("feat", project="app")
    harness.output_queue.append(_HANDOFF_OUT)
    await orch._intake_or_dispatch(eid, chan, root, "port the parser to the new API",
                                   reply_prefix="", mgmt_channel=chan)
    for _ in range(6):
        if orch._bg_tasks:
            await asyncio.gather(*list(orch._bg_tasks), return_exceptions=True)
    return eid


async def test_seam_4_requeries_against_the_handoff_after_a_real_intake(db_url, tmp_path):
    """THE DECISIVE ONE. Intake already put a block in the versioned goal; the resume must
    still ask the plane again, because the corpus changed between the two - the fix effort
    whose close TRIGGERS this resume writes its own outcome memory (memory-plane 2.2)."""
    state: dict = {}
    orch, _c, harness, db = await _orch(
        db_url, tmp_path, transport=_brain(state, by_query=_by_handoff))
    try:
        eid = await _intake_then_handoff(orch, harness)
        resumed = [w["prompt"] for w in harness.wakes if "HANDOFF RESOLVED" in w["prompt"]]
        assert resumed, "the handoff never resumed, so the seam was never reached"
        assert any(HANDOFF_SENTINEL in p for p in resumed), (
            "the resume carried the block recalled at INTAKE and never asked again")
        # the intake block is REPLACED, not doubled: one block, and it is the fresh one
        _v, goal, _s = await orch.charters.current_goal(eid)
        assert goal.count("RELEVANT MEMORIES") == 1
        assert HANDOFF_SENTINEL in goal and SENTINEL not in goal
    finally:
        await _shutdown(orch, db)


async def test_seam_4_asks_about_the_HANDOFF_and_never_about_its_own_last_answer(db_url, tmp_path):
    """The query, not the block. It must LEAD with the handoff (the client clips at 2000
    chars from the front, so a long goal would push it out), carry the goal for topical
    anchoring, and never contain the block a previous seam injected - a query that quotes
    last time's answer ranks last time's answer, and no similarity floor is configured."""
    state: dict = {}
    orch, _c, harness, db = await _orch(
        db_url, tmp_path, transport=_brain(state, by_query=_by_handoff))
    try:
        await _intake_then_handoff(orch, harness)
        qs = [r["query"] for r in state["recalls"] if "HANDOFF RESOLVED" in r["query"]]
        assert qs, "the resume never asked the plane about the handoff"
        q = qs[-1]
        assert q.startswith("HANDOFF RESOLVED"), "the handoff must survive the query clip"
        assert "port the parser to the new API" in q, "the goal is gone from the query"
        assert "RELEVANT MEMORIES" not in q, "recall embedded its own previous answer"
        assert SENTINEL not in q, "recall embedded its own previous answer"
        assert "continue from it toward your ORIGINAL goal" not in q, (
            "the query carried the resume brief's own boilerplate")
    finally:
        await _shutdown(orch, db)


async def test_seam_4_control_recall_off_means_the_resume_is_untouched(db_url, tmp_path):
    """The control. Same handoff, recall off: no block anywhere and no call to the plane."""
    state: dict = {}
    orch, _c, harness, db = await _orch(
        db_url, tmp_path, recall=False, transport=_brain(state, by_query=_by_handoff))
    try:
        await _intake_then_handoff(orch, harness)
        resumed = [w["prompt"] for w in harness.wakes if "HANDOFF RESOLVED" in w["prompt"]]
        assert resumed, "the handoff never resumed, so the control proves nothing"
        assert not any(HANDOFF_SENTINEL in p or SENTINEL in p for p in resumed)
        assert not state.get("recalls"), "recall is off and it still called the plane"
    finally:
        await _shutdown(orch, db)


async def test_a_dead_plane_at_the_resume_KEEPS_the_inherited_block(db_url, tmp_path):
    """Replace only on success. When the plane is unreachable the resume must keep what the
    goal already carried - a fail-soft that DELETED context would be worse than the bug."""
    state: dict = {}
    calls = {"n": 0}

    def flaky(query: str):
        calls["n"] += 1
        if "HANDOFF RESOLVED" in query:
            raise httpx.ConnectError("open brain is down")
        return [FIXTURE]

    orch, _c, harness, db = await _orch(db_url, tmp_path, transport=_brain(state, by_query=flaky))
    try:
        eid = await _intake_then_handoff(orch, harness)
        _v, goal, _s = await orch.charters.current_goal(eid)
        assert calls["n"] > 1, "the resume never tried the plane"
        assert SENTINEL in goal, "a failed re-query threw the inherited memories away"
        assert goal.count("RELEVANT MEMORIES") == 1
    finally:
        await _shutdown(orch, db)


# -- the controls and query guards the other seams were missing ---------------
async def test_seam_2_control_recall_off_means_the_first_step_is_untouched(db_url, tmp_path):
    """Seam 1 had a recall-off control; seams 2-4 did not, so three of the four positive
    assertions had nothing distinguishing them from a fixture that always matches."""
    state: dict = {}
    orch, _c, harness, db = await _orch(db_url, tmp_path, recall=False, transport=_brain(state))
    try:
        await orch.projects.add("app", "https://github.com/acme/app.git")
        orch._gh_transport = _remote()
        eid, chan, root = await orch.router.open_effort("fix", project="app")
        harness.output_queue = ["did the work", "pushed"]
        harness.check_queue = [(0, "ok", False)]
        await orch.delegate(eid, chan, root, "fix the fan-out timeouts", plan_steps=["work"])
        prompts = " ".join(w["prompt"] for w in harness.wakes)
        assert SENTINEL not in prompts and "RELEVANT MEMORIES" not in prompts
        assert not state.get("recalls")
    finally:
        await _shutdown(orch, db)


async def test_seam_2_asks_about_the_STEP_not_the_assembled_instruction(db_url, tmp_path):
    """By the time seam 2 runs, the instruction carries the handoff protocol, the acceptance
    corpus and the clause set - identical on every effort. Correct today, guarded by nothing."""
    state: dict = {}
    orch, _c, harness, db = await _orch(db_url, tmp_path, transport=_brain(state))
    try:
        await orch.projects.add("app", "https://github.com/acme/app.git")
        await orch.projects.add_acceptance_check("app", "operator review: reopen must exist",
                                                 "python3 todo.py reopen --help")
        orch._gh_transport = _remote()
        eid, chan, root = await orch.router.open_effort("fix", project="app")
        harness.output_queue = ["did the work", "pushed"]
        harness.check_queue = [(0, "ok", False)]
        await orch.delegate(eid, chan, root, "fix the fan-out timeouts",
                            plan_steps=["rewrite the admission queue"])
        q = state["recalls"][0]["query"]
        assert q.strip() == "rewrite the admission queue"
        assert "ACCEPTANCE CORPUS" not in q and "HANDOFF PROTOCOL" not in q
        assert "LEARNED CONSTRAINTS" not in q
    finally:
        await _shutdown(orch, db)


async def test_seam_3_control_recall_off_means_the_round_is_untouched(db_url, tmp_path):
    state: dict = {}
    orch, _c, harness, db = await _orch(db_url, tmp_path, recall=False, transport=_brain(state))
    try:
        await orch.projects.add("app", "https://github.com/acme/app.git")
        eid, chan, root = await orch.router.open_effort("fix", project="app")
        await orch.charters.set_goal(eid, "fix the fan-out timeouts", created_by="po")
        await orch._burndown_wake(eid, chan, root, 1, ["error CS0103: boom"],
                                  part=None, host=None, branch="agent/fix",
                                  repo="https://github.com/acme/app.git", branch_exists=True)
        prompts = " ".join(w["prompt"] for w in harness.wakes)
        assert SENTINEL not in prompts and "RELEVANT MEMORIES" not in prompts
        assert not state.get("recalls")
    finally:
        await _shutdown(orch, db)


async def test_a_burndown_round_never_embeds_the_block_intake_injected(db_url, tmp_path):
    """RECALL FEEDING ON ITSELF, on the real path. `base_goal` at the burn-down seam is the
    VERSIONED goal clipped to 2500 chars, and on the real path that goal carries the block
    seam 1 injected. Without the strip, round two embeds round one's summaries and recall
    re-ranks what it already returned."""
    state: dict = {}
    orch, _c, harness, db = await _orch(db_url, tmp_path, transport=_brain(state))
    try:
        await orch.projects.add("app", "https://github.com/acme/app.git")
        orch._gh_transport = _remote()
        eid, chan, root = await orch.router.open_effort("fix", project="app")
        await orch._intake_or_dispatch(eid, chan, root, "fix the fan-out timeouts",
                                       reply_prefix="", mgmt_channel=chan)
        _v, goal, _s = await orch.charters.current_goal(eid)
        assert SENTINEL in goal, "intake must have injected, or this proves nothing"
        await orch._burndown_wake(eid, chan, root, 1, ["error CS0103: name Frobnicate missing"],
                                  part=None, host=None, branch="agent/fix",
                                  repo="https://github.com/acme/app.git", branch_exists=True)
        q = state["recalls"][-1]["query"]
        assert "Frobnicate" in q
        assert "RELEVANT MEMORIES" not in q, "the round embedded recall's own previous answer"
        assert SENTINEL not in q, "the round embedded recall's own previous answer"
    finally:
        await _shutdown(orch, db)


async def test_rows_that_render_to_NOTHING_are_still_reported_unused(db_url, tmp_path):
    """THE `used=False` SIGNAL SURVIVES AN EMPTY BLOCK. The bound-the-block fix added an
    early return when nothing rendered, which skipped usage reporting entirely: three
    memories recalled, none renderable, and no trace that the plane had been asked at all.
    That is exactly the case the negative signal exists to record."""
    state: dict = {}
    junk = [{"memory_id": f"mem-blank-{i}", "summary": "   ",
             "can_use_as_instruction": False, "requires_user_confirmation": False}
            for i in range(3)]
    orch, _c, _h, db = await _orch(db_url, tmp_path, transport=_brain(state, items=junk))
    try:
        assert await orch._agent_memory_context("app", "anything") == ""
        reported = {u["memory_id"]: u["used"] for u in state.get("usage", [])}
        assert reported == {m["memory_id"]: False for m in junk}
        assert all(u.get("trace_id") == TRACE_ID for u in state["usage"])
    finally:
        await _shutdown(orch, db)


async def test_the_whole_block_including_its_header_fits_the_budget_at_a_seam(db_url, tmp_path):
    """The bound as a SEAM property, not only a renderer one - what actually reaches a goal."""
    state: dict = {}
    many = [{"memory_id": f"mem-{i}", "summary": f"memory number {i} " + "x" * 400,
             "can_use_as_instruction": False, "requires_user_confirmation": True}
            for i in range(30)]
    orch, _c, _h, db = await _orch(db_url, tmp_path, transport=_brain(state, items=many))
    try:
        block = await orch._agent_memory_context("app", "anything")
        assert len(block) <= om.RECALL_BLOCK_MAX, f"the block reached {len(block)} chars"
        assert "more memories omitted" in block
        item_lines = [ln for ln in block.splitlines() if ln.startswith("  - [")]
        assert max(len(ln) for ln in item_lines) <= om.RECALL_ITEM_LINE_MAX
    finally:
        await _shutdown(orch, db)


async def test_a_handoff_fix_effort_asks_about_the_BUG_not_the_template(db_url, tmp_path):
    """DEFECTS 4/5 AGAIN, at the one door that generates its own request. A handoff fix effort
    never passes through `_intake_or_dispatch`; its goal is a CROSS-PROJECT BUG HANDOFF
    template with the debug log dropped into a slot. Left to seam 2 the whole template became
    the query - and that template is byte-identical on every handoff, so the embedding that
    decides what the owning project's worker is told is dominated by text that is the same for
    every bug the org has ever handed off."""
    state: dict = {}
    orch, _c, harness, db = await _orch(
        db_url, tmp_path, transport=_brain(state, by_query=_by_handoff))
    try:
        await _intake_then_handoff(orch, harness)
        fix_qs = [r["query"] for r in state["recalls"]
                  if "Parser.cs" in r["query"] or "Parse() throws" in r["query"]]
        assert fix_qs, "the fix effort never asked the plane about the bug it was opened for"
        q = fix_qs[0]
        assert "Parse() throws on empty input" in q, "the handoff summary is not in the query"
        assert "LibX.Parser.Parse" in q, "the debug log is not in the query"
        assert "CROSS-PROJECT BUG HANDOFF" not in q, "the query carried the template"
        assert "First REPRODUCE this failure here" not in q, "the query carried the template"
        assert "RELEVANT MEMORIES" not in q
    finally:
        await _shutdown(orch, db)
