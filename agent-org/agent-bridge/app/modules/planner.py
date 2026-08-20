"""planner — readiness gate + plan presentation (UX-FLOW Stages 2-3; P3.8/P3.9).

Judgment-heavy, so it runs on the planner profile (cloud lane if Pc built — plan
generation caps the productivity ceiling of everything downstream, PLAN §3.4). This is
the cheapest place to catch misalignment — before any worker spawns (paper's
goal-problem-first; F5 "don't guess, surface it").

Stage 2 (readiness gate, P3.8): judge "is this plan clear AND safe against this codebase?"
On false -> clarifying questions (plan gaps + implementation-safety/blast-radius) -> PO
asks the Human Operator -> iterate; the plan stays `draft` until coherent.

Stage 3 (plan presentation, P3.9): present Feature Overview / Implementation Plan
(stop-gates embedded) / Delegation DAG (sequence + bounded parallelism, NOT wide fan-out)
/ Estimate as the top-level stop-gate; Human-Operator `approved` -> Stage 4.
"""

from __future__ import annotations

import logging


from ..db import Database
from ..models import Effort
from ..schemas import Plan, ReadinessVerdict
from .audit_sink import AuditSink
from .model_router import ModelRouter

log = logging.getLogger("agent_bridge.planner")

_READINESS_SYS = (
    "You are the readiness gate (UX-FLOW Stage 2) for a request against an EXISTING project. "
    "Your job is NOT to interrogate the operator — it is to decide whether a competent engineer "
    "could implement this correctly by ANCHORING to the existing project + standard practice, and "
    "to surface ONLY the genuine blockers such an engineer could not resolve on their own. "
    "Governance F5 is 'escalate instead of GUESSING' — it is NOT a licence to manufacture questions "
    "you can answer yourself. Over-asking is a failure; prefer proceeding.\n\n"
    "RESOLVE THESE YOURSELF — NEVER ask the operator:\n"
    "- Language / framework → match the existing project.\n"
    "- File placement / project structure → follow the project's conventions + SOLID, encapsulation, "
    "clear naming.\n"
    "- Integrate-vs-standalone & implementation pattern → use the industry-standard pattern that fits "
    "the existing structure.\n"
    "- Anything derivable from the codebase, its conventions, or ordinary best practice.\n\n"
    "ELEVATE A QUESTION ONLY IF it is a genuine blocker that materially changes the OUTCOME and you "
    "cannot resolve it from context or best practice — exactly one of:\n"
    "- feature_intent: what the feature should actually DO is genuinely ambiguous with no single "
    "sensible default.\n"
    "- missing_info: a fact only the operator has (a specific value, target system, or scope).\n"
    "- security: a real security implication of the request.\n"
    "- ethics: a real ethical concern.\n\n"
    "For EVERY elevated question set `recommendation`: for feature_intent/missing_info, the sensible "
    "DEFAULT you'll apply if unanswered; for security/ethics, a one-line statement of the SPECIFIC "
    "concern and why it matters to the intent. Set `category` to match. If there are no genuine "
    "blockers, set clear_and_safe=true with an EMPTY question list and let the worker proceed under "
    "existing conventions.\n\n"
    # P21 F2a — the classifier was previously uninstructed (identical goals classed differently on
    # different runs); this drives the risk gate, so classify by what the work TOUCHES, not by how
    # long the feature list is.
    "ALSO classify `blast_radius` — how far a CORRECT implementation reaches:\n"
    "- 'routine': a self-contained or ADDITIVE change (new features, new files, or edits confined to "
    "one bounded area). This is the COMMON case and INCLUDES building features on a small or "
    "greenfield codebase, however long the feature list.\n"
    "- 'cross_effort': it must touch code that ANOTHER in-flight effort is actively changing.\n"
    "- 'cascading_refactor': a WIDE structural refactor of EXISTING code across MANY modules where "
    "one edit forces edits elsewhere — NOT a greenfield feature-add. When genuinely unsure between "
    "two, choose the MORE-gated one."
)

_PLAN_SYS = (
    "You are the planner. Produce a structured plan: a Feature Overview (how it behaves "
    "AFTER implementation), Implementation steps with explicit stop-gates between phases, "
    "a Delegation DAG (sequence + bounded parallelism — NOT wide fan-out; concurrency is "
    "GPU-bounded to ~1-2 workers), and a rough Estimate. Bake constraints INLINE into each "
    "delegated task (governance §4.3)."
)


class Planner:
    def __init__(self, db: Database, models: ModelRouter, audit: AuditSink) -> None:
        self.db = db
        self.models = models
        self.audit = audit

    async def readiness_gate(self, effort_id: str, request: str, workspace_ctx: str = "") -> ReadinessVerdict:
        verdict = await self.models.structured(
            "planner",
            _READINESS_SYS,
            f"REQUEST:\n{request}\n\nWORKSPACE:\n{workspace_ctx}",
            ReadinessVerdict,
            temperature=0.0,   # P21 F2a — the risk/readiness gate is a governance decision: deterministic
        )
        await self.audit.log(
            "readiness_gate",
            effort_id=effort_id,
            payload={
                "clear_and_safe": verdict.clear_and_safe,
                "blast_radius": verdict.blast_radius,
                "questions": [q.model_dump() for q in verdict.clarifying_questions],
            },
        )
        return verdict

    async def draft_plan(self, effort_id: str, intent_thread: str, request: str, workspace_ctx: str = "") -> Plan:
        plan = await self.models.structured(
            "planner",
            _PLAN_SYS,
            f"INTENT:\n{intent_thread}\n\nREQUEST:\n{request}\n\nWORKSPACE:\n{workspace_ctx}",
            Plan,
        )
        plan.intent_thread = intent_thread or plan.intent_thread
        plan.status = "draft"
        async with self.db.session_factory() as s:
            e = await s.get(Effort, effort_id)
            if e is not None:
                e.plan_status = "draft"
                await s.commit()
        await self.audit.log("plan_drafted", effort_id=effort_id, payload={"plan": plan.model_dump()})
        return plan

    async def approve_plan(self, effort_id: str, *, actor_role: str = "human") -> None:
        """Top-level plan-stop-gate: only the Human Operator ratifies the plan (Stage 3)."""
        if actor_role != "human":
            raise PermissionError("only the Human Operator approves a plan (UX-FLOW Stage 3)")
        async with self.db.session_factory() as s:
            e = await s.get(Effort, effort_id)
            if e is None:
                raise KeyError(effort_id)
            e.plan_status = "approved"
            await s.commit()
        await self.audit.log("plan_approved", effort_id=effort_id, actor=actor_role)

    async def plan_status(self, effort_id: str) -> str:
        async with self.db.session_factory() as s:
            e = await s.get(Effort, effort_id)
            return e.plan_status if e else "none"

    async def is_approved(self, effort_id: str) -> bool:
        return (await self.plan_status(effort_id)) == "approved"
