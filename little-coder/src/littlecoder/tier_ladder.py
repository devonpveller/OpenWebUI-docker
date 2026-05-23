"""Tier-ladder escalation logic (design §5.6, Chapter 4 §4e).

Pure escalation rules — given a cluster + its counters + the existing
skill library, decide which tier (if any) the cluster is eligible to
escalate to next. The `judge` does the drafting; `meta` orchestrates;
this module is the policy.

Stage-3 scope: tier-0 only. Tier-1, tier-2 (routing rules), and tier-3
(code changes) come in later stages / chapters. Keeping the policy
isolated makes the locked-decision-#17 rule (baseline-covered → tier-1)
testable as a single function, separable from LLM calls.

The tier ladder per design §5.6:

  Tier | Trigger                                  | Intervention
  -----+------------------------------------------+--------------------
   0   | N ≥ ~5, no prior intervention,           | knowledge entry
       | baseline_covers == false                 |
   1   | ~20+ after tier-0, rate unchanged — OR   | tool-craft / plan-slot
       | baseline-covered cluster recurring       | (judge picks within)
   2   | persistence after tier-1                 | routing rule
   3   | persistence after tier-2 + §6 argument   | code change

This module currently implements Tier 0 only; the rest is structured-stub
so the API stays stable when later stages land.
"""

from __future__ import annotations

import dataclasses
from typing import Iterable, Literal

from .clusters import Cluster
from .cohorts import ClusterCounters

# Tier-0 trigger threshold — design §5.6 says "N ≥ ~5". The "~" is
# deliberate; this is the floor, not a calibrated value. Real
# calibration comes from preflight (design §13) once Observer has seen
# enough journal volume to set per-cluster M.
TIER0_MIN_OCCURRENCES = 5
# Tier-1 trigger — design §5.6 says "~20+ after tier-0, rate unchanged".
# Counted as new occurrences since the tier-0 was approved (the audit's
# snapshot is the "before"; current counter is "after"). Rate analysis
# is a follow-up — for now we use absolute new-count + a relative
# rate-not-improved check (uses efficacy's tolerance for symmetry).
TIER1_MIN_NEW_OCCURRENCES = 20
TIER1_RATE_IMPROVEMENT_TOLERANCE = 0.10

Tier = Literal[0, 1, 2, 3]


@dataclasses.dataclass(frozen=True)
class Escalation:
    """One eligibility verdict. `eligible` is the actionable answer;
    `reason` carries the why (audited in the operator surface)."""

    cluster_id: str
    tier: Tier
    eligible: bool
    reason: str


# Shape of the "prior interventions for this cluster" input. The
# minting layer (meta + skills loader) builds this from the active
# skill library — we keep the surface narrow so a test can mock it
# without standing up the whole skills module.
PriorInterventions = dict[str, set[Tier]]
"""Map of `cluster_id -> {tier_already_shipped_for_this_cluster}`. A
cluster with `{0}` in the set has a tier-0 entry; one with `set()` is
fresh."""


def evaluate_tier_0(
    cluster: Cluster,
    counter: ClusterCounters | None,
    prior: PriorInterventions,
) -> Escalation:
    """Per design §5.6 + locked decision #17.

    Tier-0 fires ONLY when ALL of:
      - cluster.baseline_covers is False (knowledge gap, not compliance)
      - observed count ≥ TIER0_MIN_OCCURRENCES (5)
      - no prior intervention for this cluster (no tier-0 already)
      - inherited counts don't count — escalation never fires on
        inherited evidence (design §5.3)

    Returns `Escalation(eligible=True, ...)` when all hold; otherwise
    `eligible=False` with a specific reason (the operator surface
    renders this when explaining why a cluster sits unescalated)."""
    cid = cluster.cluster_id

    # Compliance gap — baseline already covers this. Restating it as a
    # tier-0 knowledge entry would just re-teach the agent something
    # it was already told. This becomes a TIER-1 enforcement candidate
    # (design §5.6 + locked #17). Stage-3 doesn't draft tier-1 yet —
    # we report ineligibility-for-tier-0 with the compliance-gap
    # reason so the operator surface can flag it correctly.
    if cluster.baseline_covers:
        return Escalation(
            cluster_id=cid,
            tier=0,
            eligible=False,
            reason="baseline covers this cluster — tier-1 enforcement candidate (locked #17)",
        )

    observed = counter.observed if counter else 0
    if observed < TIER0_MIN_OCCURRENCES:
        return Escalation(
            cluster_id=cid,
            tier=0,
            eligible=False,
            reason=f"only {observed} observed occurrence(s); need ≥ {TIER0_MIN_OCCURRENCES}",
        )

    prior_tiers = prior.get(cid, set())
    if prior_tiers:
        return Escalation(
            cluster_id=cid,
            tier=0,
            eligible=False,
            reason=f"prior intervention already shipped: tier(s) {sorted(prior_tiers)}",
        )

    return Escalation(
        cluster_id=cid,
        tier=0,
        eligible=True,
        reason=(
            f"{observed} observed occurrence(s) ≥ {TIER0_MIN_OCCURRENCES}, "
            f"baseline silent on this cluster, no prior intervention"
        ),
    )


