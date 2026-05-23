"""Cohort store — event-sourced projection (design §5.4).

The projection has to survive replays, hold the (lang, task_shape)
scoping invariant, and never escalate on inherited counts. These tests
drive that surface with synthetic journals — they DON'T call the judge,
so a stub similarity function does the cluster matching.
"""

from __future__ import annotations

import pytest

from littlecoder.clusters import (
    UNASSIGNED,
    Cluster,
    MergeEvent,
    Occurrence,
    SplitEvent,
    new_cluster_id,
)
from littlecoder.cohorts import (
    CohortStore,
    UnassignedBucket,
    apply_merge,
    apply_split,
    checkpoint,
    from_dict,
    load_checkpoint,
    project,
    rebuild,
    to_dict,
)
from littlecoder.journals import (
    Error,
    Journals,
    TaskEnded,
    TaskStarted,
    ToolCall,
    utc_now,
)

# A stub similarity: maps cluster.label → score. Anything below the floor
# is unassigned; anything above lands. Letting the test set scores per
# label keeps each case readable.
def make_sim(scores: dict[str, float]):
    def sim(occ: Occurrence, c: Cluster) -> float:
        return scores.get(c.label, 0.0)

    return sim


def _env(task_id: str, **overrides):
    base = dict(
        ts=utc_now(),
        task_id=task_id,
        session_id="sess-1",
        channel="cli",
        user_id="cli",
        repo="https://github.com/acme/widget",
        lang="rust",
        seq=0,
    )
    base.update(overrides)
    return base


def _bugfix_task(task_id: str, message: str, **env_overrides):
    """One bugfix-shaped task with a single error → one occurrence."""
    return [
        TaskStarted(**_env(task_id, seq=0, **env_overrides), trigger_digest="abc"),
        ToolCall(**_env(task_id, seq=1, **env_overrides), tool="pytest"),
        Error(**_env(task_id, seq=2, **env_overrides), kind="test_failure", message=message),
        ToolCall(**_env(task_id, seq=3, **env_overrides), tool="write_file"),
        TaskEnded(**_env(task_id, seq=4, **env_overrides), outcome="fail", signal="pytest exit 1"),
    ]


def _passing_task(task_id: str, **env_overrides):
    """A task with no craft signal — must NOT produce an occurrence."""
    return [
        TaskStarted(**_env(task_id, seq=0, **env_overrides), trigger_digest="abc"),
        ToolCall(**_env(task_id, seq=1, **env_overrides), tool="bash"),
        TaskEnded(**_env(task_id, seq=2, **env_overrides), outcome="pass", signal="acceptance command exit 0"),
    ]


# --- projection routing -------------------------------------------------


def test_passing_tasks_produce_no_occurrences():
    """Critical invariant: only craft signals (errors or fail outcomes)
    create occurrences. A task that passes is not a cluster event."""
    store = CohortStore()
    records = _passing_task("01J0000000000000000000000P")
    project(store, records, make_sim({}))
    assert sum(b.size for b in store.unassigned.values()) == 0
    assert store.counters == {}


def test_failing_task_with_no_cluster_lands_in_unassigned():
    """No clusters exist yet → every occurrence goes to unassigned with
    the right (lang, shape) bucket."""
    store = CohortStore()
    records = _bugfix_task("01J0000000000000000000000A", "borrow checker")
    project(store, records, make_sim({}))
    bucket = store.unassigned[("rust", "bugfix")]
    assert bucket.size == 1
    assert bucket.occurrences[0].signal_text == "borrow checker"
    assert bucket.occurrences[0].source_kind == "test_failure"


def test_occurrence_above_floor_increments_observed():
    """A matching cluster + above-floor score → observed count grows; no
    unassigned residue."""
    store = CohortStore()
    cid = new_cluster_id()
    store.clusters[cid] = Cluster(
        cluster_id=cid,
        label="lifetime errors",
        discriminator="borrow checker",
        lang="rust",
        task_shape="bugfix",
    )
    records = _bugfix_task("01J0000000000000000000000B", "borrow checker complaint")
    project(store, records, make_sim({"lifetime errors": 0.9}), floor=0.7)
    assert store.counters[cid].observed == 1
    assert store.unassigned == {}


def test_occurrence_below_floor_falls_to_unassigned():
    """A weak match (0.6) below the floor (0.7) goes to unassigned even
    though a cluster in scope exists — design §5.2 floor enforcement."""
    store = CohortStore()
    cid = new_cluster_id()
    store.clusters[cid] = Cluster(
        cluster_id=cid,
        label="lifetime errors",
        discriminator="borrow checker",
        lang="rust",
        task_shape="bugfix",
    )
    records = _bugfix_task("01J0000000000000000000000C", "trait bound mismatch")
    project(store, records, make_sim({"lifetime errors": 0.6}), floor=0.7)
    assert store.counters.get(cid, None) is None
    assert store.unassigned[("rust", "bugfix")].size == 1


