"""`meta` outer loop — Observer's read-only iteration (design §3.2, Chapter 3).

`meta` reads journals, clusters occurrences, and surfaces patterns. In
Observer (Chapter 3) it WRITES NOTHING to the skill library — its only
outputs are the cohort store (a derived index, design §5.4) and the
`ObserverReport` DTOs the operator surface renders.

Two contracts pinned here:

  1. **Single-flight** (design §12.5): at most one iteration in progress.
     A second trigger arriving mid-iteration is dropped — the next
     iteration will see the same evidence (journals are durable).
  2. **Evidence-triggered** (design §3.2): an iteration runs only when
     new records have crossed a configured threshold since the last run.
     No clock-based audits.

Stage-2 scope: cohort projection, checkpoint persistence, an in-memory
report. No LLM calls (Stage 3 wires the judge). The similarity function
passed in is therefore a placeholder; `default_similarity` returns 0.0
for every pair, so every occurrence lands in the unassigned pool — which
is exactly the "no clusters yet, judge hasn't run" state described in
design §5.2.
"""

from __future__ import annotations

import dataclasses
import threading
from pathlib import Path
from typing import Iterable

from .clusters import Cluster, Occurrence, Similarity, UNASSIGNED
from .cohorts import (
    CohortStore,
    UnassignedBucket,
    checkpoint,
    load_checkpoint,
    rebuild,
)
from .config import ObserverConfig
from .journals import Envelope, utc_now
from .judge import Judge
from .skills import Skill, build_skill, iter_skills, write_skill
from .tier_ladder import eligible_tier_0


def default_similarity(occurrence: Occurrence, cluster: Cluster) -> float:
    """Stage-2 placeholder similarity. Always 0.0 — every occurrence falls
    below the floor, so everything lands in the unassigned pool. The
    Stage-3 judge wires the real embedding + discriminator function."""
    return 0.0


@dataclasses.dataclass(frozen=True)
class IterationResult:
    """Outcome of one `meta` iteration. Tier escalation reads from this in
    Chapter 4; for Observer (Chapter 3) it feeds the operator report."""

    ts: str  # UTC ISO at iteration completion
    records_consumed: int  # records walked from journals on this run
    clusters_total: int  # known clusters at the end
    occurrences_total: int  # observed across all clusters
    unassigned_total: int  # below-floor occurrences across all buckets
    # Per-(lang, task_shape) unassigned-bucket sizes — the operator wants
    # this to gauge "is this getting big enough to mint?".
    unassigned_by_scope: dict[tuple[str, str], int] = dataclasses.field(
        default_factory=dict
    )
    # Newly-minted-this-iteration cluster ids (the judge's output). Empty
    # when no Judge was wired in, or when the pool was too small / noisy.
    minted_cluster_ids: tuple[str, ...] = ()
    # Tier-0 skills drafted this iteration. Ids of newly written `Skill`
    # files (status="pending" — awaiting operator approval, §4f).
    drafted_skill_ids: tuple[str, ...] = ()


class MetaState:
    """The bit of per-process state Stage 2 needs: the lock, last-run
    counters for the evidence trigger, and a pointer to the on-disk
    cohort store. Kept tiny so the daemon can hold one of these without
    coupling to the projection internals."""

    def __init__(self) -> None:
        # Single-flight lock. `threading.Lock` — `meta.iterate` is called
        # from a worker thread (the daemon uses `asyncio.to_thread`); the
        # daemon's event loop never directly contends here.
        self._lock = threading.Lock()
        # Records consumed at the last iteration. The evidence trigger
        # compares this against the current journal record count.
        self._records_at_last_run: int = 0
        # Set once the first iteration completes — read by the operator
        # surface as a freshness indicator.
        self.last_iteration_ts: str | None = None
        self.last_result: IterationResult | None = None

    def held(self) -> bool:
        """True while an iteration is in progress. Caller-side check;
        does NOT acquire the lock."""
        return self._lock.locked()


def should_trigger(
    state: MetaState,
    current_record_count: int,
    threshold: int,
) -> bool:
    """Evidence-triggered: only return True when enough new records have
    landed since the last successful iteration. `current_record_count`
    is the total journal records visible to the projection — counted by
    walking files, NOT by polling `Journals.records_written` (which
    isn't durable across restarts)."""
    if threshold <= 0:
        return True  # threshold=0 means "always trigger"; useful in tests
    delta = current_record_count - state._records_at_last_run
    return delta >= threshold


