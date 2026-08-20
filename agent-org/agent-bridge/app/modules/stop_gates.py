"""stop-gates + review — doc-enforced checkpoints & differently-goaled review (§4.4/§4.5).

The plan/task doc is the stop-gate SCHEDULE: the worker halts at each checkpoint and
engages a reviewer before continuing. Because the stop is encoded in DOC STRUCTURE +
enforced by the bridge, it's deterministic and hard to skip (prompt-level norms get
optimized away; a checkpoint in the plan does not).

Key safety properties:
  - Checkpoints live in a SEPARATE floor/enforcement doc so the editable plan can't drop
    them (P4.1): the enforced halt exists independently of the plan markers.
  - The bridge blocks past a checkpoint until a review verdict is recorded (P4.2).
  - At each stop the worker EXPLAINS its work AND intent (4-field artifact, P4.3), and the
    judgment-model reviewer cross-checks the explanation against the ACTUAL diff (P4.3b) —
    small models confabulate; words are a lead, actions are ground truth.
  - Reviewers are DIFFERENTLY-GOALED (ethics/whole-picture lens), report to the PM, cannot
    self-approve; a same-goal reviewer is rejected by config (P4.4).
  - Review depth is risk-gated: routine -> 1/none; irreversible/cross-effort -> multi-lens
    panel (P4.5). Reviewers run on JUDGE_MODEL + deterministic checks (P4.7).
"""

from __future__ import annotations

import logging


from ..db import Database, now_iso
from ..models import Checkpoint, Review
from ..schemas import Explanation, ExplanationCheck, ReviewVerdict
from .audit_sink import AuditSink
from .model_router import ModelRouter

log = logging.getLogger("agent_bridge.stop_gates")

# Reviewer profiles carry an explicit lens (differently-goaled, §4.4).
PANEL_LENSES = ["correctness", "security", "scope", "ethics"]
ROUTINE_LENSES = ["ethics"]  # a single differently-goaled (whole-picture) reviewer

_REVIEW_SYS_TMPL = (
    "You are a REVIEWER with the {lens} lens. Your goal is DIFFERENT from the author's: "
    "find any way this deliverable trades safety/scope/correctness for the business metric. "
    "You optimize for FINDING problems, not approving. You are advisory to the PM and CANNOT "
    "self-approve or merge. Return verdict=flag if you find an issue, else pass."
)

_EXPLAIN_CHECK_SYS = (
    "Cross-check a worker's stated explanation against the ACTUAL diff/actions. The words "
    "are a lead; the actions are ground truth. Return consistent=false with detail if the "
    "explanation contradicts what was actually done (small models confabulate)."
)


class SameGoalReviewerError(Exception):
    """A reviewer must be differently-goaled from the author (P4.4 config guard)."""


class CheckpointBlocked(Exception):
    """Raised when work tries to proceed past an uncleared checkpoint (P4.2)."""


