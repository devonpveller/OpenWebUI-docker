"""ao-checks (2026-10-07) - the acceptance checks the org banks are genuine and its self-repair is honest.

gym-002 (plan store journal/notes/agent-org-gym002-loop-2026-10-07.md): 14 of 15 permanent gym checks
were shell commands cut mid-word. little-coder caps each recorded command at 240 chars; Mode B read REPRO
lines out of that capped command stream; the reproduction gate accepted ANY non-zero exit, including a
shell syntax error (2) and command-not-found (127); and the burn-down those checks triggered re-checked
only the BUILD, went green in one round, and the checks failed again on the next delivery - 7 times
since July, never surfaced.

Each test here names the defect it pins. Fakes only: a command-aware `run_check` stub stands in for the
worker daemon's `/check` (no docker, no network, no live bridge DB).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest

from app.adapters.chat import FakeChatAdapter
from app.config import Settings
from app.db import Database
from app.modules.capabilities import BranchDelivery
from app.modules.model_router import FakeModelClient
from app.orchestrator import Orchestrator
from app.worker.harness import FakeHarness

ROOT = Path(__file__).resolve().parents[1]
REPO = "https://github.com/acme/gym.git"
FINDINGS = "/tmp/lens-findings.txt"

# The live shape (event 35862.. / mode_b_check_added 07-30): one lens command echoing both lines, cut at
# 240 chars by little-coder's activity record - the REPRO ends mid-token.
FULL_REPRO = ("python3 -c \"import os,todo; os.environ['TODO_DB']='/tmp/t.json'; "
              "todo.add('x', due='2026-10-09'); "
              "assert [t for t in todo.list_tasks(due_before='2026-10-08')] == [], 'due-before kept a later task'\"")
FINDING = "FINDING: list --due-before keeps tasks due AFTER the cutoff date"
LENS_CMD = (f"echo '{FINDING}' >> {FINDINGS}\n"
            f"echo 'REPRO: {FULL_REPRO}' >> {FINDINGS}")
TRUNCATED_CMD = LENS_CMD[:240]          # exactly what little-coder's agent.py:81 recorded


def _stub_checks(harness: FakeHarness, rules: list[tuple[str, tuple]]) -> None:
    """Replace the fake daemon's /check with a COMMAND-AWARE responder: the first rule whose substring
    occurs in the command answers it; anything else passes. Order-independent, so a test does not
    encode the number or order of exec calls the code under test makes."""
    async def run_check(base_url, command, *, cwd=None, timeout=600):
        harness.checks.append({"base_url": base_url, "command": command, "cwd": cwd,
                               "timeout": timeout})
        for sub, res in rules:
            if sub in command:
                return res
        return (0, "", False)
    harness.run_check = run_check


async def _drain(orch):
    for _ in range(40):
        if not orch._bg_tasks:
            break
        await asyncio.gather(*list(orch._bg_tasks), return_exceptions=True)


async def _shutdown(orch, db):
    await _drain(orch)
    for t in (orch._capacity_task, orch._stall_task, orch._reaper_task):
        if t is not None:
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
    await db.dispose()


# ── (1) Mode B: complete sources only, and a reproduction must fail for the defect's reason ─────────────

async def _mode_b_orch(db_url):
    settings = Settings(
        _env_file=None, chat_adapter="fake",
        profiles_dir=str(ROOT / "profiles"), charters_dir=str(ROOT / "charters"),
        floor_dir=str(ROOT / "floor"), worker_instance_urls="http://w1:8090",
        max_concurrent_workers=1, database_url=db_url, project_survey_enabled=False,
        review_mode="off", plan_approval="off", mode_b=True,
    )
    db = Database(db_url)
    orch = Orchestrator(settings, db, FakeChatAdapter(),
                        model_client=FakeModelClient(), harness=FakeHarness())
    await orch.setup()
    await orch.projects.add("gym", REPO)
    eid, chan, root = await orch.router.open_effort("feat", project="gym")
    return orch, db, eid, chan, root


def _delivery():
    return BranchDelivery(branch="agent/feat", exists=True, ahead=1, head_sha="abc1234567")


def _mode_b_rules(file_text: str, repro_result: tuple, parse_result: tuple = (0, "", False)):
    return [
        (f"cat {FINDINGS}", (0, f"{file_text}\nSALVAGE-DONE", False)),   # the findings FILE (complete)
        ("echo CLEARED", (0, "CLEARED", False)),
        ("sh -n -c", parse_result),                                        # the parse gate
        ("git checkout -f agent/feat", repro_result),                      # the reproduction run
    ]


async def test_t1_truncated_stream_repro_is_not_banked_full_file_repro_is(db_url):
    """A REPRO is taken from the complete findings FILE, never from the 240-char command stream: with the
    stream cut mid-token and the file holding the full line, the banked body is the FULL command."""
    orch, db, eid, chan, root = await _mode_b_orch(db_url)
    try:
        orch.harness.stream_commands = [TRUNCATED_CMD]
        orch.harness.output_queue.append("I found one defect; details are in the findings file.")
        _stub_checks(orch.harness, _mode_b_rules(
            f"{FINDING}\nREPRO: {FULL_REPRO}",
            (1, "Traceback (most recent call last):\nAssertionError: due-before kept a later task", False)))
        r = await orch._mode_b_phase(eid, chan, root, REPO, _delivery())
        bodies = [c["body"] for c in await orch.projects.list_acceptance_checks("gym")]
        assert bodies == [FULL_REPRO], bodies          # never the truncated stream copy
        assert r["checks_added"] == 1
    finally:
        await _shutdown(orch, db)


async def test_t1_repro_present_only_in_truncated_stream_is_not_banked(db_url):
    """The live failure: the only REPRO the org can see is the 240-cut one in the command stream, and
    running it gives dash's 'Unterminated quoted string' (exit 2). Nothing is banked, and the ignored
    stream REPRO is recorded (dropped visibly, not silently)."""
    orch, db, eid, chan, root = await _mode_b_orch(db_url)
    try:
        orch.harness.stream_commands = [TRUNCATED_CMD]
        orch.harness.output_queue.append("done")
        _stub_checks(orch.harness, _mode_b_rules(
            "", (2, "sh: 1: Syntax error: Unterminated quoted string", False)))
        await orch._mode_b_phase(eid, chan, root, REPO, _delivery())
        assert await orch.projects.list_acceptance_checks("gym") == []
        assert await orch._event_count(eid, "mode_b_check_added") == 0
        assert await orch._event_count(eid, "mode_b_stream_repro_ignored") == 1
    finally:
        await _shutdown(orch, db)


@pytest.mark.parametrize("exit_code,out", [
    (2, "sh: 1: Syntax error: Unterminated quoted string"),
    (2, "sh: 1: Syntax error: end of file unexpected (expecting \")\")"),
    (127, "sh: 1: todo: not found"),
    (126, "sh: 1: ./todo.py: Permission denied"),
    (1, "bash: -c: line 1: syntax error near unexpected token `)'"),
    (1, "bash: line 1: todoctl: command not found"),
])
async def test_t2_shell_failure_is_not_a_reproduction(db_url, exit_code, out):
    """Exit 2/126/127 and a shell syntax / command-not-found error mean the CHECK did not run - it says
    nothing about the product. Not accepted as a reproduction; nothing banked."""
    orch, db, eid, chan, root = await _mode_b_orch(db_url)
    try:
        orch.harness.output_queue.append(f"{FINDING}\nREPRO: python3 todo.py list --due-before 2026-10-08")
        _stub_checks(orch.harness, _mode_b_rules("", (exit_code, out, False)))
        r = await orch._mode_b_phase(eid, chan, root, REPO, _delivery())
        assert r["reproduced"] == 0 and r["checks_added"] == 0
        assert await orch.projects.list_acceptance_checks("gym") == []
        ev = [e for e in await orch.audit.replay(eid) if e["kind"] == "mode_b_finding_unreproduced"]
        assert ev and ev[-1]["payload"].get("reason") == "not_a_defect_failure"
    finally:
        await _shutdown(orch, db)


async def test_t2_genuine_assertion_failure_is_banked(db_url):
    """The other side of the gate: a REPRO that runs and fails on the product's own assertion IS a
    reproduced defect and is banked (no genuine check dropped)."""
    orch, db, eid, chan, root = await _mode_b_orch(db_url)
    try:
        orch.harness.output_queue.append(f"{FINDING}\nREPRO: {FULL_REPRO}")
        _stub_checks(orch.harness, _mode_b_rules(
            "", (1, "Traceback (most recent call last):\n  File \"<string>\", line 1\n"
                    "AssertionError: due-before kept a later task", False)))
        r = await orch._mode_b_phase(eid, chan, root, REPO, _delivery())
        assert r["reproduced"] == 1 and r["checks_added"] == 1
        assert [c["body"] for c in await orch.projects.list_acceptance_checks("gym")] == [FULL_REPRO]
    finally:
        await _shutdown(orch, db)


async def test_t2_repro_that_does_not_parse_is_refused_before_it_runs(db_url):
    """`sh -n` first: a REPRO that does not parse is refused with reason shell_syntax and never run -
    even if running it would have 'failed'."""
    orch, db, eid, chan, root = await _mode_b_orch(db_url)
    try:
        bad = 'python3 -c "import os,todo; os.e'
        orch.harness.output_queue.append(f"{FINDING}\nREPRO: {bad}")
        _stub_checks(orch.harness, _mode_b_rules(
            "", (1, "AssertionError", False),
            parse_result=(2, "sh: 1: Syntax error: Unterminated quoted string", False)))
        await orch._mode_b_phase(eid, chan, root, REPO, _delivery())
        assert await orch.projects.list_acceptance_checks("gym") == []
        ev = [e for e in await orch.audit.replay(eid) if e["kind"] == "mode_b_finding_unreproduced"]
        assert ev and ev[-1]["payload"].get("reason") == "shell_syntax"
        assert not any("git checkout -f agent/feat" in c["command"] for c in orch.harness.checks)
    finally:
        await _shutdown(orch, db)


# ── (2)-(4) the acceptance-corpus gate and the burn-down it starts ─────────────────────────────────────

def _remote(state: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        if "/branches/" in p:
            handler.reads = getattr(handler, "reads", 0) + 1
            sha = "prehead000000" if handler.reads == 1 else "cafe1234beef"
            return httpx.Response(200, json={"commit": {"sha": sha}})
        if "/compare/" in p:
            return httpx.Response(200, json={"ahead_by": 1, "commits": [],
                "files": [{"filename": "todo.py", "additions": 1, "deletions": 0}]})
        if p.endswith("/pulls") and request.method == "POST":
            return httpx.Response(201, json={"number": 7,
                "html_url": "https://github.com/demoowner/gym/pull/7"})
        if p.endswith("/pulls") and request.method == "GET":
            return httpx.Response(200, json=[])
        if p.count("/") == 3:
            return httpx.Response(200, json={"default_branch": "main"})
        return httpx.Response(404)
    return handler


async def _corpus_orch(db_url, tmp_path):
    key = tmp_path / "app.pem"
    key.write_text("dummy")
    settings = Settings(
        _env_file=None, chat_adapter="fake",
        profiles_dir=str(ROOT / "profiles"), charters_dir=str(ROOT / "charters"),
        floor_dir=str(ROOT / "floor"), worker_instance_urls="http://w1:8090",
        max_concurrent_workers=1, database_url=db_url, project_survey_enabled=False,
        review_mode="off", plan_approval="off",
        github_app_id="1", github_app_owner="demoowner",
        github_app_private_key_path=str(key), burndown_round_cap=4,
    )
    db = Database(db_url)
    orch = Orchestrator(settings, db, FakeChatAdapter(),
                        model_client=FakeModelClient(), harness=FakeHarness())
    await orch.setup()
    await orch.projects.add("gym", "https://github.com/demoowner/gym")
    await orch.projects.set_check("gym", "python3 -m unittest -q")      # the BUILD - always green here
    orch._gh_transport = httpx.MockTransport(_remote({}))
    eid, chan, root = await orch.router.open_effort("due dates", project="gym")
    return orch, db, eid, chan, root


GENUINE = "python3 todo_due_check.py"
# A real traceback: the assertion that names the defect is the LAST line, past the first 300 chars of
# the 600-char tail the gate keeps - where the old `t[:300]` cut it off.
GENUINE_ERR = ("Traceback (most recent call last):\n"
               + "".join(f'  File "/workspace/todo_due_check.py", line {i}, in <module>\n    step_{i}()\n'
                         for i in range(1, 7))
               + "AssertionError: due_before kept the task due 2026-10-09")
BROKEN = 'python3 -c "import os,todo; os.e'


async def test_t3_burndown_with_failing_checks_and_passing_build_is_not_green(db_url, tmp_path):
    """The burn-down started by a failing acceptance corpus re-runs THOSE checks every round; a passing
    build alone is not green. The checks keep failing -> no burndown_green, the loop elevates instead."""
    orch, db, eid, chan, root = await _corpus_orch(db_url, tmp_path)
    try:
        cid = await orch.projects.add_acceptance_check("gym", "operator: due-before must exclude later tasks",
                                                       GENUINE)
        _stub_checks(orch.harness, [(GENUINE, (1, GENUINE_ERR, False))])
        orch.harness.output_queue = ["did the work", "pushed"]
        await orch.delegate(eid, chan, root, "due dates", plan_steps=["work"])
        await _drain(orch)
        kinds = [e["kind"] for e in await orch.audit.replay(eid)]
        assert "burndown_started" in kinds
        assert "burndown_green" not in kinds                       # build green, checks red => NOT green
        started = kinds.index("burndown_started")
        rechecks = [e for e in (await orch.audit.replay(eid))[started:]
                    if e["kind"] == "burndown_corpus_recheck"]
        assert rechecks and all(cid in e["payload"]["failed"] for e in rechecks)
        # the checks really were re-run after the burn-down started, not just the build
        assert sum(GENUINE in c["command"] for c in orch.harness.checks) >= 3
    finally:
        await _shutdown(orch, db)


async def test_t3_burndown_goes_green_only_when_the_checks_pass(db_url, tmp_path):
    """Control for T3: when the worker's fix makes the failing check pass, the burn-down IS green."""
    orch, db, eid, chan, root = await _corpus_orch(db_url, tmp_path)
    try:
        await orch.projects.add_acceptance_check("gym", "operator: due-before", GENUINE)
        calls = {"n": 0}

        async def run_check(base_url, command, *, cwd=None, timeout=600):
            orch.harness.checks.append({"command": command})
            if GENUINE in command:
                calls["n"] += 1
                return (1, GENUINE_ERR, False) if calls["n"] <= 2 else (0, "ok", False)
            return (0, "", False)
        orch.harness.run_check = run_check
        orch.harness.output_queue = ["did the work", "pushed"]
        await orch.delegate(eid, chan, root, "due dates", plan_steps=["work"])
        await _drain(orch)
        kinds = [e["kind"] for e in await orch.audit.replay(eid)]
        assert "burndown_started" in kinds and "burndown_green" in kinds
        assert calls["n"] >= 3                                     # green came from re-running the check
    finally:
        await _shutdown(orch, db)


