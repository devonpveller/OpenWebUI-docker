"""CDCL constraint learning (ORCHESTRATION-DESIGN §5–6). When an attempt fails, the failure becomes a
durable LEARNED CONSTRAINT on the effort and is injected into every later retry, so the small model's
search NARROWS instead of re-walking the same dead end — the mechanism that makes a dumb proposer
converge (the intelligence lives in the accumulated clause set, not the model).

Before this, NOTHING in the org accumulated across retries: burn-down injected only the current round's
error slice, and auto-iterate actively STRIPPED the previous iteration's text. Fakes only."""

from __future__ import annotations

from pathlib import Path

from app.adapters.chat import FakeChatAdapter
from app.config import Settings
from app.db import Database
from app.modules.model_router import FakeModelClient
from app.orchestrator import Orchestrator
from app.worker.harness import FakeHarness

ROOT = Path(__file__).resolve().parents[1]


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


async def _effort(orch, name="cdcl"):
    await orch.projects.add("game", "https://github.com/acme/game.git")
    return await orch.router.open_effort(name, project="game")


async def test_a_failure_becomes_a_durable_constraint(db_url):
    """The core clause-learning step: a failure is recorded, and re-recording the SAME failure is
    idempotent (clause subsumption) — several red paths report one underlying failure."""
    orch, _chat, _h, db = await _orch(db_url)
    try:
        eid, _c, _r = await _effort(orch)
        cid = await orch._record_constraint(eid, "error CS1002: ; expected\nat Foo.cs:12", origin="D2")
        assert cid
        again = await orch._record_constraint(eid, "error CS1002: ; expected\nat Foo.cs:12", origin="corpus")
        assert again == cid                                    # same clause, not a duplicate
        cs = await orch._list_constraints(eid)
        assert len(cs) == 1
        assert "CS1002" in cs[0]["body"]
        ev = [e for e in await orch.audit.replay(eid) if e["kind"] == "constraint_learned"]
        assert len(ev) == 1                                    # audited once, not per re-record
    finally:
        await db.dispose()


async def test_infra_failures_are_never_learned(db_url):
    """A proxy/clone/tool breakage is NOT a fact about the code — recording it as a constraint would
    poison the search (CDCL hygiene: noise corrupts the clause set)."""
    orch, _chat, _h, db = await _orch(db_url)
    try:
        eid, _c, _r = await _effort(orch)
        assert await orch._record_constraint(
            eid, "fatal: unable to access 'https://github.com/x': Could not resolve host: github.com",
            origin="clone") is None
        assert await orch._record_constraint(eid, "   ", origin="empty") is None
        assert await orch._list_constraints(eid) == []
    finally:
        await db.dispose()


async def test_constraints_are_injected_into_the_retry(db_url):
    """The payoff: accumulated clauses reach the next attempt, so the retry starts from the NARROWED
    search space. Each round is a fresh session, so this text is the only carrier."""
    orch, _chat, harness, db = await _orch(db_url)
    try:
        eid, chan, root = await _effort(orch)
        await orch._record_constraint(eid, "error CS1002: ; expected", origin="round 1")
        await orch._record_constraint(eid, "NullReferenceException in Bar.Update()", origin="round 2")
        ctx = await orch._constraints_context(eid)
        assert "LEARNED CONSTRAINTS (2)" in ctx
        assert "CS1002" in ctx and "NullReference" in ctx      # BOTH — it accumulates
        assert "do NOT repeat" in ctx
        # and it rides the first coding step of a re-dispatch
        await orch.delegate(eid, chan, root, "fix the build", plan_steps=["work"])
        prompts = " ".join(w["prompt"] for w in harness.wakes)
        assert "LEARNED CONSTRAINTS" in prompts
        assert "CS1002" in prompts and "NullReference" in prompts
    finally:
        await db.dispose()


