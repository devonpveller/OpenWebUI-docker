"""Cluster identity + assignment (design §5.1–§5.3).

A cluster is the unit of cohort accounting (§5.4) and the target of every
intervention (§5.6). Two facts about clusters that the design pins down and
this module enforces:

1. **`cluster_id` is immutable; the human label is mutable.** Cohort records
   key on `cluster_id`, so relabeling never touches cohort history. Without
   this split, a judge that renames "Rust async lifetimes" to "Rust borrow
   checker" would erase 200 occurrences of evidence (§5.1).

2. **Ingest-time assignment with a similarity floor + unassigned pool.** A
   new occurrence joins the nearest existing cluster ABOVE the floor; below
   the floor it lands in `unassigned`. The judge mints a new `cluster_id`
   only when `unassigned` itself forms a coherent group (§5.2). This keeps
   the cluster set from drifting into a thousand tiny near-duplicates the
   first time the judge has a bad day.

The split/merge bookkeeping (§5.3) splits `observed` counts from `inherited`
ones — escalation cannot fire on inherited counts, which would otherwise
let a split-then-escalate loop forge phantom evidence. Lineage is recorded
as parent↔child events; `cohorts.py` reads them to compute the live counts.

This module is pure: the similarity function is injected (`Similarity` is a
callable). The real similarity function — embedding + judge-written
discriminator — lives in the judge module (§3e) and is constructed at
`meta` boot. Tests use a stub.
"""

from __future__ import annotations

import dataclasses
import secrets
from typing import Callable, Sequence

# Synthetic, immutable cluster id. Generated locally — never derived from
# the label, the embedding, or the judge's output, so a label rename or a
# discriminator refinement does NOT change the id.
ClusterId = str

UNASSIGNED = "unassigned"


def new_cluster_id() -> ClusterId:
    """Generate a fresh `cluster_id`. Random 64-bit hex (16 chars) is more
    than enough — the population of live clusters per stack is small."""
    return secrets.token_hex(8)


@dataclasses.dataclass(frozen=True)
class Occurrence:
    """One journal-derived signal eligible for clustering. Errors are the
    primary source; failed `TaskEnded` records also count (a task that
    ended `fail` is a craft event even if no `error` record explains why).

    `signal_text` is the free-text the judge / similarity function reads.
    For an `Error` record that's the message; for a `TaskEnded(fail)` it
    can be a short synthesized "<signal> on <repo>" string. Stored on the
    occurrence (not derived later) so the cohort store is reproducible
    from the original input."""

    task_id: str
    ts: str  # UTC ISO from the source record
    lang: str
    task_shape: str  # from task_shape.classify()
    repo: str
    signal_text: str
    # Optional: the kind/tool tag the originating record carried (`tool` for
    # ToolCall, `kind` for Error). Stored for drill-down but not used in
    # the assignment function — assignment is similarity-only (§5.2).
    source_kind: str | None = None


@dataclasses.dataclass
class Cluster:
    """One cluster. The id is immutable; the label and discriminator are
    re-written by the judge over time as the cluster's understanding
    sharpens (§5.1)."""

    cluster_id: ClusterId
    label: str  # human-readable; mutable
    discriminator: str  # judge-written boundary text; mutable
    # `lang` + `task_shape` define the cohort scope (§5.5). Aggregated
    # across repos — a single cluster lives across many repos but exactly
    # one (lang, task_shape) pair.
    lang: str
    task_shape: str
    # Tier-0 vs tier-1 gate (locked decision #17): True when the
    # founding-knowledge baseline already states what this cluster is
    # about. A baseline-covered cluster is a COMPLIANCE gap (escalates
    # to tier-1 enforcement) — NOT a tier-0 knowledge restatement.
    # Set by the judge at mint time; refined on subsequent judge passes.
    baseline_covers: bool = False
    # Lineage (§5.3). `parents` is non-empty when this cluster was created
    # by a split or merge; `inherited_count` is the number of occurrences
    # carried over from a parent (does NOT count toward escalation
    # thresholds, only toward drill-down).
    parents: tuple[ClusterId, ...] = ()
    inherited_count: int = 0