async def test_t4_parse_error_check_escalates_instead_of_dispatching(db_url, tmp_path):
    """A failing check whose own error is a shell parse error is the CHECK being broken: no worker is
    woken to 'fix' it and no burn-down starts; the operator is told which check(s) and how to retire
    them. The check is NOT retired automatically (still active)."""
    orch, db, eid, chan, root = await _corpus_orch(db_url, tmp_path)
    try:
        cid = await orch.projects.add_acceptance_check("gym", "mode_b: crashes with ...", BROKEN,
                                                       created_by="mode_b")
        _stub_checks(orch.harness, [(BROKEN, (2, "sh: 1: Syntax error: Unterminated quoted string", False))])
        orch.harness.output_queue = ["did the work", "pushed"]
        await orch.delegate(eid, chan, root, "due dates", plan_steps=["work"])
        await _drain(orch)
        assert not any("ACCEPTANCE CHECK" in w["prompt"] and "FAILED" in w["prompt"]
                       for w in orch.harness.wakes)                # no fix wake
        kinds = [e["kind"] for e in await orch.audit.replay(eid)]
        assert "burndown_started" not in kinds
        assert "acceptance_check_broken" in kinds
        msgs = "\n".join(p["message"] for p in orch.chat.posted)
        assert "check itself is broken" in msgs and cid in msgs
        assert f"retire check gym {cid}" in msgs                    # the one-line way to retire it
        assert f"merge-{eid}" not in orch._pending_merge           # a broken check is not a pass
        assert [c["id"] for c in await orch.projects.list_acceptance_checks("gym")] == [cid]
    finally:
        await _shutdown(orch, db)


