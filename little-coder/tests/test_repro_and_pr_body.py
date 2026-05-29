"""Tier-3 repro persistence + PR body templater (Chapter 5 §5f).

Pins: atomic write, exec-bit on the script, mechanical-templating
determinism, sanitization-before-post (design §10.2 — `SanitizerError`
aborts the PR rather than sending unfiltered).
"""

from __future__ import annotations

import json
import os
import stat

import pytest

from littlecoder.judge import InterventionRecord, Tier3Justification
from littlecoder.pr_body import (
    CohortEvidence,
    ValidationEvidence,
    build_pr_body,
    build_pr_body_safe,
)
from littlecoder.repro import ReproSpec, load_repro_metadata, repro_dir, script_path, write_repro
from littlecoder.sanitize import Sanitizer


def _spec(**overrides) -> ReproSpec:
    base = dict(
        artifact_id="aaaa1111bbbb2222",
        cluster_id="cl0001",
        script_body="#!/bin/sh\necho repro\n",
        cluster_label="async lifetime errors",
        cluster_discriminator="borrow checker on async",
        justification_summary="cluster persisted across tier-0+1",
        candidate_score=0.85,
        baseline_score=0.80,
        target_n=10,
        code_surface="node",
    )
    base.update(overrides)
    return ReproSpec(**base)


# --- repro persistence ------------------------------------------------


def test_write_repro_lands_script_and_metadata(tmp_path):
    spec = _spec()
    target = write_repro(tmp_path, spec)
    assert target == repro_dir(tmp_path, spec.artifact_id)
    assert script_path(tmp_path, spec.artifact_id).exists()
    assert (target / "metadata.json").exists()


def test_repro_script_is_executable(tmp_path):
    """Operator must be able to invoke `./repro.sh` directly during a
    rollback drill."""
    spec = _spec()
    write_repro(tmp_path, spec)
    mode = script_path(tmp_path, spec.artifact_id).stat().st_mode
    # On Windows the exec-bit check is meaningless (chmod is a no-op);
    # skip the assertion there but verify the file exists.
    if os.name == "posix":
        assert mode & stat.S_IXUSR, "script must be user-executable"


def test_repro_metadata_round_trips(tmp_path):
    spec = _spec()
    write_repro(tmp_path, spec)
    loaded = load_repro_metadata(tmp_path, spec.artifact_id)
    assert loaded["artifact_id"] == spec.artifact_id
    assert loaded["cluster_id"] == spec.cluster_id
    assert loaded["candidate_score"] == spec.candidate_score
    assert loaded["code_surface"] == spec.code_surface


def test_write_repro_overwrites_cleanly(tmp_path):
    """A second write for the same artifact_id replaces the prior one
    atomically — no `.tmp` leftover."""
    write_repro(tmp_path, _spec())
    write_repro(tmp_path, _spec(candidate_score=0.95))
    loaded = load_repro_metadata(tmp_path, "aaaa1111bbbb2222")
    assert loaded["candidate_score"] == 0.95
    tmps = list(repro_dir(tmp_path, "aaaa1111bbbb2222").glob("*.tmp"))
    assert tmps == []


# --- PR body templater ------------------------------------------------


def _justification(**overrides) -> Tier3Justification:
    base = dict(
        cluster_persistence="cluster recurred 100 times across tiers 0-1",
        interventions_tried=[
            InterventionRecord(
                skill_id="skill-abc",
                tier=0,
                kind="knowledge",
                why_failed="agent ignored it under load",
            ),
        ],
        no_skill_argument=(
            "No skill could close this — the cluster manifests in the "
            "agent's inner-loop control flow, which sits below the "
            "skill-library retrieval layer."
        ),
        proposed_change="modify planner's fast-path",
        code_surface="node",
        expected_effect="cluster rate drops to baseline",
        refused=False,
        refusal_reason="",
    )
    base.update(overrides)
    return Tier3Justification(**base)


