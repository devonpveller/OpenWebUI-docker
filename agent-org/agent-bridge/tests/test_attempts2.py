"""ao-attempt2 - the follow-ups to ao-attempt, each through the real path.

B1  the gate never stops an effort over the planner's FORMAT: a VERDICT is read tolerantly, and a
    second unparseable verdict leaves the parked attempt unjudged (committed, on its ref) and the
    plan continues; only a reply with no steps at all still stops (the accepted residual).
F1  AO_ATTEMPTS_ENABLED=false is the base gate EXACTLY: the same events, prompts, tree and posts
    as 6ae15f2c on the same scenarios (tests/fixtures/ao_off_base_golden.json was written by THIS
    file's scenarios running against the base code - AO_WRITE_BASE_GOLDEN - and is compared here).
F2  an operator's answer to a held question becomes anchor items (real NL path, handle_event);
    a convergent re-report reusing an effort from before ao-attempt keeps its original request.
F3  the operator's raw request is not cut at a bridge header word (real intake).
F4  a discard sends the memory plane codes and counts only.
F5  a refused workspace command never rebinds another worker (real Router.exec_check + scheduler).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path

import pytest
from sqlalchemy import select

from app.adapters.chat import FakeChatAdapter
from app.config import Settings
from app.db import Database
from app.models import Event, WorkerInstance
from app.modules.model_router import FakeModelClient
from app.orchestrator import Orchestrator
from app.worker.harness import FakeHarness

from attempt_ws import BASH, GIT, LocalWS

ROOT = Path(__file__).resolve().parents[1]
REPO = "https://github.com/acme/gym.git"
GOAL = "- greet() returns a greeting\n- farewell() exists"
CONTENT_MARK = "CONTENTMARK_93f1"
GOLDEN = Path(__file__).parent / "fixtures" / "ao_off_base_golden.json"
_needs_git = pytest.mark.skipif(not (BASH and GIT), reason="needs bash and git")


def _a():
    from app.modules import attempts
    return attempts


async def _orch(db_url, *, on=True, urls="http://w1:8090", workers=1, gate="all"):
    # ON = opt in (ao-attempt is OFF by default since the stopping-rule ship); OFF = NOTHING set,
    # so the OFF scenarios prove the DEFAULT is the base gate
    kw = {"attempts_enabled": True} if on else {}
    settings = Settings(
        _env_file=None, chat_adapter="fake",
        profiles_dir=str(ROOT / "profiles"), charters_dir=str(ROOT / "charters"),
        floor_dir=str(ROOT / "floor"), worker_instance_urls=urls,
        max_concurrent_workers=workers, database_url=db_url, project_survey_enabled=False,
        review_mode="off", plan_approval="off", worker_plan_gate=gate, **kw,
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


def _wire(orch, ws):
    """The daemon's /check -> the workspace; a plan / coding wake first performs its queued
    action on the tree. Router.exec_check, the scheduler and the focus map stay real."""
    orch._plan_actions, orch._code_actions = [], []

    async def _check(base_url, command, *, cwd=None, timeout=600):
        orch.harness.checks.append({"base_url": base_url, "command": command})
        return await asyncio.to_thread(ws.bridge, command)

    async def _check_fn(base_url, command):
        return await asyncio.to_thread(ws.bridge, command)

    orch.harness.run_check = _check
    orch.harness.check_fn = _check_fn
    orig = orch.harness.wake

    async def _wake(base_url, session_id, prompt, **kw):
        q = orch._plan_actions if kw.get("plan_only") else orch._code_actions
        if q:
            act = q.pop(0)
            if act is not None:
                await asyncio.to_thread(act, ws)
        return await orig(base_url, session_id, prompt, **kw)

    orch.harness.wake = _wake


async def _events(orch, eid=None) -> list[tuple[str, dict]]:
    async with orch.db.session_factory() as s:
        q = select(Event).order_by(Event.id)
        if eid:
            q = q.where(Event.effort_id == eid)
        return [(e.kind, e.payload or {}) for e in (await s.execute(q)).scalars()]


async def _items(orch, eid) -> dict:
    return (await orch._effort_plan_row(eid))["items"]


def _base(ws) -> str:
    ws.write("a.py", "def greet():\n    return 'base'\n")
    ws.git("add", "-A")
    ws.git("commit", "-q", "-m", "base")
    return ws.git("rev-parse", "HEAD").strip()


def _edits(w):
    w.write("a.py", f"def greet():\n    return 'hello'  # {CONTENT_MARK}\n")
    w.write("b.py", f"def farewell():\n    return 'bye'  # {CONTENT_MARK}\n")


PLAN_1 = ("UNDERSTANDING: add greet and farewell\nALREADY DONE: nothing\n"
          "PLAN:\n1. edit a.py so greet returns hello [anchor: A1]\n"
          "2. add b.py with farewell [anchor: A2]\nWON'T DO: nothing else\nRISKS: none")


def _plan_next(verdict: str) -> str:
    return ("UNDERSTANDING: finish the goal\nALREADY DONE: see the parked attempt\n"
            "PLAN:\n1. document greet in README.md [anchor: A1]\nWON'T DO: nothing else\n"
            "RISKS: none\n" + verdict)


async def _effort(orch, goal=GOAL):
    await orch.projects.add("gym", REPO)
    eid, chan, root = await orch.router.open_effort("feat", project="gym")
    await orch.charters.set_goal(eid, goal, created_by="po")
    return eid, chan, root


async def _gate(orch, eid, chan, root, *replies, repo=REPO, goal=GOAL):
    orch.harness.output_queue.extend(replies)
    return await orch._worker_plan_gate(eid, chan, root, goal, repo, None, None, None)


async def _code(orch, eid, chan, root, action, reply="done", repo=REPO):
    orch._code_actions.append(action)
    orch.harness.output_queue.append(reply)
    return await orch._run_step(eid, chan, root, "execute the approved plan", 1, 1, repo, False)


# ── B1: verdict tolerance; a second miss continues unjudged; no steps still stops ────────────
def test_b1_the_verdict_is_read_in_the_forms_a_model_writes():
    att = _a()
    s = {1, 2, 3}
    ok = {
        "VERDICT: KEEP 1,2": ("keep", [1, 2]),
        "**VERDICT:** KEEP 1, 2": ("keep", [1, 2]),
        "**VERDICT**: **KEEP** steps 1 & 2": ("keep", [1, 2]),
        "VERDICT - KEEP 1 and 3": ("keep", [1, 3]),
        "Verdict: keep all": ("keep", [1, 2, 3]),
        "VERDICT:\nKEEP 1-3": ("keep", [1, 2, 3]),
        "## VERDICT (previous attempt): KEEP 2": ("keep", [2]),
        "VERDICT: KEEP 1, 2 - both complete [anchor: A1]": ("keep", [1, 2]),
        "Verdict: it mostly worked.\nVERDICT: KEEP 1": ("keep", [1]),     # prose is not a verdict
        "VERDICT: KEEP <numbers of the steps above>\nVERDICT: KEEP 2": ("keep", [2]),  # template
        "VERDICT: DISCARD step 2 (deviation) - it broke A2": ("discard", [2]),
        "VERDICT: DISCARD 2 because the leak persists": ("discard", [2]),
        "VERDICT: DISCARD 2: no-solution - nothing changed": ("discard", [2]),
    }
    for text, (kind, steps) in ok.items():
        v = att.parse_verdict(text, s)
        assert v is not None and (v.kind, v.steps) == (kind, steps), text
    v = att.parse_verdict("VERDICT: DISCARD step 2 (deviation) - it broke A2", s)
    assert (v.klass, v.reason) == ("deviation", "it broke A2")
    v = att.parse_verdict("VERDICT: DISCARD: wrong approach", {4})     # one-step attempt
    assert (v.kind, v.steps, v.reason) == ("discard", [4], "wrong approach")
    # still unparseable (an unparseable verdict never discards)
    for bad in ("", "VERDICT: KEEP", "VERDICT: KEEP 4", "VERDICT: DISCARD 1", "VERDICT: DISCARD 2 "
                "deviation", "keep 1", "VERDICT: KEEP 1\nVERDICT: DISCARD 1 no-solution: x",
                "VERDICT: KEEP 1, 2 and DISCARD 3", "VERDICT: DISCARD: x", "VERDICT: maybe"):
        assert att.parse_verdict(bad, s) is None, bad


def test_b1_every_grammar_input_is_bounded():
    """C6: a reply is read as its first and last REPLY_MAX/2 characters (a VERDICT or CHECKS at
    the end of a huge reply is still found); at most MAX_STEPS steps of at most STEP_TEXT_MAX."""
    att = _a()
    filler = "x" * (att.REPLY_MAX * 2)
    v = att.parse_verdict("PLAN:\n1. a\n" + filler + "\nVERDICT: KEEP 1", {1})
    assert v is not None and v.steps == [1]
    assert att.parse_checks("done\n" + filler + "\nCHECKS:\nstep 1: pytest -q\n") == [
        {"step": 1, "cmd": "pytest -q"}]
    d = att.parse_plan("PLAN:\n" + "\n".join(f"{i}. s [anchor: A1]" for i in range(1, 500)),
                       {"A1": "x"}, fixed=True)
    assert len(d.steps) == att.MAX_STEPS and not d.problems
    d = att.parse_plan("PLAN:\n1. " + "y " * 10000 + "\n" + "\n".join(["  - z"] * 5000),
                       {"A1": "x"}, fixed=True)
    assert len(d.steps) == 1 and len(d.steps[0]["text"]) <= att.STEP_TEXT_MAX


async def scenario_unjudged(orch, ws):
    """No usable verdict after the one re-ask: the plan is approved, the parked attempt stays
    exactly as committed (its ref intact), nothing is discarded, and the next attempt starts on
    it. Round 4 stopped the effort here."""
    att = _a()
    _base(ws)
    eid, chan, root = await _effort(orch)
    assert await _gate(orch, eid, chan, root, PLAN_1) is not None
    await _code(orch, eid, chan, root, _edits)
    no_verdict = _plan_next("")
    assert await _gate(orch, eid, chan, root, no_verdict, no_verdict) is not None
    ev = await _events(orch, eid)
    kinds = [k for k, _ in ev]
    assert kinds.count("attempt_verdict_unparseable") == 2
    assert kinds.count("worker_plan_stopped") == 0 and kinds.count("attempt_discarded") == 0
    assert kinds.count("attempt_unjudged") == 1
    row = await orch._current_attempt(eid)
    ref = att.ref_name(eid, 1)
    park = ws.git("rev-parse", ref).strip()
    assert row is not None and row["n"] == 2 and row["start_sha"] == park   # next attempt on it
    assert ws.git("rev-parse", "HEAD").strip() == park
    assert CONTENT_MARK in ws.read("a.py") and CONTENT_MARK in ws.read("b.py")
    from app.models import EffortAttempt
    async with orch.db.session_factory() as s:
        assert (await s.get(EffortAttempt, f"{eid}:a1")).state == "unjudged"
    assert sum(w["plan_only"] for w in orch.harness.wakes) == 3      # one re-ask only


async def _run(db_url, ws, scenario):
    orch, db = await _orch(db_url)
    _wire(orch, ws)
    try:
        await scenario(orch, ws)
    finally:
        await _shutdown(orch, db)


@_needs_git
async def test_b1_a_second_unparseable_verdict_continues_with_the_attempt_unjudged(db_url, tmp_path):
    await _run(db_url, LocalWS(tmp_path), scenario_unjudged)


async def test_b1_a_reply_with_no_steps_at_all_is_refused_once_then_stops(db_url):
    """The accepted residual: nothing to map."""
    orch, db = await _orch(db_url)
    try:
        eid, chan, root = await _effort(orch)
        empty = "UNDERSTANDING: x\nPLAN: I will look into it.\nWON'T DO: y\nRISKS: none"
        assert await _gate(orch, eid, chan, root, empty, empty, repo=None) is None
        kinds = [k for k, _ in await _events(orch, eid)]
        # round 2: a reply with no steps is audited `plan_steps_unreadable` (its own re-ask budget),
        # no longer `plan_steps_undeclared`
        assert kinds.count("plan_steps_unreadable") == 2 and kinds.count("worker_plan_stopped") == 1
        assert kinds.count("plan_steps_undeclared") == 0
        assert kinds.count("plan_steps_mapped") == 0
    finally:
        await _shutdown(orch, db)


async def test_b1_a_plan_without_a_plan_header_still_yields_its_steps(db_url):
    orch, db = await _orch(db_url)
    try:
        eid, chan, root = await _effort(orch)
        reply = ("UNDERSTANDING: add greet\n1. edit a.py [anchor: A1]\n2. add b.py (anchor A2)\n"
                 "WON'T DO: y\nRISKS:\n1. none really")
        assert await _gate(orch, eid, chan, root, reply, repo=None) is not None
        stored = [p for k, p in await _events(orch, eid) if k == "plan_steps_stored"][-1]
        assert stored["anchors"] == ["A1", "A2"] and stored["n_steps"] == 2
    finally:
        await _shutdown(orch, db)


# ── F1: OFF == base ───────────────────────────────────────────────────────────────────────────
_AO_KINDS = re.compile(r"^(anchor_items_|plan_steps_|attempt_|plan_turn_moved_head)")


def _norm(text: str, eid: str) -> str:
    return (text or "").replace(eid, "<EFFORT>")


_PLANCHECK_CMD = re.compile(
    r"""^python3 -c "import base64;exec\(compile\(base64\.b64decode\('([A-Za-z0-9+/=]+)'\)""")