async def test_t4_broken_check_beside_a_genuine_one_still_drives_the_genuine_fix(db_url, tmp_path):
    """Mixed corpus: the broken check is escalated, the genuine failing check is NOT dropped - the
    worker is still asked to fix it, and the burn-down re-runs it."""
    orch, db, eid, chan, root = await _corpus_orch(db_url, tmp_path)
    try:
        bad = await orch.projects.add_acceptance_check("gym", "mode_b: broken", BROKEN, created_by="mode_b")
        good = await orch.projects.add_acceptance_check("gym", "operator: due-before", GENUINE)
        _stub_checks(orch.harness, [
            (BROKEN, (2, "sh: 1: Syntax error: Unterminated quoted string", False)),
            (GENUINE, (1, GENUINE_ERR, False))])
        orch.harness.output_queue = ["did the work", "pushed"]
        await orch.delegate(eid, chan, root, "due dates", plan_steps=["work"])
        await _drain(orch)
        fix = [w["prompt"] for w in orch.harness.wakes if "DURABLE ACCEPTANCE CHECKS FAILED" in w["prompt"]]
        assert fix and good in fix[0] and bad not in fix[0]
        ev = await orch.audit.replay(eid)
        assert any(e["kind"] == "acceptance_check_broken" and bad in e["payload"]["ids"] for e in ev)
        started = [e for e in ev if e["kind"] == "burndown_started"]
        assert started and bad not in started[0]["payload"].get("checks", [])
        assert good in started[0]["payload"].get("checks", [])
    finally:
        await _shutdown(orch, db)


