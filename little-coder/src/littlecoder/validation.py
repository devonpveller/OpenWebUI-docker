"""Validation gates for tier-0/1 skill artifacts (design §8, Chapter 4 §4d).

A skill that the judge drafted (Chapter 4 §4e) doesn't auto-merge — it
passes through these gates first, and only those that PASS are
candidates for operator approval (§4f).

Three gates pinned in this module:

  1. **In-context assertion (design §8.4).** If the augmenter didn't
     select the artifact-under-test during the validation run, the gate
     returns `VOID` — not `pass`. A void result means "we measured
     nothing"; the validation must be re-run with a fixed augmenter or
     the artifact retired. No-context = no-evidence = no-merge.
  2. **Noise margin (design §8.3).** A single-exercise flip inside the
     measured noise margin is NOT a regression. The gate compares
     (candidate_score - baseline_score) against the margin; only a
     drop BEYOND the margin counts.
  3. **Insufficient-evidence handling (design §8.3).** Below the
     configured `min_subset_n`, the Oracle returns `INSUFFICIENT` and
     the gate emits `VOID` too — never a `pass` based on "we ran 2
     exercises and they both passed".

Per design §8.5 efficacy reversion lives separately in `efficacy.py`;
this module handles the merge-gate, not the keep-gate.
"""

from __future__ import annotations

import dataclasses
import enum

from .oracle import EvidenceLevel, Oracle, ScoredResult
from .skills import Skill


class ValidationOutcome(enum.Enum):
    """The four verdicts a gate can emit. Order matters in the rendered
    surface: a `VOID` is more cautious than a `FAIL` (one says "we
    measured nothing", the other says "we measured worse than
    baseline")."""

    PASS = "pass"  # candidate ≥ baseline - margin; merge candidate
    FAIL = "fail"  # candidate dropped beyond noise margin
    VOID = "void"  # insufficient evidence OR in-context assertion failed
    ERROR = "error"  # oracle / validation harness errored


@dataclasses.dataclass(frozen=True)
class ValidationResult:
    """The gate's verdict. `outcome` is the merge gate; `reason` carries
    the why (rendered on the operator approval surface). The two
    `ScoredResult`s are preserved for audit + drill-down."""

    skill_id: str
    outcome: ValidationOutcome
    reason: str
    candidate: ScoredResult | None = None
    baseline: ScoredResult | None = None
    noise_margin: float = 0.0

    @property
    def passed(self) -> bool:
        return self.outcome is ValidationOutcome.PASS

    @property
    def is_void(self) -> bool:
        return self.outcome is ValidationOutcome.VOID

    @property
    def score_delta(self) -> float | None:
        """Candidate-minus-baseline, or None when either run is missing."""
        if self.candidate is None or self.baseline is None:
            return None
        return self.candidate.score - self.baseline.score


# --- the gate -----------------------------------------------------------


def validate_skill(
    skill: Skill,
    oracle: Oracle,
    *,
    target_n: int = 10,
    noise_margin: float = 0.05,
) -> ValidationResult:
    """Run the validation gates for one drafted skill.

    Steps (in order):
      1. Run the Oracle's candidate measurement (with the skill in
         context) and baseline measurement (without it).
      2. Check the in-context assertion (§8.4) — was the artifact
         actually selected during the candidate run?
      3. Check evidence sufficiency (§8.3) — did the Oracle return
         MEASURED on BOTH runs?
      4. Compare scores: pass if `candidate >= baseline - noise_margin`;
         fail if the drop is beyond margin.

    Failures earlier short-circuit later checks — a VOID from in-context
    assertion doesn't get a "but it would have passed" override.

    `target_n` defaults to 10 (open item #1 placeholder; preflight sets
    the real value); `noise_margin` defaults to 0.05 (5pp of pass rate).
    Both are tunables — the daemon wires them from config."""
    cluster_lang = skill.frontmatter.lang
    cluster_domain = skill.frontmatter.domain

    try:
        candidate = oracle.run_subset(
            cluster_lang,
            cluster_domain,
            target_n=target_n,
            skill_under_test=skill,
        )
    except Exception as exc:
        return ValidationResult(
            skill_id=skill.id,
            outcome=ValidationOutcome.ERROR,
            reason=f"oracle.run_subset raised: {exc}",
        )
    try:
        baseline = oracle.baseline_for(
            cluster_lang, cluster_domain, target_n=target_n
        )
    except Exception as exc:
        return ValidationResult(
            skill_id=skill.id,
            outcome=ValidationOutcome.ERROR,
            reason=f"oracle.baseline_for raised: {exc}",
            candidate=candidate,
        )

    # §8.4 in-context assertion. The augmenter MUST have selected this
    # artifact during the candidate run — otherwise the run measured
    # nothing relevant.
    if skill.id not in candidate.selected_skill_ids():
        return ValidationResult(
            skill_id=skill.id,
            outcome=ValidationOutcome.VOID,
            reason=(
                f"in-context assertion failed: skill {skill.id} was NOT "
                f"selected by the augmenter during the candidate run "
                f"(design §8.4 — the gate measured nothing)"
            ),
            candidate=candidate,
            baseline=baseline,
            noise_margin=noise_margin,
        )

    # §8.3 evidence sufficiency. Either side INSUFFICIENT → VOID.
    if (
        candidate.evidence_level is EvidenceLevel.INSUFFICIENT
        or baseline.evidence_level is EvidenceLevel.INSUFFICIENT
    ):
        return ValidationResult(
            skill_id=skill.id,
            outcome=ValidationOutcome.VOID,
            reason=(
                f"insufficient evidence: subset "
                f"{candidate.subset_size}/{baseline.subset_size} ran "
                f"(target_n={target_n})"
            ),
            candidate=candidate,
            baseline=baseline,
            noise_margin=noise_margin,
        )

    delta = candidate.score - baseline.score
    if delta < -noise_margin:
        return ValidationResult(
            skill_id=skill.id,
            outcome=ValidationOutcome.FAIL,
            reason=(
                f"regression: candidate={candidate.score:.3f} below "
                f"baseline={baseline.score:.3f} by {-delta:.3f} "
                f"(noise margin {noise_margin:.3f})"
            ),
            candidate=candidate,
            baseline=baseline,
            noise_margin=noise_margin,
        )

    return ValidationResult(
        skill_id=skill.id,
        outcome=ValidationOutcome.PASS,
        reason=(
            f"no regression: candidate={candidate.score:.3f} vs "
            f"baseline={baseline.score:.3f} (delta={delta:+.3f}, margin "
            f"{noise_margin:.3f})"
        ),
        candidate=candidate,
        baseline=baseline,
        noise_margin=noise_margin,
    )
