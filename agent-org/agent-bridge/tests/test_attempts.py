"""ao-attempt - the turn boundary: a coding attempt is PARKED at the next plan wake, measured,
and KEPT as completed plan steps or DISCARDED (tree reset, attempt on a hidden ref, a bounded
note framed against the anchor) - never left as clutter, never silently wiped.

Evidence: C7a (plan store agent-bakeoff/findings/c7a-analysis-2026-10-11.md 3.3): the re-plan
after a loop-guard stop ran in the still-dirty tree and `_revert_plan_turn_writes` wiped the
coding turn's edits (3 of 10 editing turns). ao-planrevert's two rounds failed on the channel:
the git proxy denies plumbing, and open-terminal's framing broke the parser. So every scenario
here runs the bridge's REAL commands through a workspace whose `git` is the real
`git_proxy.classify()` and whose output is framed like the real /check (tests/attempt_ws.py), and
the same scenarios run against the REAL little-coder-open-terminal image when
AO_ATTEMPT_OT_CONTAINER names an offline container of it.

Imports of `app.modules.attempts` are LOCAL to the tests that need it, so the base-compatible
scenarios (C7a replay, plan-turn writes) run - and fail - against the pre-fix orchestrator.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest
from sqlalchemy import select

from app.adapters.chat import FakeChatAdapter
from app.config import Settings
from app.db import Database
from app.models import EffortConstraint, Event
from app.modules.model_router import FakeModelClient
from app.orchestrator import Orchestrator
from app.worker.harness import FakeHarness

from attempt_ws import BASH, GIT, ContainerWS, LocalWS, daemon_frame

ROOT = Path(__file__).resolve().parents[1]
REPO = "https://github.com/acme/gym.git"
GOAL = "greet and say farewell"
CONTENT_MARK = "CONTENTMARK_93f1"     # file content: must never reach audit or notes
CMD_MARK = "CMDMARK_5521"             # worker check command text: same

pytestmark = pytest.mark.skipif(not (BASH and GIT), reason="needs bash and git")

_CONTAINER = os.environ.get("AO_ATTEMPT_OT_CONTAINER", "")
_KEY = os.environ.get("AO_ATTEMPT_OT_KEY", "ao-attempt-test-key")


def _a():
    from app.modules import attempts
    return attempts


# ── harness ─────────────────────────────────────────────────────────────────────────────────
async def _orch(db_url):
    settings = Settings(
        _env_file=None, chat_adapter="fake",
        profiles_dir=str(ROOT / "profiles"), charters_dir=str(ROOT / "charters"),
        floor_dir=str(ROOT / "floor"), worker_instance_urls="http://w1:8090",
        max_concurrent_workers=1, database_url=db_url, project_survey_enabled=False,
        review_mode="off", plan_approval="off", worker_plan_gate="all",
    )
    db = Database(db_url)
    orch = Orchestrator(settings, db, FakeChatAdapter(),
                        model_client=FakeModelClient(), harness=FakeHarness())
    await orch.setup()
    return orch, db


async def _shutdown(orch, db):
    if orch._bg_tasks:
        await asyncio.gather(*list(orch._bg_tasks))
    for t in (orch._capacity_task, orch._stall_task, orch._reaper_task):
        if t is not None:
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
    await db.dispose()


def _wire(orch, ws, plan_actions: list):
    """exec_check -> the workspace's bridge channel; a plan wake first performs the next queued
    plan-turn action (what the read-only turn's shell did to the tree)."""
    async def _exec(effort_id, *, command, session_id=None, **kw):
        return await asyncio.to_thread(ws.bridge, command)

    orch.router.exec_check = _exec
    orig = orch.router.wake

    async def _wake(*a, **kw):
        if kw.get("plan_only") and plan_actions:
            act = plan_actions.pop(0)
            if act is not None:
                await asyncio.to_thread(act, ws)
        return await orig(*a, **kw)

    orch.router.wake = _wake


async def _effort(orch):
    await orch.projects.add("gym", REPO)
    eid, chan, root = await orch.router.open_effort("feat", project="gym")
    await orch.charters.set_goal(eid, GOAL, created_by="po")
    return eid, chan, root


async def _gate(orch, eid, chan, root, *replies):
    orch.harness.output_queue.extend(replies)
    return await orch._worker_plan_gate(eid, chan, root, GOAL, REPO, None, None, None)


async def _events(orch, eid, kind=None) -> list[dict]:
    async with orch.db.session_factory() as s:
        q = select(Event).where(Event.effort_id == eid)
        if kind:
            q = q.where(Event.kind == kind)
        return [{"kind": e.kind, "payload": e.payload} for e in (await s.execute(q)).scalars()]


async def _notes(orch, eid) -> list[str]:
    async with orch.db.session_factory() as s:
        rows = (await s.execute(select(EffortConstraint).where(
            EffortConstraint.effort_id == eid, EffortConstraint.kind == "attempt_discard",
            EffortConstraint.active.is_(True)))).scalars().all()
        return [r.body for r in rows]


def _no_denials(ws):
    denied = [c for c in ws.calls() if c.get("action") == "deny"]
    assert denied == [], f"the worker's git proxy denied: {denied}"


def _base(ws) -> str:
    ws.write("a.py", "def greet():\n    return 'base'\n")
    ws.write("check.sh", "echo 't.py::test_old PASSED'\n")
    ws.git("add", "-A")
    ws.git("commit", "-q", "-m", "base")
    return ws.git("rev-parse", "HEAD").strip()


PLAN_1 = (
    "UNDERSTANDING: add greet and farewell\n"
    "ALREADY DONE: nothing\n"
    "ANCHOR ITEMS:\n"
    "A1: greet() returns a greeting\n"
    "A2: farewell() exists\n"
    "PLAN:\n"
    "1. edit a.py so greet returns hello [anchor: A1]\n"
    "2. add b.py with farewell [anchor: A2]\n"
    "WON'T DO: nothing else\n"
    "RISKS: none")


def _plan_next(verdict: str) -> str:
    return ("UNDERSTANDING: finish the goal\n"
            "ALREADY DONE: see the parked attempt\n"
            "ANCHOR ITEMS:\nA1: greet() returns a greeting\nA2: farewell() exists\n"
            "PLAN:\n1. document greet in README.md [anchor: A1]\n"
            "WON'T DO: nothing else\nRISKS: none\n"
            f"VERDICT: {verdict}")


def _coding_turn(ws):
    """What a coding turn leaves behind: an uncommitted edit and a new untracked file."""
    ws.write("a.py", f"def greet():\n    return 'hello'  # {CONTENT_MARK}\n")
    ws.write("b.py", f"def farewell():\n    return 'bye'  # {CONTENT_MARK}\n")


async def _first_plan(orch, ws, eid, chan, root):
    plan = await _gate(orch, eid, chan, root, PLAN_1)
    assert plan is not None
    _no_denials(ws)


# ── scenarios (run against the local framed workspace AND the real image) ─────────────────
async def scenario_keep(orch, ws):
    """Acceptance: coding edits survive a no-write plan turn - parked, then kept as one commit
    per completed step."""
    att = _a()
    s0 = _base(ws)
    eid, chan, root = await _effort(orch)
    await _first_plan(orch, ws, eid, chan, root)
    stored = await _events(orch, eid, "plan_steps_stored")
    assert stored and stored[-1]["payload"]["anchors"] == ["A1", "A2"]
    _coding_turn(ws)
    await orch._record_worker_checks(eid, f"done.\nCHECKS:\nstep 1: sh check.sh {CMD_MARK}\n")
    plan = await _gate(orch, eid, chan, root, _plan_next("KEEP 1,2"))
    assert plan is not None
    assert CONTENT_MARK in ws.read("a.py") and CONTENT_MARK in ws.read("b.py")
    assert ws.git("status", "--porcelain").strip() == ""
    log = ws.git("log", "--format=%s", f"{s0}..HEAD").splitlines()
    assert len(log) == 2 and "step 1" in log[1] and "step 2" in log[0]
    first = ws.git("show", "--name-only", "--format=", "HEAD~1").split()
    assert first == ["a.py"]                                   # step 1 = the file it names
    assert ws.git("show", "--name-only", "--format=", "HEAD").split() == ["b.py"]
    ref = att.ref_name(eid, 1)
    assert CONTENT_MARK in ws.git("show", f"{ref}:b.py")       # the parked attempt, on its ref
    kept = await _events(orch, eid, "attempt_kept")
    assert kept and kept[-1]["payload"]["verified"] is True and kept[-1]["payload"]["commits"] == 2
    _no_denials(ws)
    return eid


async def scenario_discard(orch, ws):
    """Acceptance: a discard resets the tree, the diff is on the hidden ref, the note is in the
    plan (framed against the anchor), and a learning record is produced."""
    att = _a()
    s0 = _base(ws)
    eid, chan, root = await _effort(orch)
    await _first_plan(orch, ws, eid, chan, root)
    _coding_turn(ws)
    ws.write("check.sh", "echo 't.py::test_old FAILED'\necho 't_new.py::test_new FAILED'\nexit 1\n")
    ws.write("t_new.py", "def test_new():\n    assert False\n")
    await orch._record_worker_checks(eid, f"CHECKS:\nstep 1: sh check.sh {CMD_MARK}\n")
    reason = "farewell broke the greeting contract `rm -rf /` $(boom); it does not serve A2"
    plan = await _gate(orch, eid, chan, root, _plan_next(f"DISCARD 2 deviation: {reason}"))
    assert plan is not None
    # the tree is back at the attempt's start
    assert ws.git("rev-parse", "HEAD").strip() == s0
    assert ws.git("status", "--porcelain").strip() == ""
    assert CONTENT_MARK not in ws.read("a.py") and ws.read("b.py") is None
    # ...and the attempt is NOT lost: it is on the hidden ref
    ref = att.ref_name(eid, 1)
    assert CONTENT_MARK in ws.git("show", f"{ref}:b.py")
    assert CONTENT_MARK in ws.git("show", f"{ref}:a.py")
    # facts: test_old passed at the start and fails now = REGRESSION; test_new is new, not a
    # regression; both carry the anchor item of the step the worker attributed the check to
    facts = (await _events(orch, eid, "attempt_facts"))[-1]["payload"]
    assert [f["test"] for f in facts["regression"]] == ["t.py::test_old"]
    assert [f["test"] for f in facts["new_not_passing"]] == ["t_new.py::test_new"]
    assert {f["anchor"] for f in facts["regression"] + facts["new_not_passing"]} == {"A1"}
    # the planner saw the facts and was asked about the anchor
    plan_prompt = orch.harness.wakes[-1]["prompt"]
    assert "REGRESSIONS" in plan_prompt and "VERDICT" in plan_prompt and "anchor" in plan_prompt
    # the note: framed against the anchor, bounded, scrubbed of code and commands
    notes = await _notes(orch, eid)
    assert len(notes) == 1
    note = notes[0]
    assert "anchor item A2" in note and len(note) <= att.NOTE_MAX
    for bad in (CONTENT_MARK, CMD_MARK, "rm -rf", "$(", "`"):
        assert bad not in note
    # the next plan turn carries it as WHAT NOT TO DO
    assert "farewell broke the greeting contract" in note
    rec = await _events(orch, eid, "attempt_learning_record")
    assert rec and rec[-1]["payload"]["anchor"] == "A2"
    from app.models import EffortAttempt
    async with orch.db.session_factory() as s:
        row = await s.get(EffortAttempt, f"{eid}:a1")
    assert row.state == "discarded" and row.learning_record["outcome"]["summary"] == note
    _no_denials(ws)
    return eid


async def scenario_plan_writes(orch, ws):
    """Acceptance: a plan turn that writes files is still reverted - in a clean tree, on top of a
    parked attempt (only ITS writes go), and when it commits."""
    s0 = _base(ws)
    eid, chan, root = await _effort(orch)

    def plan_writes(w):
        w.write("c.py", "sneaky\n")
        w.write("a.py", "plan turn overwrote a.py\n")

    # (a) clean tree
    orch._plan_actions.append(plan_writes)
    await _first_plan(orch, ws, eid, chan, root)
    assert ws.read("c.py") is None and "base" in ws.read("a.py")
    assert ws.git("rev-parse", "HEAD").strip() == s0
    # (b) a parked attempt: the plan turn's writes go, the attempt stays
    _coding_turn(ws)
    orch._plan_actions.append(plan_writes)
    await _gate(orch, eid, chan, root, _plan_next("KEEP 1,2"))
    assert ws.read("c.py") is None
    assert CONTENT_MARK in ws.read("a.py") and CONTENT_MARK in ws.read("b.py")
    assert ws.git("status", "--porcelain").strip() == ""

    # (c) the plan turn COMMITS its write
    def plan_commits(w):
        w.write("d.py", "committed by the plan turn\n")
        w.git("add", "-A")
        w.git("commit", "-q", "-m", "plan turn commit")

    head = ws.git("rev-parse", "HEAD").strip()
    orch._plan_actions.append(plan_commits)
    await _gate(orch, eid, chan, root, _plan_next("KEEP 1").replace("VERDICT: KEEP 1", ""))
    assert ws.read("d.py") is None
    assert ws.git("rev-parse", "HEAD").strip() == head
    assert await orch._event_count(eid, "plan_turn_moved_head") == 1

    # (d) the plan turn only STAGES its write (the unchanged check restores from the index)
    def plan_stages(w):
        w.write("a.py", "staged by the plan turn\n")
        w.git("add", "a.py")

    orch._plan_actions.append(plan_stages)
    await _gate(orch, eid, chan, root, _plan_next("KEEP 1").replace("VERDICT: KEEP 1", ""))
    assert CONTENT_MARK in ws.read("a.py")
    assert ws.git("status", "--porcelain").strip() == ""
    assert ws.git("rev-parse", "HEAD").strip() == head
    assert await orch._event_count(eid, "plan_turn_wrote_files") >= 4
    _no_denials(ws)
    return eid


async def _run(db_url, ws, scenario):
    orch, db = await _orch(db_url)
    orch._plan_actions = []
    _wire(orch, ws, orch._plan_actions)
    try:
        eid = await scenario(orch, ws)
        # no file content and no command text in ANY audit payload
        blob = json.dumps(await _events(orch, eid))
        assert CONTENT_MARK not in blob and CMD_MARK not in blob
    finally:
        await _shutdown(orch, db)


# ── Case 1 / 2 / 3 on the framed local workspace ─────────────────────────────────────────────
async def test_case1_coding_edits_survive_a_no_write_plan_turn_kept_as_step_commits(db_url, tmp_path):
    await _run(db_url, LocalWS(tmp_path), scenario_keep)


async def test_case2_a_discard_resets_the_tree_keeps_the_attempt_and_notes_why(db_url, tmp_path):
    await _run(db_url, LocalWS(tmp_path), scenario_discard)


async def test_case3_a_plan_turn_that_writes_is_still_reverted(db_url, tmp_path):
    await _run(db_url, LocalWS(tmp_path), scenario_plan_writes)


# ── Case 4: the C7a replay (base-compatible: RED at base, GREEN at tip) ──────────────────────
C7A_RUNS = {
    "ao-queue-r2": (
        ["inference/llm-queue/src/llm_queue/app_state.py",
         "inference/llm-queue/src/llm_queue/config.py",
         "inference/llm-queue/src/llm_queue/routes/data.py",
         "inference/llm-queue/src/llm_queue/routes/health.py",
         "inference/llm-queue/src/llm_queue/scheduler.py"],
        ["inference/llm-queue/src/llm_queue/shedding.py"]),
    "ao-wd-offset-r2": (
        ["agent-org/agent-bridge/app/orchestrator.py",
         "agent-org/agent-bridge/app/worker/harness.py",
         "little-coder/src/littlecoder/daemon.py"],
        ["agent-org/agent-bridge/tests/test_stall_watchdog_task_switch.py",
         "little-coder/tests/test_event_lines.py"]),
    "ao-queue-r3": (
        ["inference/llm-queue/src/llm_queue/app_state.py",
         "inference/llm-queue/src/llm_queue/config.py",
         "inference/llm-queue/src/llm_queue/routes/data.py",
         "inference/llm-queue/src/llm_queue/routes/health.py",
         "inference/llm-queue/src/llm_queue/scheduler.py"],
        ["inference/llm-queue/src/llm_queue/shedding.py",
         "inference/llm-queue/tests/test_leak.py"]),
}

C7A_PLAN = ("UNDERSTANDING: continue the queue fix\nALREADY DONE: the edits in the tree\n"
            "ANCHOR ITEMS:\nA1: the queue sheds load\n"
            "PLAN:\n1. finish shedding.py and run its tests [anchor: A1]\n"
            "WON'T DO: nothing else\nRISKS: none\nVERDICT: KEEP 1")


@pytest.mark.parametrize("run", sorted(C7A_RUNS))
async def test_case4_c7a_replay_keeps_the_coding_turns_edits(db_url, tmp_path, run):
    modified, added = C7A_RUNS[run]
    ws = LocalWS(tmp_path)
    for p in modified:
        ws.write(p, f"original {p}\n")
    ws.git("add", "-A")
    ws.git("commit", "-q", "-m", "base")
    orch, db = await _orch(db_url)
    orch._plan_actions = []
    _wire(orch, ws, orch._plan_actions)
    try:
        eid, chan, root = await _effort(orch)
        # the coding turn (stopped by the loop guard) left these uncommitted
        for p in modified:
            ws.write(p, f"coding turn edit {p}\n")
        for p in added:
            ws.write(p, f"coding turn new file {p}\n")
        # the re-plan: a read-only plan turn that writes nothing
        assert await _gate(orch, eid, chan, root, C7A_PLAN) is not None
        for p in modified:
            assert ws.read(p) == f"coding turn edit {p}\n", p
        for p in added:
            assert ws.read(p) == f"coding turn new file {p}\n", p
        assert await orch._event_count(eid, "plan_turn_wrote_files") == 0
        _no_denials(ws)
    finally:
        await _shutdown(orch, db)


# ── Case 5: plan steps carry a declared anchor item; refused once, then escalated ─────────────
async def test_case5_a_plan_without_anchor_items_is_refused_once_then_escalated(db_url, tmp_path):
    ws = LocalWS(tmp_path)
    _base(ws)
    orch, db = await _orch(db_url)
    orch._plan_actions = []
    _wire(orch, ws, orch._plan_actions)
    try:
        eid, chan, root = await _effort(orch)
        bare = "UNDERSTANDING: x\nPLAN:\n1. edit a.py\nWON'T DO: y\nRISKS: none"
        assert await _gate(orch, eid, chan, root, bare, bare) is None
        assert await orch._event_count(eid, "plan_steps_undeclared") == 2
        assert await orch._event_count(eid, "worker_plan_stopped") == 1
        assert all(w["plan_only"] for w in orch.harness.wakes)        # nothing delegated
        assert "REFUSED" in orch.harness.wakes[1]["prompt"]
        assert await orch._event_count(eid, "plan_steps_stored") == 0
    finally:
        await _shutdown(orch, db)


async def test_case5_refused_once_then_a_declared_plan_is_stored_before_delegation(db_url, tmp_path):
    ws = LocalWS(tmp_path)
    _base(ws)
    orch, db = await _orch(db_url)
    orch._plan_actions = []
    _wire(orch, ws, orch._plan_actions)
    try:
        eid, chan, root = await _effort(orch)
        bare = "UNDERSTANDING: x\nPLAN:\n1. edit a.py\nWON'T DO: y\nRISKS: none"
        assert await _gate(orch, eid, chan, root, bare, PLAN_1) is not None
        ev = await _events(orch, eid)
        kinds = [e["kind"] for e in ev]
        assert kinds.index("plan_steps_stored") < kinds.index("worker_plan_approved")
        stored = [e for e in ev if e["kind"] == "plan_steps_stored"][0]["payload"]
        assert stored["anchors"] == ["A1", "A2"] and stored["items"] == ["A1", "A2"]
        plan = await orch._effort_plan_row(eid)
        assert [s["anchor"] for s in plan["steps"]] == ["A1", "A2"]
    finally:
        await _shutdown(orch, db)


# ── Case 6: an unparseable verdict never discards ─────────────────────────────────────────────
async def test_case6_an_unparseable_verdict_keeps_the_attempt_parked_and_escalates(db_url, tmp_path):
    att = _a()
    ws = LocalWS(tmp_path)
    _base(ws)
    orch, db = await _orch(db_url)
    orch._plan_actions = []
    _wire(orch, ws, orch._plan_actions)
    try:
        eid, chan, root = await _effort(orch)
        await _first_plan(orch, ws, eid, chan, root)
        _coding_turn(ws)
        no_verdict = _plan_next("KEEP 1").replace("VERDICT: KEEP 1", "VERDICT: maybe later")
        assert await _gate(orch, eid, chan, root, no_verdict, no_verdict) is None
        assert await orch._event_count(eid, "attempt_verdict_unparseable") == 2
        assert await orch._event_count(eid, "attempt_discarded") == 0
        assert await orch._event_count(eid, "worker_plan_stopped") == 1
        # parked: committed, on its ref, tree clean, nothing lost
        assert CONTENT_MARK in ws.read("a.py") and CONTENT_MARK in ws.read("b.py")
        ref = att.ref_name(eid, 1)
        assert ws.git("rev-parse", ref).strip() == ws.git("rev-parse", "HEAD").strip()
        assert ws.git("status", "--porcelain").strip() == ""
        # the re-ask asked for the verdict, with the attempt
        assert "no single, valid VERDICT" in orch.harness.wakes[-1]["prompt"]
        _no_denials(ws)
    finally:
        await _shutdown(orch, db)


# ── Case 7: three discards of the same step escalate once, classified ─────────────────────────
async def test_case7_three_discards_of_one_step_escalate_once_with_context(db_url, tmp_path):
    ws = LocalWS(tmp_path)
    s0 = _base(ws)
    orch, db = await _orch(db_url)
    orch._plan_actions = []
    _wire(orch, ws, orch._plan_actions)
    try:
        eid, chan, root = await _effort(orch)
        await _first_plan(orch, ws, eid, chan, root)
        results = []
        for i in range(4):
            ws.write("a.py", f"attempt {i} {CONTENT_MARK}\n")
            results.append(await _gate(
                orch, eid, chan, root,
                PLAN_1 + f"\nVERDICT: DISCARD 1 no-solution: try {i} did not make greet return a greeting"))
            assert ws.git("rev-parse", "HEAD").strip() == s0
            assert "base" in ws.read("a.py")
        assert results[0] is not None and results[1] is not None
        assert results[2] is None                       # the third discard escalated
        esc = await _events(orch, eid, "attempt_step_escalated")
        assert len(esc) == 1                            # once, not again on the fourth
        p = esc[0]["payload"]
        assert p["anchor"] == "A1" and p["discards"] == 3 and p["class"] == "no-solution-identified"
        assert len(p["attempts"]) == 3 and all(r.startswith("ao-attempts/") for r in p["refs"])
        posted = [m["message"] for m in orch.chat.posted if "discarded 3 times" in m["message"]]
        assert posted and "anchor item **A1**" in posted[0]
        # notes are capped and every one is framed against the anchor
        notes = await _notes(orch, eid)
        assert 1 <= len(notes) <= _a().NOTES_CAP and all("anchor item A1" in n for n in notes)
        _no_denials(ws)
    finally:
        await _shutdown(orch, db)


async def test_case7_notes_are_capped_in_count(db_url, tmp_path):
    att = _a()
    ws = LocalWS(tmp_path)
    _base(ws)
    orch, db = await _orch(db_url)
    try:
        eid, _c, _r = await _effort(orch)
        for i in range(att.NOTES_CAP + 3):
            await orch._store_attempt_note(eid, f"{eid}:a{i}", f"note {i} anchor item A1")
        notes = await _notes(orch, eid)
        assert len(notes) == att.NOTES_CAP and notes[-1].startswith(f"note {att.NOTES_CAP + 2}")
    finally:
        await _shutdown(orch, db)


# ── Case 8: refs expire at delivery ───────────────────────────────────────────────────────────
async def test_case8_attempt_refs_expire_at_delivery(db_url, tmp_path):
    ws = LocalWS(tmp_path)
    orch, db = await _orch(db_url)
    orch._plan_actions = []
    _wire(orch, ws, orch._plan_actions)
    try:
        _base(ws)
        eid, chan, root = await _effort(orch)
        await _first_plan(orch, ws, eid, chan, root)
        _coding_turn(ws)
        await _gate(orch, eid, chan, root, _plan_next("KEEP 1,2"))
        await orch._expire_attempts(eid)
        from app.models import EffortAttempt
        async with orch.db.session_factory() as s:
            rows = (await s.execute(select(EffortAttempt).where(
                EffortAttempt.effort_id == eid))).scalars().all()
        assert rows and all(r.expired for r in rows)
        assert await orch._event_count(eid, "attempt_refs_expired") == 1
    finally:
        await _shutdown(orch, db)


# ── Case 9: every git argv the bridge sends passes the REAL proxy ─────────────────────────────
def test_case9_every_git_argv_passes_the_real_git_proxy():
    import git_proxy
    att = _a()
    table = {" ".join(a): git_proxy.classify(a) for a in att.GIT_ARGVS}
    denied = {k: (d.rule, d.reason) for k, d in table.items() if d.action != "allow"}
    assert denied == {}
    # the plumbing ao-planrevert round 1 relied on stays denied (the table means something)
    for bad in (["write-tree"], ["update-ref", "x", "y"], ["branch", "-D", "x"],
                ["-c", "a=b", "status"], ["reset", "--hard", "abc"]):
        assert git_proxy.classify(bad).action == "deny"


async def test_case9_the_argvs_actually_sent_are_exactly_the_declared_table(db_url, tmp_path):
    """The shim logs every git argv a scenario ran; each one's shape is in GIT_ARGVS."""
    import git_proxy
    att = _a()
    ws = LocalWS(tmp_path)
    await _run(db_url, ws, scenario_discard)
    shapes = {tuple(a) for a in att.GIT_ARGVS}
    sent = [c["argv"] for c in ws.calls()]
    assert sent

    def shape(argv):
        out = []
        for i, tok in enumerate(argv):
            if att.is_sha(tok):
                out.append("SHA")
            elif tok.startswith("refs/heads/ao-attempts/"):
                out.append("refs/heads/REF")
            elif tok.startswith("ao-attempts/"):
                out.append("REF")
            elif i > 0 and argv[i - 1] == "-m":
                out.append("MSG")
            elif i > 0 and argv[i - 1] == "--" and argv[0] == "--literal-pathspecs":
                out.append("PATH")
            else:
                out.append(tok)
        return tuple(out)

    # the UNCHANGED plan-turn write check's own argvs (pre-existing, also proxy-legal)
    legacy = {("status", "--porcelain"), ("checkout", "--", "."), ("clean", "-fd")}
    branch = {("checkout", "-q", "master"), ("checkout", "-q", "main")}
    unknown = sorted({shape(a) for a in sent} - shapes - legacy - branch)
    assert unknown == []
    for argv in legacy | branch:
        assert git_proxy.classify(list(argv)).action == "allow"


async def test_case9_the_audit_records_a_label_not_the_command(db_url):
    """router.exec_check audits `command[:200]`; the bridge's attempt commands carry a worker's
    check command and commit paths, so they pass a label instead."""
    orch, db = await _orch(db_url)
    try:
        eid, _c, _r = await _effort(orch)
        orch.harness.check_queue.append((0, "ok", False))
        # no worker holds this effort's workspace: refused, nothing runs
        assert await orch._attempt_exec(eid, f"echo {CMD_MARK}") is None
        assert orch.harness.checks == []
        # the worker focused on it (as a plan or coding wake records it)
        for row in await orch.scheduler.snapshot():
            orch.router._ws_focus[row["id"]] = (eid, REPO, "")
        assert await orch._attempt_exec(eid, f"echo {CMD_MARK}", label="ao-attempt:checks") == "ok"
        blob = json.dumps(await _events(orch, eid, "check_exec"))
        assert "ao-attempt:checks" in blob and CMD_MARK not in blob
    finally:
        await _shutdown(orch, db)


# ── Case 10: the parser reads open-terminal's real framing, and fails closed ──────────────────
def test_case10_records_survive_chunk_splits_and_the_extra_join_newline():
    import random
    att = _a()
    raw = att.BEGIN + "".join(f"@@N 1\t2\tdir/file_{i}.py N@@" for i in range(300)) + att.END
    for seed in range(20):
        out = daemon_frame(raw + "\n", "", random.Random(seed))
        recs = att.records(out)
        assert recs is not None and len(recs) == 300
        assert recs[17] == ("N", "1\t2\tdir/file_17.py")


def test_case10_truncated_or_missing_markers_are_untrusted():
    att = _a()
    assert att.records("") is None
    assert att.records("@@HEAD x HEAD@@" + att.END) is None          # no BEGIN (cut to 64 KB)
    assert att.records(att.BEGIN + "@@HEAD x HEAD@@") is None        # no END (died)
    assert att.parse_probe(att.BEGIN + "@@HEAD nothex HEAD@@@@DIRTY 0 DIRTY@@" + att.END) is None


def test_case10_a_token_shaped_path_is_masked_but_never_trusted_as_another_path():
    att = _a()
    tok = "ghp_" + "a" * 36
    out = daemon_frame(att.BEGIN + f"@@N 1\t0\tfixtures/{tok}.txt N@@@@NT 1 NT@@" + att.END,
                       "", __import__("random").Random(1))
    d = att.parse_numstat(out)
    assert d is not None and tok not in json.dumps(d)                 # display only, masked


# ── Case 11: grammars and the fact classifier ────────────────────────────────────────────────
def test_case11_verdict_grammar_is_strict():
    att = _a()
    steps = {1, 2}
    assert att.parse_verdict("VERDICT: KEEP 1,2", steps).steps == [1, 2]
    assert att.parse_verdict("VERDICT: KEEP steps 1 and 2", steps).steps == [1, 2]
    v = att.parse_verdict("VERDICT: DISCARD 2 deviation: broke A2", steps)
    assert (v.kind, v.steps, v.klass, v.reason) == ("discard", [2], "deviation", "broke A2")
    for bad in ("", "VERDICT: KEEP 3", "VERDICT: DISCARD 1", "VERDICT: KEEP",
                "VERDICT: KEEP 1\nVERDICT: DISCARD 1 no-solution: x", "keep 1", "VERDICT: maybe"):
        assert att.parse_verdict(bad, steps) is None, bad


def test_case11_plan_grammar_requires_an_anchor_per_step():
    att = _a()
    d = att.parse_plan(PLAN_1)
    assert not d.problems and [s["anchor"] for s in d.steps] == ["A1", "A2"]
    d = att.parse_plan("PLAN:\n1. x [anchor: A9]\n2. y\n")
    assert len(d.problems) == 2
    assert att.parse_plan("PLAN:\n1. x [anchor: A1]", {"A1": "kept"}).problems == []
    assert att.parse_plan("PLAN: nothing remains").nothing_remains


def test_case11_facts_regression_vs_new_not_passing_carry_the_anchor():
    att = _a()
    checks = [{"step": 1, "cmd": "pytest -v"}]
    steps = [{"n": 1, "anchor": "A1"}]
    before = {0: {"rc": 0, "tests": {"t.py::a": "PASSED", "t.py::b": "FAILED"}}}
    after = {0: {"rc": 1, "tests": {"t.py::a": "FAILED", "t.py::b": "FAILED",
                                    "n.py::c": "FAILED", "t.py::d": "PASSED"}}}
    f = att.classify(checks, before, after, steps, {"n.py"})
    assert [x["test"] for x in f["regression"]] == ["t.py::a"]
    assert [x["test"] for x in f["new_not_passing"]] == ["n.py::c"]
    assert [x["test"] for x in f["still_failing"]] == ["t.py::b"]
    assert all(x["anchor"] == "A1" for k in ("regression", "new_not_passing") for x in f[k])
    # the start not measurable: never guessed into a regression
    f = att.classify(checks, None, after, steps, set())
    assert f["regression"] == [] and len(f["unknown_before"]) == 3


def test_case11_scrub_keeps_prose_and_drops_code():
    att = _a()
    t = att.scrub("it broke A2 `rm -rf /` because $(x) | y ; z", 200)
    assert "rm -rf" not in t and "$" not in t and "|" not in t and t.startswith("it broke A2")


# ── Case 12: the real little-coder-open-terminal image, offline ──────────────────────────────
@pytest.mark.skipif(not _CONTAINER, reason="set AO_ATTEMPT_OT_CONTAINER to an offline container "
                                           "of the real little-coder-open-terminal image")
@pytest.mark.parametrize("scenario", [scenario_keep, scenario_discard, scenario_plan_writes],
                         ids=["keep", "discard", "plan_writes"])
async def test_case12_real_open_terminal_image(db_url, scenario):
    ws = ContainerWS(_CONTAINER, _KEY)
    await _run(db_url, ws, scenario)
    log = ws.calls()
    assert not any("DENIED" in json.dumps(c) or c.get("action") == "deny" for c in log), log


@pytest.mark.skipif(not _CONTAINER, reason="needs AO_ATTEMPT_OT_CONTAINER")
@pytest.mark.parametrize("run", sorted(C7A_RUNS))
async def test_case12_real_image_c7a_replay(db_url, run):
    modified, added = C7A_RUNS[run]
    ws = ContainerWS(_CONTAINER, _KEY)
    for p in modified:
        ws.write(p, f"original {p}\n")
    ws.git("add", "-A")
    ws.git("commit", "-q", "-m", "base")
    orch, db = await _orch(db_url)
    orch._plan_actions = []
    _wire(orch, ws, orch._plan_actions)
    try:
        eid, chan, root = await _effort(orch)
        for p in modified:
            ws.write(p, f"coding turn edit {p}\n")
        for p in added:
            ws.write(p, f"coding turn new file {p}\n")
        assert await _gate(orch, eid, chan, root, C7A_PLAN) is not None
        for p in modified:
            assert ws.read(p) == f"coding turn edit {p}\n", p
        for p in added:
            assert ws.read(p) == f"coding turn new file {p}\n", p
    finally:
        await _shutdown(orch, db)