async def test_t5_fix_tasks_carry_each_checks_error_output(db_url, tmp_path):
    """When a worker IS asked to fix failing checks, its task includes each check's error output - the
    assertion line that names the defect, not a 300-char cut of the traceback header - both on the
    gate's route-back and in every burn-down round."""
    orch, db, eid, chan, root = await _corpus_orch(db_url, tmp_path)
    try:
        cid = await orch.projects.add_acceptance_check("gym", "operator: due-before", GENUINE)
        _stub_checks(orch.harness, [(GENUINE, (1, GENUINE_ERR, False))])
        orch.harness.output_queue = ["did the work", "pushed"]
        await orch.delegate(eid, chan, root, "due dates", plan_steps=["work"])
        await _drain(orch)
        last_line = "AssertionError: due_before kept the task due 2026-10-09"
        route_back = [w["prompt"] for w in orch.harness.wakes
                      if "DURABLE ACCEPTANCE CHECKS FAILED" in w["prompt"]]
        assert route_back and last_line in route_back[0] and cid in route_back[0]
        bd = [w["prompt"] for w in orch.harness.wakes if "~bd" in w["session_id"]
              or "build for `gym` is failing" in w["prompt"]]
        assert bd, [w["session_id"] for w in orch.harness.wakes]
        assert all(last_line in p and cid in p and GENUINE in p for p in bd)
    finally:
        await _shutdown(orch, db)


