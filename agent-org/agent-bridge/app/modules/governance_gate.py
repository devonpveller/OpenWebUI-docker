"""governance-gate — the escalation gate FSM (machine A, governance §3.0). THE safety spine.

Per-effort state {active <-> frozen}. This is the ONE safety FSM; the scheduler's
{computing, waiting, suspended} (machine B) is a *different* module and `frozen` is NOT
one of its states (conflating them is called out as a safety bug).

Fail-safe invariants (governance §3.0 — enforced here + covered by P2 tests):
  (i)   `frozen` persists across a bridge restart — it's read from the DB, never memory.
  (ii)  there is NO timeout that auto-resumes a hard-gate `frozen` — this module has no
        clock and no auto-clear path at all.
  (iii) a refusal/objection cannot be cleared by routing to another worker — the gate has
        no "reroute" verb; the scheduler refuses to dispatch a frozen effort (checked via
        `can_dispatch`), so progress can only come THROUGH a cleared decision.
  (iv)  the PO cannot self-clear a hard-gate trigger — authority is checked in `clear`.
Default-deny: any unknown/corrupt state is treated as NOT dispatchable.

This module is deliberately free of chat/WS/REST coupling. `freeze()` persists a CONCERN
and returns it; the orchestration layer is responsible for POSTing it to #mgmt.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select

from ..db import Database, now_iso
from ..models import (
    GATE_ACTIVE,
    GATE_FROZEN,
    Concern as ConcernRow,
    Effort,
    GlobalState,
)
from ..schemas import Concern, Decision, Level, Trigger
from .audit_sink import AuditSink

log = logging.getLogger("agent_bridge.gate")


class GateError(Exception):
    pass


class AuthorityError(GateError):
    """Raised when a clear violates the authority rules (invariant iv)."""


def _new_id(prefix: str) -> str:
    # Monotonic-ish id without a wall clock in hot paths; uuid4 is fine here.
    import uuid

    return f"{prefix}-{uuid.uuid4().hex[:12]}"


class GovernanceGate:
    def __init__(self, db: Database, audit: AuditSink) -> None:
        self.db = db
        self.audit = audit

    # ── effort lifecycle ────────────────────────────────────────────────────
    async def ensure_effort(
        self, effort_id: str, name: str, channel_id: str | None = None,
        parent_effort_id: str | None = None,
    ) -> None:
        async with self.db.session_factory() as s:
            row = await s.get(Effort, effort_id)
            if row is None:
                s.add(
                    Effort(
                        id=effort_id,
                        name=name,
                        channel_id=channel_id,
                        parent_effort_id=parent_effort_id,
                        state=GATE_ACTIVE,
                    )
                )
                await s.commit()

    async def _dependent_closure(self, s, effort_id: str) -> list[str]:
        """effort + its transitive dependents (children via parent_effort_id)."""
        result = [effort_id]
        frontier = [effort_id]
        seen = {effort_id}
        while frontier:
            parent = frontier.pop()
            kids = (
                await s.execute(
                    select(Effort.id).where(Effort.parent_effort_id == parent)
                )
            ).scalars().all()
            for k in kids:
                if k not in seen:
                    seen.add(k)
                    result.append(k)
                    frontier.append(k)
        return result

    # ── freeze (any §3 trigger) ─────────────────────────────────────────────
    async def freeze(
        self,
        effort_id: str,
        trigger: Trigger,
        concern: Concern,
        *,
        actor: str = "pm",
        level: Level | None = None,
    ) -> Concern:
        """Freeze the effort AND its dependents; persist a CONCERN; return it.

        `level` defaults from the trigger (hard-gate triggers reach the human).
        """
        from ..schemas import HARD_GATE_TRIGGERS

        if level is None:
            level = Level.hard_gate if trigger in HARD_GATE_TRIGGERS else Level.steering

        async with self.db.session_factory() as s:
            effort = await s.get(Effort, effort_id)
            if effort is None:
                raise GateError(f"unknown effort {effort_id}")
            closure = await self._dependent_closure(s, effort_id)
            for eid in closure:
                e = await s.get(Effort, eid)
                if e is not None and e.state != GATE_FROZEN:
                    e.state = GATE_FROZEN
                    e.freeze_reason = trigger.value
                    e.freeze_level = level.value
                    e.frozen_by = actor
            concern.blocked_efforts = list(dict.fromkeys(concern.blocked_efforts + closure))
            cid = _new_id("concern")
            s.add(
                ConcernRow(
                    id=cid,
                    effort_id=effort_id,
                    level=level.value,
                    trigger=trigger.value,
                    payload=concern.model_dump(),
                    status="open",
                )
            )
            await s.commit()

        await self.audit.log(
            "effort_frozen",
            effort_id=effort_id,
            actor=actor,
            payload={"trigger": trigger.value, "level": level.value, "dependents": closure},
        )
        await self.audit.log(
            "concern_posted",
            effort_id=effort_id,
            actor=actor,
            payload={"concern_id": cid, "level": level.value, "concern": concern.model_dump()},
        )
        log.info("FROZEN effort=%s trigger=%s level=%s (+%d dependents)",
                 effort_id, trigger.value, level.value, len(closure) - 1)
        return concern

    # ── read state (default-deny) ────────────────────────────────────────────
    async def can_dispatch(self, effort_id: str) -> bool:
        """True ONLY if the effort is active and the kill switch is off.
        Any unknown/corrupt/frozen state => False (default-deny, invariant)."""
        async with self.db.session_factory() as s:
            gs = await s.get(GlobalState, 1)
            if gs is not None and gs.kill_switch:
                return False
            e = await s.get(Effort, effort_id)
            if e is None:
                return False
            return e.state == GATE_ACTIVE

    async def is_frozen(self, effort_id: str) -> bool:
        return not await self.can_dispatch(effort_id)

    async def state_of(self, effort_id: str) -> str:
        async with self.db.session_factory() as s:
            e = await s.get(Effort, effort_id)
            # Default-deny presentation: unknown -> reported as frozen.
            if e is None:
                return GATE_FROZEN
            return e.state if e.state in (GATE_ACTIVE, GATE_FROZEN) else GATE_FROZEN

    async def open_concerns(self, effort_id: str) -> list[ConcernRow]:
        async with self.db.session_factory() as s:
            return list(
                (
                    await s.execute(
                        select(ConcernRow).where(
                            ConcernRow.effort_id == effort_id,
                            ConcernRow.status == "open",
                        )
                    )
                ).scalars().all()
            )

    # ── clear (authority-checked) ────────────────────────────────────────────
    async def clear(
        self, effort_id: str, decision: Decision, *, actor_role: str
    ) -> None:
        """Clear the open CONCERN(s) for an effort and unfreeze it + dependents.

        Authority (invariant iv): the PO may clear a `steering` concern; a `hard_gate`
        concern can be cleared ONLY by the human. `actor_role` in {human, po}.
        On `abort`, the effort stays frozen (aborted), only the CONCERN closes.
        """
        async with self.db.session_factory() as s:
            open_c = (
                await s.execute(
                    select(ConcernRow).where(
                        ConcernRow.effort_id == effort_id, ConcernRow.status == "open"
                    )
                )
            ).scalars().all()
            if not open_c:
                raise GateError(f"no open concern for effort {effort_id}")

            # Authority check BEFORE any state change.
            for c in open_c:
                if c.level == Level.hard_gate.value and actor_role != "human":
                    raise AuthorityError(
                        f"{actor_role} cannot clear a hard-gate concern — only the "
                        f"Human Operator may (governance §3 invariant iv)"
                    )

            now = datetime.now(timezone.utc).isoformat()
            for c in open_c:
                c.status = "cleared"
                c.decision = decision.decision
                c.cleared_by = actor_role
                c.cleared_at = now

            if decision.decision == "abort":
                # Aborted efforts are NOT re-admitted to the scheduler.
                pass
            else:
                # approve / modify => unfreeze the effort + its dependents.
                closure = await self._dependent_closure(s, effort_id)
                for eid in closure:
                    e = await s.get(Effort, eid)
                    if e is not None and e.state == GATE_FROZEN:
                        e.state = GATE_ACTIVE
                        e.freeze_reason = None
                        e.freeze_level = None
                        e.frozen_by = None
            await s.commit()

        await self.audit.log(
            "effort_cleared" if decision.decision != "abort" else "effort_aborted",
            effort_id=effort_id,
            actor=actor_role,
            payload={"decision": decision.model_dump()},
        )
        await self.audit.log(
            "operator_decision",
            effort_id=effort_id,
            actor=actor_role,
            payload=decision.model_dump(),
        )
        log.info("CLEARED effort=%s decision=%s by=%s",
                 effort_id, decision.decision, actor_role)

    # ── global kill switch (§3) ──────────────────────────────────────────────
    async def kill_switch(self, on: bool = True, actor: str = "human") -> None:
        async with self.db.session_factory() as s:
            gs = await s.get(GlobalState, 1)
            if gs is None:
                gs = GlobalState(id=1, kill_switch=on)
                s.add(gs)
            else:
                gs.kill_switch = on
                gs.updated_at = now_iso()
            await s.commit()
        await self.audit.log("kill_switch", actor=actor, payload={"on": on})
        log.warning("KILL SWITCH %s by %s", "ENGAGED" if on else "released", actor)

    async def kill_switch_engaged(self) -> bool:
        async with self.db.session_factory() as s:
            gs = await s.get(GlobalState, 1)
            return bool(gs and gs.kill_switch)
