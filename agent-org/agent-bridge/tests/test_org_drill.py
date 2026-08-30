"""THE ORG DRILL — agent-org's governance choreography, executed (U3).

§2's U3 row: "port the harness's drill pattern to agent-org as an executable org drill".

WHAT A DRILL IS, AND WHY THE UNIT TESTS ARE NOT ONE. agent-org has 830 tests and they cover
SEMANTICS: what `freeze` does, what `set_lifecycle` refuses. A drill covers CHOREOGRAPHY —
the laws that only exist in the SEQUENCE, between calls that are each individually correct.
The harness's `verify-merge-protocol.ps1` was written for exactly that reason, and its first
run proved the then-current Step 4 unsafe; no unit test had noticed, because every step was
fine on its own.

The laws below are not invented. Each is written down in the code or was learned from a live
incident, and each is cited where it is asserted.

Runs as pytest, or standalone for a human:

    python -m pytest tests/test_org_drill.py -q
    python tests/test_org_drill.py
"""
import asyncio
import sys
from pathlib import Path

import pytest
import pytest_asyncio

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings  # noqa: E402
from app.db import Database  # noqa: E402
from app.models import Effort  # noqa: E402
from app.modules.audit_sink import AuditSink  # noqa: E402
from app.modules.governance_gate import GovernanceGate  # noqa: E402
from app.schemas import Concern, Decision, Trigger  # noqa: E402


@pytest_asyncio.fixture
async def gate(db):
    return GovernanceGate(db, AuditSink(db, Settings()))


async def _effort(gate: GovernanceGate, eid: str) -> None:
    await gate.ensure_effort(eid, name=f"drill effort {eid}")


def _concern(what: str = "the drill raised a concern") -> Concern:
    """A minimally-valid intent-framed concern. The drill is about the CHOREOGRAPHY, not
    about concern authoring - but it must build a REAL one, because a gate driven with a
    stand-in would be a gate nobody proved accepts what the org sends it."""
    return Concern(intent_thread="drill", what_surfaced=what,
                   intent_of_change="proving the governance laws hold in sequence")


async def _freeze(gate: GovernanceGate, eid: str) -> None:
    await gate.freeze(eid, Trigger.deviation, _concern(), actor="pm")


async def _clear(gate: GovernanceGate, eid: str) -> None:
    await gate.clear(eid, Decision(decision="approve", note="drill"), actor_role="po")


# ── LAW 1: a frozen effort cannot dispatch ───────────────────────────────────
@pytest.mark.asyncio
async def test_LAW_a_frozen_effort_cannot_dispatch(gate):
    """Freezing that left dispatch open would make the kill switch decorative."""
    await _effort(gate, "e1")
    assert await gate.can_dispatch("e1") is True
    await _freeze(gate, "e1")
    assert await gate.is_frozen("e1") is True
    assert await gate.can_dispatch("e1") is False


@pytest.mark.asyncio
async def test_LAW_clearing_a_freeze_restores_dispatch(gate):
    # The other half. A freeze nobody can lift is an outage, not a control.
    await _effort(gate, "e1")
    await _freeze(gate, "e1")
    await _clear(gate, "e1")
    assert await gate.is_frozen("e1") is False
    assert await gate.can_dispatch("e1") is True


# ── LAW 2: ABORT WINS EVERY RACE ─────────────────────────────────────────────
@pytest.mark.asyncio
async def test_LAW_a_machine_done_may_not_overwrite_an_operator_abort(gate):
    """set_lifecycle's own docstring, from a live incident.

    "ABORT WINS EVERY RACE (live 2026-07-15, the gym 'ouroboros': an operator abort landed
    while a run was in flight; the run's finish stamped `done` over `aborted`, the D2-fail
    path then machine-reopened it, and the effort resurrected TWICE)".

    This is the clearest example of a law that lives in the SEQUENCE. Both calls are correct
    alone; only their order makes one of them wrong.
    """
    await _effort(gate, "e1")
    await gate.set_lifecycle("e1", "aborted")
    await gate.set_lifecycle("e1", "done")          # the machine tries to finish it
    assert await gate.lifecycle_of("e1") == "aborted"


@pytest.mark.asyncio
async def test_LAW_the_operator_can_still_reopen_an_aborted_effort(gate):
    # The exception the docstring names: "Only the operator's own re-run path (which sets
    # `open` explicitly when reopening) brings an aborted effort back." A law with no
    # release is a trap.
    await _effort(gate, "e1")
    await gate.set_lifecycle("e1", "aborted")
    await gate.set_lifecycle("e1", "open")
    assert await gate.lifecycle_of("e1") == "open"


# ── LAW 3: the lifecycle observer never breaks a transition ──────────────────
@pytest.mark.asyncio
async def test_LAW_an_exploding_observer_does_not_roll_back_governance_state(gate):
    """A lifecycle transition is governance state; a memory feature is not worth trading it.

    Added with memory-plane §2.2's thin abort records - the observer is how an aborted
    effort still leaves a memory. If it could break the transition, the org would lose an
    abort because a database it does not depend on was down.
    """
    fired = {"n": 0}

    async def boom(effort_id, lifecycle):
        fired["n"] += 1
        raise RuntimeError("observer exploded")

    gate.on_lifecycle = boom
    await _effort(gate, "e1")
    await gate.set_lifecycle("e1", "aborted")
    assert fired["n"] == 1, "the observer must actually have been called"
    assert await gate.lifecycle_of("e1") == "aborted", "the transition must have survived it"


