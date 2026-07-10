"""ORM models — the bridge's persisted state (PLAN §5.1).

Design notes:
- The governance gate's `Effort.state` is the ONE safety FSM (machine A). It is a
  plain string column with a DB-level default-deny (`unknown -> treated as frozen`
  in the gate module) so a corrupt/unknown value fails safe (governance §3.0).
- The scheduler FSM (machine B: computing/waiting/suspended) lives on
  `WorkerInstance.sched_state` and is explicitly NOT the same column — conflating
  them is called out as a safety bug in the spec.
- Enums are stored as strings (no native DB enum) to stay dialect-neutral across
  SQLite (tests) and Postgres (prod).
"""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base, now_iso

# ── Gate FSM (machine A) states ────────────────────────────────────────────
GATE_ACTIVE = "active"
GATE_FROZEN = "frozen"

# ── Scheduler FSM (machine B) states ───────────────────────────────────────
SCHED_COMPUTING = "computing"
SCHED_WAITING = "waiting"
SCHED_SUSPENDED = "suspended"
SCHED_IDLE = "idle"  # unassigned pool instance


class Effort(Base):
    """A unit of work. Under the comms model (COMMS-MODEL §4) an effort is a **thread**
    (`root_post_id`) inside its **project channel** (`channel_id` = `#proj-<slug>`), NOT a
    channel of its own — this kills the per-effort channel sprawl. Carries gate state."""

    __tablename__ = "efforts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    # channel_id = the shared PROJECT channel (many efforts share one); root_post_id = the
    # effort-card root post whose id IS the effort's thread (COMMS-MODEL §4 / CM.1).
    project: Mapped[str | None] = mapped_column(String(64), index=True)
    channel_id: Mapped[str | None] = mapped_column(String(64), index=True)
    root_post_id: Mapped[str | None] = mapped_column(String(64), index=True)
    parent_effort_id: Mapped[str | None] = mapped_column(
        ForeignKey("efforts.id"), index=True
    )  # dependents freeze with the parent (§3 pause granularity)
    # Machine A — the only safety FSM. Default-deny handled in the gate module.
    state: Mapped[str] = mapped_column(String(16), default=GATE_ACTIVE)
    freeze_reason: Mapped[str | None] = mapped_column(String(64))
    freeze_level: Mapped[str | None] = mapped_column(String(16))  # steering | hard_gate
    frozen_by: Mapped[str | None] = mapped_column(String(64))
    plan_status: Mapped[str] = mapped_column(String(16), default="none")  # none|draft|approved
    # Risk-gated dry-run execution gate (P4.0): risk classifies blast radius; dry_run_status
    # gates REAL-code execution for risky efforts (none|skipped|required|running|passed|failed).
    risk: Mapped[str] = mapped_column(String(24), default="routine")
    dry_run_status: Mapped[str] = mapped_column(String(16), default="none")
    # Effort lifecycle (distinct from the gate FSM): open ⇒ still in play; done/aborted ⇒ closed.
    # `/status` shows open efforts by default so completed test efforts don't drown the signal.
    lifecycle: Mapped[str] = mapped_column(String(16), default="open")
    created_at: Mapped[str] = mapped_column(default=now_iso)
    updated_at: Mapped[str] = mapped_column(default=now_iso, onupdate=now_iso)


class Concern(Base):
    """An intent-framed CONCERN (UX-FLOW §3). Freezing an effort posts one to #mgmt."""

    __tablename__ = "concerns"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    effort_id: Mapped[str] = mapped_column(ForeignKey("efforts.id"), index=True)
    level: Mapped[str] = mapped_column(String(16))  # steering | hard_gate
    trigger: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON)  # the full CONCERN schema
    status: Mapped[str] = mapped_column(String(16), default="open")  # open | cleared
    decision: Mapped[str | None] = mapped_column(String(16))  # approve|modify|abort
    cleared_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[str] = mapped_column(default=now_iso)
    cleared_at: Mapped[str | None] = mapped_column()


class Event(Base):
    """Append-only audit log (governance §5). Never updated or deleted."""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[str] = mapped_column(default=now_iso, index=True)
    kind: Mapped[str] = mapped_column(String(48), index=True)
    effort_id: Mapped[str | None] = mapped_column(String(64), index=True)
    actor: Mapped[str | None] = mapped_column(String(64))
    rule_version: Mapped[int | None] = mapped_column()
    goal_version: Mapped[int | None] = mapped_column()
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    mirrored: Mapped[bool] = mapped_column(Boolean, default=False)  # to Open Brain (P6.2)