async def test_queue_burndown_records_the_clause_at_the_chokepoint(db_url):
    """Every red path funnels through `_queue_burndown`, so recording there covers them all — and the
    clause is written BEFORE the defer/spawn branch, so the loop sees it."""
    orch, _chat, _h, db = await _orch(db_url)
    try:
        eid, _c, _r = await _effort(orch)
        orch._delegating.add(eid)                  # simulate "inside delegate" → deferred path
        orch._queue_burndown(eid, "error CS0246: type or namespace not found", origin="D2 gate")
        await _drain(orch)
        cs = await orch._list_constraints(eid)
        assert len(cs) == 1 and "CS0246" in cs[0]["body"]
        assert "D2 gate" in cs[0]["origin_note"]
    finally:
        orch._delegating.discard(eid)
        await db.dispose()


async def _drain(orch, rounds: int = 3):
    import asyncio
    for _ in range(rounds):
        if orch._bg_tasks:
            await asyncio.gather(*list(orch._bg_tasks), return_exceptions=True)
        await asyncio.sleep(0)


# ── §11 FAITHFUL ESCALATION — a ticket may not close by assertion ─────────────
async def test_verifiable_escalation_cannot_be_closed_while_its_check_is_red(db_url):
    """ORCHESTRATION-DESIGN §11: the paper's proven-lossy step is a concern RAISED and then not
    INCORPORATED. Prose can be waved through; a failing test cannot. A concern carrying its own
    executable check re-runs it on clear and refuses while red — 'resolved' is verified, not
    asserted. `abort` (giving up) and an explicit `override` stay allowed, the latter audited."""
    from app.schemas import Concern as ConcernSchema, ConcernOption, Decision, Trigger
    orch, chat, harness, db = await _orch(db_url)
    try:
        eid, _c, _r = await _effort(orch, "esc")
        await orch.raise_verifiable_concern(
            eid, Trigger.deviation,
            ConcernSchema(intent_thread=f"effort {eid}", what_surfaced="the check is red",
                          intent_of_change="verify", risk_if_wrong="ships broken",
                          options=[ConcernOption(action="fix", effect_on_outcome="green")],
                          recommendation="fix"),
            verify_cmd="pytest tests/test_thing.py", branch="agent/x")
        # NO passing run recorded since it was raised → the clear is refused
        await orch.apply_operator_decision(eid, Decision(decision="approve", note="looks fine to me"))
        assert await orch.gate.state_of(eid) == "frozen"                  # NOT cleared
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "can't close" in msgs and "has not passed" in msgs
        assert [e for e in await orch.audit.replay(eid) if e["kind"] == "escalation_clear_refused"]
        # a PASSING run of that exact check is recorded (as a fix round would) → the clear proceeds
        await orch.audit.log("check_exec", effort_id=eid,
                             payload={"command": "pytest tests/test_thing.py", "exit_code": 0})
        await orch.apply_operator_decision(eid, Decision(decision="approve", note="fixed"))
        assert await orch.gate.state_of(eid) != "frozen"                  # cleared on proof
    finally:
        await db.dispose()


async def test_override_and_abort_still_close_a_verifiable_escalation(db_url):
    """The human governor is never trapped: `abort` always closes, and an explicit `override` closes
    a red ticket — but the override is AUDITED, never silent."""
    from app.schemas import Concern as ConcernSchema, ConcernOption, Decision, Trigger
    orch, _chat, harness, db = await _orch(db_url)
    try:
        eid, _c, _r = await _effort(orch, "esc2")
        await orch.raise_verifiable_concern(
            eid, Trigger.deviation,
            ConcernSchema(intent_thread=f"effort {eid}", what_surfaced="red",
                          intent_of_change="verify", risk_if_wrong="x",
                          options=[ConcernOption(action="fix", effect_on_outcome="green")],
                          recommendation="fix"),
            verify_cmd="pytest tests/test_thing.py")
        await orch.apply_operator_decision(
            eid, Decision(decision="approve", note="known issue — override, tracked elsewhere"))
        assert await orch.gate.state_of(eid) != "frozen"                  # …but override closes it
        assert [e for e in await orch.audit.replay(eid) if e["kind"] == "escalation_override"]
    finally:
        await db.dispose()
