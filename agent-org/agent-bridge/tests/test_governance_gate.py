"""P2 safety-critical tests (PLAN §7 P2). The gate is the spine — these are the
fail-safe invariants (governance §3.0)."""

from __future__ import annotations

import pytest

from app.db import Database
from app.modules.audit_sink import AuditSink
from app.modules.governance_gate import AuthorityError, GateError, GovernanceGate
from app.schemas import Concern, Decision, Trigger


def _concern(effort_id: str) -> Concern:
    return Concern(
        intent_thread="ship feature X aligned",
        what_surfaced="worker hit an irreversible action",
        intent_of_change="pushing would deploy unreviewed code",
        pm_recommendation="hold for human review",
        blocked_efforts=[effort_id],
    )


async def _gate(db) -> GovernanceGate:
    from app.config import Settings

    return GovernanceGate(db, AuditSink(db, Settings(_env_file=None)))


async def test_freeze_moves_active_to_frozen(db):
    gate = await _gate(db)
    await gate.ensure_effort("e1", "eff1")
    assert await gate.can_dispatch("e1") is True
    await gate.freeze("e1", Trigger.irreversible_action, _concern("e1"))
    assert await gate.state_of("e1") == "frozen"
    assert await gate.can_dispatch("e1") is False


async def test_default_deny_unknown_effort(db):
    gate = await _gate(db)
    # An effort the gate never saw is NOT dispatchable (default-deny).
    assert await gate.can_dispatch("never-seen") is False
    assert await gate.state_of("never-seen") == "frozen"


@pytest.mark.parametrize("trigger", list(Trigger))
async def test_every_trigger_freezes(db, trigger):
    gate = await _gate(db)
    await gate.ensure_effort("e", "e")
    await gate.freeze("e", trigger, _concern("e"))
    assert await gate.can_dispatch("e") is False


async def test_dependents_freeze_with_parent(db):
    gate = await _gate(db)
    await gate.ensure_effort("parent", "parent")
    await gate.ensure_effort("child", "child", parent_effort_id="parent")
    await gate.ensure_effort("grandchild", "gc", parent_effort_id="child")
    await gate.freeze("parent", Trigger.deviation, _concern("parent"))
    assert await gate.can_dispatch("child") is False
    assert await gate.can_dispatch("grandchild") is False


async def test_hard_gate_cannot_be_cleared_by_po(db):
    gate = await _gate(db)
    await gate.ensure_effort("e", "e")
    # refusal is a hard-gate trigger -> only the human may clear (invariant iv).
    await gate.freeze("e", Trigger.refusal, _concern("e"))
    with pytest.raises(AuthorityError):
        await gate.clear("e", Decision(decision="approve"), actor_role="po")
    # still frozen after the rejected clear
    assert await gate.can_dispatch("e") is False


async def test_human_clears_hard_gate_and_unfreezes(db):
    gate = await _gate(db)
    await gate.ensure_effort("e", "e")
    await gate.freeze("e", Trigger.refusal, _concern("e"))
    await gate.clear("e", Decision(decision="approve"), actor_role="human")
    assert await gate.can_dispatch("e") is True


async def test_auto_recovery_clears_an_infra_hard_gate(db):
    # Sanctioned exception (operator-authorized 2026-07-13): the autonomous infra recovery, having
    # classified the concern as an environment/workspace symptom, may clear a hard-gate.
    gate = await _gate(db)
    await gate.ensure_effort("e", "e")
    await gate.freeze("e", Trigger.refusal, _concern("e"))     # refusal = hard-gate
    await gate.clear("e", Decision(decision="approve"),
                     actor_role="auto-recovery", infra_recovery=True)
    assert await gate.can_dispatch("e") is True


async def test_auto_recovery_actor_without_the_infra_flag_is_still_denied(db):
    # The actor alone is NOT enough — without infra_recovery=True the hard-gate stays human-only, so
    # a mis-set actor can never quietly bypass the invariant.
    gate = await _gate(db)
    await gate.ensure_effort("e", "e")
    await gate.freeze("e", Trigger.refusal, _concern("e"))
    with pytest.raises(AuthorityError):
        await gate.clear("e", Decision(decision="approve"), actor_role="auto-recovery")
    assert await gate.can_dispatch("e") is False


async def test_po_may_clear_steering(db):
    gate = await _gate(db)
    await gate.ensure_effort("e", "e")
    # deviation defaults to steering level -> PO may clear.
    await gate.freeze("e", Trigger.deviation, _concern("e"))
    await gate.clear("e", Decision(decision="modify", note="narrow scope"), actor_role="po")
    assert await gate.can_dispatch("e") is True


async def test_abort_keeps_effort_frozen(db):
    gate = await _gate(db)
    await gate.ensure_effort("e", "e")
    await gate.freeze("e", Trigger.refusal, _concern("e"))
    await gate.clear("e", Decision(decision="abort"), actor_role="human")
    # aborted efforts are NOT re-admitted (still not dispatchable).
    assert await gate.can_dispatch("e") is False


async def test_no_reroute_verb_exists(db):
    # Invariant (iii): there is no way to clear a refusal by "asking a different worker".
    gate = await _gate(db)
    assert not hasattr(gate, "reroute")
    assert not hasattr(gate, "reassign_around")


async def test_frozen_persists_across_restart(db_url):
    """Invariant (i): a frozen effort survives a bridge restart (persisted, not memory)."""
    from app.config import Settings

    d1 = Database(db_url)
    await d1.create_all()
    g1 = GovernanceGate(d1, AuditSink(d1, Settings(_env_file=None)))
    await g1.ensure_effort("e", "e")
    await g1.freeze("e", Trigger.refusal, _concern("e"))
    await d1.dispose()  # simulate a bridge bounce

    d2 = Database(db_url)  # fresh engine, SAME file
    g2 = GovernanceGate(d2, AuditSink(d2, Settings(_env_file=None)))
    assert await g2.state_of("e") == "frozen"
    assert await g2.can_dispatch("e") is False
    await d2.dispose()


async def test_kill_switch_freezes_everything(db):
    gate = await _gate(db)
    await gate.ensure_effort("a", "a")
    await gate.ensure_effort("b", "b")
    assert await gate.can_dispatch("a") is True
    await gate.kill_switch(on=True)
    assert await gate.can_dispatch("a") is False
    assert await gate.can_dispatch("b") is False
    await gate.kill_switch(on=False)
    assert await gate.can_dispatch("a") is True


async def test_clear_without_open_concern_errors(db):
    gate = await _gate(db)
    await gate.ensure_effort("e", "e")
    with pytest.raises(GateError):
        await gate.clear("e", Decision(decision="approve"), actor_role="human")
