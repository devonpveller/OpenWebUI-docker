"""ao-attempt2 round 2 - after attempt 1 FAIL (test-evidence/ao-attempt2.attempt1.md).

Principle: TOLERANT where a miss is safe (unjudged / re-ask / map), STRICT where a miss destroys
work (a DISCARD resets the branch).

R1  a DISCARD is accepted only as the ONLY directive: every mixed or conditional verdict - the
    tester's lines verbatim - is unparseable, so the attempt is left unjudged, never discarded
    (real gate, local + real image). Round 4's lowercase / odd-spacing KEEP lines read again.
R2  a grammar miss on a plan WITH steps never stops the effort: a revision after a lens rejection
    in the tester's shapes (`Revised plan:`, bullets, ...) is read; the tag re-ask and the
    "no steps" re-ask are separate budgets; the one stop left (no steps at all, twice) says so.
R3  the cheap findings: a fenced template before the plan, the LAST plan wins, indented
    `Checks:`/`Risks:` lines, numbered section headers; F5's post-acquire focus re-check; test ids
    in the attempt_facts audit row are an allow-list.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models import WorkerInstance
from attempt_ws import LocalWS
from test_attempts2 import (
    CONTENT_MARK, PLAN_1, REPO, _a, _base, _code, _edits, _effort, _events, _gate, _needs_git,
    _orch, _plan_next, _run, _shutdown, _wire,
)

CORPUS = {r["id"]: r for r in json.loads(
    (Path(__file__).parent / "fixtures" / "c7_plan_corpus.json").read_text(encoding="utf-8"))["replies"]}

# ── R1: DISCARD is strict ────────────────────────────────────────────────────────────────────
# the tester's lines verbatim (attempt 1, FAIL 1), then more of the same family
MIXED = [
    "DISCARD 2 and KEEP 1",
    "DISCARD 2, KEEP 1",
    "DISCARD 2 if the tests still fail, otherwise KEEP all",
    "DISCARD step 2 (deviation) - wrong file; KEEP step 1",
    "DISCARD 2 - but KEEP step 1, it is complete",
    "KEEP 1, DISCARD 2",
    "DISCARD 2 unless the leak is gone",
    "DISCARD 2 and 1 - both wrong",
    "DISCARD 2, 1",
    "DISCARD steps 2 & 1",
    "discard 2 (no-solution) - else keep it",
]


def test_r1_a_mixed_or_conditional_discard_is_unparseable():
    att = _a()
    for line in MIXED:
        assert att.parse_verdict("VERDICT: " + line, {1, 2}) is None, line
    # two VERDICT lines, or a directive elsewhere in the reply
    for text in ("VERDICT: DISCARD 2 - wrong file\nVERDICT: KEEP 1",
                 "VERDICT: DISCARD 2 - wrong file\nVERDICT: DISCARD 2 - it broke the API",
                 "VERDICT: DISCARD 2 - wrong file\nKEEP step 1 though, it is done",
                 "- KEEP 1\nVERDICT: DISCARD 2 - wrong file"):
        assert att.parse_verdict(text, {1, 2}) is None, text
    # a single, plain DISCARD still discards (numbers that are not step references are prose)
    for text, reason in (("VERDICT: DISCARD 2 - wrong file", "wrong file"),
                         ("VERDICT: DISCARD 2 deviation: it failed 3 tests", "it failed 3 tests"),
                         ("VERDICT: discard 2 - the leak persists", "the leak persists"),
                         ("VERDICT: DISCARD 2 - wrong\nVERDICT: DISCARD 2 - wrong", "wrong")):
        v = att.parse_verdict(text, {1, 2})
        assert v is not None and (v.kind, v.steps, v.reason) == ("discard", [2], reason), text


def test_r1_round4s_lowercase_and_odd_spacing_keep_lines_read_again():
    att = _a()
    for text in ("verdict: keep 1, 2", "Verdict: KEEP 1 , 2", "VERDICT: KEEP 1, 2\nVERDICT: KEEP 1,2",
                 "VERDICT: keep 1,2\nverdict: KEEP 1, 2"):
        v = att.parse_verdict(text, {1, 2})
        assert v is not None and (v.kind, v.steps) == ("keep", [1, 2]), text
    # safe-direction misses stay unparseable (unjudged): mid-line, conflicting keeps
    assert att.parse_verdict("So my verdict is: KEEP 1, 2", {1, 2}) is None
    assert att.parse_verdict("VERDICT: KEEP 1\nVERDICT: KEEP 1, 2", {1, 2}) is None


def _scenario_mixed(line):
    async def scenario(orch, ws):
        """The real gate: a mixed verdict (twice) leaves the attempt UNJUDGED - step 1's and
        step 2's work both stay on the branch; nothing is discarded."""
        att = _a()
        _base(ws)
        eid, chan, root = await _effort(orch)
        assert await _gate(orch, eid, chan, root, PLAN_1) is not None
        await _code(orch, eid, chan, root, _edits)
        reply = _plan_next("VERDICT: " + line)
        assert await _gate(orch, eid, chan, root, reply, reply) is not None
        kinds = [k for k, _ in await _events(orch, eid)]
        assert kinds.count("attempt_discarded") == 0 and kinds.count("attempt_kept") == 0
        assert kinds.count("attempt_verdict_unparseable") == 2 and kinds.count("attempt_unjudged") == 1
        assert kinds.count("worker_plan_stopped") == 0
        assert CONTENT_MARK in ws.read("a.py") and CONTENT_MARK in ws.read("b.py")
        assert ws.git("rev-parse", "HEAD").strip() == ws.git("rev-parse", att.ref_name(eid, 1)).strip()
    scenario.__name__ = "scenario_mixed"
    return scenario