def _norm_check(command: str, eid: str) -> str:
    """c7f-int: ao-plancheck's plan-turn check ships a base64 python3 script that embeds a fresh
    per-turn token / journal nonce (and the baseline blob keyed by it). Compare its SOURCE with
    those values masked (sha256), so the golden pins the script, not the random token."""
    import base64
    import hashlib
    m = _PLANCHECK_CMD.match(command or "")
    if not m:
        return _norm(command, eid)
    src = base64.b64decode(m.group(1)).decode("utf-8").replace(eid, "<EFFORT>")
    src = re.sub(r"^(TOKEN|NONCE|INLINE|BLOB_SHA) = '[^'\n]*'$", r"\1 = '<v>'", src, flags=re.M)
    return "ao-plancheck-script sha256:" + hashlib.sha256(src.encode("utf-8")).hexdigest()


async def _off_c7a(db_url, ws, run) -> dict:
    """The C7a replay with ao-attempt OFF (the coding turn leaves edits; the plan turn writes
    nothing). Base-compatible: runs unchanged against 6ae15f2c."""
    from test_attempts import C7A_RUNS
    modified, added = C7A_RUNS[run]
    for p in modified:
        ws.write(p, f"original {p}\n")
    ws.git("add", "-A")
    ws.git("commit", "-q", "-m", "base")
    orch, db = await _orch(db_url, on=False)
    _wire(orch, ws)
    try:
        eid, chan, root = await _effort(orch)

        def coding(w):
            for p in modified:
                w.write(p, f"coding turn edit {p}\n")
            for p in added:
                w.write(p, f"coding turn new file {p}\n")

        await _code(orch, eid, chan, root, coding)
        plan = ("UNDERSTANDING: continue the queue fix\nALREADY DONE: the edits in the tree\n"
                "PLAN:\n1. finish shedding.py and run its tests\nWON'T DO: nothing else\n"
                "RISKS: none")
        out = await _gate(orch, eid, chan, root, plan)
        return {
            "approved": out is not None,
            "kinds": [k for k, _ in await _events(orch, eid)],
            "prompts": [_norm(w["prompt"], eid) for w in orch.harness.wakes],
            "plan_only": [bool(w["plan_only"]) for w in orch.harness.wakes],
            "checks": [_norm_check(c["command"], eid) for c in orch.harness.checks],
            "files": {p: (ws.read(p) if (ws.root / p).exists() else None)
                      for p in modified + added} if hasattr(ws, "root") else {},
            "posts": [_norm(m["message"], eid) for m in orch.chat.posted],
        }
    finally:
        await _shutdown(orch, db)


