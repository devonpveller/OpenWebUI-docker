"""Efficacy reversion (design §8.5, Chapter 4 §4d).

Pins: the indistinguishability heuristic, the window-too-short
abstention, and the retire-walks-skill-library + flips-status path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from littlecoder.cohorts import ClusterCounters
from littlecoder.efficacy import (
    DEFAULT_INDISTINGUISHABLE_TOLERANCE,
    DEFAULT_MIN_WINDOW,
    EfficacyWindow,
    RetirementDecision,
    build_window,
    evaluate_active_skills,
    is_ineffective,
    retire_ineffective_skills,
)
from littlecoder.skills import build_skill, iter_skills, write_skill


# --- is_ineffective ----------------------------------------------------


def _window(*, pre=10, post=8, pre_tasks=100, post_tasks=100):
    return EfficacyWindow(
        skill_id="skill-x",
        cluster_id="cl001",
        pre_count=pre,
        post_count=post,
        pre_window_tasks=pre_tasks,
        post_window_tasks=post_tasks,
    )


def test_ineffective_when_post_rate_indistinguishable_from_pre():
    """0.10/task pre, 0.095/task post = ~5% improvement; below 10%
    tolerance → ineffective."""
    w = _window(pre=10, post=9, pre_tasks=100, post_tasks=100)
    assert is_ineffective(w, min_window=20, tolerance=0.10) is True


def test_effective_when_post_rate_clearly_lower():
    """50% improvement is well above any reasonable tolerance."""
    w = _window(pre=10, post=5, pre_tasks=100, post_tasks=100)
    assert is_ineffective(w, min_window=20, tolerance=0.10) is False


def test_short_window_returns_false_no_judgement():
    """Below `min_window` post tasks → cannot judge. Returning False
    here keeps the artifact ALIVE; we don't retire on noise."""
    w = _window(post_tasks=5)
    assert is_ineffective(w, min_window=20) is False


def test_zero_pre_rate_only_ineffective_when_post_also_zero():
    """No pre-intervention rate — we can't claim improvement. Only
    retire if post is also zero AND the window is long."""
    w_active = EfficacyWindow(
        skill_id="x",
        cluster_id="c",
        pre_count=0,
        post_count=3,
        pre_window_tasks=100,
        post_window_tasks=100,
    )
    assert is_ineffective(w_active, min_window=20) is False

    w_dormant = EfficacyWindow(
        skill_id="x",
        cluster_id="c",
        pre_count=0,
        post_count=0,
        pre_window_tasks=100,
        post_window_tasks=100,
    )
    assert is_ineffective(w_dormant, min_window=20) is True


def test_tolerance_is_relative_to_pre_rate():
    """Tolerance is a fraction of the pre-rate. At pre-rate=0.01,
    tolerance=0.10 means post must drop below 0.009 to be effective."""
    # pre-rate=0.01 (10/1000), tolerance=0.1 → need post-rate < 0.009
    # post=8/1000=0.008 → effective
    w_effective = _window(pre=10, post=8, pre_tasks=1000, post_tasks=1000)
    assert is_ineffective(w_effective, min_window=20, tolerance=0.10) is False
    # post=10/1000=0.01 → same rate, ineffective
    w_ineffective = _window(pre=10, post=10, pre_tasks=1000, post_tasks=1000)
    assert is_ineffective(w_ineffective, min_window=20, tolerance=0.10) is True


# --- build_window helper ------------------------------------------------


def test_build_window_subtracts_pre_count_from_post(tmp_path):
    """`post_count` on the window is occurrences observed SINCE merge —
    not the cumulative count. The cohort store's counter is cumulative;
    we subtract the snapshotted pre."""
    skill = build_skill(
        kind="knowledge",
        cluster_id="cl001",
        tier=0,
        lang="rust",
        domain="async",
        task_shape="bugfix",
        name="x" * 5,
        description="y" * 5,
        body="z" * 20,
        status="active",
    )
    counters = {"cl001": ClusterCounters(cluster_id="cl001", observed=42)}
    window = build_window(
        skill,
        counters,
        pre_count=30,
        pre_window_tasks=500,
        current_task_total=200,
    )
    assert window.pre_count == 30
    assert window.post_count == 12  # 42 - 30
    assert window.post_window_tasks == 200


# --- evaluate_active_skills + retire ----------------------------------


def test_evaluate_returns_no_snapshot_reason_when_unknown(tmp_path):
    """Pre Stage-6 snapshotting, no skill has a window. The evaluator
    returns each skill as 'kept' with a clear reason."""
    skill = build_skill(
        kind="knowledge",
        cluster_id="cl001",
        tier=0,
        lang="rust",
        domain="async",
        task_shape="bugfix",
        name="x" * 5,
        description="y" * 5,
        body="z" * 20,
        status="active",
    )
    write_skill(tmp_path, skill)

    decisions = evaluate_active_skills(
        tmp_path,
        counters={"cl001": ClusterCounters("cl001", observed=10)},
    )
    assert len(decisions) == 1
    assert decisions[0].retired is False
    assert "no snapshot" in decisions[0].reason


