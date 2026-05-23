"""Tier-ladder escalation policy (design §5.6, locked decision #17).

Pure-function tests over the eligibility rule. The judge + meta
integration tests in `test_judge.py` / `test_meta.py` cover the
end-to-end path; here we drive the policy alone, because the
compliance-vs-knowledge-gap rule (locked #17) is the most consequential
shift in the tier ladder and wants its own focused surface.
"""

from __future__ import annotations

from littlecoder.clusters import Cluster, new_cluster_id
from littlecoder.cohorts import ClusterCounters
from littlecoder.tier_ladder import (
    TIER0_MIN_OCCURRENCES,
    Escalation,
    eligible_tier_0,
    evaluate_all,
    evaluate_tier_0,
)


def _cluster(cid="c1", *, baseline_covers=False, lang="rust", shape="bugfix"):
    return Cluster(
        cluster_id=cid,
        label="L",
        discriminator="d",
        lang=lang,
        task_shape=shape,
        baseline_covers=baseline_covers,
    )


def _counter(cid="c1", observed=5, inherited=0):
    return ClusterCounters(cluster_id=cid, observed=observed, inherited=inherited)


# --- evaluate_tier_0 — the core policy ---------------------------------


def test_eligible_when_threshold_met_and_baseline_silent_and_no_prior():
    """Happy path — the canonical tier-0 candidate."""
    cluster = _cluster()
    counter = _counter(observed=TIER0_MIN_OCCURRENCES)
    verdict = evaluate_tier_0(cluster, counter, prior={})
    assert verdict.eligible is True
    assert verdict.tier == 0


def test_ineligible_when_baseline_covers_cluster():
    """Locked decision #17 — baseline-covered clusters must NOT enter as
    tier-0 (they would just restate what the agent was already told).
    Reason names "tier-1 enforcement" so the operator surface can
    render the routing correctly when Stage 4 adds it."""
    cluster = _cluster(baseline_covers=True)
    counter = _counter(observed=10)  # well over threshold
    verdict = evaluate_tier_0(cluster, counter, prior={})
    assert verdict.eligible is False
    assert "baseline covers" in verdict.reason
    assert "tier-1" in verdict.reason


def test_ineligible_below_threshold():
    cluster = _cluster()
    counter = _counter(observed=TIER0_MIN_OCCURRENCES - 1)
    verdict = evaluate_tier_0(cluster, counter, prior={})
    assert verdict.eligible is False
    assert f"≥ {TIER0_MIN_OCCURRENCES}" in verdict.reason


def test_inherited_count_does_not_satisfy_threshold():
    """Design §5.3 — escalation never fires on inherited counts. A
    cluster with 0 observed + 100 inherited stays ineligible."""
    cluster = _cluster()
    counter = _counter(observed=0, inherited=100)
    verdict = evaluate_tier_0(cluster, counter, prior={})
    assert verdict.eligible is False


def test_ineligible_when_prior_intervention_for_this_cluster():
    """A tier-0 skill already exists for this cluster → don't draft
    another. The next escalation step is tier-1 (Stage 4)."""
    cluster = _cluster("c-with-prior")
    counter = _counter("c-with-prior", observed=20)
    verdict = evaluate_tier_0(cluster, counter, prior={"c-with-prior": {0}})
    assert verdict.eligible is False
    assert "prior intervention" in verdict.reason


def test_prior_intervention_for_a_different_cluster_does_not_block():
    cluster = _cluster("c-new")
    counter = _counter("c-new", observed=10)
    verdict = evaluate_tier_0(cluster, counter, prior={"c-other": {0, 1}})
    assert verdict.eligible is True


def test_no_counter_means_zero_observed():
    """A cluster that exists in the store but has no counter row yet
    (judge minted, projection hasn't seen new occurrences) should NOT
    be erroneously declared eligible."""
    cluster = _cluster()
    verdict = evaluate_tier_0(cluster, counter=None, prior={})
    assert verdict.eligible is False