async def _off_intake(db_url, request) -> dict:
    orch, db = await _orch(db_url, on=False)
    try:
        await orch.projects.add("gym", REPO)
        eid, chan, root = await orch.router.open_effort("intake", project="gym")
        await orch._intake_or_dispatch(eid, chan, root, request, reply_prefix="",
                                       mgmt_channel=await orch.mgmt_channel_id() or chan)
        if orch._bg_tasks:
            await asyncio.gather(*list(orch._bg_tasks))
        _v, goal, _s = await orch.charters.current_goal(eid)
        return {"kinds": [k for k, _ in await _events(orch, eid)], "goal": _norm(goal, eid),
                "prompts": [_norm(w["prompt"], eid) for w in orch.harness.wakes]}
    finally:
        await _shutdown(orch, db)


async def _off_gate_twice_misaligned(db_url) -> dict:
    """OFF, no repo: an untagged plan, rejected by the lens once, then approved - the base
    revision prompt."""
    from app.schemas import MonitorVerdict
    orch, db = await _orch(db_url, on=False)
    try:
        eid, chan, root = await _effort(orch)
        orch.models._client.queue_structured(
            MonitorVerdict.model_construct(deviates=True, rationale="it skips farewell"))
        plan = "UNDERSTANDING: x\nPLAN:\n1. edit a.py\nWON'T DO: y\nRISKS: none"
        out = await _gate(orch, eid, chan, root, plan, plan, repo=None)
        return {"approved": out is not None,
                "kinds": [k for k, _ in await _events(orch, eid)],
                "prompts": [_norm(w["prompt"], eid) for w in orch.harness.wakes]}
    finally:
        await _shutdown(orch, db)