def test_per_repo_drill_down_aggregates_across_repos():
    """Cohort key is (lang, task_shape) across repos (§5.5). The cluster
    sees occurrences from multiple repos; per_repo_observed lets the
    operator drill down."""
    store = CohortStore()
    cid = new_cluster_id()
    store.clusters[cid] = Cluster(
        cluster_id=cid,
        label="lifetime errors",
        discriminator="borrow checker",
        lang="rust",
        task_shape="bugfix",
    )
    sim = make_sim({"lifetime errors": 0.9})
    records = _bugfix_task(
        "01J000000000000000000000RA", "borrow", repo="https://github.com/a/x"
    ) + _bugfix_task(
        "01J000000000000000000000RB", "borrow", repo="https://github.com/a/y"
    ) + _bugfix_task(
        "01J000000000000000000000RC", "borrow", repo="https://github.com/a/x"
    )
    project(store, records, sim, floor=0.7)
    assert store.counters[cid].observed == 3
    assert store.counters[cid].per_repo_observed == {
        "https://github.com/a/x": 2,
        "https://github.com/a/y": 1,
    }


def test_scope_isolation_lang_split():
    """Two clusters, same label, different `lang` → only the matching
    cluster gets credit. Cross-scope matching would corrupt cohort math."""
    store = CohortStore()
    rust_id = new_cluster_id()
    py_id = new_cluster_id()
    store.clusters[rust_id] = Cluster(
        cluster_id=rust_id,
        label="generic",
        discriminator="d",
        lang="rust",
        task_shape="bugfix",
    )
    store.clusters[py_id] = Cluster(
        cluster_id=py_id,
        label="generic",
        discriminator="d",
        lang="python",
        task_shape="bugfix",
    )
    # Score 1.0 for label="generic" — both clusters tie if scope didn't matter.
    sim = make_sim({"generic": 1.0})
    project(store, _bugfix_task("01J000000000000000000000P1", "x", lang="python"), sim)
    project(store, _bugfix_task("01J000000000000000000000R1", "x", lang="rust"), sim)
    assert store.counters[py_id].observed == 1
    assert store.counters[rust_id].observed == 1


def test_taskend_fail_produces_occurrence_when_no_error_record():
    """A task that ended `fail` without an explicit `error` record is
    still a craft signal (the acceptance command failed). Occurrence
    signal_text reads from `TaskEnded.signal`."""
    store = CohortStore()
    records = [
        TaskStarted(**_env("01J00000000000000000000FAI", seq=0), trigger_digest="x"),
        ToolCall(**_env("01J00000000000000000000FAI", seq=1), tool="pytest"),
        TaskEnded(
            **_env("01J00000000000000000000FAI", seq=2),
            outcome="fail",
            signal="acceptance command exit 1",
        ),
    ]
    project(store, records, make_sim({}))
    bucket = store.unassigned[("rust", "unknown")]
    assert bucket.size == 1
    assert "acceptance" in bucket.occurrences[0].signal_text
    assert bucket.occurrences[0].source_kind == "task_failed"


def test_error_record_wins_over_taskend_when_both_present():
    """If both an error and a fail-end exist, the error message is the
    occurrence signal — it's more specific."""
    store = CohortStore()
    project(
        store,
        _bugfix_task("01J0000000000000000000WIN", "specific borrow error"),
        make_sim({}),
    )
    bucket = store.unassigned[("rust", "bugfix")]
    assert "borrow error" in bucket.occurrences[0].signal_text


# --- lineage (§5.3) -----------------------------------------------------


def test_apply_split_inherits_but_does_not_count_observed():
    """A split copies parent's window to each child as INHERITED. The
    children's `observed` stays at 0 — escalation can't fire on
    inherited evidence."""
    store = CohortStore()
    parent_id = new_cluster_id()
    child_a, child_b = new_cluster_id(), new_cluster_id()
    ev = SplitEvent(
        ts="2026-05-23T00:00:00.000Z",
        parent_id=parent_id,
        child_ids=(child_a, child_b),
        inherited_per_child=12,
        reason="judge: split lifetime-vs-trait-bounds",
    )
    apply_split(store, ev)
    assert store.counters[child_a].inherited == 12
    assert store.counters[child_a].observed == 0
    assert store.counters[child_b].inherited == 12
    assert store.counters[child_b].observed == 0
    assert store.lineage[0] is ev


