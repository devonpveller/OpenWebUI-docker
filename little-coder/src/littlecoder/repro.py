"""Tier-3 repro persistence (design §11.1 step 5, Chapter 5 §5f).

When a tier-3 candidate passes validation, we persist an EXECUTABLE
reproducer to `little-coder-cohorts/repro/<artifact_id>/` BEFORE
candidate teardown. The repro is the canonical post-deploy smoke
test (design §11.1 step 8 — re-run the persisted §6 repro against the
rebuilt active to confirm the deployed binary is what got validated).

What lands in the repro directory:
  - `repro.sh` — executable script that drives the agent to re-create
    the cluster's signal. Operator-callable directly during a rollback
    drill; meta-callable as the smoke test after a deploy.
  - `metadata.json` — cluster_id, justification snippet, validation
    result reference, the §6 argument. The post-deploy verification
    reads this to know what `repro.sh` is asserting.

This module is pure file IO + atomic writes. The repro CONTENT (what
the script does) is written by the candidate validation flow in §5e —
this module just persists it.
"""

from __future__ import annotations

import dataclasses
import json
import os
import stat
from pathlib import Path

from . import SCHEMA_VERSION
from .journals import utc_now


@dataclasses.dataclass(frozen=True)
class ReproSpec:
    """What gets persisted. `script_body` is the shell script the
    repro will run; `metadata` is the audit context the post-deploy
    smoke test reads."""

    artifact_id: str
    cluster_id: str
    script_body: str
    cluster_label: str
    cluster_discriminator: str
    justification_summary: str
    candidate_score: float
    baseline_score: float
    target_n: int
    code_surface: str  # "node" | "python" | "both" — from the §6 justification


def repro_dir(cohorts_dir: Path | str, artifact_id: str) -> Path:
    return Path(cohorts_dir) / "repro" / artifact_id


def script_path(cohorts_dir: Path | str, artifact_id: str) -> Path:
    return repro_dir(cohorts_dir, artifact_id) / "repro.sh"


def metadata_path(cohorts_dir: Path | str, artifact_id: str) -> Path:
    return repro_dir(cohorts_dir, artifact_id) / "metadata.json"


def write_repro(cohorts_dir: Path | str, spec: ReproSpec) -> Path:
    """Persist the repro. Returns the directory path. Atomic per-file
    (writes `.tmp` + rename), so a partially-written repro never
    appears to the post-deploy verifier.

    The script is written with `0o755` so the operator can invoke it
    directly (`./repro.sh`); metadata is `0o644`."""
    target_dir = repro_dir(cohorts_dir, spec.artifact_id)
    target_dir.mkdir(parents=True, exist_ok=True)

    script = script_path(cohorts_dir, spec.artifact_id)
    meta = metadata_path(cohorts_dir, spec.artifact_id)

    # Atomic-write the script.
    tmp_script = script.with_suffix(script.suffix + ".tmp")
    tmp_script.write_text(spec.script_body, encoding="utf-8")
    # Make executable BEFORE rename so the visible file is always
    # ready-to-run.
    current = tmp_script.stat().st_mode
    tmp_script.chmod(
        current | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
    )
    tmp_script.replace(script)

    # Atomic-write the metadata.
    metadata_dict = {
        "schema_version": SCHEMA_VERSION,
        "ts": utc_now(),
        "artifact_id": spec.artifact_id,
        "cluster_id": spec.cluster_id,
        "cluster_label": spec.cluster_label,
        "cluster_discriminator": spec.cluster_discriminator,
        "justification_summary": spec.justification_summary,
        "candidate_score": spec.candidate_score,
        "baseline_score": spec.baseline_score,
        "target_n": spec.target_n,
        "code_surface": spec.code_surface,
    }
    tmp_meta = meta.with_suffix(meta.suffix + ".tmp")
    tmp_meta.write_text(json.dumps(metadata_dict, indent=2), encoding="utf-8")
    tmp_meta.replace(meta)
    return target_dir


def load_repro_metadata(cohorts_dir: Path | str, artifact_id: str) -> dict:
    """Read back the metadata. The post-deploy verifier uses this to
    know what cluster + code surface the repro is asserting on."""
    return json.loads(
        metadata_path(cohorts_dir, artifact_id).read_text(encoding="utf-8")
    )