async def _off_scenarios(db_url_base: str, tmp_path) -> dict:
    from test_attempts import C7A_RUNS
    out = {}
    for i, run in enumerate(sorted(C7A_RUNS)):
        out[f"c7a:{run}"] = await _off_c7a(
            db_url_base.replace("test.db", f"c7a{i}.db"), LocalWS(tmp_path / f"w{i}"), run)
    out["intake:runtime"] = await _off_intake(
        db_url_base.replace("test.db", "in1.db"),
        "When I click Save the app crashes with a NullReferenceException.")
    out["intake:scope"] = await _off_intake(
        db_url_base.replace("test.db", "in2.db"),
        "Add CSV export.\n\nYOUR SCOPE is the reports page only.")
    out["gate:revised"] = await _off_gate_twice_misaligned(db_url_base.replace("test.db", "g.db"))
    return out


@_needs_git
async def test_f1_off_is_the_base_gate_exactly(db_url, tmp_path):
    """AO_ATTEMPTS_ENABLED=false on the C7a replays, two real intakes and a lens revision: the
    events, every prompt the worker got, every /check sent, every post and the final tree equal
    what the gate WITHOUT ao-attempt produced on the same scenarios (the golden). With
    AO_WRITE_BASE_GOLDEN set the scenarios are RECORDED instead (run that against the base code
    only). c7f-int (adapted): the base is now development 1956d88c (ao-plancheck's plan-turn check
    replaced the full revert the 6ae15f2c golden recorded), re-recorded twice, identical; plan-turn
    check commands are compared by their script with the per-turn token masked (`_norm_check`)."""
    got = await _off_scenarios(db_url, tmp_path)
    dest = os.environ.get("AO_WRITE_BASE_GOLDEN")
    if dest:
        Path(dest).write_text(json.dumps(got, indent=1, sort_keys=True), encoding="utf-8")
        pytest.skip(f"recorded the golden to {dest}")
    want = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert sorted(got) == sorted(want)
    for key in sorted(want):
        for field in sorted(want[key]):
            assert got[key][field] == want[key][field], (key, field)
    # and nothing of ao-attempt ran
    for key, sc in got.items():
        assert not [k for k in sc["kinds"] if _AO_KINDS.match(k)], key
    # the base outcome of the C7a replay since ao-plancheck: the plan turn wrote nothing, so the
    # check reverts nothing and the coding turn's edits survive (6ae15f2c wiped them)
    c7a = got["c7a:ao-queue-r2"]
    assert c7a["kinds"].count("plan_turn_wrote_files") == 0
    assert all(v is not None and v.startswith("coding turn") for v in c7a["files"].values())


