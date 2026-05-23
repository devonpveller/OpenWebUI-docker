"""Efficacy reversion (design §8.5, Chapter 4 §4d).

No-regression at merge time isn't enough — it justifies merging, not
keeping. After a tier-0 / tier-1 artifact has been live for the cluster's
quarantine window, we measure whether the post-intervention rate of
cluster occurrences is statistically distinguishable from the pre-
intervention rate. If indistinguishable → the artifact is `ineffective`
and auto-retired on the next iteration. Retirement is journaled to
`audit.jsonl`.

What this module does:
  - `EfficacyWindow` — the pre/post counter pair for one merged
    artifact's cluster.
  - `is_ineffective(window, ...)` — pure judgement function.
  - `retire_ineffective_skills(skill_dir, store, since)` — walks active
    skills, evaluates each, flips ineffective ones to `retired` via
    `skills.flip_status`.

What this module does NOT do:
  - It does not gate merges (that is `validation.py`).
  - It does not move counters around (cohorts.py owns the projection).
  - It does not write audit records — the caller (meta) does, so the
    audit log carries the iteration context.

Stage-4 ships the policy; Stage 5+ (Polyglot oracle live + real
journals) is what makes the windows non-trivial.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

from .cohorts import ClusterCounters, CohortStore
from .skills import Skill, SkillStatus, flip_status, iter_skills


# Default minimum window length (occurrences). Below this we never
# declare ineffective — too noisy. Preflight derives the real value per
# cluster (design §13); this is a sane lower floor.
DEFAULT_MIN_WINDOW = 20
# Default indistinguishability tolerance. Post-rate ≥ pre-rate * (1 - tol)
# = still ineffective. Preflight tunes this; 0.10 is a starting point
# (artifact dropped fewer than 10% of recurrences → not paying off).
DEFAULT_INDISTINGUISHABLE_TOLERANCE = 0.10


@dataclasses.dataclass(frozen=True)
class EfficacyWindow:
    """One artifact's pre/post observation counts.

    `pre_count` is the cluster's `observed` at the moment the artifact
    was merged; `post_count` is the `observed` recorded since merge.
    The runner derives both from the cohort store + the audit log's
    `approve_decision` (Stage-6 — when merging records the snapshot).
    """

    skill_id: str
    cluster_id: str
    pre_count: int
    post_count: int
    pre_window_tasks: int  # number of tasks observed pre-intervention
    post_window_tasks: int  # number of tasks observed post-intervention

    @property
    def pre_rate(self) -> float:
        """Occurrences per task, pre-intervention. Zero-tasks → 0."""
        return self.pre_count / self.pre_window_tasks if self.pre_window_tasks else 0.0

    @property
    def post_rate(self) -> float:
        return (
            self.post_count / self.post_window_tasks
            if self.post_window_tasks
            else 0.0
        )

    @property
    def rate_delta(self) -> float:
        """Pre minus post — positive means the rate dropped (good)."""
        return self.pre_rate - self.post_rate


def is_ineffective(
    window: EfficacyWindow,
    *,
    min_window: int = DEFAULT_MIN_WINDOW,
    tolerance: float = DEFAULT_INDISTINGUISHABLE_TOLERANCE,
) -> bool:
    """Per design §8.5: post-window rate statistically indistinguishable
    from pre → `ineffective`. Our pragmatic test:

      - The post-window must be long enough (`>= min_window` tasks);
        below that we don't have enough evidence to judge.
      - The post-rate must be within `tolerance` of pre-rate (i.e.
        the artifact did NOT drop occurrence rate by at least
        `tolerance` of the original rate). When pre-rate is 0, any
        post-rate that's also 0 is indistinguishable too.

    This is a deliberately simple heuristic. A real statistical test
    (chi-square on contingency, or a small-sample exact test) is a
    follow-up; the contract here — "evidence-based decision, never
    fail-open" — is what matters for the design lock."""
    if window.post_window_tasks < min_window:
        return False  # not enough evidence to judge
    if window.pre_rate <= 0:
        # No pre-intervention rate — can't show improvement. Treat
        # as "ineffective" only if post is also zero AND the window
        # is long; otherwise leave it alone.
        return window.post_rate <= 0.0
    # Improvement required: post must be below pre by MORE THAN
    # `tolerance` * pre. The boundary case (improvement equals the
    # tolerance threshold exactly) is treated as indistinguishable —
    # design §8.5 conservative read: a small-but-visible improvement
    # isn't statistical evidence yet.
    #
    # `+ 1e-9` absorbs floating-point noise on integer rate divisions
    # (e.g. 10/100 - 9/100 doesn't round-trip cleanly in binary FP).
    improvement = window.pre_rate - window.post_rate
    return improvement <= (window.pre_rate * tolerance) + 1e-9


# --- retirement walk ----------------------------------------------------