def test_evaluate_marks_ineffective_when_window_indicates(tmp_path):
    """With a snapshot showing 30 pre + 100 tasks, and current state
    showing 42 cumulative (12 post on 200 tasks → 0.06/task), and
    pre rate 30/100=0.30 → big improvement; effective."""
    skill = build_skill(
        kind="knowledge",
        cluster_id="cl001",
        tier=0,
        lang="rust",
        domain="async",
        task_shape="bugfix",
        name="x" * 5,
        description="y" * 5,
        body="z" * 20,
        status="active",
    )
    write_skill(tmp_path, skill)
    decisions = evaluate_active_skills(
        tmp_path,
        counters={"cl001": ClusterCounters("cl001", observed=42)},
        snapshots={skill.id: (30, 100)},
        current_task_total=200,
    )
    assert decisions[0].retired is False  # post-rate dropped sharply


def test_evaluate_marks_ineffective_when_no_improvement(tmp_path):
    """Same pre and post rate ⇒ ineffective."""
    skill = build_skill(
        kind="knowledge",
        cluster_id="cl001",
        tier=0,
        lang="rust",
        domain="async",
        task_shape="bugfix",
        name="x" * 5,
        description="y" * 5,
        body="z" * 20,
        status="active",
    )
    write_skill(tmp_path, skill)
    # pre: 10/100=0.10; post: 20/200=0.10 (same).
    decisions = evaluate_active_skills(
        tmp_path,
        counters={"cl001": ClusterCounters("cl001", observed=30)},
        snapshots={skill.id: (10, 100)},
        current_task_total=200,
    )
    assert decisions[0].retired is True
    assert "ineffective" in decisions[0].reason


def test_retire_flips_status_for_ineffective(tmp_path):
    """`retire_ineffective_skills` actually writes the status flip;
    `evaluate_active_skills` only decides."""
    skill = build_skill(
        kind="knowledge",
        cluster_id="cl001",
        tier=0,
        lang="rust",
        domain="async",
        task_shape="bugfix",
        name="x" * 5,
        description="y" * 5,
        body="z" * 20,
        status="active",
    )
    write_skill(tmp_path, skill)
    decisions = retire_ineffective_skills(
        tmp_path,
        counters={"cl001": ClusterCounters("cl001", observed=30)},
        snapshots={skill.id: (10, 100)},
        current_task_total=200,
    )
    assert any(d.retired for d in decisions)
    # Reload — the status is now 'retired' on disk.
    remaining_active = list(iter_skills(tmp_path, status="active"))
    assert remaining_active == []
    retired = list(iter_skills(tmp_path, status="retired"))
    assert len(retired) == 1
    assert retired[0].id == skill.id


def test_snapshots_from_audit_reads_approve_decisions(tmp_path):
    """The audit log has approve_decision rows; the reader pulls
    (observed_at_approve, tasks_at_approve) per artifact."""
    import json

    from littlecoder.efficacy import snapshots_from_audit

    audit = tmp_path / "audit.jsonl"
    rows = [
        {
            "ts": "t1",
            "event": "approve_decision",
            "actor": "operator",
            "detail": {
                "artifact_id": "skill-1",
                "cluster_id": "c1",
                "tier": 0,
                "kind": "knowledge",
                "observed_at_approve": 7,
                "tasks_at_approve": 100,
            },
            "schema_version": 1,
        },
        {
            "ts": "t2",
            "event": "shutdown",  # noise row
            "actor": "system",
            "detail": {},
            "schema_version": 1,
        },
        {
            "ts": "t3",
            "event": "approve_decision",
            "actor": "operator",
            "detail": {
                "artifact_id": "skill-2",
                "decision": "reject",  # rejects must be excluded
                "prior_status": "active",
                "observed_at_approve": 50,
                "tasks_at_approve": 200,
            },
            "schema_version": 1,
        },
    ]
    audit.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")

    out = snapshots_from_audit(audit)
    assert out == {"skill-1": (7, 100)}


def test_snapshots_from_audit_empty_when_no_audit_log(tmp_path):
    from littlecoder.efficacy import snapshots_from_audit

    assert snapshots_from_audit(tmp_path / "missing.jsonl") == {}


def test_snapshots_from_audit_skips_legacy_rows_without_snapshot(tmp_path):
    """Approve rows written before Stage-8 don't carry the snapshot
    fields — the reader must skip them silently (they can't be measured
    against, but they shouldn't crash the read)."""
    import json

    from littlecoder.efficacy import snapshots_from_audit

    audit = tmp_path / "audit.jsonl"
    audit.write_text(
        json.dumps(
            {
                "ts": "t",
                "event": "approve_decision",
                "actor": "operator",
                "detail": {"artifact_id": "old-skill"},  # no snapshot fields
                "schema_version": 1,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    assert snapshots_from_audit(audit) == {}


def test_retire_no_op_when_no_snapshots(tmp_path):
    """Without snapshots, nothing gets retired — the safe default."""
    skill = build_skill(
        kind="knowledge",
        cluster_id="cl001",
        tier=0,
        lang="rust",
        domain="async",
        task_shape="bugfix",
        name="x" * 5,
        description="y" * 5,
        body="z" * 20,
        status="active",
    )
    write_skill(tmp_path, skill)
    retire_ineffective_skills(tmp_path, counters={})
    actives = list(iter_skills(tmp_path, status="active"))
    assert len(actives) == 1
    assert actives[0].id == skill.id
