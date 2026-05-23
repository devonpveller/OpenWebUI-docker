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
