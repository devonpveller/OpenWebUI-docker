"""Tier-2 routing rules (design §5.8, §7.2, Chapter 5 §5b).

Routing rules are tier-2 interventions — they reshape DECISIONS the
agent makes per task (e.g. "for Rust async tasks, route through the
reasoning model not the fast one", or "for plan-shape=refactor, skip
the planner's pre-step"). Unlike tier-0 / tier-1 skills, routing rules
are CONFIGURATION the agent's planner-process consumes at boot — not
markdown the augmenter retrieves.

Two design contracts pinned here:

  1. **Staged-freeze (design §5.8).** A routing rule must NOT enter
     the live ruleset until the cluster's tier-0 AND tier-1 windows
     have run and the cluster has DEMONSTRABLY RESISTED them. Without
     this, a tier-2 rule could short-circuit the cheaper interventions
     and become self-confirming. `staged_freeze_allows` is the pure
     policy gate.

  2. **5–10% random exploration (design §5.8).** A routing rule
     "wants" to be self-confirming — once it suppresses a plan step,
     that step never runs, so there's no counter-evidence. The
     exploration scheduler picks ~5% of matching tasks and DELIBERATELY
     does NOT apply the rule, generating fresh evidence for the
     efficacy reversion check (design §8.5).

Stage-5b ships: the YAML data shape + writer/reader (atomic-rename),
the staged-freeze gate, the exploration scheduler, and the rule
matcher. NOT in scope: hot-reload integration into the upstream
little-coder planner-process (lives in upstream's `pi` framework;
needs an upstream patch or sidecar — tracked as a separate follow-up).
"""

from __future__ import annotations

import dataclasses
import hashlib
import random
import re
from pathlib import Path
from typing import Iterable, Literal

import yaml
from pydantic import BaseModel, Field, ValidationError

from . import SCHEMA_VERSION
from .clusters import Cluster
from .cohorts import ClusterCounters
from .journals import utc_now
from .tier_ladder import PriorInterventions


ROUTING_SUBDIR = "routing"
_RULE_FENCE = "---"

# Exploration rate — design §5.8 specifies "5–10% random". Default
# 5% (the lower end) for tier-2 hygiene; operator can tune up.
DEFAULT_EXPLORATION_RATE = 0.05


class RoutingRuleFormatError(ValueError):
    """The YAML file didn't parse back into a valid `RoutingRule`."""


# Action types — every tier-2 rule chooses ONE. The planner-process
# reads these at boot (design §7.2) and applies them per task; the
# exact wiring lives in the upstream `pi` planner. Constrained set
# so the planner integration has a bounded surface area.
RoutingAction = Literal[
    "use_reasoning_model",  # route LLM calls through the reasoning model
    "use_fast_model",  # route LLM calls through the fast model
    "skip_planner",  # bypass the planner pre-step
    "force_test_first",  # require a test run before any edit
    "lengthen_context_budget",  # raise the augmenter's token budget
]


class RoutingRule(BaseModel):
    """One tier-2 routing rule, stored as YAML on disk under
    `skill/routing/<id>.yaml`. The frontmatter-style fields below
    are the COMPLETE rule — there's no body, unlike skill files."""

    model_config = {"extra": "forbid"}

    id: str = Field(..., min_length=8, max_length=64)
    cluster_id: str = Field(..., min_length=4, max_length=64)
    # Matchers — applied as an AND. `lang` and `task_shape` accept "*"
    # for any. `tool` is None when not used.
    lang: str = Field(..., min_length=1)
    task_shape: str = Field(..., min_length=1)
    tool: str | None = None
    # The single decision this rule makes.
    action: RoutingAction
    # Exploration rate for THIS rule (design §5.8). Per-rule because a
    # rule with stronger evidence can have a lower exploration rate.
    exploration_rate: float = Field(default=DEFAULT_EXPLORATION_RATE, ge=0.0, le=1.0)
    # Provenance + lifecycle.
    created: str
    status: Literal["active", "superseded", "retired", "pending"] = "active"
    supersedes: str | None = None
    schema_version: int = SCHEMA_VERSION


def new_rule_id() -> str:
    """Same shape as `clusters.new_cluster_id` / `skills.new_skill_id`."""
    import secrets

    return secrets.token_hex(8)