@_needs_git
@pytest.mark.parametrize("line", MIXED[:6], ids=[f"m{i}" for i in range(6)])
async def test_r1_a_mixed_verdict_through_the_real_gate_never_discards(db_url, tmp_path, line):
    await _run(db_url, LocalWS(tmp_path), _scenario_mixed(line))


_CONTAINER = os.environ.get("AO_ATTEMPT_OT_CONTAINER", "")
_KEY = os.environ.get("AO_ATTEMPT_OT_KEY", "ao-attempt-test-key")


@pytest.mark.skipif(not _CONTAINER, reason="needs AO_ATTEMPT_OT_CONTAINER (the real image, offline)")
@pytest.mark.parametrize("line", MIXED[:3], ids=["m0", "m1", "m2"])
async def test_r1_real_image_a_mixed_verdict_never_discards(db_url, line):
    from attempt_ws import ContainerWS
    ws = ContainerWS(_CONTAINER, _KEY)
    await _run(db_url, ws, _scenario_mixed(line))
    assert not any("DENIED" in json.dumps(c) for c in ws.calls())


# ── R2: a grammar miss on a plan with steps never stops ──────────────────────────────────────
def _rehead(text, header):
    lines = text.splitlines()
    i = next(i for i, ln in enumerate(lines) if "PLAN" in ln and not ln.lstrip(" *").startswith("UNDER"))
    lines[i] = header
    return "\n".join(lines)


def _bulleted(text):
    import re
    return re.sub(r"(?m)^(?:\*\*)?\d+[.)](?:\*\*)?\s+", "- ", text)


def _tabled(text):
    return "UNDERSTANDING: same as before\nPLAN:\n| # | step |\n|---|---|\n" + "\n".join(
        f"| {i} | step {i} of the plan |" for i in range(1, 6)) + "\nRISKS: none"


REVISIONS = {
    "verbatim": lambda t: t,
    "REVISED PLAN:": lambda t: _rehead(t, "REVISED PLAN:"),
    "Revised plan:": lambda t: _rehead(t, "Revised plan:"),
    "Implementation plan:": lambda t: _rehead(t, "Implementation plan:"),
    "My plan:": lambda t: _rehead(t, "My plan:"),
    "The plan is as follows:": lambda t: _rehead(t, "The plan is as follows:"),
    "bullets": _bulleted,
    "table": _tabled,
    "Revised plan + bullets": lambda t: _bulleted(_rehead(t, "Revised plan:")),
}


@pytest.mark.parametrize("rid", ["C7-ao-queue-r1", "C7a-ao-queue-r2"])
@pytest.mark.parametrize("shape", sorted(REVISIONS))
async def test_r2_a_revision_after_a_lens_rejection_is_read_and_approved(db_url, rid, shape):
    """The tester's scenario: a REAL corpus reply twice (mapped), the lens rejects it, then the
    revision in each shape. Round 2: read, mapped, approved - never stopped."""
    from app.schemas import MonitorVerdict
    first = CORPUS[rid]["text"]
    orch, db = await _orch(db_url)
    try:
        eid, chan, root = await _effort(orch)
        orch.models._client.queue_structured(MonitorVerdict(deviates=True, rationale="it skips x"))
        out = await _gate(orch, eid, chan, root, first, first, REVISIONS[shape](first), repo=None)
        kinds = [k for k, _ in await _events(orch, eid)]
        assert out is not None, kinds
        assert kinds.count("worker_plan_stopped") == 0 and kinds.count("worker_plan_rejected") == 1
        assert kinds.count("plan_steps_stored") == 1 and kinds.count("plan_steps_unreadable") == 0
        assert len(orch.harness.wakes) == 3
    finally:
        await _shutdown(orch, db)