@pytest.mark.asyncio
async def test_LAW_the_observer_does_NOT_fire_on_a_suppressed_transition(gate):
    """A done-after-abort is refused, so nothing downstream should hear about it.

    Firing here would write a 'done' memory for an effort the operator aborted - the
    observer would contradict the very law above it.
    """
    seen = []

    async def record(effort_id, lifecycle):
        seen.append(lifecycle)

    await _effort(gate, "e1")
    await gate.set_lifecycle("e1", "aborted")
    gate.on_lifecycle = record
    await gate.set_lifecycle("e1", "done")          # suppressed
    assert seen == [], f"observer fired on a suppressed transition: {seen}"


@pytest.mark.asyncio
async def test_LAW_the_observer_does_NOT_fire_on_a_no_op_reset(gate):
    seen = []

    async def record(effort_id, lifecycle):
        seen.append(lifecycle)

    await _effort(gate, "e1")
    await gate.set_lifecycle("e1", "done")
    gate.on_lifecycle = record
    await gate.set_lifecycle("e1", "done")          # already there
    assert seen == []


# ── LAW 4: the kill switch is org-wide ───────────────────────────────────────
@pytest.mark.asyncio
async def test_LAW_the_kill_switch_stops_every_effort_not_just_one(gate):
    """A kill switch that missed an effort created after it was thrown would be worse than
    none: the operator would believe the org was stopped."""
    await _effort(gate, "e1")
    await gate.kill_switch(True, actor="operator")
    assert await gate.is_killed() is True
    assert await gate.can_dispatch("e1") is False
    await _effort(gate, "e2")                        # created AFTER the switch
    assert await gate.can_dispatch("e2") is False


@pytest.mark.asyncio
async def test_LAW_clearing_the_kill_switch_releases_the_org(gate):
    await _effort(gate, "e1")
    await gate.kill_switch(True, actor="operator")
    await gate.kill_switch(False, actor="operator")
    assert await gate.is_killed() is False
    assert await gate.can_dispatch("e1") is True


# ── standalone runner: the harness drill's shape, in agent-org's language ────
async def _run_drill() -> int:
    """Drive the laws in sequence and print named checks, like verify-merge-protocol.ps1.

    A drill a person can RUN is different from a suite CI runs: the operator reads these
    names when they want to know whether the org's laws still hold, and a pytest summary
    line does not tell them which law.
    """
    import tempfile

    results = []

    def check(label, ok):
        results.append((label, ok))
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")

    with tempfile.TemporaryDirectory() as tmp:
        d = Database(f"sqlite+aiosqlite:///{Path(tmp) / 'drill.db'}")
        await d.create_all()
        g = GovernanceGate(d, AuditSink(d, Settings()))

        print("\n=== agent-org ORG DRILL — governance choreography ===")
        await g.ensure_effort("d1", name="drill d1")
        check("a fresh effort may dispatch", await g.can_dispatch("d1") is True)
        await g.freeze("d1", Trigger.deviation, _concern(), actor="pm")
        check("a frozen effort may NOT dispatch", await g.can_dispatch("d1") is False)
        await g.clear("d1", Decision(decision="approve", note="drill"), actor_role="po")
        check("clearing the freeze restores dispatch", await g.can_dispatch("d1") is True)

        await g.set_lifecycle("d1", "aborted")
        await g.set_lifecycle("d1", "done")
        check("ABORT WINS: a machine done cannot overwrite an operator abort",
              await g.lifecycle_of("d1") == "aborted")
        await g.set_lifecycle("d1", "open")
        check("the operator can still reopen an aborted effort",
              await g.lifecycle_of("d1") == "open")

        fired = {"n": 0}

        async def boom(effort_id, lifecycle):
            fired["n"] += 1
            raise RuntimeError("drill observer")

        g.on_lifecycle = boom
        await g.set_lifecycle("d1", "aborted")
        check("an exploding lifecycle observer does not roll back the transition",
              fired["n"] == 1 and await g.lifecycle_of("d1") == "aborted")
        g.on_lifecycle = None

        await g.ensure_effort("d2", name="drill d2")
        await g.kill_switch(True, actor="operator")
        check("the kill switch stops the whole org", await g.is_killed() is True)
        await g.ensure_effort("d3", name="drill d3")
        check("including efforts created after it was thrown",
              await g.can_dispatch("d3") is False)
        await g.kill_switch(False, actor="operator")
        check("clearing it releases the org", await g.can_dispatch("d2") is True)

        # DISPOSE before the TemporaryDirectory unlinks the file. On Windows an open
        # sqlite handle makes rmtree raise PermissionError, and the drill would exit on a
        # cleanup error AFTER every check passed - a red that says nothing about the org.
        await d.dispose()

    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} org-drill checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run_drill()))