@dataclasses.dataclass(frozen=True)
class RetirementDecision:
    """One artifact's retirement verdict — recorded for audit and
    surfaced to the operator. `retired` means the status was flipped
    on this run; `kept` is a no-op verdict carried for completeness
    (a future surface could render "skills currently being watched")."""

    skill_id: str
    cluster_id: str
    retired: bool
    reason: str
    window: EfficacyWindow | None = None


def build_window(
    skill: Skill,
    counters: dict[str, ClusterCounters],
    *,
    pre_count: int,
    pre_window_tasks: int,
    current_task_total: int,
) -> EfficacyWindow:
    """Construct the EfficacyWindow for one skill from the cohort store.

    `pre_count` + `pre_window_tasks` come from the audit log's
    `approve_decision` snapshot when the skill was merged (Stage-6
    wires that). `current_task_total` is the number of tasks observed
    since then — derived from the journals' task counter at iteration
    time. Post counts come from the live cluster counter."""
    cid = skill.frontmatter.cluster_id
    counter = counters.get(cid)
    post_count = counter.observed if counter else 0
    # Post-window observed minus the snapshotted pre — only what
    # happened AFTER merge counts toward "did the artifact help".
    post_observed_since_merge = max(0, post_count - pre_count)
    return EfficacyWindow(
        skill_id=skill.id,
        cluster_id=cid,
        pre_count=pre_count,
        post_count=post_observed_since_merge,
        pre_window_tasks=pre_window_tasks,
        post_window_tasks=current_task_total,
    )


def evaluate_active_skills(
    skill_dir: str | Path,
    counters: dict[str, ClusterCounters],
    *,
    snapshots: dict[str, tuple[int, int]] | None = None,
    current_task_total: int = 0,
    min_window: int = DEFAULT_MIN_WINDOW,
    tolerance: float = DEFAULT_INDISTINGUISHABLE_TOLERANCE,
) -> list[RetirementDecision]:
    """Walk all active skills, decide whether each is ineffective.

    `snapshots` maps skill_id → (pre_count, pre_window_tasks) — the
    state at merge time. When None (Stage-4 default — Stage-6 wires the
    snapshotting), no skill has a valid window and all are kept.

    Returns the verdicts. Caller (meta) decides whether to actually
    flip statuses (production) or just render the decisions (operator
    surface preview)."""
    snapshots = snapshots or {}
    decisions: list[RetirementDecision] = []
    for skill in iter_skills(skill_dir, status="active"):
        snap = snapshots.get(skill.id)
        if snap is None:
            decisions.append(
                RetirementDecision(
                    skill_id=skill.id,
                    cluster_id=skill.frontmatter.cluster_id,
                    retired=False,
                    reason="no snapshot at merge time — efficacy window unknown",
                )
            )
            continue
        pre_count, pre_window_tasks = snap
        window = build_window(
            skill,
            counters,
            pre_count=pre_count,
            pre_window_tasks=pre_window_tasks,
            current_task_total=current_task_total,
        )
        ineffective = is_ineffective(window, min_window=min_window, tolerance=tolerance)
        if not ineffective:
            decisions.append(
                RetirementDecision(
                    skill_id=skill.id,
                    cluster_id=skill.frontmatter.cluster_id,
                    retired=False,
                    reason=(
                        f"active: post_rate={window.post_rate:.3f} vs "
                        f"pre_rate={window.pre_rate:.3f} "
                        f"(post_window={window.post_window_tasks})"
                    ),
                    window=window,
                )
            )
            continue
        decisions.append(
            RetirementDecision(
                skill_id=skill.id,
                cluster_id=skill.frontmatter.cluster_id,
                retired=True,
                reason=(
                    f"ineffective: post_rate={window.post_rate:.3f} did "
                    f"not improve on pre_rate={window.pre_rate:.3f} "
                    f"by ≥ {tolerance:.0%} after {window.post_window_tasks} "
                    f"tasks (min window {min_window})"
                ),
                window=window,
            )
        )
    return decisions


def retire_ineffective_skills(
    skill_dir: str | Path,
    counters: dict[str, ClusterCounters],
    *,
    snapshots: dict[str, tuple[int, int]] | None = None,
    current_task_total: int = 0,
    min_window: int = DEFAULT_MIN_WINDOW,
    tolerance: float = DEFAULT_INDISTINGUISHABLE_TOLERANCE,
) -> list[RetirementDecision]:
    """Production path: evaluate + flip status for ineffective skills.

    Returns the same decisions list as `evaluate_active_skills`; the
    caller (meta) is responsible for writing `audit.jsonl` records
    ('artifact_retired' is in the audit whitelist already)."""
    decisions = evaluate_active_skills(
        skill_dir,
        counters,
        snapshots=snapshots,
        current_task_total=current_task_total,
        min_window=min_window,
        tolerance=tolerance,
    )
    for decision in decisions:
        if decision.retired:
            flip_status(skill_dir, decision.skill_id, "retired")
    return decisions