async def test_t4_operator_retires_broken_checks_in_one_line(db_url, tmp_path):
    """The one-line way the escalation names: `retire check <project> <id> ...` - deterministic, operator-issued,
    dispatches no work, keeps the row (active=false) and its audit trail."""
    orch, db, eid, chan, root = await _corpus_orch(db_url, tmp_path)
    try:
        a = await orch.projects.add_acceptance_check("gym", "mode_b: broken 1", BROKEN, created_by="mode_b")
        b = await orch.projects.add_acceptance_check("gym", "mode_b: broken 2", BROKEN + "x",
                                                     created_by="mode_b")
        keep = await orch.projects.add_acceptance_check("gym", "operator: due-before", GENUINE)
        await orch.nl_intake(f"retire check gym {a} {b}", channel_id="c1", user_id="operator-api")
        assert [c["id"] for c in await orch.projects.list_acceptance_checks("gym")] == [keep]
        assert len(await orch.projects.list_acceptance_checks("gym", active_only=False)) == 3
        assert orch.harness.wakes == []
        assert any("retired 2 acceptance check" in p["message"] for p in orch.chat.posted)
    finally:
        await _shutdown(orch, db)


# ── (5) the org reads the FULL command where the daemon exposes it ─────────────────────────────────────

def test_t6_harness_reads_the_full_command_not_the_display_copy():
    """little-coder now sends `command_full` beside the 240-char `command`; the bridge's command record
    (WorkResult.commands, the flail key) uses the full text, and still works with an older daemon."""
    from app.worker.harness import _command_texts, _one_command_text
    item = {"command": LENS_CMD[:240], "command_truncated": True, "command_full": LENS_CMD, "ok": True}
    assert _command_texts([item]) == [LENS_CMD]
    assert _one_command_text(item) == LENS_CMD
    assert _command_texts([{"command": "git status", "ok": True}]) == ["git status"]   # older daemon


async def test_t3_burndown_stops_rerunning_a_check_the_operator_retired(db_url, tmp_path):
    """Retiring is the operator's answer; once a check is retired (active=false) the burn-down no longer
    re-runs it, and with nothing left failing the round is green."""
    orch, db, eid, chan, root = await _corpus_orch(db_url, tmp_path)
    try:
        cid = await orch.projects.add_acceptance_check("gym", "operator: due-before", GENUINE)
        check = (await orch.projects.list_acceptance_checks("gym"))[0]
        _stub_checks(orch.harness, [(GENUINE, (1, GENUINE_ERR, False))])
        assert await orch._rerun_corpus_checks(eid, [check], repo="https://github.com/demoowner/gym")
        await orch.projects.set_acceptance_check_active(cid, False)
        assert await orch._rerun_corpus_checks(eid, [check], repo="https://github.com/demoowner/gym") == []
    finally:
        await _shutdown(orch, db)


# ══ Round 2 (tester attempt 1) ══════════════════════════════════════════════════════════════════════
# Fixture: the REAL 15 `gym` acceptance-check rows (bodies read read-only from the live bridge DB by the
# tester, 2026-10-07) with the REAL dash 0.5.12 `sh -n` and run results (Ubuntu WSL, clean PATH, no product
# checkout). 14 are the retired broken checks; ac-e01b73c72ed7 (`python3 todo.py reopen --help`) is the
# one genuine check.
import json  # noqa: E402

GYM = json.loads((Path(__file__).parent / "fixtures" / "ao_checks_gym_checks.json").read_text("utf-8"))
GYM_BROKEN = {k: v for k, v in GYM.items() if not v["active"]}
INLINE_ERR = GYM["ac-7b96f8711316"]["run_out"]      # File "<string>", line 1 ... SyntaxError, no header


def test_r2_fixture_is_the_real_corpus():
    assert len(GYM_BROKEN) == 14 and GYM["ac-e01b73c72ed7"]["active"]