def eligible_tier_0(
    clusters: Iterable[Cluster],
    counters: dict[str, ClusterCounters],
    prior: PriorInterventions,
) -> list[Escalation]:
    """Run `evaluate_tier_0` over every cluster. Returns ONLY the
    eligible ones — `meta._draft_eligible_clusters` walks this list to
    decide what to draft this iteration. Stable order: most-observed
    first, so the loudest gap gets the budget when budgets cap the
    drafts/iteration (design §12.5)."""
    out: list[Escalation] = []
    for cluster in clusters:
        verdict = evaluate_tier_0(cluster, counters.get(cluster.cluster_id), prior)
        if verdict.eligible:
            out.append(verdict)
    out.sort(
        key=lambda e: counters.get(e.cluster_id, ClusterCounters(e.cluster_id)).observed,
        reverse=True,
    )
    return out


def evaluate_all(
    clusters: Iterable[Cluster],
    counters: dict[str, ClusterCounters],
    prior: PriorInterventions,
) -> list[Escalation]:
    """Full diagnostic view for the operator surface — every cluster's
    eligibility verdict (eligible or not), with the reason. The Stage-3
    operator surface uses this to render "why isn't this cluster
    escalated?" alongside the eligible list."""
    return [
        evaluate_tier_0(cluster, counters.get(cluster.cluster_id), prior)
        for cluster in clusters
    ]


# --- tier-1 (Chapter 4 §4e) -------------------------------------------


# Map of cluster_id → (skill_id, observed_at_approve, tasks_at_approve)
# Telling the ladder when tier-0 was approved + what the cohort looked
# like at that moment. Read from `efficacy.snapshots_from_audit` paired
# with skill metadata.
Tier0Snapshot = dict[str, tuple[str, int, int]]