def build_rule(
    *,
    cluster_id: str,
    lang: str,
    task_shape: str,
    action: RoutingAction,
    rule_id: str | None = None,
    tool: str | None = None,
    exploration_rate: float = DEFAULT_EXPLORATION_RATE,
    status: str = "pending",  # human gate by default (locked: tier-2 NEVER auto-merges)
    created: str | None = None,
) -> RoutingRule:
    """Construct a `RoutingRule` from the judge's drafted fields. Raises
    `RoutingRuleFormatError` on schema violation — same uniform error
    type as skills."""
    try:
        return RoutingRule(
            id=rule_id or new_rule_id(),
            cluster_id=cluster_id,
            lang=lang,
            task_shape=task_shape,
            tool=tool,
            action=action,
            exploration_rate=exploration_rate,
            created=created or utc_now(),
            status=status,  # type: ignore[arg-type]
            supersedes=None,
        )
    except ValidationError as exc:
        raise RoutingRuleFormatError(f"rule rejected: {exc}") from exc


# --- file IO -----------------------------------------------------------


def rule_path(skill_dir: Path | str, rule: RoutingRule) -> Path:
    return Path(skill_dir) / ROUTING_SUBDIR / f"{rule.id}.yaml"


def serialize_rule(rule: RoutingRule) -> str:
    data = rule.model_dump(exclude_none=True)
    return yaml.safe_dump(data, sort_keys=False)


def parse_rule(text: str) -> RoutingRule:
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise RoutingRuleFormatError(f"YAML invalid: {exc}") from exc
    if not isinstance(data, dict):
        raise RoutingRuleFormatError(
            f"rule file must be a YAML mapping (got {type(data).__name__})"
        )
    try:
        return RoutingRule.model_validate(data)
    except ValidationError as exc:
        raise RoutingRuleFormatError(f"rule rejected: {exc}") from exc


def write_rule(skill_dir: Path | str, rule: RoutingRule) -> Path:
    """Atomic write — `.tmp` + `rename(2)`. Mirrors `skills.write_skill`.
    Round-trip-checks the serialized text before publishing so a
    corrupt file never lands on the router's reload path."""
    target = rule_path(skill_dir, rule)
    target.parent.mkdir(parents=True, exist_ok=True)
    text = serialize_rule(rule)
    parse_rule(text)  # self-check
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(target)
    return target


def iter_rules(
    skill_dir: Path | str, *, status: str | None = "active"
) -> Iterable[RoutingRule]:
    root = Path(skill_dir) / ROUTING_SUBDIR
    if not root.exists():
        return
    for path in sorted(root.glob("*.yaml")):
        if path.name.endswith(".tmp"):
            continue
        try:
            rule = parse_rule(path.read_text(encoding="utf-8"))
        except (RoutingRuleFormatError, OSError):
            continue
        if status is not None and rule.status != status:
            continue
        yield rule


def list_rules(skill_dir: Path | str, *, status: str | None = "active") -> list[RoutingRule]:
    return list(iter_rules(skill_dir, status=status))


# --- staged-freeze gate (design §5.8) ---------------------------------


@dataclasses.dataclass(frozen=True)
class FreezeVerdict:
    """The staged-freeze gate's answer. `allowed=True` means the cluster
    can host a tier-2 routing rule; `False` means defer (the prior
    tiers haven't been exhausted yet)."""

    cluster_id: str
    allowed: bool
    reason: str