# --- eligible_tier_0 — list view ----------------------------------------


def test_eligible_list_orders_by_observed_descending():
    """Loudest cluster first — when the per-iteration draft cap is 1
    (design §12.5), the noisiest gap gets the budget."""
    c1 = _cluster("c1")
    c2 = _cluster("c2")
    c3 = _cluster("c3")
    counters = {
        "c1": _counter("c1", observed=8),
        "c2": _counter("c2", observed=20),  # loudest
        "c3": _counter("c3", observed=5),
    }
    out = eligible_tier_0([c1, c2, c3], counters, prior={})
    assert [v.cluster_id for v in out] == ["c2", "c1", "c3"]


def test_eligible_list_drops_ineligible():
    """Only eligible verdicts come back from `eligible_tier_0`; the
    full diagnostic view is `evaluate_all`."""
    c1 = _cluster("c1", baseline_covers=False)
    c2 = _cluster("c2", baseline_covers=True)  # compliance gap
    c3 = _cluster("c3")
    counters = {
        "c1": _counter("c1", observed=10),
        "c2": _counter("c2", observed=10),
        "c3": _counter("c3", observed=3),  # under threshold
    }
    out = eligible_tier_0([c1, c2, c3], counters, prior={})
    assert [v.cluster_id for v in out] == ["c1"]


def test_evaluate_all_returns_one_verdict_per_cluster_with_reason():
    """The operator surface uses this to explain why each cluster sits
    where it sits."""
    c1 = _cluster("c1")
    c2 = _cluster("c2", baseline_covers=True)
    counters = {
        "c1": _counter("c1", observed=10),
        "c2": _counter("c2", observed=10),
    }
    verdicts = evaluate_all([c1, c2], counters, prior={})
    assert len(verdicts) == 2
    by_id = {v.cluster_id: v for v in verdicts}
    assert by_id["c1"].eligible is True
    assert by_id["c2"].eligible is False
    assert "baseline covers" in by_id["c2"].reason


# --- tier-1 (Chapter 4 §4e) -------------------------------------------


from littlecoder.tier_ladder import (
    TIER1_MIN_NEW_OCCURRENCES,
    eligible_tier_1,
    evaluate_tier_1,
)


def test_tier1_compliance_path_fires_when_baseline_covers_and_threshold_met():
    """Locked #17: a baseline-covered cluster with ≥ TIER0_MIN_OCCURRENCES
    enters AT tier-1 — skipping tier-0 entirely. No tier-0 snapshot
    needed for this path."""
    cluster = _cluster("c-compliance", baseline_covers=True)
    counter = _counter("c-compliance", observed=10)
    verdict = evaluate_tier_1(cluster, counter, prior={}, tier0_snapshots={}, current_task_total=100)
    assert verdict.eligible is True
    assert "compliance-gap entry" in verdict.reason


def test_tier1_compliance_path_below_threshold_ineligible():
    cluster = _cluster("c", baseline_covers=True)
    counter = _counter("c", observed=2)
    verdict = evaluate_tier_1(cluster, counter, prior={}, tier0_snapshots={}, current_task_total=100)
    assert verdict.eligible is False
    assert "need ≥" in verdict.reason


def test_tier1_knowledge_path_requires_prior_tier0():
    """Knowledge-gap path needs a tier-0 to have shipped (otherwise the
    cluster should be ESCALATING to tier-0 first, not skipping)."""
    cluster = _cluster("c", baseline_covers=False)
    counter = _counter("c", observed=50)
    verdict = evaluate_tier_1(
        cluster, counter, prior={}, tier0_snapshots={}, current_task_total=100
    )
    assert verdict.eligible is False
    assert "requires a tier-0" in verdict.reason