async def test_f1_off_by_default_and_on_by_env(monkeypatch):
    """Stopping-rule ship: experimental, OFF unless AO_ATTEMPTS_ENABLED=true."""
    monkeypatch.delenv("AO_ATTEMPTS_ENABLED", raising=False)
    assert Settings(_env_file=None).attempts_enabled is False
    monkeypatch.setenv("AO_ATTEMPTS_ENABLED", "true")
    assert Settings(_env_file=None).attempts_enabled is True


# ── F2: clarification items; convergent reuse keeps the original request ─────────────────────
async def test_f2_an_operators_clarification_becomes_anchor_items(db_url):
    """Real NL path (handle_event): a vague request HOLDS; the operator's answer is part of what
    was accepted - its sentences are appended as A2.., A1 unchanged."""
    from app.schemas import ClarifyingQuestion, OperatorIntent, ReadinessVerdict
    orch, db = await _orch(db_url, gate="off")
    try:
        mgmt = await orch.mgmt_channel_id()
        orch.models._client.queue_structured(
            OperatorIntent(kind="request", effort_name="hello-fn", reply="Sure."))
        orch.models._client.queue_structured(ReadinessVerdict(
            clear_and_safe=False, blast_radius="routine",
            clarifying_questions=[ClarifyingQuestion(question="What should hello() return?",
                                                     recommendation="'Hello, World!'")]))
        await orch.handle_event({"id": "q1", "channel_id": mgmt, "message": "add a hello function",
                                 "is_bot": False, "ts": 1})
        if orch._bg_tasks:
            await asyncio.gather(*orch._bg_tasks)
        eid = "effort-hello-fn"
        assert eid in orch._pending
        assert await _items(orch, eid) == {"A1": "add a hello function"}
        answer = "It returns the string Hello. It also prints a help list."
        orch.models._client.queue_structured(OperatorIntent(
            kind="clarification", effort_id=eid, steering=answer, reply="Got it."))
        await orch.handle_event({"id": "q2", "channel_id": mgmt, "message": answer,
                                 "is_bot": False, "ts": 2})
        if orch._bg_tasks:
            await asyncio.gather(*orch._bg_tasks)
        assert await _items(orch, eid) == {"A1": "add a hello function",
                                           "A2": "It returns the string Hello.",
                                           "A3": "It also prints a help list."}
        fixed = [p for k, p in await _events(orch, eid) if k == "anchor_items_fixed"]
        assert fixed[-1]["source"] == "operator clarification" and fixed[-1]["added"] == ["A2", "A3"]
        assert answer not in json.dumps(fixed)                    # ids only in the audit
    finally:
        await _shutdown(orch, db)


