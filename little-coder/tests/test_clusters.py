"""Cluster identity + assignment (design §5.1–§5.3).

Drives the pure assignment logic with a stub similarity function. The real
embedding + judge discriminator pipeline lives in the judge module; here
we want to know the assignment rules are right — floor enforcement, scope
isolation, unassigned fallthrough, lineage data shape.
"""

from __future__ import annotations

import pytest

from littlecoder.clusters import (
    UNASSIGNED,
    AssignmentResult,
    Cluster,
    MergeEvent,
    Occurrence,
    SplitEvent,
    assign,
    new_cluster_id,
)


def _occ(**overrides):
    base = dict(
        task_id="01J0000000000000000000000A",
        ts="2026-05-23T00:00:00.000Z",
        lang="rust",
        task_shape="bugfix",
        repo="https://github.com/acme/widget",
        signal_text="cannot borrow `x` as mutable",
        source_kind="test_failure",
    )
    base.update(overrides)
    return Occurrence(**base)


def _cluster(cid="c1", label="lifetime errors", lang="rust", shape="bugfix"):
    return Cluster(
        cluster_id=cid,
        label=label,
        discriminator="borrow checker + lifetime annotations",
        lang=lang,
        task_shape=shape,
    )


# --- new_cluster_id ------------------------------------------------------


def test_new_cluster_id_is_random_and_stable_length():
    ids = {new_cluster_id() for _ in range(50)}
    assert len(ids) == 50  # collision-free at this scale
    for cid in ids:
        assert len(cid) == 16  # 64-bit hex
        int(cid, 16)  # parses as hex


# --- assign --------------------------------------------------------------


def test_assign_picks_highest_in_scope_above_floor():
    """Best-of-many: similarity decides; floor is the gate."""
    c1 = _cluster("c1", label="generic rust error")
    c2 = _cluster("c2", label="lifetime errors")
    c3 = _cluster("c3", label="trait bounds")

    # The signal text is a borrow-checker complaint → matches c2 hardest.
    scores = {"c1": 0.4, "c2": 0.9, "c3": 0.5}
    sim = lambda occ, cl: scores[cl.cluster_id]

    result = assign(_occ(), [c1, c2, c3], sim, floor=0.7)
    assert result.cluster_id == "c2"
    assert result.score == pytest.approx(0.9)
    assert result.same_scope is True


def test_assign_returns_unassigned_when_all_below_floor():
    """Loud occurrence, soft cluster set → unassigned. The score travels
    along so the drift trigger can see how close we got."""
    c1 = _cluster("c1")
    c2 = _cluster("c2")
    sim = lambda occ, cl: {"c1": 0.4, "c2": 0.6}[cl.cluster_id]
    result = assign(_occ(), [c1, c2], sim, floor=0.7)
    assert result.cluster_id == UNASSIGNED
    assert result.score == pytest.approx(0.6)


def test_assign_returns_unassigned_with_zero_when_no_clusters_in_scope():
    """An occurrence in a scope that has no existing clusters lands in
    unassigned with score 0 — the judge will mint a new cluster if the
    pool itself coheres (design §5.2)."""
    c1 = _cluster(lang="python", shape="bugfix")
    sim = lambda occ, cl: 1.0
    result = assign(_occ(lang="rust"), [c1], sim, floor=0.7)
    assert result.cluster_id == UNASSIGNED
    assert result.score == 0.0
    assert result.same_scope is False


def test_assign_never_crosses_lang_scope():
    """Even a perfect-similarity match in a different `lang` is ignored —
    the cohort key is (lang, task_shape) per §5.5. Cross-scope clustering
    is never permitted."""
    rust_c = _cluster("rust1", lang="rust", shape="bugfix")
    py_c = _cluster("py1", lang="python", shape="bugfix")
    # Identical similarity score for both — the in-scope one wins.
    sim = lambda occ, cl: 1.0
    result = assign(_occ(lang="rust"), [rust_c, py_c], sim, floor=0.7)
    assert result.cluster_id == "rust1"


def test_assign_never_crosses_task_shape_scope():
    """Same lang, different shape ⇒ different scope. A refactor cluster
    and a bugfix cluster are distinct even when the lang matches."""
    refactor_c = _cluster("r1", lang="rust", shape="refactor")
    bugfix_c = _cluster("b1", lang="rust", shape="bugfix")
    sim = lambda occ, cl: 1.0
    # The occurrence is a bugfix — should land on the bugfix cluster.
    result = assign(_occ(task_shape="bugfix"), [refactor_c, bugfix_c], sim)
    assert result.cluster_id == "b1"


def test_assign_floor_boundary_inclusive():
    """`>= floor` (not strict) — a 0.7 match with floor=0.7 should land."""
    c = _cluster()
    sim = lambda occ, cl: 0.7
    result = assign(_occ(), [c], sim, floor=0.7)
    assert result.cluster_id == c.cluster_id


def test_assign_floor_just_below_unassigned():
    """0.699 < 0.7 → unassigned, with the score preserved."""
    c = _cluster()
    sim = lambda occ, cl: 0.6999
    result = assign(_occ(), [c], sim, floor=0.7)
    assert result.cluster_id == UNASSIGNED
    assert result.score == pytest.approx(0.6999)


def test_assign_with_empty_cluster_list():
    """No clusters at all → unassigned."""
    result = assign(_occ(), [], lambda o, c: 0.0)
    assert result.cluster_id == UNASSIGNED
    assert result.score == 0.0


# --- lineage data shape (§5.3) ------------------------------------------


def test_split_event_carries_inherited_marker():
    """The data shape — a split copies the parent's window to each child
    as `inherited`, NOT `observed`. The projection (cohorts.py) enforces
    the don't-escalate-on-inherited rule; this test pins the carrier."""
    ev = SplitEvent(
        ts="2026-05-23T00:00:00.000Z",
        parent_id="parent1",
        child_ids=("child1", "child2"),
        inherited_per_child=12,
        reason="judge: split lifetime-vs-trait-bounds",
    )
    assert ev.inherited_per_child == 12
    assert "parent1" in (ev.parent_id,)
    assert ev.child_ids == ("child1", "child2")


def test_merge_event_records_observed_sum():
    """A merge sums observed counts and resets the quarantine window — the
    summed `merged_observed` is the carrier the projection trusts."""
    ev = MergeEvent(
        ts="2026-05-23T00:00:00.000Z",
        parent_ids=("a", "b"),
        child_id="ab",
        merged_observed=37,
        reason="judge: same craft gap, different surface",
    )
    assert ev.merged_observed == 37
    assert set(ev.parent_ids) == {"a", "b"}