def test_tier1_knowledge_path_requires_snapshot():
    """Tier-0 shipped but no audit snapshot recorded — can't compute
    rate-unchanged. Skip with an explicit reason."""
    cluster = _cluster("c", baseline_covers=False)
    counter = _counter("c", observed=50)
    prior = {"c": {0}}  # tier-0 shipped
    verdict = evaluate_tier_1(
        cluster, counter, prior=prior, tier0_snapshots={}, current_task_total=100
    )
    assert verdict.eligible is False
    assert "audit snapshot" in verdict.reason


def test_tier1_knowledge_path_fires_when_rate_unchanged():
    """Pre-rate 0.20/task, post-rate 0.20/task — clearly unchanged.
    Plus ≥ TIER1_MIN_NEW_OCCURRENCES new occurrences → tier-1."""
    cluster = _cluster("c", baseline_covers=False)
    counter = _counter("c", observed=30)  # 10 pre + 20 post
    prior = {"c": {0}}
    tier0_snapshots = {"skill-abc": ("c", 10, 50)}  # snapshot at observed=10, tasks=50
    verdict = evaluate_tier_1(
        cluster,
        counter,
        prior=prior,
        tier0_snapshots=tier0_snapshots,
        current_task_total=150,  # 100 tasks since snapshot
    )
    # pre-rate: 10/50 = 0.20; post-rate: 20/100 = 0.20; unchanged.
    # new occurrences: 20 == TIER1_MIN_NEW_OCCURRENCES → eligible.
    assert verdict.eligible is True
    assert "rate unchanged" in verdict.reason


def test_tier1_skips_when_rate_clearly_improved():
    """Post-rate well below pre-rate → tier-0 is working; don't escalate.
    Use ≥ TIER1_MIN_NEW_OCCURRENCES post-count so the rate check is
    actually exercised (count threshold checked first)."""
    cluster = _cluster("c", baseline_covers=False)
    # pre-rate: 100/100 = 1.00. post: 20/1000 = 0.02 (huge drop).
    counter = _counter("c", observed=120)  # 100 pre + 20 post
    prior = {"c": {0}}
    tier0_snapshots = {"skill-abc": ("c", 100, 100)}
    verdict = evaluate_tier_1(
        cluster,
        counter,
        prior=prior,
        tier0_snapshots=tier0_snapshots,
        current_task_total=1100,  # 1000 tasks since snapshot
    )
    assert verdict.eligible is False
    assert "tier-0 is working" in verdict.reason


def test_tier1_skips_when_post_occurrences_below_threshold():
    """≥ TIER1_MIN_NEW_OCCURRENCES required — below that, defer."""
    cluster = _cluster("c", baseline_covers=False)
    counter = _counter("c", observed=15)  # only 5 post
    prior = {"c": {0}}
    tier0_snapshots = {"skill-abc": ("c", 10, 50)}
    verdict = evaluate_tier_1(
        cluster,
        counter,
        prior=prior,
        tier0_snapshots=tier0_snapshots,
        current_task_total=150,
    )
    assert verdict.eligible is False
    assert f"need ≥ {TIER1_MIN_NEW_OCCURRENCES}" in verdict.reason


def test_tier1_skips_when_already_shipped():
    """A cluster with tier-1 already done doesn't get a second one."""
    cluster = _cluster("c", baseline_covers=True)
    counter = _counter("c", observed=50)
    verdict = evaluate_tier_1(
        cluster,
        counter,
        prior={"c": {0, 1}},  # both tiers shipped
        tier0_snapshots={},
        current_task_total=100,
    )
    assert verdict.eligible is False
    assert "tier-1 already shipped" in verdict.reason


def test_eligible_tier_1_sorts_by_observed_desc():
    """Loudest cluster first — the per-iteration draft budget goes to
    the noisiest gap."""
    c1 = _cluster("c1", baseline_covers=True)
    c2 = _cluster("c2", baseline_covers=True)
    counters = {
        "c1": _counter("c1", observed=8),
        "c2": _counter("c2", observed=20),
    }
    out = eligible_tier_1([c1, c2], counters, prior={}, tier0_snapshots={}, current_task_total=100)
    assert [v.cluster_id for v in out] == ["c2", "c1"]