class MetaRunner:
    """One Observer iteration. Stateless apart from `MetaState` — every
    iteration re-reads the journals to derive a fresh `CohortStore`, then
    persists the checkpoint atomically.

    Stage-2 keeps the iteration deterministic: same inputs → same store.
    The Stage-3 judge introduces non-determinism (LLM minting); the
    architectural seam is preserved (judge calls happen on the unassigned
    pool AFTER the projection, never inside it)."""

    def __init__(
        self,
        observer_cfg: ObserverConfig,
        journals_dir: str | Path,
        cohorts_dir: str | Path,
        similarity: Similarity | None = None,
        state: MetaState | None = None,
        judge: Judge | None = None,
        skill_dir: str | Path | None = None,
        drafts_per_iteration: int = 1,
    ) -> None:
        self.cfg = observer_cfg
        self.journals_dir = Path(journals_dir)
        self.cohorts_dir = Path(cohorts_dir)
        self.cohorts_dir.mkdir(parents=True, exist_ok=True)
        self.similarity = similarity or default_similarity
        self.state = state or MetaState()
        # Optional — Stage 2 still works with no judge wired in (every
        # occurrence stays in the unassigned pool). Stage 3 wires a real
        # `Judge`; tests can pass a Judge-with-MockChatClient.
        self.judge = judge
        # Chapter-4 §4e drafting: where to write `Skill` files. When
        # None, no drafting is attempted (Chapter-3-compatible mode).
        self.skill_dir = Path(skill_dir) if skill_dir else None
        # Per design §12.5: at most 1 artifact/iteration. Tunable for
        # tests, never for production (operator catches up via the
        # approval surface, not by raising the rate).
        self.drafts_per_iteration = drafts_per_iteration

    @property
    def checkpoint_path(self) -> Path:
        return self.cohorts_dir / self.cfg.store_filename

    def load_store(self) -> CohortStore:
        return load_checkpoint(self.checkpoint_path)

    def iterate(self) -> IterationResult | None:
        """Run one iteration. Returns None if another iteration is already
        running (single-flight, design §12.5 — second trigger drops, not
        queues). Otherwise re-projects journals from scratch, checkpoints
        the store atomically, and returns the result.

        Re-projecting from scratch (not incrementally) is the right call
        in Observer: the projection is fast (no LLM), the journals are
        bounded by Tool/OWUI volume, and full replay is what proves the
        store is a real derived index. The incremental path is an
        optimization for later chapters when judge calls dominate
        wall-time."""
        # acquire without blocking — single-flight semantics
        if not self.state._lock.acquire(blocking=False):
            return None
        try:
            store = self._rebuild_carrying_clusters()
            minted = self._mint_from_unassigned(store) if self.judge else ()
            drafted = self._draft_eligible_clusters(store) if self._can_draft() else ()
            checkpoint(store, self.checkpoint_path)
            result = self._summarize(store, minted, drafted)
            self.state.last_iteration_ts = result.ts
            self.state.last_result = result
            self.state._records_at_last_run = result.records_consumed
            return result
        finally:
            self.state._lock.release()

    def _rebuild_carrying_clusters(self) -> CohortStore:
        """Build a fresh `CohortStore` for this iteration, but carry
        cluster identities + lineage forward from the prior checkpoint.

        Background: design §5.4 makes cohort counters event-sourced over
        journals (replay-deterministic). Cluster IDENTITIES, however,
        are minted by the judge — they're not in the journals' replay
        stream. A pure rebuild loses them. The design-correct fix would
        be to journal cluster-minting events to `audit.jsonl`; until
        that lands, we preserve clusters by loading the prior
        checkpoint and re-projecting onto it.

        Mechanism:
          1. Load the prior store (carries clusters, discriminators,
             lineage, baseline_covers flags).
          2. Reset all counters + unassigned buckets — those are
             derived from journals.
          3. Re-project all journals onto the seeded store. Occurrences
             that match an existing cluster (similarity ≥ floor) hit
             that cluster's counter; the rest go to unassigned where
             the judge can mint NEW clusters from them on top of the
             existing set.
        """
        from .cohorts import project

        prior = self.load_store()
        prior.counters = {}
        prior.unassigned = {}
        # Walk the same journals `rebuild()` would, but project onto
        # `prior` so the carried-forward clusters get matched against.
        from .cohorts import _iter_journals

        project(
            prior,
            _iter_journals(self.journals_dir, "tool_calls", "errors", "outcomes"),
            self.similarity,
            self.cfg.similarity_floor,
        )
        return prior

    def _can_draft(self) -> bool:
        """Tier-0 drafting needs a judge + a skill_dir + a writable
        skill library on disk. All three optional, so a Chapter-3-shape
        deployment continues to work."""
        return self.judge is not None and self.skill_dir is not None

    def _draft_eligible_clusters(self, store: CohortStore) -> tuple[str, ...]:
        """Per design §5.6 + §12.5: per-iteration draft cap is 1 by
        default. Walk eligible clusters in observed-count-descending
        order; draft until the cap is hit or the eligible list is
        empty. Returns the ids of the `Skill` files written."""
        if not self.judge or self.skill_dir is None:
            return ()
        prior = _prior_interventions(self.skill_dir)
        eligibles = eligible_tier_0(
            list(store.clusters.values()), store.counters, prior
        )
        if not eligibles:
            return ()

        drafted_ids: list[str] = []
        for verdict in eligibles[: self.drafts_per_iteration]:
            cluster = store.clusters[verdict.cluster_id]
            counter = store.counters.get(cluster.cluster_id)
            samples = _signal_sample(store, cluster.cluster_id)
            try:
                draft_result = self.judge.draft_tier_0_skill(cluster, counter, samples)
            except Exception:
                # A failed draft must not poison the iteration; the
                # cluster stays eligible for the next pass. Re-raise
                # would also work, but evidence-triggered design
                # tolerates one bad LLM call.
                continue
            if draft_result.escaped_to_compliance or draft_result.output is None:
                # Judge re-judged: this is actually a compliance gap.
                # Flip the cluster's baseline_covers so the next ladder
                # pass routes it to tier-1 (Stage 4+).
                cluster.baseline_covers = True
                continue
            output = draft_result.output
            skill = build_skill(
                kind="knowledge",
                cluster_id=cluster.cluster_id,
                tier=0,
                lang=cluster.lang,
                domain=_domain_from_cluster(cluster),
                task_shape=cluster.task_shape,
                name=output.name,
                description=output.description,
                body=output.body,
                status="pending",  # operator approves via §4f surface
            )
            write_skill(self.skill_dir, skill)
            drafted_ids.append(skill.id)
        return tuple(drafted_ids)

    def _mint_from_unassigned(self, store: CohortStore) -> tuple[str, ...]:
        """For each scope's unassigned bucket, ask the judge whether it
        coheres. Newly-minted clusters are added to `store.clusters`;
        consumed occurrences are removed from their bucket and counted
        against the new cluster. The buckets that aren't ripe (judge
        returned no clusters, or the pool was too small) are LEFT IN
        PLACE — the next iteration sees them again with whatever new
        evidence has accumulated."""
        if not self.judge:
            return ()
        minted_ids: list[str] = []
        # Take a snapshot of the keys — we mutate the dict below.
        for key in list(store.unassigned.keys()):
            bucket = store.unassigned[key]
            if not bucket.occurrences:
                continue
            result = self.judge.mint_clusters(
                bucket.occurrences,
                lang=bucket.lang,
                task_shape=bucket.task_shape,
            )
            if not result.new_clusters:
                continue
            consumed_keys = {(o.task_id, o.signal_text) for o in result.consumed}
            # Add the new clusters to the store and create their counters.
            from .cohorts import ClusterCounters

            for cluster in result.new_clusters:
                store.clusters[cluster.cluster_id] = cluster
                store.counters[cluster.cluster_id] = ClusterCounters(cluster.cluster_id)
                minted_ids.append(cluster.cluster_id)
            # Route consumed occurrences from the pool onto the new
            # clusters (in the order the judge returned them). We don't
            # know which proposal each consumed occurrence belongs to
            # from the materialize step's return shape, so we re-walk
            # the judge output once.
            consumed_routed: set[tuple[str, str]] = set()
            for proposal, cluster in zip(result.raw_output.clusters, result.new_clusters):
                for idx in proposal.signal_indices:
                    if not (0 <= idx < len(bucket.occurrences)):
                        continue
                    occ = bucket.occurrences[idx]
                    ckey = (occ.task_id, occ.signal_text)
                    if ckey in consumed_routed:
                        continue
                    consumed_routed.add(ckey)
                    store.counters[cluster.cluster_id].record(occ)
            # Remove consumed occurrences from the bucket.
            bucket.occurrences = [
                occ
                for occ in bucket.occurrences
                if (occ.task_id, occ.signal_text) not in consumed_keys
            ]
            # An empty bucket is left in place — the dict key still
            # signals "this scope is live", and the next iteration will
            # refill it as new failures arrive.
        return tuple(minted_ids)

    def _summarize(
        self,
        store: CohortStore,
        minted: tuple[str, ...] = (),
        drafted: tuple[str, ...] = (),
    ) -> IterationResult:
        """Build the iteration's `IterationResult` — counts the operator
        surface (and the metrics endpoint) reads. The full store goes to
        disk; this is the cheap snapshot."""
        unassigned_by_scope = {
            (b.lang, b.task_shape): b.size for b in store.unassigned.values()
        }
        unassigned_total = sum(unassigned_by_scope.values())
        occurrences_total = sum(c.observed for c in store.counters.values())
        return IterationResult(
            ts=utc_now(),
            records_consumed=self._count_journal_records(),
            clusters_total=len(store.clusters),
            occurrences_total=occurrences_total,
            unassigned_total=unassigned_total,
            unassigned_by_scope=unassigned_by_scope,
            minted_cluster_ids=minted,
            drafted_skill_ids=drafted,
        )

    def _count_journal_records(self) -> int:
        """Count records across all journal segments (live + rotated). Used
        ONLY for the evidence-trigger threshold. Cheap (line count); we
        deliberately don't validate each line here — the projection is
        the contract enforcer, not this counter."""
        total = 0
        for name in ("tool_calls", "errors", "outcomes"):
            for path in self.journals_dir.glob(f"{name}*.jsonl"):
                try:
                    with open(path, "rb") as fh:
                        for _ in fh:
                            total += 1
                except OSError:
                    continue
        return total


