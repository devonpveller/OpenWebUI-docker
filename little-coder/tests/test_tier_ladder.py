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