async def test_r2_the_no_steps_re_ask_is_its_own_budget_after_the_tag_re_ask_was_spent(db_url):
    """Untagged first plan (re-ask spent), lens rejection, then a revision with NO steps: it still
    gets its own re-ask; the steps that come back are approved."""
    from app.schemas import MonitorVerdict
    first = CORPUS["C7-ao-queue-r1"]["text"]
    prose = "UNDERSTANDING: x\nPLAN: I will fix the leak in the data route and add tests.\nRISKS: none"
    orch, db = await _orch(db_url)
    try:
        eid, chan, root = await _effort(orch)
        orch.models._client.queue_structured(MonitorVerdict(deviates=True, rationale="it skips x"))
        out = await _gate(orch, eid, chan, root, first, first, prose,
                          _rehead(first, "Revised plan:"), repo=None)
        ev = await _events(orch, eid)
        kinds = [k for k, _ in ev]
        assert out is not None and kinds.count("worker_plan_stopped") == 0
        assert [p["ask"] for k, p in ev if k == "plan_steps_unreadable"] == [1]
        assert "no numbered plan steps" in orch.harness.wakes[3]["prompt"]
    finally:
        await _shutdown(orch, db)


async def test_r2_the_one_stop_left_says_exactly_why(db_url):
    """No steps at all, twice: the accepted residual stops, and its reason is TRUE (C5)."""
    prose = "UNDERSTANDING: x\nPLAN: I will look into it and fix what I find.\nRISKS: none"
    orch, db = await _orch(db_url)
    try:
        eid, chan, root = await _effort(orch)
        assert await _gate(orch, eid, chan, root, prose, prose, repo=None) is None
        ev = await _events(orch, eid)
        stop = [p for k, p in ev if k == "worker_plan_stopped"]
        assert len(stop) == 1 and "no numbered or bulleted plan steps" in stop[0]["reason"]
        assert "re-ask" in stop[0]["reason"]
        assert [p["ask"] for k, p in ev if k == "plan_steps_unreadable"] == [1, 2]
        assert not [k for k, _ in ev if k == "plan_steps_undeclared"]
    finally:
        await _shutdown(orch, db)


# ── R3: the cheap findings ───────────────────────────────────────────────────────────────────
def test_r3_grammar_findings_from_attempt_1():
    att = _a()
    k = {"A1": "x"}

    def steps(t):
        return [(s["n"], s["text"]) for s in att.parse_plan(t, k, fixed=True).steps]

    # a fenced template quoting the format BEFORE the real plan never becomes the plan
    t = ("UNDERSTANDING: x\nThe format is:\n```\nPLAN:\n1. <step> [anchor: A1]\n```\n"
         "PLAN:\n1. edit a.py\n2. add b.py\n3. run tests\nRISKS: none")
    assert [n for n, _ in steps(t)] == [1, 2, 3] and steps(t)[0][1] == "edit a.py"
    d = att.parse_plan(t, k, fixed=True)
    assert d.unanchored == [1, 2, 3]                     # the fenced tag did not count
    # a fenced block INSIDE a step stays inside it
    t = "PLAN:\n1. edit a.py:\n```\nPLAN:\n1. x\n2. y\n```\n2. add b.py\nRISKS: none"
    assert [n for n, _ in steps(t)] == [1, 2]
    # PLAN (original) then REVISED PLAN / Revised plan: the LAST plan wins
    for hdr in ("REVISED PLAN:", "Revised plan:"):
        t = f"PLAN (original):\n1. a\n2. b\n{hdr}\n1. c\n2. d\n3. e\n4. f\nRISKS: none"
        assert steps(t) == [(1, "c"), (2, "d"), (3, "e"), (4, "f")], hdr
    # indented Checks:/Risks: continuation lines do not end the PLAN; a prose Title-case line
    # with text after it does not either; CAPITALS / markdown / alone still do
    t = "PLAN:\n1. edit a.py\n   Checks: pytest -q\n   Risks: low\n2. add b.py\n3. tests\nWON'T DO: x"
    assert [n for n, _ in steps(t)] == [1, 2, 3]
    t = "PLAN:\n1. a\nRisks: the reaper may race\n2. b\nWON'T DO: x"
    assert [n for n, _ in steps(t)] == [1, 2]
    for end in ("RISKS: x", "**Risks:** x", "Risks:", "## Risks"):
        assert [n for n, _ in steps(f"PLAN:\n1. a\n{end}\n2. b")] == [1], end
    # numbered section headers are headers, not steps
    t = ("### 1. Understanding\nfix it\n### 2. Already done\nnothing\n### 3. Plan\n1. edit a.py\n"
         "2. add b.py\n### 4. Won't do\nx\n### 5. Risks\n1. none")
    assert steps(t) == [(1, "edit a.py"), (2, "add b.py")]
    # ... but a numbered STEP that mentions a plan with text after it is a step
    t = "PLAN:\n1. a\n2. b\n3. Plan (phase 2): migrate the DB\n4. c\nRISKS: none"
    assert [n for n, _ in steps(t)] == [1, 2, 3, 4]
    # dash-separated and table steps; `2-3 retries` is not a step
    assert [n for n, _ in steps("PLAN:\n1 - edit a.py\n2 - add b.py\nRISKS: none")] == [1, 2]
    assert [n for n, _ in steps("PLAN:\n1. a\n2-3 retries are fine\n2. b")] == [1, 2]