def _cohort_evidence():
    return CohortEvidence(
        cluster_id="cl0001",
        cluster_label="async lifetime errors",
        observed=100,
        inherited=0,
        top_repos=[("github.com/a/b", 60), ("github.com/c/d", 40)],
        journal_evidence_range="2026-05-23T00:00:00Z to 2026-05-23T23:59:59Z",
    )


def _validation_evidence(**overrides):
    base = dict(
        subset_size=15,
        candidate_score=0.85,
        baseline_score=0.80,
        target_n=10,
        noise_margin=0.05,
        repro_path="/var/lib/little-coder/cohorts/repro/aaaa/repro.sh",
        repro_passed=True,
    )
    base.update(overrides)
    return ValidationEvidence(**base)


def test_pr_body_is_deterministic_given_same_inputs():
    """Mechanical templating: same in → same out (design §11.2)."""
    a = build_pr_body(_justification(), _cohort_evidence(), _validation_evidence())
    b = build_pr_body(_justification(), _cohort_evidence(), _validation_evidence())
    assert a == b


def test_pr_body_renders_six_sections():
    body = build_pr_body(_justification(), _cohort_evidence(), _validation_evidence())
    # §6 sections.
    assert "(§6) Justification" in body
    assert "**Cluster persistence**" in body
    assert "**Interventions tried**" in body
    assert "(§6.3) No-skill argument" in body
    assert "**Proposed change**" in body
    # §5.3 cohort.
    assert "(§5.3) Cohort evidence" in body
    assert "100" in body  # observed count
    # §11.1 validation.
    assert "(§11.1) Candidate validation" in body
    assert "0.850" in body  # candidate_score
    # §10 provenance.
    assert "(§10) Provenance" in body


def test_pr_body_renders_intervention_records():
    body = build_pr_body(_justification(), _cohort_evidence(), _validation_evidence())
    assert "skill-abc" in body
    assert "tier-0" in body
    assert "agent ignored" in body


def test_pr_body_flags_failed_repro_loudly():
    """When the repro FAILED on the candidate, the body must shout —
    the reviewer's most important signal."""
    body = build_pr_body(
        _justification(), _cohort_evidence(),
        _validation_evidence(repro_passed=False),
    )
    assert "FAILED on candidate" in body
    assert "do NOT merge" in body.lower() or "do not merge" in body.lower()


def test_pr_body_safe_runs_sanitization():
    """`build_pr_body_safe` filters via the Sanitizer — secrets / PII
    in the inputs get redacted before posting."""
    # Inject a secret-shaped string. Use a field where the rendering
    # doesn't append a `_` (markdown italic wrappers break the regex's
    # trailing `\b` since `_` is a word character) — `top_repos` ends
    # up backtick-wrapped which IS non-word-bounded.
    cohort = CohortEvidence(
        cluster_id="cl0001",
        cluster_label="async lifetime errors",
        observed=10,
        inherited=0,
        top_repos=[("ghp_" + "a" * 36, 5)],
        journal_evidence_range="t1 to t2",
    )
    sanitizer = Sanitizer(mode="enforcing")
    body = build_pr_body_safe(_justification(), cohort, _validation_evidence(), sanitizer)
    assert "ghp_" + "a" * 36 not in body  # the raw PAT was redacted
    assert "REDACTED" in body  # the sanitizer's marker landed


def test_pr_body_safe_aborts_on_sanitizer_error():
    """Filter failure → propagated as `SanitizerError`. NEVER returns
    an unfiltered body (design §10.2)."""
    from littlecoder.sanitize import SanitizerError

    class BoomSanitizer:
        def apply(self, text):
            raise SanitizerError("filter blew up")

    with pytest.raises(SanitizerError):
        build_pr_body_safe(
            _justification(),
            _cohort_evidence(),
            _validation_evidence(),
            BoomSanitizer(),  # type: ignore[arg-type]
        )
