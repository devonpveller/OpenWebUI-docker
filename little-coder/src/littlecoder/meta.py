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
    ) -> None:
        self.cfg = observer_cfg
        self.journals_dir = Path(journals_dir)
        self.cohorts_dir = Path(cohorts_dir)
        self.cohorts_dir.mkdir(parents=True, exist_ok=True)
        self.similarity = similarity or default_similarity
        self.state = state or MetaState()

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
            store = rebuild(self.journals_dir, self.similarity, self.cfg.similarity_floor)
            checkpoint(store, self.checkpoint_path)
            result = self._summarize(store)
            self.state.last_iteration_ts = result.ts
            self.state.last_result = result
            self.state._records_at_last_run = result.records_consumed
            return result
        finally:
            self.state._lock.release()

    def _summarize(self, store: CohortStore) -> IterationResult:
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