@pytest.mark.parametrize("cid", sorted(GYM_BROKEN))
async def test_r2_t9_no_real_broken_gym_body_is_banked_by_mode_b(db_url, cid):
    """Each of the 14 real broken bodies, offered to Mode B as a complete REPRO with dash's real parse and
    run result: none is banked (ac-7b96f8711316 parses and exits 1 with an inline SyntaxError)."""
    fx = GYM_BROKEN[cid]
    orch, db, eid, chan, root = await _mode_b_orch(db_url)
    try:
        orch.harness.output_queue.append(f"{FINDING}\nREPRO: {fx['body']}")
        _stub_checks(orch.harness, _mode_b_rules(
            "", (fx["run_exit"], fx["run_out"], False),
            parse_result=(fx["parse_exit"], fx["parse_out"], False)))
        r = await orch._mode_b_phase(eid, chan, root, REPO, _delivery())
        assert r["checks_added"] == 0, (cid, fx["run_exit"], fx["run_out"][-120:])
    finally:
        await _shutdown(orch, db)


async def test_r2_t9_all_14_real_broken_gym_checks_escalate_none_dispatched(db_url, tmp_path):
    """All 14 real broken checks active in one corpus, each failing with dash's real output: all 14 are
    escalated as broken, none reaches a fix prompt, no burn-down starts."""
    orch, db, eid, chan, root = await _corpus_orch(db_url, tmp_path)
    try:
        ids = {}
        for cid, fx in GYM_BROKEN.items():
            ids[await orch.projects.add_acceptance_check("gym", "mode_b: x", fx["body"],
                                                         created_by="mode_b")] = cid
        # longest body first, so a body that contains another as a substring answers with its own result
        _stub_checks(orch.harness, [(fx["body"], (fx["run_exit"], fx["run_out"], False))
                                    for fx in sorted(GYM_BROKEN.values(), key=lambda f: -len(f["body"]))])
        orch.harness.output_queue = ["did the work", "pushed"]
        await orch.delegate(eid, chan, root, "due dates", plan_steps=["work"])
        await _drain(orch)
        ev = await orch.audit.replay(eid)
        broken = set()
        for e in ev:
            if e["kind"] == "acceptance_check_broken":
                broken |= set(e["payload"]["ids"])
        fix = [w["prompt"] for w in orch.harness.wakes if "DURABLE ACCEPTANCE CHECKS FAILED" in w["prompt"]]
        assert broken == set(ids)
        assert [i for i in ids if any(i in p for p in fix)] == []
        assert "burndown_started" not in [e["kind"] for e in ev]
    finally:
        await _shutdown(orch, db)


@pytest.mark.parametrize("out", [
    "Traceback (most recent call last):\n  File \"/workspace/todo.py\", line 12, in parse_due\n"
    "    return eval(expr)\n  File \"<string>\", line 1\n    2026-10-\n            ^\n"
    "SyntaxError: invalid syntax",                                      # product eval() error: has header
    "  File \"/workspace/todo.py\", line 3\n    def f(:\nSyntaxError: invalid syntax",  # product source
])
def test_r2_t9_product_syntax_errors_stay_genuine(out):
    from app.orchestrator import _shell_broken
    assert not _shell_broken(out)


def test_r2_t9_inline_compile_error_is_broken():
    from app.orchestrator import _shell_broken
    assert _shell_broken(INLINE_ERR)
    assert _shell_broken('  File "<stdin>", line 4\n    x = (\nIndentationError: unexpected indent')


@pytest.mark.parametrize("out", [
    "usage: todo.py [-h] {add,list,done}\ntodo.py: error: argument cmd: invalid choice: 'reopen'",
    "ERROR collecting tests/test_todo.py\nImportError: cannot import name 'due_before'\n"
    "Interrupted: 1 error during collection",
    "make: *** [Makefile:3: check] Error 1",
])
async def test_r2_t10_genuine_exit_2_is_banked(db_url, out):
    """Exit 2 from the program (argparse usage error - the class of the only genuine gym check -, pytest
    collection error, make) is a genuine reproduction and IS banked."""
    orch, db, eid, chan, root = await _mode_b_orch(db_url)
    try:
        orch.harness.output_queue.append(f"{FINDING}\nREPRO: python3 todo.py reopen --help")
        _stub_checks(orch.harness, _mode_b_rules("", (2, out, False)))
        r = await orch._mode_b_phase(eid, chan, root, REPO, _delivery())
        assert r["checks_added"] == 1
    finally:
        await _shutdown(orch, db)


@pytest.mark.parametrize("exit_code,out", [
    (2, "sh: 1: Syntax error: \"(\" unexpected"),
    (127, "python3: some text without a shell prefix"),
    (126, ""),
])
async def test_r2_t10_shell_exit_2_and_126_127_still_refused(db_url, exit_code, out):
    orch, db, eid, chan, root = await _mode_b_orch(db_url)
    try:
        orch.harness.output_queue.append(f"{FINDING}\nREPRO: python3 todo.py reopen --help")
        _stub_checks(orch.harness, _mode_b_rules("", (exit_code, out, False)))
        r = await orch._mode_b_phase(eid, chan, root, REPO, _delivery())
        assert r["checks_added"] == 0
    finally:
        await _shutdown(orch, db)