def staged_freeze_allows(
    cluster: Cluster,
    counter: ClusterCounters | None,
    prior: PriorInterventions,
    *,
    min_observed_after_tier1: int = 20,
) -> FreezeVerdict:
    """Per design §5.8: a routing rule can enter the live set ONLY when
    tier-0 AND tier-1 have shipped for this cluster AND the cluster
    continued recurring beyond their windows.

    Rules:
      - Compliance-gap cluster (`baseline_covers=True`): only tier-1
        is required (tier-0 was skipped per locked #17). Same
        `min_observed_after_tier1` post-tier-1 floor.
      - Knowledge-gap cluster: both tier-0 and tier-1 must be shipped
        for this cluster_id.
      - In both cases: cluster must have at least
        `min_observed_after_tier1` observed occurrences in total
        (rough proxy — without a tier-1 snapshot we can't know
        post-tier-1 count precisely; this floor is conservative)."""
    cid = cluster.cluster_id
    prior_tiers = prior.get(cid, set())

    if 2 in prior_tiers:
        return FreezeVerdict(
            cluster_id=cid,
            allowed=False,
            reason="a tier-2 rule is already live for this cluster",
        )

    if cluster.baseline_covers:
        # Compliance-gap path — tier-0 skipped, tier-1 required.
        if 1 not in prior_tiers:
            return FreezeVerdict(
                cluster_id=cid,
                allowed=False,
                reason=(
                    "compliance-gap cluster requires tier-1 enforcement "
                    "shipped before a tier-2 routing rule (§5.8 staged-freeze)"
                ),
            )
    else:
        # Knowledge-gap path — both required.
        missing = {0, 1} - prior_tiers
        if missing:
            return FreezeVerdict(
                cluster_id=cid,
                allowed=False,
                reason=(
                    f"missing prior interventions for staged-freeze: "
                    f"need tier(s) {sorted(missing)}"
                ),
            )

    observed = counter.observed if counter else 0
    if observed < min_observed_after_tier1:
        return FreezeVerdict(
            cluster_id=cid,
            allowed=False,
            reason=(
                f"insufficient post-tier-1 evidence of resistance: "
                f"{observed} observed; need ≥ {min_observed_after_tier1}"
            ),
        )

    return FreezeVerdict(
        cluster_id=cid,
        allowed=True,
        reason=(
            f"staged-freeze cleared: prior tiers {sorted(prior_tiers)} "
            f"shipped, {observed} observed occurrences"
        ),
    )


# --- exploration scheduler (design §5.8) ------------------------------


def explore_this_task(
    rule: RoutingRule,
    task_id: str,
    *,
    rng_seed: int | None = None,
) -> bool:
    """Deterministic per-task explore? `True` = SKIP applying the rule
    on this task (so we generate counter-evidence). Hash-based so the
    same task always gets the same answer (replayable for incident
    drill-down).

    The hash mixes the rule id + task id; that way different rules
    explore on different tasks even if the task id is the same.
    """
    rate = max(0.0, min(1.0, rule.exploration_rate))
    if rate <= 0.0:
        return False
    if rate >= 1.0:
        return True
    seed_bytes = f"{rule.id}|{task_id}|{rng_seed or 0}".encode("utf-8")
    digest = hashlib.sha256(seed_bytes).digest()
    # First 4 bytes → 32-bit unsigned integer mapped to [0, 1).
    value = int.from_bytes(digest[:4], "big") / (2 ** 32)
    return value < rate


# --- rule matcher (used by the planner-process integration) -----------


def matches_task(
    rule: RoutingRule,
    *,
    lang: str,
    task_shape: str,
    tool: str | None = None,
) -> bool:
    """`True` when the rule's matchers all apply to this task. The
    planner-process calls this once per rule when deciding which
    actions to compose for a task. Case-insensitive on lang to match
    the augmenter's tolerance."""
    if rule.lang != "*" and rule.lang.lower() != lang.lower():
        return False
    if rule.task_shape != "*" and rule.task_shape != task_shape:
        return False
    if rule.tool is not None and rule.tool != "*":
        if tool is None or rule.tool.lower() != tool.lower():
            return False
    return True


@dataclasses.dataclass(frozen=True)
class RoutingDecision:
    """The planner-process gets this back per task. `applied_actions`
    is the set of actions to compose into the task plan;
    `explored_rule_ids` is the set of rules that MATCHED but were
    deliberately skipped for exploration (§5.8 — counter-evidence)."""

    applied_actions: tuple[RoutingAction, ...]
    matched_rule_ids: tuple[str, ...]
    explored_rule_ids: tuple[str, ...]


def evaluate(
    rules: Iterable[RoutingRule],
    *,
    task_id: str,
    lang: str,
    task_shape: str,
    tool: str | None = None,
    rng_seed: int | None = None,
) -> RoutingDecision:
    """Walk all active rules; for each matcher, decide apply-or-explore.
    Returns the composed decision."""
    applied: list[RoutingAction] = []
    matched_ids: list[str] = []
    explored_ids: list[str] = []
    for rule in rules:
        if not matches_task(rule, lang=lang, task_shape=task_shape, tool=tool):
            continue
        matched_ids.append(rule.id)
        if explore_this_task(rule, task_id, rng_seed=rng_seed):
            explored_ids.append(rule.id)
            continue
        applied.append(rule.action)
    return RoutingDecision(
        applied_actions=tuple(applied),
        matched_rule_ids=tuple(matched_ids),
        explored_rule_ids=tuple(explored_ids),
    )
