"""execution-gate — risk-gated dry-run before real-code execution (P4.0b, UX-FLOW Stage 4).

The policy (operator-confirmed, mirrors the review-depth gate P4.5): a **dry-run in an isolated
throwaway workspace is MANDATORY for high-blast-radius efforts** (irreversible / cross-effort /
cascading-refactor) and **skipped for routine ones** — so the ~1–2-slot GPU budget isn't spent
rehearsing trivial edits. This module is the *bridge-side gate*: it will not let a risky effort
reach REAL-code execution until a dry-run is recorded complete. (The dry-run *execution* itself is
a worker step — little-coder's per-instance containment + git-proxy make an isolated branch
naturally safe, and it never merges.)

Two orthogonal state machines already exist (governance §3.0): the gate (machine A, active⇄frozen)
and the scheduler (machine B). This is neither — it's a per-effort *readiness* precondition on
`dry_run_status`, checked at dispatch, distinct from the safety freeze.
"""

from __future__ import annotations

import logging

from ..db import Database
from ..models import Effort
from .audit_sink import AuditSink

log = logging.getLogger("agent_bridge.execution_gate")

# Blast-radius classes that MUST rehearse in an isolated dry-run first (mirrors P4.5 review depth
# + the readiness gate's blast_radius enum).
RISKY = {"irreversible", "cross_effort", "cascading_refactor"}

# dry_run_status values that permit real-code execution.
_EXECUTABLE = {"none", "skipped", "passed"}


class ExecutionGate:
    def __init__(self, db: Database, audit: AuditSink) -> None:
        self.db = db
        self.audit = audit

    @staticmethod
    def dry_run_required(risk: str) -> bool:
        return risk in RISKY

    async def set_risk(self, effort_id: str, risk: str) -> str:
        """Classify an effort's blast radius and set its dry-run requirement. Returns the new
        dry_run_status ('required' for risky, 'skipped' for routine). Idempotent per classification."""
        status = "required" if self.dry_run_required(risk) else "skipped"
        async with self.db.session_factory() as s:
            e = await s.get(Effort, effort_id)
            if e is None:
                raise KeyError(effort_id)
            e.risk = risk
            # Don't clobber a dry-run already passed for the same risk classification.
            if not (status == "required" and e.dry_run_status == "passed"):
                e.dry_run_status = status
            await s.commit()
            new_status = e.dry_run_status
        await self.audit.log(
            "effort_risk_set", effort_id=effort_id,
            payload={"risk": risk, "dry_run_status": new_status},
        )
        return new_status

    async def start_dry_run(self, effort_id: str) -> None:
        async with self.db.session_factory() as s:
            e = await s.get(Effort, effort_id)
            if e is not None:
                e.dry_run_status = "running"
                await s.commit()
        await self.audit.log("dry_run_started", effort_id=effort_id)

    async def record_dry_run(self, effort_id: str, *, passed: bool) -> None:
        """Record the isolated dry-run outcome. `passed` unlocks real execution; a failure keeps
        the effort blocked (re-ground → retry, Stage 6)."""
        async with self.db.session_factory() as s:
            e = await s.get(Effort, effort_id)
            if e is None:
                raise KeyError(effort_id)
            e.dry_run_status = "passed" if passed else "failed"
            await s.commit()
        await self.audit.log(
            "dry_run_recorded", effort_id=effort_id, payload={"passed": passed}
        )

    async def may_execute(self, effort_id: str) -> tuple[bool, str]:
        """(ok, reason). Real-code execution is allowed only when the dry-run requirement is
        satisfied. Routine efforts (never risk-set, or classified routine) pass immediately."""
        async with self.db.session_factory() as s:
            e = await s.get(Effort, effort_id)
        if e is None:
            return False, "unknown effort"
        st = e.dry_run_status
        if st in _EXECUTABLE:
            return True, ""
        reasons = {
            "required": "a dry-run is required before real-code execution (high blast radius, P4.0)",
            "running": "the isolated dry-run is still in progress",
            "failed": "the dry-run failed — re-ground before executing (P4.0/Stage 6)",
        }
        return False, reasons.get(st, f"dry-run status {st!r} does not permit execution")

    async def status(self, effort_id: str) -> dict:
        async with self.db.session_factory() as s:
            e = await s.get(Effort, effort_id)
        if e is None:
            return {"effort_id": effort_id, "risk": None, "dry_run_status": None, "may_execute": False}
        ok, reason = await self.may_execute(effort_id)
        return {
            "effort_id": effort_id, "risk": e.risk, "dry_run_status": e.dry_run_status,
            "may_execute": ok, "reason": reason,
        }
