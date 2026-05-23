"""Polyglot oracle interface — validation harness (design §8.1, Chapter 4 §4c).

The Polyglot benchmark (225 exercises across Rust / Python / Go / JS / C++ /
Java) is the system's model-independent score function. The interface here
abstracts ANY benchmark behind `Oracle.run_subset(...)`, so adding
MultiPL-E or SWE-bench later doesn't require rewriting validation.

Three contracts pinned in this module:

  1. **Subset selection is biased toward the cluster's domain** (design
     §8.1). Pure-random subsets are noisy at small N; biasing on
     domain shrinks variance so a real regression is statistically
     visible at the N we can afford to run.
  2. **`ScoredResult.augmenter_selections` carries the in-context log**
     (design §8.4). A validation pass that didn't select the
     artifact-under-test into context didn't measure anything — the
     validation gate (validation.py) reads this and emits `void`.
  3. **Below N exercises → "insufficient evidence", not pass** (design
     §8.3). The Oracle never lies about how much it measured;
     small-N runs return `EvidenceLevel.INSUFFICIENT` and the gate
     defers rather than merging.

Stage 5 ships the interface + the biased-subset selector + a `MockOracle`
that tests use. The real `PolyglotOracle` is a thin wrapper around a
Polyglot clone (declared in Tool on the `little-coder-polyglot/` volume,
populated by an operator command); its scoring is deferred — what
matters here is that everything downstream (validation gates, efficacy
reversion) is testable against a deterministic mock.
"""

from __future__ import annotations

import dataclasses
import enum
from pathlib import Path
from typing import Iterable, Protocol

from .augmenter import SkillSelection
from .skills import Skill


# --- exercise / subset --------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Exercise:
    """One Polyglot exercise — minimal shape the Oracle interface needs.

    `tags` is the set the biased-subset selector matches on; for the
    canonical Polyglot clone the operator's importer maps the
    exercise's language + topic tag (e.g. `rust`, `lifetimes`) into
    this set. `domain` is one of the cluster-domain tags the judge
    uses; the biased-subset selector prefers `domain` matches over
    pure `lang` matches when they exist."""

    id: str
    lang: str
    domain: str  # e.g. "async", "fs", "parsing"
    tags: frozenset[str] = frozenset()


def select_biased_subset(
    exercises: Iterable[Exercise],
    *,
    cluster_lang: str,
    cluster_domain: str,
    target_n: int,
    rng_seed: int | None = None,
) -> list[Exercise]:
    """Bias the subset toward the cluster's (lang, domain) per design §8.1.

    The bias is "fill from the matching pool first, then top up from
    the lang-matching pool, then the global pool". A deterministic
    order on ties (id sort) makes the selection reproducible — the
    same cluster + same exercise corpus always yields the same subset,
    which the operator can re-run for incident drill-down.

    `rng_seed` is honored when ties need breaking beyond id-sort, but
    Stage-5 default is None (pure id-order) — adding randomness comes
    in §5.8 random-exploration for tier-2 routing rules, not here."""
    pool = list(exercises)
    if target_n <= 0 or not pool:
        return []

    # Tier 1: full match on (lang, domain). The narrowest, most-biased
    # set — design §8.1 prefers these.
    tier1 = sorted(
        (e for e in pool if e.lang == cluster_lang and e.domain == cluster_domain),
        key=lambda e: e.id,
    )
    # Tier 2: lang match only. Cross-domain but in-language; still
    # measures the agent's craft for that language.
    tier2 = sorted(
        (e for e in pool if e.lang == cluster_lang and e.domain != cluster_domain),
        key=lambda e: e.id,
    )
    # Tier 3: rest of the corpus. Used only when the first two can't
    # fill the target — a stratified-fallback rather than the global-
    # random Polyglot subset that would be noisy.
    tier3 = sorted(
        (e for e in pool if e.lang != cluster_lang),
        key=lambda e: e.id,
    )

    out: list[Exercise] = []
    for source in (tier1, tier2, tier3):
        if len(out) >= target_n:
            break
        out.extend(source[: target_n - len(out)])
    return out


# --- result shape --------------------------------------------------------


