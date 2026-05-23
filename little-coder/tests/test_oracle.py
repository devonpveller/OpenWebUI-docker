"""Polyglot oracle interface (design §8.1, §8.3, §8.4).

Drive the biased-subset selector + the MockOracle + the
in-context-assertion query. The real `PolyglotOracle` is an operator-
populated shell — its corpus-walk lands in a follow-up; what matters
here is that everything downstream depends only on the interface.
"""

from __future__ import annotations

from littlecoder.oracle import (
    EvidenceLevel,
    Exercise,
    ExerciseOutcome,
    MockOracle,
    PolyglotOracle,
    ScoredResult,
    select_biased_subset,
)


def _ex(eid, lang="rust", domain="async", tags=()):
    return Exercise(id=eid, lang=lang, domain=domain, tags=frozenset(tags))


# --- select_biased_subset ----------------------------------------------


def test_subset_prefers_lang_and_domain_matches():
    """Tier 1 (lang + domain) fills first. A perfect match always
    appears before a lang-only or unrelated exercise."""
    corpus = [
        _ex("r-async-a", "rust", "async"),
        _ex("r-fs-a", "rust", "fs"),
        _ex("py-async-a", "python", "async"),
        _ex("r-async-b", "rust", "async"),
    ]
    out = select_biased_subset(
        corpus, cluster_lang="rust", cluster_domain="async", target_n=2
    )
    ids = [e.id for e in out]
    assert ids == ["r-async-a", "r-async-b"]  # both tier-1 matches


def test_subset_falls_through_tiers_when_target_n_exceeds_matches():
    """Tier-1 has 1 entry; tier-2 fills the rest; tier-3 only if
    needed. The stratified fallback keeps variance low while still
    hitting target_n."""
    corpus = [
        _ex("r-async-1", "rust", "async"),  # tier 1
        _ex("r-fs-1", "rust", "fs"),  # tier 2 (rust, not async)
        _ex("r-fs-2", "rust", "fs"),  # tier 2
        _ex("py-anything", "python", "fs"),  # tier 3
    ]
    out = select_biased_subset(
        corpus, cluster_lang="rust", cluster_domain="async", target_n=3
    )
    ids = [e.id for e in out]
    assert ids == ["r-async-1", "r-fs-1", "r-fs-2"]  # tier 1 + tier 2


def test_subset_uses_tier3_only_as_last_resort():
    corpus = [
        _ex("py-1", "python", "async"),
        _ex("py-2", "python", "fs"),
        _ex("r-only-1", "rust", "fs"),
    ]
    out = select_biased_subset(
        corpus, cluster_lang="rust", cluster_domain="async", target_n=3
    )
    ids = [e.id for e in out]
    # No rust-async; r-only-1 (tier 2 — rust) comes first, then tier 3.
    assert ids[0] == "r-only-1"
    assert set(ids[1:]) == {"py-1", "py-2"}


def test_subset_returns_empty_for_zero_or_empty():
    assert select_biased_subset([], cluster_lang="rust", cluster_domain="x", target_n=5) == []
    assert (
        select_biased_subset(
            [_ex("a")], cluster_lang="rust", cluster_domain="x", target_n=0
        )
        == []
    )


def test_subset_is_deterministic():
    """Same inputs → same subset. Required so an incident drill-down
    re-runs the same exercises."""
    corpus = [_ex(f"id-{i}", "rust", "async") for i in range(8)]
    a = select_biased_subset(corpus, cluster_lang="rust", cluster_domain="async", target_n=4)
    b = select_biased_subset(corpus, cluster_lang="rust", cluster_domain="async", target_n=4)
    assert a == b


def test_subset_caps_at_target_n_when_corpus_is_huge():
    corpus = [_ex(f"id-{i}", "rust", "async") for i in range(100)]
    out = select_biased_subset(corpus, cluster_lang="rust", cluster_domain="async", target_n=5)
    assert len(out) == 5


# --- ScoredResult / MockOracle -----------------------------------------