class ProcessedEvent(Base):
    """Idempotency ledger for at-least-once event delivery (PLAN §3.1.1)."""

    __tablename__ = "processed_events"

    event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    processed_at: Mapped[str] = mapped_column(default=now_iso)


class ChannelCursor(Base):
    """Last-processed post timestamp per channel — reconnect REST catch-up (P1.0)."""

    __tablename__ = "channel_cursors"

    channel_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    # Mattermost create_at is a MILLISECOND epoch (~1.78e12) — needs 64-bit, not int32.
    last_ts: Mapped[int] = mapped_column(BigInteger, default=0)


class SessionMap(Base):
    """channel/thread <-> effort <-> little-coder --session id (P1.1)."""

    __tablename__ = "session_map"

    thread_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    channel_id: Mapped[str] = mapped_column(String(64), index=True)
    effort_id: Mapped[str] = mapped_column(ForeignKey("efforts.id"), index=True)
    session_id: Mapped[str] = mapped_column(String(64))  # == thread_id by convention


class ScopeGrant(Base):
    """Who-may-touch-what (governance §5, §4.1). Grants follow hard-rule #2."""

    __tablename__ = "scope_grants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    subject: Mapped[str] = mapped_column(String(64), index=True)  # worker/role id
    effort_id: Mapped[str | None] = mapped_column(String(64), index=True)
    resource: Mapped[str] = mapped_column(String(256))  # path glob / domain
    granted_by: Mapped[str] = mapped_column(String(64))  # PM | human | po
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[str] = mapped_column(default=now_iso)
    revoked_at: Mapped[str | None] = mapped_column()
    revoked_by: Mapped[str | None] = mapped_column(String(64))