# --- helpers used by the drafting wire (§4e) ----------------------------


def _prior_interventions(skill_dir: Path) -> dict[str, set[int]]:
    """Build the `cluster_id -> {tiers_already_shipped}` map for the
    ladder. We read the skill library from disk so a restart re-derives
    intervention state from ground truth — never trust an in-memory
    cache. Includes pending drafts: a tier-0 draft awaiting operator
    approval still BLOCKS a second tier-0 draft for the same cluster
    (a fresh draft per iteration would queue up duplicates)."""
    out: dict[str, set[int]] = {}
    for skill in iter_skills(skill_dir, status=None):
        fm = skill.frontmatter
        if fm.status == "retired":
            continue  # retired interventions free the cluster up
        out.setdefault(fm.cluster_id, set()).add(int(fm.tier))
    return out


def _signal_sample(store: CohortStore, cluster_id: str, limit: int = 16) -> list[str]:
    """Pick representative signal texts for the judge's drafting prompt.

    The cohort store doesn't currently hold per-cluster occurrence
    history — only counters. For now we sample from the unassigned
    pool's matching scope (lang + task_shape) when available; in a
    future pass this should pull the actual journal records that fed
    the cluster's `observed` counter. Returning [] is acceptable —
    the judge has the cluster's discriminator + label and can draft
    from those alone."""
    cluster = store.clusters.get(cluster_id)
    if cluster is None:
        return []
    bucket = store.unassigned.get((cluster.lang, cluster.task_shape))
    if not bucket:
        return []
    return [occ.signal_text for occ in bucket.occurrences[:limit]]