# A similarity function: how close is this occurrence to the cluster's
# discriminator? Returns a float in [0, 1] where 1 is identical. The judge
# module wires the real implementation (embedding + discriminator-as-anchor);
# `meta` injects it at runtime. Tests pass a stub.
Similarity = Callable[[Occurrence, Cluster], float]


@dataclasses.dataclass(frozen=True)
class AssignmentResult:
    """Outcome of `assign()`. `cluster_id` is the target — either an existing
    cluster's id or `UNASSIGNED`. `score` is the best match score; on
    `UNASSIGNED` it is the score that failed the floor (kept for the
    drift-trigger metric and the unassigned-pool coherence check)."""

    cluster_id: ClusterId
    score: float
    # Lang+task_shape matched too. False if the best similarity match was
    # in a different scope (cross-scope matches are NEVER allowed — the
    # cohort key is (lang, task_shape) per §5.5).
    same_scope: bool


def assign(
    occurrence: Occurrence,
    clusters: Sequence[Cluster],
    similarity: Similarity,
    floor: float = 0.7,
) -> AssignmentResult:
    """Assign an occurrence to a cluster, or to the unassigned pool.

    Rules (design §5.2 + §5.5):
      1. Filter clusters to the occurrence's (lang, task_shape) scope —
         cross-scope clustering is never permitted.
      2. Score every in-scope cluster; pick the highest.
      3. If best score ≥ floor → that cluster.
      4. Else → UNASSIGNED, score=best (so meta can compute drift).
      5. No in-scope clusters → UNASSIGNED, score=0.

    The similarity function is opaque to this layer — it can use
    embeddings, the cluster's judge-written discriminator, or both. What
    matters is that it returns [0, 1] and is deterministic per (occurrence,
    cluster) pair (otherwise cohort projection isn't replayable)."""
    in_scope = [
        c
        for c in clusters
        if c.lang == occurrence.lang and c.task_shape == occurrence.task_shape
    ]
    if not in_scope:
        return AssignmentResult(UNASSIGNED, 0.0, same_scope=False)

    best_cluster = in_scope[0]
    best_score = similarity(occurrence, best_cluster)
    for c in in_scope[1:]:
        score = similarity(occurrence, c)
        if score > best_score:
            best_score = score
            best_cluster = c

    if best_score >= floor:
        return AssignmentResult(best_cluster.cluster_id, best_score, same_scope=True)
    # Below the floor: occurrence lands in UNASSIGNED, but we keep the
    # best score so the drift trigger can see "we almost matched X".
    return AssignmentResult(UNASSIGNED, best_score, same_scope=True)


# --- lineage events (§5.3) ------------------------------------------------
#
# Splits and merges are journaled to `audit.jsonl` and consumed by the
# cohort projection. The events live here so the model is one place.


@dataclasses.dataclass(frozen=True)
class SplitEvent:
    """Parent cluster split into N children. The parent stays alive — it
    becomes a historical bucket; new occurrences route to whichever child
    matches best. Each child inherits the parent's window marked as
    `inherited`; escalation never fires on inherited counts (§5.3)."""

    ts: str
    parent_id: ClusterId
    child_ids: tuple[ClusterId, ...]
    inherited_per_child: int  # occurrences copied to each child
    reason: str  # judge's argument for the split


@dataclasses.dataclass(frozen=True)
class MergeEvent:
    """N clusters → one. The merged cluster's `observed` count is the SUM
    of parent observed counts; the quarantine window resets (§5.3) so the
    operator gets a fresh look at whether the combined cluster behaves
    differently than its parents did separately."""

    ts: str
    parent_ids: tuple[ClusterId, ...]
    child_id: ClusterId
    merged_observed: int
    reason: str