def evaluate_tier_1(
    cluster: Cluster,
    counter: ClusterCounters | None,
    prior: PriorInterventions,
    tier0_snapshots: Tier0Snapshot,
    *,
    current_task_total: int,
) -> Escalation:
    """Per design §5.6 tier-1 entry:

      A — Knowledge-gap path: tier-0 shipped, but cluster keeps
          recurring with the rate unchanged (~20+ new occurrences since
          merge, no meaningful improvement). The tier-0 entry didn't
          land — escalate to tool-craft / plan-slot enforcement.
      B — Compliance-gap path (locked decision #17): cluster is
          baseline-covered (the agent was already told about this) and
          has ≥ TIER0_MIN_OCCURRENCES observed. Enters AT tier-1
          (skips tier-0 entirely — tier-0 would just restate the
          baseline). No prior intervention required.

    Tier-1 eligibility checks:
      - cluster has not already received a tier-1 intervention
      - exactly one of A or B holds for the cluster's current state
      - inherited counts don't satisfy the threshold (§5.3)
    """
    cid = cluster.cluster_id
    prior_tiers = prior.get(cid, set())
    if 1 in prior_tiers:
        return Escalation(
            cluster_id=cid,
            tier=1,
            eligible=False,
            reason="tier-1 already shipped for this cluster",
        )

    observed = counter.observed if counter else 0

    # Path B — compliance gap. Direct entry, no tier-0 needed.
    if cluster.baseline_covers:
        if observed < TIER0_MIN_OCCURRENCES:
            return Escalation(
                cluster_id=cid,
                tier=1,
                eligible=False,
                reason=(
                    f"compliance-gap path: only {observed} observed; "
                    f"need ≥ {TIER0_MIN_OCCURRENCES}"
                ),
            )
        return Escalation(
            cluster_id=cid,
            tier=1,
            eligible=True,
            reason=(
                f"compliance-gap entry (locked #17): baseline covers this "
                f"cluster and {observed} occurrence(s) ≥ {TIER0_MIN_OCCURRENCES}"
            ),
        )

    # Path A — tier-0 shipped, rate unchanged. Requires the audit
    # snapshot so we can count occurrences SINCE merge, not total.
    if 0 not in prior_tiers:
        return Escalation(
            cluster_id=cid,
            tier=1,
            eligible=False,
            reason=(
                "knowledge-gap path requires a tier-0 to be shipped first; "
                "this cluster has no tier-0 intervention"
            ),
        )

    # Find the tier-0 snapshot for this cluster.
    snapshot_for_cluster = None
    for skill_id, (cid_in_snap, observed_at_approve, tasks_at_approve) in (
        (sid, (snap_cid, obs, tasks))
        for sid, snap in tier0_snapshots.items()
        for snap_cid, obs, tasks in [snap]  # unpack the 3-tuple
    ):
        if cid_in_snap == cid:
            snapshot_for_cluster = (skill_id, observed_at_approve, tasks_at_approve)
            break
    if snapshot_for_cluster is None:
        return Escalation(
            cluster_id=cid,
            tier=1,
            eligible=False,
            reason=(
                "knowledge-gap path requires a tier-0 audit snapshot "
                "(approve_decision with observed_at_approve); none found"
            ),
        )
    skill_id, observed_at_approve, tasks_at_approve = snapshot_for_cluster
    new_occurrences = max(0, observed - observed_at_approve)
    post_tasks = max(0, current_task_total - tasks_at_approve)

    if new_occurrences < TIER1_MIN_NEW_OCCURRENCES:
        return Escalation(
            cluster_id=cid,
            tier=1,
            eligible=False,
            reason=(
                f"knowledge-gap path: only {new_occurrences} new occurrence(s) "
                f"since tier-0 (skill={skill_id}); need ≥ {TIER1_MIN_NEW_OCCURRENCES}"
            ),
        )

    # Rate-unchanged check. Pre-rate = observed_at_approve /
    # tasks_at_approve; post-rate = new_occurrences / post_tasks.
    # "Unchanged" = improvement is below TIER1_RATE_IMPROVEMENT_TOLERANCE.
    pre_rate = observed_at_approve / tasks_at_approve if tasks_at_approve else 0.0
    post_rate = new_occurrences / post_tasks if post_tasks else 0.0
    improved_by = pre_rate - post_rate
    rate_unchanged = (
        pre_rate <= 0
        or improved_by <= (pre_rate * TIER1_RATE_IMPROVEMENT_TOLERANCE) + 1e-9
    )
    if not rate_unchanged:
        return Escalation(
            cluster_id=cid,
            tier=1,
            eligible=False,
            reason=(
                f"knowledge-gap path: rate dropped from {pre_rate:.3f} to "
                f"{post_rate:.3f} ({improved_by / pre_rate:.0%} improvement) — "
                f"tier-0 is working, don't escalate"
            ),
        )

    return Escalation(
        cluster_id=cid,
        tier=1,
        eligible=True,
        reason=(
            f"knowledge-gap path: {new_occurrences} new occurrences since "
            f"tier-0 (skill={skill_id}), rate unchanged "
            f"({pre_rate:.3f} → {post_rate:.3f})"
        ),
    )


def eligible_tier_1(
    clusters: Iterable[Cluster],
    counters: dict[str, ClusterCounters],
    prior: PriorInterventions,
    tier0_snapshots: Tier0Snapshot,
    *,
    current_task_total: int,
) -> list[Escalation]:
    """Like `eligible_tier_0`, but for tier-1. Sorted observed-desc so
    the loudest cluster gets the per-iteration budget first."""
    out: list[Escalation] = []
    for cluster in clusters:
        verdict = evaluate_tier_1(
            cluster,
            counters.get(cluster.cluster_id),
            prior,
            tier0_snapshots,
            current_task_total=current_task_total,
        )
        if verdict.eligible:
            out.append(verdict)
    out.sort(
        key=lambda e: counters.get(
            e.cluster_id, ClusterCounters(e.cluster_id)
        ).observed,
        reverse=True,
    )
    return out