def _domain_from_cluster(cluster: Cluster) -> str:
    """Pick the `domain` for a drafted skill. The judge sees the cluster
    label + discriminator and crafts the body; the augmenter's tag
    filter (§7.4 step 1) keys on `domain`. The cluster doesn't carry
    one today — until the judge starts annotating domain, use the
    cluster id (always unique). The augmenter's `*` wildcard means a
    skill with domain=<cluster_id> still gets retrieved when a task
    has a matching cluster context. A follow-up pass should add an
    explicit domain field to `Cluster` and have the judge populate it
    at mint time."""
    return cluster.cluster_id[:16] or "unknown"


# --- iteration report rendering (Stage 4 lifts this into the surface) ----


def report_lines(result: IterationResult) -> list[str]:
    """Human-readable rendering of an `IterationResult`. Used by
    `lc admin observe` (Stage 4) and useful in tests."""
    lines = [
        f"meta iteration @ {result.ts}",
        f"  records consumed: {result.records_consumed}",
        f"  clusters known:   {result.clusters_total}",
        f"  observed total:   {result.occurrences_total}",
        f"  unassigned total: {result.unassigned_total}",
    ]
    if result.unassigned_by_scope:
        lines.append("  unassigned by scope (lang | task_shape → count):")
        for (lang, shape), n in sorted(result.unassigned_by_scope.items()):
            lines.append(f"    {lang or '?'} | {shape or '?'} → {n}")
    return lines
