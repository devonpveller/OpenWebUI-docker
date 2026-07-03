"""Pydantic wire/validation schemas.

The CONCERN schema is the canonical intent-framed escalation payload (UX-FLOW §3);
the explanation schema is the plan-stop-gate artifact (§4.5); the structured-output
schemas are what the model-router validates with Instructor + GBNF (small-model
reliability, TOOLING §3.2).
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


# ── Escalation levels & triggers (governance §3) ────────────────────────────
class Level(str, Enum):
    steering = "steering"        # PO may clear
    hard_gate = "hard_gate"      # ONLY the Human Operator may clear


class Trigger(str, Enum):
    refusal = "refusal"                       # F3 — never routed around/dropped
    objection = "objection"                   # F3
    deviation = "deviation"                   # PM sees drift from intent/spec
    ambiguous_scope = "ambiguous_scope"       # F5 — escalate, don't guess
    cross_effort_conflict = "cross_effort_conflict"  # F4
    irreversible_action = "irreversible_action"      # push/deploy/delete/spend/send
    unresolved_disagreement = "unresolved_disagreement"
    wake_storm = "wake_storm"                 # rate cap tripped
    undeliverable_wake = "undeliverable_wake"  # PLAN §3.1.1 — not a silent stall


# Which triggers are hard-gate (reach the human) vs steering (PO can clear).
HARD_GATE_TRIGGERS = {
    Trigger.refusal,
    Trigger.objection,
    Trigger.irreversible_action,
}


class ConcernOption(BaseModel):
    action: str
    effect_on_outcome: str            # how THIS option changes the outcome vs. the intent
    risk: str = ""


class Concern(BaseModel):
    """The intent-framed CONCERN (UX-FLOW §3). No bare technical choice reaches the PO."""

    intent_thread: str
    what_surfaced: str
    intent_of_change: str             # WHY it matters to the intent/outcome
    options: list[ConcernOption] = Field(default_factory=list)
    pm_recommendation: str = ""
    blocked_efforts: list[str] = Field(default_factory=list)


class Decision(BaseModel):
    """Human Operator / PO reply to a CONCERN (§3)."""

    decision: Literal["approve", "modify", "abort"]
    note: str = ""
    modify_scope: str | None = None


# ── Plan-stop-gate explanation (§4.5, P4.3) ─────────────────────────────────
class Explanation(BaseModel):
    intent: str                       # what I understood the goal to be
    goal_as_understood: str
    tradeoffs_hit: str
    what_id_flag: str


# ── Readiness gate (UX-FLOW Stage 2, P3.8) ──────────────────────────────────
class ClarifyingQuestion(BaseModel):
    """A question the readiness gate elevates to the operator — ONLY a genuine blocker a
    competent engineer couldn't resolve from the existing project + standard practice (F5).
    Each carries a `recommendation` so the operator can accept the default (or research it),
    and a `category` so security/ethics concerns are surfaced with their specific stakes."""

    question: str
    # For feature_intent/missing_info: the sensible default applied if unanswered.
    # For security/ethics: the SPECIFIC concern + why it matters to the intent.
    recommendation: str = ""
    category: Literal["feature_intent", "missing_info", "security", "ethics"] = "feature_intent"


class ReadinessVerdict(BaseModel):
    clear_and_safe: bool
    clarifying_questions: list[ClarifyingQuestion] = Field(default_factory=list)
    blast_radius: Literal["routine", "cross_effort", "cascading_refactor"] = "routine"
    reasoning: str = ""


# ── Plan presentation (UX-FLOW Stage 3, P3.9) ───────────────────────────────
class DelegationStep(BaseModel):
    role: str
    task: str
    depends_on: list[str] = Field(default_factory=list)  # DAG, not wide fan-out


class Plan(BaseModel):
    intent_thread: str
    feature_overview: str
    implementation_steps: list[str]
    stop_gates: list[str] = Field(default_factory=list)
    delegation: list[DelegationStep] = Field(default_factory=list)
    estimate: str = "unknown (cold-start — from dry-run)"
    status: Literal["draft", "approved"] = "draft"


# ── Review verdict (§4.4, P4.4) ─────────────────────────────────────────────
class ReviewVerdict(BaseModel):
    verdict: Literal["pass", "flag"]
    lens: str
    findings: list[str] = Field(default_factory=list)
    reasoning: str = ""


# ── Monitor / deviation judgment (§3, P3.7) ─────────────────────────────────
class MonitorVerdict(BaseModel):
    deviates: bool
    trigger: Trigger | None = None
    level: Level | None = None
    rationale: str = ""


# ── Explanation-vs-diff cross-check (§4.5, P4.3b) ───────────────────────────
class ExplanationCheck(BaseModel):
    consistent: bool
    mismatch_detail: str = ""


# ── Natural-language operator intake (the conversational PO surface) ─────────
class OperatorIntent(BaseModel):
    """The PO's structured interpretation of a natural-language operator message. All fields
    default so a weak/partial model response still parses (the bridge degrades gracefully)."""

    kind: Literal[
        "request", "clarification", "status", "steering", "decision", "question", "chitchat"
    ] = "chitchat"
    reply: str = ""                       # the PO's conversational, first-person response
    effort_name: str | None = None        # kebab-case slug for a NEW request
    effort_id: str | None = None          # target for clarification/status/steering/decision
    project: str | None = None            # a named project/repo to work on (a registered project)
    repo_url: str | None = None           # a git URL to ONBOARD as a new project (creates its channel)
    upstream_url: str | None = None       # if repo_url is a FORK, the parent/upstream repo URL (D0.f)
    steering: str | None = None           # the clarification / steering / direction text
    decision: Literal["approve", "modify", "abort"] | None = None  # interpreted, NOT auto-run


# ── Grounding result (UX-FLOW Stage 4, P4.0) ────────────────────────────────
class GroundingResult(BaseModel):
    """What the grounding step returns: prior grounded claims to inject before execution.
    Best-effort — `grounded=False` means research was unavailable/timed out (advisory, not a gate)."""

    grounded: bool = False
    claims: list[str] = Field(default_factory=list)
    summary: str = ""
    job_id: str | None = None


# ── Profile (C4, PLAN §5.4) ─────────────────────────────────────────────────
class ProfileSchema(BaseModel):
    profile: str
    lane: Literal["local", "cloud"] = "local"
    model: str = "qwen36-27b"
    system_prompt_ref: str
    temperature: float = 0.2
    tool_access: list[str] = Field(default_factory=list)
    caller_key: str