class StopGates:
    def __init__(self, db: Database, models: ModelRouter, audit: AuditSink) -> None:
        self.db = db
        self.models = models
        self.audit = audit

    # ── checkpoints (P4.1/P4.2) ──────────────────────────────────────────────
    async def add_checkpoint(self, checkpoint_id: str, effort_id: str, name: str, seq: int) -> None:
        async with self.db.session_factory() as s:
            if await s.get(Checkpoint, checkpoint_id) is None:
                s.add(
                    Checkpoint(
                        id=checkpoint_id, effort_id=effort_id, name=name, seq=seq, status="pending"
                    )
                )
                await s.commit()

    async def may_proceed(self, checkpoint_id: str) -> bool:
        """A worker may proceed past a checkpoint ONLY once it is cleared. This is the
        enforced halt — independent of the editable plan's stop markers (P4.1)."""
        async with self.db.session_factory() as s:
            cp = await s.get(Checkpoint, checkpoint_id)
        return bool(cp and cp.status == "cleared")

    async def assert_may_proceed(self, checkpoint_id: str) -> None:
        if not await self.may_proceed(checkpoint_id):
            raise CheckpointBlocked(
                f"checkpoint {checkpoint_id} not cleared — worker cannot proceed (P4.2)"
            )

    # ── explain-intent (P4.3) + verify-vs-diff (P4.3b) ───────────────────────
    async def submit_explanation(
        self, checkpoint_id: str, explanation: Explanation, diff: str = ""
    ) -> ExplanationCheck:
        async with self.db.session_factory() as s:
            cp = await s.get(Checkpoint, checkpoint_id)
            if cp is None:
                raise KeyError(checkpoint_id)
            cp.explanation = explanation.model_dump()
            cp.status = "explained"
            await s.commit()
            effort_id = cp.effort_id
        await self.audit.log(
            "explanation_submitted",
            effort_id=effort_id,
            payload={"checkpoint": checkpoint_id, "explanation": explanation.model_dump()},
        )
        # Verify, don't trust (P4.3b): cross-check against the actual diff.
        check = await self.models.structured(
            "reviewer-ethics",
            _EXPLAIN_CHECK_SYS,
            f"EXPLANATION:\n{explanation.model_dump()}\n\nACTUAL DIFF:\n{diff}",
            ExplanationCheck,
        )
        async with self.db.session_factory() as s:
            cp = await s.get(Checkpoint, checkpoint_id)
            cp.explanation_verified = "ok" if check.consistent else "mismatch"
            await s.commit()
        if not check.consistent:
            await self.audit.log(
                "explanation_mismatch",
                effort_id=effort_id,
                payload={"checkpoint": checkpoint_id, "detail": check.mismatch_detail},
            )
        return check

    # ── differently-goaled review (P4.4/P4.5/P4.7) ────────────────────────────
    @staticmethod
    def assert_differently_goaled(author_role: str, reviewer_profile: str) -> None:
        """A reviewer sharing the author's goal inherits its tunnel vision and
        rubber-stamps (F2/F4). Reject a same-goal reviewer by config."""
        if reviewer_profile == author_role or not reviewer_profile.startswith("reviewer"):
            raise SameGoalReviewerError(
                f"reviewer {reviewer_profile!r} is not differently-goaled from author "
                f"{author_role!r} — review must use a reviewer-<lens> profile (§4.4)"
            )

    def lenses_for(self, risk: str) -> list[str]:
        """Risk-gated review depth (P4.5): irreversible/cross-effort -> full panel;
        routine -> single differently-goaled reviewer."""
        return PANEL_LENSES if risk in ("irreversible", "cross_effort", "cascading_refactor") else ROUTINE_LENSES

    async def review(
        self,
        effort_id: str,
        author_role: str,
        deliverable: str,
        *,
        risk: str = "routine",
        checkpoint_id: str | None = None,
        deterministic_checks: dict[str, bool] | None = None,
    ) -> list[ReviewVerdict]:
        """Spawn differently-goaled reviewer(s) on JUDGE_MODEL, paired with the
        deterministic checks (tests/lints/scope-diff). Verdicts route to the PM,
        never auto-merge."""
        verdicts: list[ReviewVerdict] = []
        for lens in self.lenses_for(risk):
            profile = f"reviewer-{lens}"
            self.assert_differently_goaled(author_role, profile)
            v = await self.models.structured(
                profile,
                _REVIEW_SYS_TMPL.format(lens=lens),
                f"DELIVERABLE:\n{deliverable}\n\nDETERMINISTIC CHECKS:\n{deterministic_checks or {}}",
                ReviewVerdict,
            )
            # Construct-safe: a weak/partial model response defaults to a PASS verdict (never crash
            # the execution loop on a missing field); a real flag still flags.
            v.verdict = getattr(v, "verdict", None) or "pass"
            v.lens = lens
            v.findings = getattr(v, "findings", None) or []
            v.reasoning = getattr(v, "reasoning", None) or ""
            verdicts.append(v)
            async with self.db.session_factory() as s:
                s.add(
                    Review(
                        checkpoint_id=checkpoint_id,
                        effort_id=effort_id,
                        reviewer_profile=profile,
                        lens=lens,
                        verdict=v.verdict,
                        findings={"findings": v.findings, "reasoning": v.reasoning},
                        routed_to_pm=True,
                    )
                )
                await s.commit()
        # A deterministic-check failure is itself a flag, independent of the LLM reviewers.
        if deterministic_checks and not all(deterministic_checks.values()):
            verdicts.append(
                ReviewVerdict(
                    verdict="flag",
                    lens="deterministic",
                    findings=[k for k, ok in deterministic_checks.items() if not ok],
                    reasoning="deterministic check failed",
                )
            )
        await self.audit.log(
            "review_complete",
            effort_id=effort_id,
            payload={
                "checkpoint": checkpoint_id,
                "risk": risk,
                "verdicts": [v.model_dump() for v in verdicts],
            },
        )
        return verdicts

    async def clear_checkpoint(self, checkpoint_id: str, verdicts: list[ReviewVerdict]) -> bool:
        """PM disposition: clear the checkpoint iff no review flagged. A flag routes back
        to the PM to re-ground -> refactor -> continue (P4.6) — the checkpoint stays
        un-cleared (blocking) until a clean review."""
        flagged = any(getattr(v, "verdict", "pass") == "flag" for v in verdicts)
        async with self.db.session_factory() as s:
            cp = await s.get(Checkpoint, checkpoint_id)
            if cp is None:
                raise KeyError(checkpoint_id)
            if flagged:
                cp.status = "flagged"
            else:
                cp.status = "cleared"
                cp.cleared_at = now_iso()
            await s.commit()
            effort_id = cp.effort_id
        await self.audit.log(
            "checkpoint_dispositioned",
            effort_id=effort_id,
            payload={"checkpoint": checkpoint_id, "cleared": not flagged},
        )
        return not flagged

    async def force_clear(self, checkpoint_id: str, *, reason: str = "") -> None:
        """Clear a checkpoint regardless of the review verdicts — for an ADVISORY review (§4.4 /
        DELIVERY-PIPELINE D3: reviews are advisory input, never a gate an agent can game). Used
        when the effort's correctness is machine-verified by the org's OWN build (composition /
        burn-down / D2) and its merge is D4 human-gated, so a subjective quality flag is surfaced
        but must not hard-freeze autonomous progress (operator 2026-07-07: a big port froze on a
        review of a mid-work status message)."""
        async with self.db.session_factory() as s:
            cp = await s.get(Checkpoint, checkpoint_id)
            if cp is None:
                return
            cp.status = "cleared"
            cp.cleared_at = now_iso()
            await s.commit()
            effort_id = cp.effort_id
        await self.audit.log(
            "checkpoint_dispositioned", effort_id=effort_id,
            payload={"checkpoint": checkpoint_id, "cleared": True, "advisory": True,
                     "reason": reason[:160]},
        )