def test_mock_oracle_returns_measured_when_enough_run():
    """`target_n` exercises selected and run → MEASURED. Pass-rate is
    the count of `True` outcomes over the subset."""
    corpus = [_ex(f"r-async-{i}", "rust", "async") for i in range(5)]
    candidate = {f"r-async-{i}": (i % 2 == 0) for i in range(5)}  # 3 pass, 2 fail
    oracle = MockOracle(exercises=corpus, candidate_outcomes=candidate)
    result = oracle.run_subset("rust", "async", target_n=5)
    assert result.evidence_level is EvidenceLevel.MEASURED
    assert result.subset_size == 5
    assert result.passed_count == 3
    assert result.score == 0.6


def test_mock_oracle_returns_insufficient_when_corpus_too_small():
    corpus = [_ex("only", "rust", "async")]
    oracle = MockOracle(exercises=corpus, candidate_outcomes={"only": True})
    result = oracle.run_subset("rust", "async", target_n=5)
    assert result.evidence_level is EvidenceLevel.INSUFFICIENT
    assert result.subset_size == 1


def test_mock_oracle_baseline_and_candidate_use_different_outcomes():
    """Baseline runs WITHOUT the skill under test → different outcomes
    are expected. The Oracle returns separate runs."""
    corpus = [_ex(f"ex{i}", "rust", "async") for i in range(3)]
    oracle = MockOracle(
        exercises=corpus,
        candidate_outcomes={"ex0": True, "ex1": True, "ex2": True},  # all pass
        baseline_outcomes={"ex0": True, "ex1": False, "ex2": False},  # 1 pass
    )
    candidate = oracle.run_subset("rust", "async", target_n=3)
    baseline = oracle.baseline_for("rust", "async", target_n=3)
    assert candidate.score == 1.0
    assert baseline.score == pytest_approx(1 / 3)


# Inline a one-liner to avoid pulling pytest.approx into the namespace —
# the Oracle module wants to stay free of test deps.
def pytest_approx(value):
    import pytest

    return pytest.approx(value)


def test_mock_oracle_records_skill_under_test():
    """Validation needs to know what skill was being measured."""
    from littlecoder.skills import build_skill

    corpus = [_ex("ex", "rust", "async")]
    oracle = MockOracle(exercises=corpus)
    skill = build_skill(
        kind="knowledge",
        cluster_id="cl01",
        tier=0,
        lang="rust",
        domain="async",
        task_shape="bugfix",
        name="x" * 5,
        description="y" * 5,
        body="z" * 20,
    )
    oracle.run_subset("rust", "async", target_n=1, skill_under_test=skill)
    assert oracle.calls[-1]["skill_under_test"] == skill.id
    assert oracle.calls[-1]["kind"] == "candidate"


# --- §8.4 in-context assertion query ------------------------------------


def test_selected_skill_ids_aggregates_per_exercise_selections():
    """The §8.4 query: a `ScoredResult.selected_skill_ids()` set lets
    validation assert the artifact-under-test was actually selected
    into context during the run."""
    corpus = [_ex("a", "rust", "async"), _ex("b", "rust", "async")]
    oracle = MockOracle(
        exercises=corpus,
        candidate_outcomes={"a": True, "b": True},
        selections={
            "a": ("skill-1", "skill-2"),
            "b": ("skill-1",),  # skill-2 only on exercise a
        },
    )
    result = oracle.run_subset("rust", "async", target_n=2)
    assert result.selected_skill_ids() == {"skill-1", "skill-2"}


def test_selected_skill_ids_empty_when_no_selections_logged():
    corpus = [_ex("a", "rust", "async")]
    oracle = MockOracle(exercises=corpus, candidate_outcomes={"a": True})
    result = oracle.run_subset("rust", "async", target_n=1)
    assert result.selected_skill_ids() == set()


# --- PolyglotOracle shell ----------------------------------------------


def test_polyglot_oracle_returns_insufficient_when_corpus_empty(tmp_path):
    """Until the operator imports the Polyglot corpus, the real Oracle
    behaves as 'no evidence' — validation gates emit `void`, nothing
    merges by accident."""
    oracle = PolyglotOracle(tmp_path)
    result = oracle.run_subset("rust", "async", target_n=5)
    assert result.evidence_level is EvidenceLevel.INSUFFICIENT
    assert result.subset_size == 0
    assert result.outcomes == ()


def test_polyglot_oracle_baseline_also_insufficient_when_empty(tmp_path):
    oracle = PolyglotOracle(tmp_path)
    result = oracle.baseline_for("rust", "async", target_n=5)
    assert result.evidence_level is EvidenceLevel.INSUFFICIENT
