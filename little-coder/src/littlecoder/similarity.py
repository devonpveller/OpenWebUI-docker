"""Embedding-based cluster similarity (design §5.2).

`clusters.assign` takes a `Similarity` callable — a function `(occurrence,
cluster) -> [0, 1]`. This module builds that callable from an embedding
client + the cluster's judge-written discriminator.

The math:
  1. Embed the cluster's discriminator once when the cluster is observed
     (the judge writes it; until refined, it's the cluster's label).
  2. Embed each occurrence's `signal_text` on demand.
  3. Cosine similarity between the two; map [-1, 1] → [0, 1] linearly so
     the result fits `clusters.assign`'s domain.

A `Similarity` factory caches discriminator embeddings — each cluster's
discriminator gets embedded once per iteration, not once per occurrence.
For N occurrences and M clusters this turns O(N*M) embedding calls into
O(N+M), which matters once cluster counts grow.

The factory is the seam tests mock at: pass a `MockEmbeddingClient` from
`llm.py` with deterministic vectors and the assignment logic becomes
purely arithmetic.
"""

from __future__ import annotations

import math
from typing import Iterable

from .clusters import Cluster, Occurrence
from .llm import EmbedLike


def cosine_to_unit(a: list[float], b: list[float]) -> float:
    """Cosine similarity mapped from [-1, 1] to [0, 1]. Two vectors with
    cosine 1.0 → 1.0; orthogonal → 0.5; opposite → 0.0. Zero-norm
    vectors return 0.5 (no information, halfway score). Returning a
    well-defined value on zero vectors is what keeps the assignment
    function from blowing up on an empty discriminator."""
    if not a or not b or len(a) != len(b):
        return 0.5
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.5
    cosine = dot / (norm_a * norm_b)
    # Clamp before mapping — floating-point can drift cosine slightly
    # outside [-1, 1] on near-parallel vectors.
    cosine = max(-1.0, min(1.0, cosine))
    return 0.5 * (cosine + 1.0)


def _cluster_anchor(cluster: Cluster) -> str:
    """The text the cluster is embedded as. Discriminator is the
    judge-authored boundary text; if empty (e.g. a freshly minted
    cluster before the judge has refined it), fall back to the label.
    Never an empty string — empty input would map every occurrence to
    0.5 and silently corrupt assignment."""
    return cluster.discriminator or cluster.label or cluster.cluster_id


class EmbeddingSimilarity:
    """Callable wrapper meeting the `clusters.Similarity` protocol.

    Holds two caches per iteration: cluster anchors and occurrence
    signals. Both are intentionally per-instance, not module-global —
    each `meta` iteration constructs a fresh one, so stale embeddings
    from a previous iteration can't leak across runs."""

    def __init__(self, embed_client: EmbedLike) -> None:
        self.embed = embed_client
        self._cluster_vec: dict[str, list[float]] = {}
        self._occurrence_vec: dict[str, list[float]] = {}

    def preload(
        self,
        clusters: Iterable[Cluster],
        occurrences: Iterable[Occurrence],
    ) -> None:
        """Batch-embed every distinct anchor + signal up front. This is
        the optimization that turns O(N*M) into O(N+M) — call once at
        the start of an iteration. Safe to call again with new inputs;
        already-cached texts are skipped."""
        new_cluster_texts: list[str] = []
        cluster_keys: list[str] = []
        for c in clusters:
            if c.cluster_id in self._cluster_vec:
                continue
            new_cluster_texts.append(_cluster_anchor(c))
            cluster_keys.append(c.cluster_id)
        if new_cluster_texts:
            vecs = self.embed.embed(new_cluster_texts)
            for key, vec in zip(cluster_keys, vecs):
                self._cluster_vec[key] = vec

        new_signal_texts: list[str] = []
        for o in occurrences:
            if o.signal_text in self._occurrence_vec:
                continue
            new_signal_texts.append(o.signal_text)
        if new_signal_texts:
            vecs = self.embed.embed(new_signal_texts)
            for text, vec in zip(new_signal_texts, vecs):
                self._occurrence_vec[text] = vec

    def __call__(self, occurrence: Occurrence, cluster: Cluster) -> float:
        """The `Similarity` interface — score one (occurrence, cluster)
        pair. Cache-miss on either side falls back to a single-call
        embed; ordinarily `preload()` has populated everything."""
        ov = self._occurrence_vec.get(occurrence.signal_text)
        if ov is None:
            ov = self.embed.embed([occurrence.signal_text])[0]
            self._occurrence_vec[occurrence.signal_text] = ov
        cv = self._cluster_vec.get(cluster.cluster_id)
        if cv is None:
            cv = self.embed.embed([_cluster_anchor(cluster)])[0]
            self._cluster_vec[cluster.cluster_id] = cv
        return cosine_to_unit(ov, cv)