async def _toggled(orch):
    from sqlalchemy import select

    from app.models import Event
    async with orch.db.session_factory() as s:
        rows = (await s.execute(select(Event).where(Event.kind == "acceptance_check_toggled"))
                ).scalars().all()
    return [(r.actor, r.payload) for r in rows]


async def test_r2_t11_retire_records_actor_is_project_scoped_and_idempotent(db_url, tmp_path):
    orch, db, eid, chan, root = await _corpus_orch(db_url, tmp_path)
    try:
        await orch.projects.add("other", "https://github.com/demoowner/other")
        foreign = await orch.projects.add_acceptance_check("other", "op", "true")
        mine = await orch.projects.add_acceptance_check("gym", "op", BROKEN)
        await orch.nl_intake(f"retire check gym {mine} {foreign}", channel_id="c1", user_id="op-user")
        msg = orch.chat.posted[-1]["message"]
        assert "retired 1 acceptance check" in msg and mine in msg
        assert foreign in msg and "Not a check of `gym`" in msg
        assert [c["id"] for c in await orch.projects.list_acceptance_checks("other")] == [foreign]
        assert await _toggled(orch) == [("op-user", {"id": mine, "active": False})]   # WHO retired it
        await orch.nl_intake(f"retire check gym {mine}", channel_id="c1", user_id="op-user")
        msg = orch.chat.posted[-1]["message"]
        assert "retired 0" in msg and "Already retired" in msg and mine in msg
        assert len(await _toggled(orch)) == 1                                       # no second toggle
        # the foreign id retires only when ITS project is named
        await orch.nl_intake(f"retire check other {foreign}", channel_id="c1", user_id="op-user")
        assert await orch.projects.list_acceptance_checks("other") == []
        assert orch.harness.wakes == []
    finally:
        await _shutdown(orch, db)


@pytest.mark.parametrize("line", [
    "retire check ac-0123456789ab",            # no project
    "retire check gym",                        # no id
    "retire check gym ac-12",                  # malformed id
    "retire checks nope ac-0123456789ab",      # unknown project
])
async def test_r2_t11_malformed_retire_answers_with_the_grammar(db_url, tmp_path, line):
    orch, db, eid, chan, root = await _corpus_orch(db_url, tmp_path)
    try:
        cid = await orch.projects.add_acceptance_check("gym", "op", BROKEN)
        n_posts, n_wakes = len(orch.chat.posted), len(orch.harness.wakes)
        await orch.nl_intake(line, channel_id="c1", user_id="op-user")
        await _drain(orch)
        new = [p["message"] for p in orch.chat.posted[n_posts:]]
        assert len(new) == 1 and "retire check <project> ac-" in new[0], new
        assert len(orch.harness.wakes) == n_wakes                     # no fall-through to the PO model
        assert [c["id"] for c in await orch.projects.list_acceptance_checks("gym")] == [cid]
    finally:
        await _shutdown(orch, db)


async def test_r2_t12_corpus_cleared_after_broken_escalation_in_burndown(db_url, tmp_path):
    """The check turns broken mid-burn-down: escalated, and the loop's corpus is cleared on that return."""
    orch, db, eid, chan, root = await _corpus_orch(db_url, tmp_path)
    try:
        await orch.projects.add_acceptance_check("gym", "operator: due-before", GENUINE)
        n = {"n": 0}

        async def run_check(base_url, command, *, cwd=None, timeout=600):
            orch.harness.checks.append({"command": command})
            if GENUINE in command:
                n["n"] += 1
                return ((2, "sh: 1: Syntax error: Unterminated quoted string", False) if n["n"] >= 3
                        else (1, GENUINE_ERR, False))
            return (0, "", False)
        orch.harness.run_check = run_check
        orch.harness.output_queue = ["did the work", "pushed"]
        await orch.delegate(eid, chan, root, "due dates", plan_steps=["work"])
        await _drain(orch)
        kinds = [e["kind"] for e in await orch.audit.replay(eid)]
        assert "burndown_started" in kinds and "acceptance_check_broken" in kinds
        assert not orch._burndown_corpus.get(eid)
        assert not getattr(orch, "_burndown_corpus_pending", {}).get(eid)
    finally:
        await _shutdown(orch, db)