async def test_f2_a_convergent_reuse_of_an_older_effort_keeps_its_original_request(db_url):
    """An effort from before ao-attempt (goal v1, no stored items) is reused by a re-report
    through the real NL path: its items come from its ORIGINAL request (goal v1, bridge clauses
    cut), not from the re-report's text."""
    from app.schemas import OperatorIntent, ReadinessVerdict
    orch, db = await _orch(db_url, gate="off")
    try:
        mgmt = await orch.mgmt_channel_id()
        await orch.projects.add("gym", REPO)
        sig = "src/app/export.py fails with KeyError 'rows' on an empty report"
        eid, chan, root = await orch.router.open_effort("export-bug", project="gym")
        await orch.charters.set_goal(
            eid, f"Fix the export crash.\n{sig}\n\nHow this gets checked: I'll build it.",
            created_by="po")
        assert await _items(orch, eid) == {}                      # predates ao-attempt
        orch.models._client.queue_structured(OperatorIntent(
            kind="request", effort_name="export-again", project="gym", reply="On it."))
        orch.models._client.queue_structured(ReadinessVerdict(clear_and_safe=True))
        await orch.handle_event({"id": "r1", "channel_id": mgmt,
                                 "message": f"Still broken!\n{sig}\nAlso make the button blue.",
                                 "is_bot": False, "ts": 1})
        if orch._bg_tasks:
            await asyncio.gather(*orch._bg_tasks)
        assert any("Continuing the existing effort" in p["message"] for p in orch.chat.posted)
        items = await _items(orch, eid)
        assert items == {"A1": "Fix the export crash.", "A2": sig}, items
        assert not any("blue" in v or "Still broken" in v for v in items.values())
    finally:
        await _shutdown(orch, db)