def test_apply_merge_sums_observed_and_carries_repos():
    """A merge sums parent observed counts onto the child and carries
    per_repo_observed over (drill-down survives merges)."""
    store = CohortStore()
    a, b, ab = new_cluster_id(), new_cluster_id(), new_cluster_id()
    from littlecoder.cohorts import ClusterCounters

    store.counters[a] = ClusterCounters(
        cluster_id=a,
        observed=10,
        per_repo_observed={"https://github.com/x/a": 7, "https://github.com/x/b": 3},
    )
    store.counters[b] = ClusterCounters(
        cluster_id=b,
        observed=5,
        per_repo_observed={"https://github.com/x/b": 5},
    )
    ev = MergeEvent(
        ts="2026-05-23T00:00:00.000Z",
        parent_ids=(a, b),
        child_id=ab,
        merged_observed=15,  # 10 + 5
        reason="judge: same craft gap",
    )
    apply_merge(store, ev)
    assert store.counters[ab].observed == 15
    # per_repo_observed sums the two parents.
    assert store.counters[ab].per_repo_observed == {
        "https://github.com/x/a": 7,
        "https://github.com/x/b": 8,
    }


# --- checkpoint + load --------------------------------------------------


def test_round_trip_through_checkpoint(tmp_path):
    """Write the store to disk and load it back — the result is
    equivalent. Atomic write means no half-state."""
    store = CohortStore()
    cid = new_cluster_id()
    store.clusters[cid] = Cluster(
        cluster_id=cid,
        label="L",
        discriminator="D",
        lang="rust",
        task_shape="bugfix",
    )
    project(
        store,
        _bugfix_task("01J0000000000000000000000R", "x"),
        make_sim({"L": 0.9}),
    )

    target = tmp_path / "cohort-store.json"
    checkpoint(store, target)
    loaded = load_checkpoint(target)
    assert set(loaded.clusters) == set(store.clusters)
    assert loaded.counters[cid].observed == store.counters[cid].observed
    assert loaded.schema_version == store.schema_version


def test_load_checkpoint_returns_empty_when_missing(tmp_path):
    """First boot: no checkpoint exists. The function returns an empty
    store, NOT a None."""
    loaded = load_checkpoint(tmp_path / "missing.json")
    assert isinstance(loaded, CohortStore)
    assert loaded.clusters == {}
    assert loaded.counters == {}


def test_from_dict_refuses_newer_schema_version():
    """A newer schema_version on disk than this build understands ⇒
    refuse (forward-compat is one-way, design §12.9)."""
    with pytest.raises(ValueError, match="schema_version"):
        from_dict({"schema_version": 9999})


# --- rebuild from journals ---------------------------------------------


def test_rebuild_replays_real_journals(tmp_path):
    """The recovery path: blow away the checkpoint, rebuild from journals,
    get the same counters. Pin the deterministic-replay property."""
    j = Journals(tmp_path)
    # Two failing tasks, one passing.
    for rec in _bugfix_task("01J0000000000000000000A01", "borrow A"):
        j.write(rec)
    for rec in _bugfix_task("01J0000000000000000000A02", "borrow B"):
        j.write(rec)
    for rec in _passing_task("01J0000000000000000000A03"):
        j.write(rec)

    store = rebuild(tmp_path, make_sim({}))
    bucket = store.unassigned[("rust", "bugfix")]
    assert bucket.size == 2
    # The passing task did not create an occurrence.
    assert all("A03" not in o.task_id for o in bucket.occurrences)


def test_rebuild_walks_rotated_segments(tmp_path):
    """The reader walks every segment — rotated files AND the live one.
    Rotated files outlive any single cluster's quarantine window, so
    missing them would silently zero out tier-0 evidence."""
    j = Journals(tmp_path)
    for rec in _bugfix_task("01J0000000000000000000R01", "x"):
        j.write(rec)
    # Simulate a rotation: rename the live segment to a rotated name.
    (tmp_path / "errors.jsonl").rename(tmp_path / "errors.20260523000000.jsonl")
    (tmp_path / "outcomes.jsonl").rename(tmp_path / "outcomes.20260523000000.jsonl")
    # New segment for fresh records.
    for rec in _bugfix_task("01J0000000000000000000R02", "y"):
        j.write(rec)

    store = rebuild(tmp_path, make_sim({}))
    bucket = store.unassigned[("rust", "bugfix")]
    assert bucket.size == 2  # both pre- and post-rotation occurrences seen


def test_rebuild_is_deterministic(tmp_path):
    """The contract of a derived index: same inputs → same outputs. If
    rebuild were non-deterministic, a corrupted-checkpoint recovery
    could quietly diverge from the prior store."""
    j = Journals(tmp_path)
    for i, msg in enumerate(["alpha", "beta", "gamma"]):
        tid = f"01J000000000000000000000{i:02d}D"[-26:]
        for rec in _bugfix_task(tid, msg):
            j.write(rec)
    first = rebuild(tmp_path, make_sim({}))
    second = rebuild(tmp_path, make_sim({}))
    assert to_dict(first) == to_dict(second)
