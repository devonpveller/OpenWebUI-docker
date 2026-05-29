"""Tier-3 PR body templater (design §11.2, Chapter 5 §5f).

When meta opens a tier-3 PR on the private self-improvement remote
(design §11.1 step 6), the PR body is templated MECHANICALLY from four
inputs (design §11.2):

  - §6 justification (cluster + interventions + structural argument)
  - §5.3 cohort evidence (cluster_id + occurrences + lineage)
  - §11.1 validation outputs (exercises run + scores vs baseline + repro)
  - §10 provenance (journal evidence range + sanitization pass)

Mechanical templating means: same inputs → same body. The REVIEWER's
judgement is the qualitative part; the template is the consistent
container.

This module composes the Markdown. The actual `gh pr create` call
lives downstream (Stage-§5f-host-integration); the sanitization-
before-post check (design §10.2 — filter failure aborts the PR) lives
HERE, in `build_pr_body_safe` which raises rather than returning a
not-yet-filtered string.
"""

from __future__ import annotations

import dataclasses

from .judge import Tier3Justification
from .repro import ReproSpec
from .sanitize import Sanitizer, SanitizerError


@dataclasses.dataclass(frozen=True)
class CohortEvidence:
    """The §5.3 cohort-evidence block. Built from the cohort store at
    PR-draft time by the caller."""

    cluster_id: str
    cluster_label: str
    observed: int
    inherited: int
    top_repos: list[tuple[str, int]]
    journal_evidence_range: str  # e.g. "ts 2026-05-23T00:00:00Z to ..."


@dataclasses.dataclass(frozen=True)
class ValidationEvidence:
    """The §11.1 candidate-validation outputs block."""

    subset_size: int
    candidate_score: float
    baseline_score: float
    target_n: int
    noise_margin: float
    repro_path: str
    repro_passed: bool


_TEMPLATE = """\
# Tier-3 self-modification — cluster `{cluster_id}`

> Generated mechanically per design §11.2. Reviewer judgement decides
> merge; the template is the container, not the verdict.

## (§6) Justification

**Cluster persistence**

{justification_cluster_persistence}

**Interventions tried**

{justification_interventions}

**(§6.3) No-skill argument**

{justification_no_skill_argument}

**Proposed change**

- Code surface: `{justification_code_surface}`
- {justification_proposed_change}

**Expected effect**

{justification_expected_effect}

## (§5.3) Cohort evidence

- Cluster: `{cluster_id}` — _{cluster_label}_
- Observed: **{observed}** occurrences (inherited: {inherited})
- Top repos: {top_repos_rendered}
- Journal evidence range: {journal_evidence_range}

## (§11.1) Candidate validation

- Subset size: {subset_size} (target N: {target_n})
- Candidate score: **{candidate_score:.3f}**
- Baseline score: {baseline_score:.3f}
- Delta: **{delta:+.3f}** (noise margin: {noise_margin:.3f})
- Repro persisted at: `{repro_path}`
- Repro result on candidate: {repro_result}

## (§10) Provenance

- All evidence travels through the §10.2 sanitization filter (this
  body has been filter-applied; a `SanitizerError` aborts the PR
  per design §1 — never "send anyway").
- Journal range: {journal_evidence_range}

---
_Reviewer: this PR is a tier-3 change — the agent's OWN code. Read
the §6(3) argument carefully. If you can think of a skill that COULD
have closed the gap, reject and ask meta to draft it instead._
"""


def _render_interventions(interventions) -> str:
    if not interventions:
        return "_(none — review §6(3) carefully)_"
    lines = []
    for entry in interventions:
        # Accept either pydantic InterventionRecord or plain dicts so
        # tests can pass dicts without instantiating the full chain.
        if hasattr(entry, "model_dump"):
            d = entry.model_dump()
        else:
            d = dict(entry)
        lines.append(
            f"- `{d['skill_id']}` (tier-{d['tier']}, {d['kind']}): "
            f"{d['why_failed']}"
        )
    return "\n".join(lines)


def _render_top_repos(top_repos: list[tuple[str, int]]) -> str:
    if not top_repos:
        return "_(none recorded)_"
    return ", ".join(f"`{repo}`={n}" for repo, n in top_repos)


def build_pr_body(
    justification: Tier3Justification,
    cohort_evidence: CohortEvidence,
    validation_evidence: ValidationEvidence,
) -> str:
    """Compose the PR body Markdown. NEVER applies sanitization — call
    `build_pr_body_safe` for the safe path (design §10.2 demands the
    sanitization pass on every outbound, and a PR body to a remote
    counts)."""
    return _TEMPLATE.format(
        cluster_id=cohort_evidence.cluster_id,
        cluster_label=cohort_evidence.cluster_label,
        observed=cohort_evidence.observed,
        inherited=cohort_evidence.inherited,
        top_repos_rendered=_render_top_repos(cohort_evidence.top_repos),
        journal_evidence_range=cohort_evidence.journal_evidence_range,
        justification_cluster_persistence=justification.cluster_persistence or "_(empty)_",
        justification_interventions=_render_interventions(justification.interventions_tried),
        justification_no_skill_argument=justification.no_skill_argument,
        justification_code_surface=justification.code_surface,
        justification_proposed_change=justification.proposed_change,
        justification_expected_effect=justification.expected_effect or "_(empty)_",
        subset_size=validation_evidence.subset_size,
        target_n=validation_evidence.target_n,
        candidate_score=validation_evidence.candidate_score,
        baseline_score=validation_evidence.baseline_score,
        delta=validation_evidence.candidate_score - validation_evidence.baseline_score,
        noise_margin=validation_evidence.noise_margin,
        repro_path=validation_evidence.repro_path,
        repro_result=(
            "PASSED on candidate"
            if validation_evidence.repro_passed
            else "**FAILED on candidate — do NOT merge**"
        ),
    )


def build_pr_body_safe(
    justification: Tier3Justification,
    cohort_evidence: CohortEvidence,
    validation_evidence: ValidationEvidence,
    sanitizer: Sanitizer,
) -> str:
    """Build + sanitize. Raises `SanitizerError` if the filter rejects;
    design §10.2 mandates abort-on-filter-failure for every outbound.
    The caller (the PR opener) must NEVER fall back to the unfiltered
    body."""
    raw = build_pr_body(justification, cohort_evidence, validation_evidence)
    result = sanitizer.apply(raw)
    return result.cleaned