# ── F3: no clause cut on the operator's raw request ──────────────────────────────────────────
@pytest.mark.parametrize("request_text,second", [
    ("Add CSV export.\n\nYOUR SCOPE is the reports page only.",
     "YOUR SCOPE is the reports page only."),
    ("Add CSV export.\n\nHow this gets checked: the export must round-trip 1000 rows.",
     "How this gets checked: the export must round-trip 1000 rows."),
], ids=["your-scope", "how-checked"])
async def test_f3_an_operator_paragraph_starting_with_a_header_word_is_kept(
        db_url, request_text, second):
    orch, db = await _orch(db_url, gate="off")
    try:
        await orch.projects.add("gym", REPO)
        eid, chan, root = await orch.router.open_effort("intake", project="gym")
        await orch._intake_or_dispatch(eid, chan, root, request_text, reply_prefix="",
                                       mgmt_channel=await orch.mgmt_channel_id() or chan)
        assert await _items(orch, eid) == {"A1": "Add CSV export.", "A2": second}
        # the goal-v1 (lazy) derivation still cuts the bridge's own clauses
        assert _a().derive_items("Add CSV export.\n\nHow this gets checked: I'll build `make`.") \
            == {"A1": "Add CSV export."}
    finally:
        await _shutdown(orch, db)


# ── F4: the memory plane gets codes and counts only ──────────────────────────────────────────
class _Mem:
    enabled = True

    def __init__(self):
        self.writes: list[dict] = []

    async def write_signature(self, **kw):
        self.writes.append(kw)
        return True

    def __getattr__(self, name):          # every other memory call: a quiet no-op
        async def _noop(*a, **k):
            return None
        return _noop


async def scenario_f4_memory(orch, ws):
    _base(ws)
    ws.write("check.sh", "grep -q hello a.py\n")
    ws.git("add", "-A")
    ws.git("commit", "-q", "-m", "check")
    mem = _Mem()
    orch.memory = mem
    secretish = "ghp_" + "Z" * 36
    eid, chan, root = await _effort(orch, goal=f"- greet() returns a greeting {secretish}\n"
                                                "- farewell() exists")
    assert await _gate(orch, eid, chan, root, PLAN_1,
                       goal="- greet() returns a greeting\n- farewell() exists") is not None
    await _code(orch, eid, chan, root, _edits, "done.\nCHECKS:\nstep 1: sh check.sh\n")
    reason = f"the approach in a.py was wrong {secretish} see /etc/passwd"
    assert await _gate(orch, eid, chan, root,
                       _plan_next(f"VERDICT: DISCARD 1 deviation: {reason}")) is not None
    assert len(mem.writes) == 1
    w = mem.writes[0]
    blob = json.dumps(w)
    for leak in (secretish, "greet() returns", "approach", "/etc/passwd", "a.py", "b.py",
                 "check.sh", CONTENT_MARK):
        assert leak not in blob, leak
    body = w["body"]
    assert re.fullmatch(
        r"agent-org attempt discarded: attempt 1, step 1, anchor item A1, class deviation, "
        r"discards of this item 1; changed files 2 \(\+\d+/-\d+\); worker checks: "
        r"(?:[a-z_]+ \d+(?:, |$))+", body), body
    # the effort's own plan prompt still carries the full note (prompt, not memory)
    notes = [c["body"] for c in await orch._list_constraints(eid)
             if c["kind"] == "attempt_discard"]
    assert notes and "anchor item A1" in notes[0]


@_needs_git
async def test_f4_a_discard_sends_memory_codes_and_counts_only(db_url, tmp_path):
    await _run(db_url, LocalWS(tmp_path), scenario_f4_memory)