class EvidenceLevel(enum.Enum):
    """How much evidence did this Oracle run produce?

    - `INSUFFICIENT`: fewer than N exercises ran (could not be selected,
      timed out, harness errored). The validation gate emits `void`
      (design §8.3 — not a pass).
    - `MEASURED`: at least N ran. The gate compares vs baseline and
      emits pass/fail.
    """

    INSUFFICIENT = "insufficient"
    MEASURED = "measured"


@dataclasses.dataclass(frozen=True)
class ExerciseOutcome:
    """One exercise's result. `passed=True` means the agent's solution
    passed the exercise's own test suite. `augmenter_selection` is the
    list of skill ids the augmenter chose for the validation run of
    THIS exercise — the §8.4 in-context assertion reads it to confirm
    the artifact-under-test was actually inlined."""

    exercise_id: str
    passed: bool
    duration_seconds: float
    augmenter_selection: tuple[str, ...] = ()


@dataclasses.dataclass(frozen=True)
class ScoredResult:
    """Outcome of one `Oracle.run_subset` call.

    `score` is the pass rate on the subset (0.0 to 1.0); `evidence_level`
    is the §8.3 sufficiency verdict. `augmenter_selections_by_exercise`
    is the flat in-context log: which skills landed in context for
    which exercises during the validation run."""

    cluster_id: str
    subset_size: int
    outcomes: tuple[ExerciseOutcome, ...]
    score: float
    evidence_level: EvidenceLevel
    duration_seconds: float

    @property
    def passed_count(self) -> int:
        return sum(1 for o in self.outcomes if o.passed)

    def selected_skill_ids(self) -> set[str]:
        """All skill ids the augmenter selected across this run.
        Validation's §8.4 in-context assertion queries this set."""
        ids: set[str] = set()
        for outcome in self.outcomes:
            ids.update(outcome.augmenter_selection)
        return ids


# --- Oracle protocol ----------------------------------------------------


class Oracle(Protocol):
    """The abstraction validation gates depend on. A real implementation
    runs the agent against a benchmark; tests pass `MockOracle`.

    Both `run_subset` (candidate measurement) and `baseline_for` (the
    last-green-tag re-measurement on the SAME biased subset, design
    §8.2) are protocol members so any benchmark (Polyglot, MultiPL-E,
    SWE-bench) can implement both."""

    def run_subset(
        self,
        cluster_lang: str,
        cluster_domain: str,
        *,
        target_n: int,
        skill_under_test: Skill | None = None,
    ) -> ScoredResult:
        """Run the biased subset for one cluster. The Oracle's job is to
        produce `ScoredResult`; the gate decides pass/fail/void based
        on it. `skill_under_test` is the artifact being validated —
        the augmenter MUST be configured to be eligible to select it
        (active or pending; the validation harness flips the
        in-context-assertion flag based on what actually got
        selected)."""
        ...

    def baseline_for(
        self,
        cluster_lang: str,
        cluster_domain: str,
        *,
        target_n: int,
    ) -> ScoredResult:
        """Re-measure the SAME biased subset at the baseline (last green
        tag, design §8.2). A stale global score is NOT a valid
        baseline — every validation re-measures."""
        ...


# --- MockOracle ---------------------------------------------------------