class Profile(Base):
    """Role = model profile (C4, PLAN §5.4). Adding a role = adding a profile."""

    __tablename__ = "profiles"
    __table_args__ = (UniqueConstraint("name", "version", name="uq_profile_name_ver"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    lane: Mapped[str] = mapped_column(String(16))  # local | cloud
    model: Mapped[str] = mapped_column(String(64))
    system_prompt_ref: Mapped[str] = mapped_column(String(256))  # = the charter
    temperature: Mapped[float] = mapped_column(Float, default=0.2)
    tool_access: Mapped[list] = mapped_column(JSON, default=list)  # = scope
    caller_key: Mapped[str] = mapped_column(String(64))  # analytics attribution (C7)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class RuleVersion(Base):
    """Versioned floor/steering store (§4.2). Floor change needs human approval (P3.4)."""

    __tablename__ = "rule_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    layer: Mapped[str] = mapped_column(String(16))  # floor | steering
    name: Mapped[str] = mapped_column(String(64), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    content: Mapped[str] = mapped_column(Text)
    scope_effort_id: Mapped[str | None] = mapped_column(String(64))  # steering can be per-effort
    approved_by: Mapped[str | None] = mapped_column(String(64))  # floor requires human
    created_at: Mapped[str] = mapped_column(default=now_iso)


class GoalVersion(Base):
    """Per-effort canonical objective with constraints INLINE (§4.3)."""

    __tablename__ = "goal_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    effort_id: Mapped[str] = mapped_column(ForeignKey("efforts.id"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    objective: Mapped[str] = mapped_column(Text)          # constraints baked in
    scope_slice: Mapped[str | None] = mapped_column(Text)  # the worker's faithful slice
    created_by: Mapped[str] = mapped_column(String(64))    # PM | PO | human
    invalidates_in_progress: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[str] = mapped_column(default=now_iso)


class WorkerInstance(Base):
    """A pooled (little-coder + open-terminal) pair. Carries scheduler FSM (machine B)."""

    __tablename__ = "worker_instances"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    base_url: Mapped[str] = mapped_column(String(256))
    role: Mapped[str | None] = mapped_column(String(64))  # profile bound while assigned
    effort_id: Mapped[str | None] = mapped_column(String(64), index=True)
    session_id: Mapped[str | None] = mapped_column(String(64))
    sched_state: Mapped[str] = mapped_column(String(16), default=SCHED_IDLE)  # machine B
    waiting_on_effort: Mapped[str | None] = mapped_column(String(64))
    rule_version: Mapped[int | None] = mapped_column()
    retired: Mapped[bool] = mapped_column(Boolean, default=False)
    # Health quarantine: an ISO-8601 UTC instant a wedged/unreachable worker is un-pickable UNTIL.
    # Set when a dispatch fails (409 busy / connection error) so the scheduler stops routing work to a
    # stuck daemon; cleared automatically once the instant passes (self-healing back-off) or on boot.
    quarantined_until: Mapped[str | None] = mapped_column(String(32))
    updated_at: Mapped[str] = mapped_column(default=now_iso, onupdate=now_iso)


class Checkpoint(Base):
    """Plan-doc stop-gate (§4.5). Worker halts; explains intent; review clears."""

    __tablename__ = "checkpoints"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    effort_id: Mapped[str] = mapped_column(ForeignKey("efforts.id"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    seq: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(24), default="pending")
    # pending -> explained -> reviewed -> cleared | flagged
    explanation: Mapped[dict | None] = mapped_column(JSON)  # 4-field artifact (P4.3)
    explanation_verified: Mapped[str | None] = mapped_column(String(16))  # ok|mismatch (P4.3b)
    created_at: Mapped[str] = mapped_column(default=now_iso)
    cleared_at: Mapped[str | None] = mapped_column()


class Review(Base):
    """Differently-goaled review verdict (§4.4). Advisory to PM — never self-approve."""

    __tablename__ = "reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    checkpoint_id: Mapped[str | None] = mapped_column(String(64), index=True)
    effort_id: Mapped[str] = mapped_column(String(64), index=True)
    reviewer_profile: Mapped[str] = mapped_column(String(64))
    lens: Mapped[str] = mapped_column(String(32))  # correctness|security|scope|ethics
    verdict: Mapped[str] = mapped_column(String(16))  # pass | flag
    findings: Mapped[dict] = mapped_column(JSON, default=dict)
    routed_to_pm: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[str] = mapped_column(default=now_iso)


class Suggestion(Base):
    """Worker suggestion pool — bottom-up intent signal (§6, P6.3)."""

    __tablename__ = "suggestions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    effort_id: Mapped[str | None] = mapped_column(String(64))
    worker: Mapped[str] = mapped_column(String(64))
    text: Mapped[str] = mapped_column(Text)
    signature: Mapped[str] = mapped_column(String(128), index=True)  # for pattern grouping
    status: Mapped[str] = mapped_column(String(16), default="open")
    created_at: Mapped[str] = mapped_column(default=now_iso)


class Pattern(Base):
    """A recurring failure/suggestion pattern (§6, P6.4). One incident is noise."""

    __tablename__ = "patterns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    signature: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    count: Mapped[int] = mapped_column(Integer, default=1)
    effort_ids: Mapped[list] = mapped_column(JSON, default=list)
    proposal: Mapped[str | None] = mapped_column(Text)  # PM-synthesized *proposed* change
    status: Mapped[str] = mapped_column(String(16), default="observed")
    # observed -> proposed -> approved | rejected  (propose-not-dispose, P6.5)
    first_seen: Mapped[str] = mapped_column(default=now_iso)
    last_seen: Mapped[str] = mapped_column(default=now_iso, onupdate=now_iso)


class RoleCatalog(Base):
    """Approved-role catalog (§4.1, P5.3). Pre-cleared domains skip the human gate."""

    __tablename__ = "role_catalog"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    charter_ref: Mapped[str] = mapped_column(String(256))
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    retired: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[str] = mapped_column(default=now_iso)


class WakeLog(Base):
    """Wake bus log — rate-cap accounting + undeliverable-wake detection (§5, P5.6)."""

    __tablename__ = "wake_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    effort_id: Mapped[str | None] = mapped_column(String(64), index=True)
    target: Mapped[str | None] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(24), default="work")  # work | brake (brake is exempt)
    delivered: Mapped[bool] = mapped_column(Boolean, default=False)
    ts: Mapped[str] = mapped_column(default=now_iso, index=True)


class Project(Base):
    """A repo the org works on (COMMS-MODEL §4: channel = project = repo). Operators onboard
    projects via `/project add` (Mattermost); efforts resolve their worker's repo from here.
    `AO_DEFAULT_REPO` is only a FALLBACK for requests that don't name a project."""

    __tablename__ = "projects"

    slug: Mapped[str] = mapped_column(String(64), primary_key=True)  # = #proj-<slug>
    name: Mapped[str] = mapped_column(String(200))
    repo_url: Mapped[str] = mapped_column(String(512))
    git_host: Mapped[str | None] = mapped_column(String(200), index=True)  # for the egress allowlist
    # NAME of the env var holding this project's deploy token (NOT the token — secrets stay in env).
    # Lets different projects use different PATs (personal vs org). Empty ⇒ the pool's LC_DEPLOY_TOKEN.
    token_env: Mapped[str | None] = mapped_column(String(64))
    # FORK parent (D0.f): when this project is a fork, the parent repo URL. The bridge re-bakes it as
    # a read-only `upstream` remote on every worker focus (the workspace is ephemeral) so the worker
    # can fetch/merge from upstream but push only to `origin`. This row is the persistent source of
    # truth that survives workspace wipes + container rebuilds. Empty ⇒ not a fork.
    upstream_url: Mapped[str | None] = mapped_column(String(512))
    # D2 (DELIVERY-PIPELINE): the project's check/test command (e.g. `dotnet build Build.sln`,
    # `npm test`). Run on a delivered PR branch BEFORE the merge gate is presented; red routes back
    # to the owning effort. Empty ⇒ D2 is skipped with an honest note (never silently pretended).
    check_cmd: Mapped[str | None] = mapped_column(String(1024))
    # STANDING INTENT (anti-drift): a durable architectural invariant for this project, in plain
    # language, injected into EVERY effort goal and enforced at delivery — e.g. "murder builds from
    # the vendored MonoGame source; never use the `Murder.FNA` NuGet package". Terms the operator
    # marks as forbidden (a `backticked` token after never/not/avoid) are checked against each
    # delivery's diff: a delivery that RE-INTRODUCES a forbidden term is rejected, so the org can't
    # manufacture a green build by reverting the architecture (live 2026-07-07: it did exactly that).
    standing_intent: Mapped[str | None] = mapped_column(String(1024))
    channel_id: Mapped[str | None] = mapped_column(String(64))
    created_by: Mapped[str] = mapped_column(String(64), default="operator")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[str] = mapped_column(default=now_iso)


class EgressHost(Base):
    """A git host the worker egress proxy is allowed to reach — the scope boundary for what
    workers can clone/push. Onboarded projects add theirs automatically; a role can add/remove
    hosts remotely via `/egress` (Mattermost). Rendered to the tinyproxy filter file the
    `ao-git-egress` proxy reloads on change (governance §5 — scope is operator-controlled)."""

    __tablename__ = "egress_hosts"

    host: Mapped[str] = mapped_column(String(200), primary_key=True)
    added_by: Mapped[str] = mapped_column(String(64), default="operator")
    source: Mapped[str] = mapped_column(String(16), default="project")  # project | manual | seed
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[str] = mapped_column(default=now_iso)


class ParkedEffort(Base):
    """An effort parked because an inference step was SHED by queue backpressure (the shared GPU is
    saturated) — machine B `suspended` with reason `inference_backpressure`, NOT a governance freeze.
    DB-backed so a bridge restart mid-saturation doesn't lose the parked work (resume-on-boot). The
    row is a RESUME TOKEN: enough to re-run the shed stage when capacity returns. Removed on resume.

    Distinct from the scheduler's dependency-DAG wait (`WorkerInstance.waiting_on_effort`): that parks
    an INSTANCE blocked on another effort's finish; this parks an EFFORT blocked on GPU capacity."""

    __tablename__ = "parked_efforts"

    effort_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    stage: Mapped[str] = mapped_column(String(24), default="delegate")  # "intake" | "delegate"
    reason: Mapped[str] = mapped_column(String(32), default="inference_backpressure")
    channel_id: Mapped[str | None] = mapped_column(String(64))
    root_post_id: Mapped[str | None] = mapped_column(String(64))
    request: Mapped[str] = mapped_column(Text, default="")
    plan_steps_json: Mapped[str | None] = mapped_column(Text)          # JSON list, or None (single-goal)
    from_step: Mapped[int] = mapped_column(Integer, default=1)          # resume delegate from this 1-based step
    mgmt_thread: Mapped[str | None] = mapped_column(String(64))
    attempts: Mapped[int] = mapped_column(Integer, default=0)           # resume attempts (starvation guard)
    parked_at: Mapped[str] = mapped_column(default=now_iso)


class GlobalState(Base):
    """Singleton — the global kill switch (§3). One row, id=1."""

    __tablename__ = "global_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    kill_switch: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[str] = mapped_column(default=now_iso, onupdate=now_iso)


class PendingApproval(Base):
    """A proposed decision awaiting the operator's `approve <id>` — a drafted lifecycle plan (P-APL.3),
    a proposed capability action (P-APL.1, e.g. fork), or a held Stage-3 effort plan (P3.9). Persisted
    so a bridge restart doesn't SILENTLY DROP the proposal (the in-memory pending dicts are rehydrated
    from here on setup — a hard gate the operator hasn't yet decided must survive a bounce, §3). The
    row is removed the instant the decision is made (approve OR abort). `payload` is the store entry,
    made JSON-safe (pydantic plans → `model_dump(mode="json")`)."""

    __tablename__ = "pending_approvals"

    id: Mapped[str] = mapped_column(String(96), primary_key=True)   # plan-… / cap-… / <effort_id>
    kind: Mapped[str] = mapped_column(String(24))                   # lifecycle | capability | effort_plan
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[str] = mapped_column(default=now_iso)