# ── the same workspace scenarios on the REAL open-terminal image, offline ────────────────────
_CONTAINER = os.environ.get("AO_ATTEMPT_OT_CONTAINER", "")
_KEY = os.environ.get("AO_ATTEMPT_OT_KEY", "ao-attempt-test-key")
_REAL = [scenario_unjudged, scenario_f4_memory]


@pytest.mark.skipif(not _CONTAINER, reason="set AO_ATTEMPT_OT_CONTAINER to an offline container "
                                           "of the real little-coder-open-terminal image")
@pytest.mark.parametrize("scenario", _REAL, ids=[f.__name__[9:] for f in _REAL])
async def test_real_image_ao_attempt2(db_url, scenario):
    from attempt_ws import ContainerWS
    ws = ContainerWS(_CONTAINER, _KEY)
    await _run(db_url, ws, scenario)
    assert not any("DENIED" in json.dumps(c) for c in ws.calls())


def test_f4_the_memory_note_is_an_allow_list():
    att = _a()
    body = att.memory_note(step=2, anchor="A1; rm -rf /", attempt_no=3, klass="evil text",
                           diff={"n_files": "x", "added": 5, "deleted": -2},
                           facts={"n_regression": 1, "n_hang": "2"}, discards_of_item=2)
    assert "rm" not in body and "evil" not in body
    assert "anchor item ?" in body and "class ?" in body and "changed files 0 (+5/-0)" in body
    assert "regression 1" in body and "hang 2" in body


# ── F5: a refused workspace command never rebinds another worker ─────────────────────────────
async def test_f5_exec_check_refuses_before_acquire_can_rebind(db_url):
    """w1 holds effort E's workspace but is busy; w2 is free and parked on effort F's session.
    An attempt command for E (pinned to w1: instance_id + expect_effort, c7f-int) is refused and w2 keeps F's binding."""
    # c7f-int: the exact refusals (a wrong kwarg must not pass as 'refused')
    from app.modules.router import WorkspaceMoved
    from app.modules.scheduler import NoCapacityError
    orch, db = await _orch(db_url, urls="http://w1:8090,http://w2:8090", workers=2)
    try:
        await orch.projects.add("gym", REPO)
        e, _c, _r = await orch.router.open_effort("e", project="gym")
        f, _c2, _r2 = await orch.router.open_effort("f", project="gym")
        async with orch.db.session_factory() as s:
            rows = {r.base_url: r for r in (await s.execute(select(WorkerInstance))).scalars()}
            w1, w2 = rows["http://w1:8090"], rows["http://w2:8090"]
            w1.sched_state, w1.effort_id, w1.session_id = "computing", e, "sess-e"
            w2.sched_state, w2.effort_id, w2.session_id = "suspended", f, "sess-f"
            w1_id, w2_id = w1.id, w2.id
            await s.commit()
        orch.router._ws_focus[w1_id] = (e, REPO, "")
        orch.router._ws_focus[w2_id] = (f, REPO, "")
        orch.harness.check_queue.append((0, "ok", False))
        with pytest.raises((NoCapacityError, WorkspaceMoved)):
            await orch.router.exec_check(e, command="echo x", session_id="sess-e",
                                         instance_id=w1_id, expect_effort=e, label="ao-attempt")
        async with orch.db.session_factory() as s:
            w2r = await s.get(WorkerInstance, w2_id)
            assert (w2r.effort_id, w2r.session_id, w2r.sched_state) == (f, "sess-f", "suspended")
        assert orch.harness.checks == []                          # nothing ran anywhere
        # a worker focused on ANOTHER effort is refused before any acquire, too
        with pytest.raises((NoCapacityError, WorkspaceMoved)):
            await orch.router.exec_check(e, command="echo x", session_id="sess-f",
                                         instance_id=w2_id, expect_effort=e, label="ao-attempt")
        async with orch.db.session_factory() as s:
            w2r = await s.get(WorkerInstance, w2_id)
            assert (w2r.effort_id, w2r.session_id, w2r.sched_state) == (f, "sess-f", "suspended")
        acq = [p for k, p in await _events(orch) if k == "worker_acquire"]
        assert acq == []
    finally:
        await _shutdown(orch, db)