class MockOracle:
    """Deterministic stand-in for tests. Configure with:

      - `exercises`: the corpus the subset selector samples from.
      - `candidate_outcomes`: id → bool (passed?) for the candidate run.
      - `baseline_outcomes`: id → bool for the baseline run.
      - `selections`: id → tuple of skill ids the augmenter picked.

    Anything not pre-configured is `False` (failed) for outcomes and
    `()` (no selection) for augmenter logs."""

    def __init__(
        self,
        *,
        exercises: list[Exercise] | None = None,
        candidate_outcomes: dict[str, bool] | None = None,
        baseline_outcomes: dict[str, bool] | None = None,
        selections: dict[str, tuple[str, ...]] | None = None,
        per_exercise_duration: float = 0.5,
    ) -> None:
        self.exercises = exercises or []
        self.candidate_outcomes = dict(candidate_outcomes or {})
        self.baseline_outcomes = dict(baseline_outcomes or {})
        self.selections = dict(selections or {})
        self.per_exercise_duration = per_exercise_duration
        # Test inspection — what runs has the Oracle been asked for?
        self.calls: list[dict] = []

    def _scored(
        self,
        cluster_lang: str,
        cluster_domain: str,
        target_n: int,
        outcomes_map: dict[str, bool],
        skill_under_test: Skill | None,
    ) -> ScoredResult:
        subset = select_biased_subset(
            self.exercises,
            cluster_lang=cluster_lang,
            cluster_domain=cluster_domain,
            target_n=target_n,
        )
        outcomes = tuple(
            ExerciseOutcome(
                exercise_id=e.id,
                passed=outcomes_map.get(e.id, False),
                duration_seconds=self.per_exercise_duration,
                augmenter_selection=self.selections.get(e.id, ()),
            )
            for e in subset
        )
        score = (
            sum(1 for o in outcomes if o.passed) / len(outcomes) if outcomes else 0.0
        )
        evidence = (
            EvidenceLevel.MEASURED
            if len(subset) >= target_n
            else EvidenceLevel.INSUFFICIENT
        )
        return ScoredResult(
            cluster_id=f"{cluster_lang}|{cluster_domain}",
            subset_size=len(subset),
            outcomes=outcomes,
            score=score,
            evidence_level=evidence,
            duration_seconds=len(subset) * self.per_exercise_duration,
        )

    def run_subset(
        self,
        cluster_lang: str,
        cluster_domain: str,
        *,
        target_n: int,
        skill_under_test: Skill | None = None,
    ) -> ScoredResult:
        self.calls.append(
            {
                "kind": "candidate",
                "lang": cluster_lang,
                "domain": cluster_domain,
                "n": target_n,
                "skill_under_test": skill_under_test.id if skill_under_test else None,
            }
        )
        return self._scored(
            cluster_lang, cluster_domain, target_n, self.candidate_outcomes, skill_under_test
        )

    def baseline_for(
        self,
        cluster_lang: str,
        cluster_domain: str,
        *,
        target_n: int,
    ) -> ScoredResult:
        self.calls.append(
            {
                "kind": "baseline",
                "lang": cluster_lang,
                "domain": cluster_domain,
                "n": target_n,
            }
        )
        return self._scored(
            cluster_lang, cluster_domain, target_n, self.baseline_outcomes, None
        )


# --- PolyglotOracle (operator-populated shell) --------------------------


class PolyglotOracle:
    """Wraps the on-disk Polyglot clone (`little-coder-polyglot/`) as an
    `Oracle`. Stage-5 ships the SHELL — the operator runs
    `lc admin polyglot clone` to populate the volume, then implements
    the per-exercise harness (`_run_exercise`) which is benchmark-
    specific (Polyglot uses `aider` to drive each exercise; the
    harness wires the agent into that path).

    Until the operator populates the corpus, this class behaves as
    `MockOracle(exercises=[])` — `EvidenceLevel.INSUFFICIENT` for every
    call. Validation gates correctly emit `void` in that mode, so
    nothing accidentally merges before the harness is wired."""

    def __init__(self, polyglot_dir: str | Path) -> None:
        self.polyglot_dir = Path(polyglot_dir)

    def _load_corpus(self) -> list[Exercise]:
        """Walk the on-disk Polyglot clone and yield Exercise records.
        Returns [] when the clone hasn't been populated yet — the
        operator action is `lc admin polyglot clone`."""
        # Stage-5: shell. The real implementation walks
        # `<polyglot_dir>/exercises/<lang>/...` and reads each
        # exercise's metadata. Returning [] keeps the surface honest
        # while the operator wires the import.
        return []

    def run_subset(
        self,
        cluster_lang: str,
        cluster_domain: str,
        *,
        target_n: int,
        skill_under_test: Skill | None = None,
    ) -> ScoredResult:
        # No corpus → no measurement. Validation will emit `void`.
        return ScoredResult(
            cluster_id=f"{cluster_lang}|{cluster_domain}",
            subset_size=0,
            outcomes=(),
            score=0.0,
            evidence_level=EvidenceLevel.INSUFFICIENT,
            duration_seconds=0.0,
        )

    def baseline_for(
        self,
        cluster_lang: str,
        cluster_domain: str,
        *,
        target_n: int,
    ) -> ScoredResult:
        return ScoredResult(
            cluster_id=f"{cluster_lang}|{cluster_domain}",
            subset_size=0,
            outcomes=(),
            score=0.0,
            evidence_level=EvidenceLevel.INSUFFICIENT,
            duration_seconds=0.0,
        )
