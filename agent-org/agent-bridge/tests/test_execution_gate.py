"""P4.0 — risk-gated dry-run execution gate + grounding (ground → inject → gated dispatch).
Fakes only: no OB1/GPU (FakeGrounding), no live worker (FakeHarness)."""

from __future__ import annotations

from pathlib import Path

from app.adapters.chat import FakeChatAdapter
from app.config import Settings
from app.db import Database
from app.modules.audit_sink import AuditSink
from app.modules.execution_gate import ExecutionGate
from app.modules.governance_gate import GovernanceGate
from app.modules.grounding import FakeGrounding
from app.modules.model_router import FakeModelClient
from app.orchestrator import Orchestrator
from app.schemas import GroundingResult
from app.worker.harness import FakeHarness

ROOT = Path(__file__).resolve().parents[1]


async def _eg(db, settings):
    audit = AuditSink(db, settings)
    return ExecutionGate(db, audit), GovernanceGate(db, audit)


# ── the gate: routine passes, risky blocks until a dry-run is recorded ────────
async def test_routine_effort_passes_execution_gate(db, settings):
    eg, g = await _eg(db, settings)
    await g.ensure_effort("e1", "e1")
    assert await eg.set_risk("e1", "routine") == "skipped"
    ok, _ = await eg.may_execute("e1")
    assert ok is True


async def test_risky_effort_blocked_until_dry_run(db, settings):
    eg, g = await _eg(db, settings)
    await g.ensure_effort("e2", "e2")
    assert await eg.set_risk("e2", "irreversible") == "required"
    ok, reason = await eg.may_execute("e2")
    assert ok is False and "dry-run" in reason.lower()
    await eg.record_dry_run("e2", passed=True)
    ok, _ = await eg.may_execute("e2")
    assert ok is True  # unblocked by a passing dry-run


async def test_failed_dry_run_keeps_blocked(db, settings):
    eg, g = await _eg(db, settings)
    await g.ensure_effort("e3", "e3")
    await eg.set_risk("e3", "cascading_refactor")
    await eg.record_dry_run("e3", passed=False)
    ok, reason = await eg.may_execute("e3")
    assert ok is False and "fail" in reason.lower()


async def test_never_classified_effort_executes(db, settings):
    """A routine effort that never gets a risk set (dry_run_status='none') must not be blocked —
    the gate is opt-in on blast radius, so the live routine flow is unaffected."""
    eg, g = await _eg(db, settings)
    await g.ensure_effort("e4", "e4")
    ok, _ = await eg.may_execute("e4")
    assert ok is True


# ── orchestrator wiring: delegate honors the gate; prepare_execution grounds ──
async def _orch(db_url, grounding=None):
    settings = Settings(
        _env_file=None, chat_adapter="fake",
        profiles_dir=str(ROOT / "profiles"), charters_dir=str(ROOT / "charters"),
        floor_dir=str(ROOT / "floor"), worker_instance_urls="http://w1:8090",
        max_concurrent_workers=1, database_url=db_url, grounding_enabled=True,
    )
    db = Database(db_url)
    orch = Orchestrator(
        settings, db, FakeChatAdapter(), model_client=FakeModelClient(),
        harness=FakeHarness(),
        grounding=grounding or FakeGrounding(
            GroundingResult(grounded=True, claims=["use bcrypt for password hashing"], summary="auth notes")
        ),
    )
    await orch.setup()
    return orch, orch.chat, db


async def test_delegate_holds_risky_effort_without_dry_run(db_url):
    orch, chat, db = await _orch(db_url)
    try:
        eid, chan, root = await orch.router.open_effort("risky")
        await orch.exec_gate.set_risk(eid, "irreversible")
        # delegate must HOLD — no worker wake — until the dry-run is recorded.
        await orch.delegate(eid, chan, root, "delete the prod database")
        assert len(orch.harness.wakes) == 0
        assert any("execution held" in p["message"].lower() for p in chat.posted)
        # record a passing dry-run → delegate now dispatches.
        await orch.exec_gate.record_dry_run(eid, passed=True)
        await orch.delegate(eid, chan, root, "do the safe subset")
        assert len(orch.harness.wakes) == 1
    finally:
        await db.dispose()


async def test_prepare_execution_grounds_and_requires_dry_run(db_url):
    orch, chat, db = await _orch(db_url)
    try:
        eid, chan, root = await orch.router.open_effort("groundme")
        status = await orch.prepare_execution(eid, "add a password reset flow", risk="irreversible")
        assert status["dry_run_status"] == "required" and status["may_execute"] is False
        # grounded claims were injected as steering (advisory context).
        steering = await orch.charters.current_steering(eid)
        assert "bcrypt" in steering
        assert orch.grounding.calls  # the grounding client was actually called
    finally:
        await db.dispose()


async def test_routine_prepare_skips_grounding_and_dry_run(db_url):
    orch, chat, db = await _orch(db_url)
    try:
        eid, _, _ = await orch.router.open_effort("routine1")
        status = await orch.prepare_execution(eid, "fix a typo", risk="routine")
        assert status["dry_run_status"] == "skipped" and status["may_execute"] is True
        assert not orch.grounding.calls  # routine → no grounding spend
    finally:
        await db.dispose()
