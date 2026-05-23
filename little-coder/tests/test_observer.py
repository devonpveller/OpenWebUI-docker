"""Observer report rendering (design §3f, Chapter 3).

Pins the knowledge-gap-vs-compliance-gap split (locked decision #17) on
the surface side: a cluster with `baseline_covers=True` lands in the
compliance section, `=False` in the knowledge section. The operator
reads this distinction to decide where tier-0 vs tier-1 attention goes
in Chapter 4.
"""

from __future__ import annotations

from littlecoder.clusters import Cluster, new_cluster_id
from littlecoder.cohorts import ClusterCounters, CohortStore, UnassignedBucket
from littlecoder.clusters import Occurrence
from littlecoder.meta import IterationResult
from littlecoder.observer import (
    ClusterSummary,
    UnassignedSummary,
    render_text,
    report_dict,
    summarize_store,
)


def _store_with_clusters() -> CohortStore:
    store = CohortStore()
    # Knowledge gap — baseline silent on async lifetimes.
    k_id = "kkkkkkkkkkkkkkkk"
    store.clusters[k_id] = Cluster(
        cluster_id=k_id,
        label="async lifetime errors",
        discriminator="borrow checker on async",
        lang="rust",
        task_shape="bugfix",
        baseline_covers=False,
    )
    store.counters[k_id] = ClusterCounters(
        cluster_id=k_id,
        observed=8,
        per_repo_observed={"r/a": 5, "r/b": 3},
    )
    # Compliance gap — baseline covers it (the agent isn't following).
    c_id = "cccccccccccccccc"
    store.clusters[c_id] = Cluster(
        cluster_id=c_id,
        label="reads files instead of git log",
        discriminator="agent re-cats files to recover context",
        lang="python",
        task_shape="investigation",
        baseline_covers=True,
    )
    store.counters[c_id] = ClusterCounters(
        cluster_id=c_id,
        observed=12,
        per_repo_observed={"r/c": 12},
    )
    # An unassigned bucket too.
    bucket = UnassignedBucket(lang="go", task_shape="refactor")
    bucket.occurrences.append(
        Occurrence(
            task_id="01J000000000000000000UNAS",
            ts="2026-05-23T00:00:00.000Z",
            lang="go",
            task_shape="refactor",
            repo="r/d",
            signal_text="unassigned thing",
            source_kind="test_failure",
        )
    )
    store.unassigned[("go", "refactor")] = bucket
    return store


# --- summarize_store ----------------------------------------------------


def test_summarize_splits_knowledge_and_compliance_by_baseline_covers():
    store = _store_with_clusters()
    knowledge, compliance, unassigned = summarize_store(store)
    assert [r.label for r in knowledge] == ["async lifetime errors"]
    assert [r.label for r in compliance] == ["reads files instead of git log"]
    assert len(unassigned) == 1


def test_summarize_orders_clusters_by_observed_count():
    """Most-prominent first — operator sees the loudest gap on top."""
    store = CohortStore()
    for i, n in enumerate([3, 10, 1, 5]):
        cid = f"id{i:014d}"
        store.clusters[cid] = Cluster(
            cluster_id=cid,
            label=f"L{i}",
            discriminator="d",
            lang="rust",
            task_shape="bugfix",
            baseline_covers=False,
        )
        store.counters[cid] = ClusterCounters(cluster_id=cid, observed=n)
    knowledge, _, _ = summarize_store(store)
    counts = [r.observed for r in knowledge]
    assert counts == sorted(counts, reverse=True)


def test_summarize_top_repos_capped_at_three():
    """The drill-down is at-a-glance: 3 repos max in the surface row."""
    store = CohortStore()
    cid = new_cluster_id()
    store.clusters[cid] = Cluster(
        cluster_id=cid,
        label="L",
        discriminator="d",
        lang="rust",
        task_shape="bugfix",
        baseline_covers=False,
    )
    repos = {f"r/{c}": i + 1 for i, c in enumerate("abcdefgh")}
    store.counters[cid] = ClusterCounters(
        cluster_id=cid, observed=sum(repos.values()), per_repo_observed=repos
    )
    knowledge, _, _ = summarize_store(store)
    assert len(knowledge[0].top_repos) == 3
    # In descending count order.
    nums = [n for _r, n in knowledge[0].top_repos]
    assert nums == sorted(nums, reverse=True)


# --- report_dict --------------------------------------------------------


def test_report_dict_includes_last_iteration_when_present():
    store = _store_with_clusters()
    last = IterationResult(
        ts="2026-05-23T01:00:00.000Z",
        records_consumed=42,
        clusters_total=2,
        occurrences_total=20,
        unassigned_total=1,
        unassigned_by_scope={("go", "refactor"): 1},
        minted_cluster_ids=("kkkkkkkkkkkkkkkk",),
    )
    out = report_dict(store, last)
    assert out["last_iteration"]["ts"] == "2026-05-23T01:00:00.000Z"
    assert out["last_iteration"]["minted_cluster_ids"] == ["kkkkkkkkkkkkkkkk"]
    assert len(out["knowledge_gaps"]) == 1
    assert len(out["compliance_gaps"]) == 1
    assert len(out["unassigned"]) == 1


def test_report_dict_null_last_iteration_when_unset():
    out = report_dict(CohortStore(), last_result=None)
    assert out["last_iteration"] is None
    assert out["knowledge_gaps"] == []
    assert out["compliance_gaps"] == []
    assert out["unassigned"] == []


# --- render_text --------------------------------------------------------


def test_render_text_separates_sections():
    store = _store_with_clusters()
    last = IterationResult(
        ts="2026-05-23T01:00:00.000Z",
        records_consumed=42,
        clusters_total=2,
        occurrences_total=20,
        unassigned_total=1,
        unassigned_by_scope={("go", "refactor"): 1},
    )
    rendered = render_text(report_dict(store, last))
    assert "KNOWLEDGE GAPS" in rendered
    assert "COMPLIANCE GAPS" in rendered
    assert "tier-0 candidates" in rendered  # knowledge section hint
    assert "tier-1 enforcement" in rendered  # compliance section hint
    assert "async lifetime errors" in rendered
    assert "reads files instead of git log" in rendered
    assert "unassigned scopes" in rendered


def test_render_text_handles_no_iteration_yet():
    rendered = render_text(report_dict(CohortStore(), last_result=None))
    assert "no iteration has completed yet" in rendered