async def test_r3_f5_the_focus_is_checked_again_after_the_acquire(db_url, monkeypatch):
    """The tester's TOCTOU: w1 is free and focused on E; while acquire awaits the gate, w1's focus
    moves to F. The attempt command must be refused (0 /checks) - round 4's post-acquire check,
    restored next to round 2's pre-acquire one."""
    # c7f-int: the exact refusals (a wrong kwarg must not pass as 'refused')
    from app.modules.router import WorkspaceMoved
    from app.modules.scheduler import NoCapacityError
    orch, db = await _orch(db_url)
    try:
        await orch.projects.add("gym", REPO)
        e, _c, _r = await orch.router.open_effort("e", project="gym")
        async with orch.db.session_factory() as s:
            w1 = (await s.execute(select(WorkerInstance))).scalars().first()
            w1.session_id, w1.effort_id, w1.sched_state = "sess-e", e, "suspended"
            wid = w1.id
            await s.commit()
        orch.router._ws_focus[wid] = (e, REPO, "")
        orch.harness.check_queue.append((0, "ok", False))
        gate = orch.router.scheduler.gate
        orig = gate.can_dispatch

        async def moving(effort_id):
            orch.router._ws_focus[wid] = ("effort-f", REPO, "")
            return await orig(effort_id)

        monkeypatch.setattr(gate, "can_dispatch", moving)
        with pytest.raises((NoCapacityError, WorkspaceMoved)):
            await orch.router.exec_check(e, command="echo x", session_id="sess-e",
                                         instance_id=wid, expect_effort=e, label="ao-attempt")
        assert orch.harness.checks == []
    finally:
        await _shutdown(orch, db)


def test_r3_audit_test_ids_are_an_allow_list():
    att = _a()
    tok = "ghp_" + "Zq3" * 12
    for ok in ("t.py::test_old", "tests/test_x.py::test_a[1-2]", "Calc.Tests.AddTests.Adds"):
        assert att.audit_id(ok) == ok
    for bad in (f"t.py::test_{tok}", "t.py::test_sk-" + "a1" * 15, "t.py::test x$(boom)",
                "t.py::test_" + "p" * 170):
        shown = att.audit_id(bad)
        assert shown.startswith("#") and len(shown) == 11 and tok not in shown, bad
    facts = {"tests": {"regressed": [{"test": f"t.py::test_{tok}"}], "new": [{"test": "t.py::test_ok"}]}}
    out = att.fact_ids(facts)
    assert tok not in json.dumps(out) and out["tests"]["new"] == ["t.py::test_ok"]


@_needs_git
async def test_r3_a_token_shaped_test_id_never_reaches_the_attempt_facts_audit_row(db_url, tmp_path):
    """Through the real gate and the real check runner: the worker's check prints a test id that
    carries a token; the attempt_facts audit row shows its hash, the counts stay exact."""
    tok = "ghp_" + "Zq3" * 12
    ws = LocalWS(tmp_path)
    _base(ws)
    ws.write("check.sh", f"echo 't.py::test_{tok} PASSED'\n")
    ws.git("add", "-A")
    ws.git("commit", "-q", "-m", "check")
    orch, db = await _orch(db_url)
    _wire(orch, ws)
    try:
        eid, chan, root = await _effort(orch)
        assert await _gate(orch, eid, chan, root, PLAN_1) is not None

        def coding(w):
            _edits(w)
            w.write("check.sh", f"echo 't.py::test_{tok} FAILED'\nexit 1\n")

        await _code(orch, eid, chan, root, coding, "done.\nCHECKS:\nstep 1: sh check.sh\n")
        await _gate(orch, eid, chan, root, _plan_next("VERDICT: KEEP 1, 2"))
        facts = [p for k, p in await _events(orch, eid) if k == "attempt_facts"]
        assert facts and facts[-1]["tests_counts"]["regressed"] == 1
        assert tok not in json.dumps(facts) and facts[-1]["tests"]["regressed"][0].startswith("#")
    finally:
        await _shutdown(orch, db)

