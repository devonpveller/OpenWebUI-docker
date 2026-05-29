"""Validation gates (design §8.3, §8.4, Chapter 4 §4d).

Drive the merge-gate against the MockOracle. The three pinned
behaviors are: in-context assertion is mandatory; insufficient
evidence is `void`; noise margin protects against single-exercise
flips.
"""

from __future__ import annotations

import pytest

from littlecoder.oracle import (
    EvidenceLevel,
    Exercise,
    ExerciseOutcome,
    MockOracle,
    ScoredResult,
)
from littlecoder.skills import Skill, build_skill
from littlecoder.validation import (
    ValidationOutcome,
    ValidationResult,
    validate_skill,
)


def _skill(skill_id="testskillid00000", lang="rust", domain="async"):
    return build_skill(
        kind="knowledge",
        cluster_id="cl001",
        tier=0,
        lang=lang,
        domain=domain,
        task_shape="bugfix",
        name="test skill",
        description="when the agent does X, do Y",
        body="# Body\n\nSome content.\n",
        skill_id=skill_id,
    )


def _ex(eid, lang="rust", domain="async"):
    return Exercise(id=eid, lang=lang, domain=domain)


def _oracle_with(*, candidate, baseline, selections=None, exercises=None):
    """Convenience — `candidate`/`baseline` are dicts of id→passed."""
    ex_corpus = exercises or [_ex(f"ex-{i}") for i in range(max(len(candidate), len(baseline)))]
    return MockOracle(
        exercises=ex_corpus,
        candidate_outcomes=candidate,
        baseline_outcomes=baseline,
        selections=selections or {},
    )


# --- happy path: pass --------------------------------------------------


def test_pass_when_candidate_at_or_above_baseline():
    """Identical scores → delta=0 ≥ -margin → pass."""
    skill = _skill()
    selections = {f"ex-{i}": (skill.id,) for i in range(3)}
    oracle = _oracle_with(
        candidate={f"ex-{i}": True for i in range(3)},
        baseline={f"ex-{i}": True for i in range(3)},
        selections=selections,
    )
    result = validate_skill(skill, oracle, target_n=3)
    assert result.outcome is ValidationOutcome.PASS
    assert result.passed is True
    assert result.score_delta == pytest.approx(0.0)


def test_pass_when_candidate_better_than_baseline():
    """Improvement is unambiguous pass."""
    skill = _skill()
    selections = {f"ex-{i}": (skill.id,) for i in range(4)}
    oracle = _oracle_with(
        candidate={"ex-0": True, "ex-1": True, "ex-2": True, "ex-3": True},
        baseline={"ex-0": True, "ex-1": False, "ex-2": False, "ex-3": False},
        selections=selections,
    )
    result = validate_skill(skill, oracle, target_n=4)
    assert result.outcome is ValidationOutcome.PASS
    assert result.score_delta == pytest.approx(0.75)


# --- §8.3 noise margin ------------------------------------------------


def test_pass_when_drop_within_noise_margin():
    """Single-exercise flip on a 10-exercise subset is 10% — within
    a 15% noise margin, should NOT be a regression."""
    skill = _skill()
    selections = {f"ex-{i}": (skill.id,) for i in range(10)}
    oracle = _oracle_with(
        candidate={f"ex-{i}": (i != 0) for i in range(10)},  # 9/10
        baseline={f"ex-{i}": True for i in range(10)},  # 10/10
        selections=selections,
    )
    result = validate_skill(skill, oracle, target_n=10, noise_margin=0.15)
    assert result.outcome is ValidationOutcome.PASS


def test_fail_when_drop_beyond_noise_margin():
    """Big drop — beyond the margin → FAIL."""
    skill = _skill()
    selections = {f"ex-{i}": (skill.id,) for i in range(10)}
    oracle = _oracle_with(
        candidate={f"ex-{i}": (i >= 5) for i in range(10)},  # 5/10
        baseline={f"ex-{i}": True for i in range(10)},  # 10/10
        selections=selections,
    )
    result = validate_skill(skill, oracle, target_n=10, noise_margin=0.05)
    assert result.outcome is ValidationOutcome.FAIL
    assert "regression" in result.reason