async def test_r2_t12_corpus_cleared_after_stall_and_after_archive(db_url, tmp_path):
    orch, db, eid, chan, root = await _corpus_orch(db_url, tmp_path)
    try:
        await orch.projects.add_acceptance_check("gym", "operator: due-before", GENUINE)
        _stub_checks(orch.harness, [(GENUINE, (1, GENUINE_ERR, False))])
        orch.harness.output_queue = ["did the work", "pushed"]
        await orch.delegate(eid, chan, root, "due dates", plan_steps=["work"])
        await _drain(orch)
        kinds = [e["kind"] for e in await orch.audit.replay(eid)]
        assert "burndown_started" in kinds and "burndown_green" not in kinds   # stalled / capped out
        assert not orch._burndown_corpus.get(eid)
        # archive path: a corpus queued for an archived effort is dropped, never run
        check = (await orch.projects.list_acceptance_checks("gym"))[0]

        async def aborted(_eid):
            return True
        orch._is_aborted = aborted
        orch._queue_burndown(eid, "acceptance corpus failing:\n- x", checks=[(check, GENUINE_ERR)])
        await _drain(orch)
        assert not orch._burndown_corpus.get(eid)
        assert not getattr(orch, "_burndown_corpus_pending", {}).get(eid)
    finally:
        await _shutdown(orch, db)


async def test_r2_t13_parse_check_exception_has_its_own_reason(db_url):
    orch, db, eid, chan, root = await _mode_b_orch(db_url)
    try:
        orch.harness.output_queue.append(f"{FINDING}\nREPRO: {FULL_REPRO}")
        rules = _mode_b_rules("", (1, "AssertionError", False))

        async def run_check(base_url, command, *, cwd=None, timeout=600):
            orch.harness.checks.append({"command": command})
            if "sh -n -c" in command:
                raise RuntimeError("409 busy")
            for sub, res in rules:
                if sub in command:
                    return res
            return (0, "", False)
        orch.harness.run_check = run_check
        await orch._mode_b_phase(eid, chan, root, REPO, _delivery())
        ev = [e for e in await orch.audit.replay(eid) if e["kind"] == "mode_b_finding_unreproduced"]
        assert ev and ev[-1]["payload"]["reason"] == "parse_check_unavailable"
        assert await orch.projects.list_acceptance_checks("gym") == []
    finally:
        await _shutdown(orch, db)


async def test_r2_t14_findings_file_is_read_from_the_lens_worker(db_url):
    """Worker affinity: with two workers, the findings-file clear and read use the LENS session, so they
    land on the worker that ran the lens and its complete REPRO is recovered."""
    settings = Settings(
        _env_file=None, chat_adapter="fake",
        profiles_dir=str(ROOT / "profiles"), charters_dir=str(ROOT / "charters"),
        floor_dir=str(ROOT / "floor"), database_url=db_url, project_survey_enabled=False,
        review_mode="off", plan_approval="off", mode_b=True,
        worker_instance_urls="http://w1:8090,http://w2:8090", max_concurrent_workers=2)
    db = Database(db_url)
    orch = Orchestrator(settings, db, FakeChatAdapter(), model_client=FakeModelClient(),
                        harness=FakeHarness())
    await orch.setup()
    try:
        await orch.projects.add("gym", REPO)
        eid, chan, root = await orch.router.open_effort("feat", project="gym")
        sessions: list[tuple[str, str]] = []
        orig = orch.router.exec_check

        async def exec_check(effort_id, *, command, session_id, **kw):
            sessions.append((command, session_id))
            return await orig(effort_id, command=command, session_id=session_id, **kw)
        orch.router.exec_check = exec_check
        lens_url: dict[str, str] = {}
        orig_wake = orch.harness.wake

        async def wake(base_url, session_id, prompt, **kw):
            lens_url["url"] = base_url
            return await orig_wake(base_url, session_id, prompt, **kw)
        orch.harness.wake = wake

        async def run_check(base_url, command, *, cwd=None, timeout=600):
            orch.harness.checks.append({"base_url": base_url, "command": command})
            if "cat /tmp/lens-findings.txt" in command:      # only the lens worker has the file
                txt = f"{FINDING}\nREPRO: {FULL_REPRO}" if base_url == lens_url.get("url") else ""
                return (0, f"{txt}\nSALVAGE-DONE", False)
            if "git checkout -f agent/feat" in command:
                return (1, "AssertionError: due-before kept a later task", False)
            return (0, "", False)
        orch.harness.run_check = run_check
        orch.harness.output_queue.append("done - see the findings file")
        r = await orch._mode_b_phase(eid, chan, root, REPO, _delivery())
        lens_session = orch.harness.wakes[-1]["session_id"]
        read = [sid for cmd, sid in sessions if "cat /tmp/lens-findings.txt" in cmd]
        clear = [sid for cmd, sid in sessions if "echo CLEARED" in cmd]
        assert read == [lens_session] and clear == [lens_session]
        reads = [c["base_url"] for c in orch.harness.checks if "cat /tmp/lens" in c["command"]]
        assert reads == [lens_url["url"]]
        assert r["checks_added"] == 1
    finally:
        await _shutdown(orch, db)