# --- §8.4 in-context assertion ----------------------------------------


def test_void_when_skill_not_selected_into_context():
    """A candidate run where the augmenter never chose the skill →
    VOID. The gate measured nothing relevant."""
    skill = _skill()
    # selections map does NOT contain skill.id anywhere
    oracle = _oracle_with(
        candidate={f"ex-{i}": True for i in range(5)},
        baseline={f"ex-{i}": True for i in range(5)},
        selections={f"ex-{i}": ("some-other-skill",) for i in range(5)},
    )
    result = validate_skill(skill, oracle, target_n=5)
    assert result.outcome is ValidationOutcome.VOID
    assert "in-context assertion failed" in result.reason


def test_void_when_no_selections_logged_at_all():
    """An Oracle that doesn't log selections (e.g. a misconfigured
    harness) → VOID. We can't claim the skill was in-context if no
    log exists."""
    skill = _skill()
    oracle = _oracle_with(
        candidate={f"ex-{i}": True for i in range(5)},
        baseline={f"ex-{i}": True for i in range(5)},
        # selections=None ⇒ {} ⇒ empty tuple per exercise
    )
    result = validate_skill(skill, oracle, target_n=5)
    assert result.outcome is ValidationOutcome.VOID


def test_pass_when_skill_selected_only_on_some_exercises():
    """The §8.4 contract requires the skill be selected SOMEWHERE in
    the candidate run — not on EVERY exercise. A run where only 2 of
    5 exercises picked the skill still passes the assertion."""
    skill = _skill()
    selections = {
        "ex-0": (skill.id,),
        "ex-1": ("some-other",),
        "ex-2": (skill.id, "some-other"),
        "ex-3": ("some-other",),
        "ex-4": ("some-other",),
    }
    oracle = _oracle_with(
        candidate={f"ex-{i}": True for i in range(5)},
        baseline={f"ex-{i}": True for i in range(5)},
        selections=selections,
    )
    result = validate_skill(skill, oracle, target_n=5)
    assert result.outcome is ValidationOutcome.PASS


# --- §8.3 insufficient evidence ---------------------------------------


def test_void_when_oracle_reports_insufficient():
    """Below target_n the Oracle returns INSUFFICIENT — gate emits
    VOID, NOT pass."""
    skill = _skill()
    # Corpus has only 2 exercises; target_n=5 → INSUFFICIENT
    oracle = MockOracle(
        exercises=[_ex("a"), _ex("b")],
        candidate_outcomes={"a": True, "b": True},
        baseline_outcomes={"a": True, "b": True},
        selections={"a": (skill.id,), "b": (skill.id,)},
    )
    result = validate_skill(skill, oracle, target_n=5)
    assert result.outcome is ValidationOutcome.VOID
    assert "insufficient evidence" in result.reason


# --- error handling ----------------------------------------------------


def test_error_when_oracle_run_subset_raises():
    """An Oracle internal failure → ERROR, NOT pass. Design §12.10:
    nothing fails open."""
    skill = _skill()

    class BoomOracle:
        def run_subset(self, *args, **kwargs):
            raise RuntimeError("polyglot harness died")

        def baseline_for(self, *args, **kwargs):
            raise RuntimeError("unreachable")

    result = validate_skill(skill, BoomOracle(), target_n=5)
    assert result.outcome is ValidationOutcome.ERROR
    assert "polyglot harness died" in result.reason


def test_error_when_baseline_raises():
    """Candidate succeeded; baseline failed. The candidate is preserved
    on the result for audit."""
    skill = _skill()

    class HalfBoomOracle:
        def run_subset(self, *args, **kwargs):
            return ScoredResult(
                cluster_id="x",
                subset_size=1,
                outcomes=(ExerciseOutcome("a", True, 0.0, (skill.id,)),),
                score=1.0,
                evidence_level=EvidenceLevel.MEASURED,
                duration_seconds=0.0,
            )

        def baseline_for(self, *args, **kwargs):
            raise RuntimeError("baseline crash")

    result = validate_skill(skill, HalfBoomOracle(), target_n=1)
    assert result.outcome is ValidationOutcome.ERROR
    assert result.candidate is not None
    assert "baseline crash" in result.reason
