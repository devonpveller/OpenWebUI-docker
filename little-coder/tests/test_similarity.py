"""Embedding-based similarity (design §5.2).

Drives the cosine math + caching behaviour. Real embeddings are mocked
via `MockEmbeddingClient`, so these tests are deterministic and
arithmetic-only — the cosine implementation is the contract under test,
not llama-cpp's behavior.
"""

from __future__ import annotations

import math

import pytest

from littlecoder.clusters import Cluster, Occurrence
from littlecoder.llm import MockEmbeddingClient
from littlecoder.similarity import EmbeddingSimilarity, cosine_to_unit


# --- cosine_to_unit -----------------------------------------------------


def test_cosine_identical_vectors_maps_to_1():
    assert cosine_to_unit([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)


def test_cosine_opposite_vectors_maps_to_0():
    assert cosine_to_unit([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(0.0)


def test_cosine_orthogonal_vectors_maps_to_0_5():
    assert cosine_to_unit([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.5)


def test_cosine_zero_vector_returns_0_5():
    """Zero-norm input: no information → halfway score (won't pass any
    sensible floor, but won't crash either)."""
    assert cosine_to_unit([0.0, 0.0], [1.0, 0.0]) == pytest.approx(0.5)
    assert cosine_to_unit([1.0, 0.0], [0.0, 0.0]) == pytest.approx(0.5)


def test_cosine_clamps_floating_point_drift():
    """Near-parallel vectors can drift cosine slightly outside [-1, 1] —
    the function clamps so the [0, 1] map is preserved."""
    # Use two identical-up-to-fp-noise vectors. The result must not
    # exceed 1.0 even if intermediate cosine is 1.0000000001.
    a = [1.0, 2.0, 3.0]
    b = [1.0000000001, 2.0, 3.0]
    assert cosine_to_unit(a, b) <= 1.0


def test_cosine_mismatched_lengths_returns_0_5():
    assert cosine_to_unit([1.0], [1.0, 2.0]) == pytest.approx(0.5)


# --- EmbeddingSimilarity ------------------------------------------------


def _occ(text: str, lang="rust", shape="bugfix") -> Occurrence:
    return Occurrence(
        task_id="01J0000000000000000000000A",
        ts="2026-05-23T00:00:00.000Z",
        lang=lang,
        task_shape=shape,
        repo="r",
        signal_text=text,
        source_kind="test_failure",
    )


def _cluster(cid: str, disc: str, lang="rust", shape="bugfix") -> Cluster:
    return Cluster(
        cluster_id=cid,
        label="L",
        discriminator=disc,
        lang=lang,
        task_shape=shape,
    )


def test_preload_batches_embeddings():
    """One call covers all clusters; one call covers all signals. The
    O(N+M) optimization is the whole point."""
    vectors = {
        "disc-a": [1.0, 0.0],
        "disc-b": [0.0, 1.0],
        "signal-x": [1.0, 0.0],  # matches cluster A
    }
    embed = MockEmbeddingClient(vectors, dim=2)
    sim = EmbeddingSimilarity(embed)

    clusters = [_cluster("a", "disc-a"), _cluster("b", "disc-b")]
    occs = [_occ("signal-x")]
    sim.preload(clusters, occs)

    # Two embed calls total: one for clusters, one for signals. The
    # cache means __call__ does ZERO additional calls.
    assert len(embed.calls) == 2
    score = sim(_occ("signal-x"), _cluster("a", "disc-a"))
    assert score == pytest.approx(1.0)
    assert len(embed.calls) == 2  # no extra call


def test_falls_back_to_single_embed_on_cache_miss():
    """An occurrence the caller didn't preload should still score —
    `__call__` does a single-shot embed."""
    embed = MockEmbeddingClient({"disc": [1.0, 0.0], "signal": [1.0, 0.0]}, dim=2)
    sim = EmbeddingSimilarity(embed)
    sim.preload([_cluster("a", "disc")], [])
    score = sim(_occ("signal"), _cluster("a", "disc"))
    assert score == pytest.approx(1.0)


def test_uses_label_when_discriminator_empty():
    """A freshly-minted cluster may have no discriminator yet; the
    similarity falls back to the label so assignment still works."""
    embed = MockEmbeddingClient({"my label": [1.0, 0.0], "x": [1.0, 0.0]}, dim=2)
    sim = EmbeddingSimilarity(embed)
    cluster = Cluster(
        cluster_id="c",
        label="my label",
        discriminator="",  # empty
        lang="rust",
        task_shape="bugfix",
    )
    score = sim(_occ("x"), cluster)
    assert score == pytest.approx(1.0)


def test_orthogonal_signals_score_below_typical_floor():
    """Sanity: orthogonal signal vs cluster anchor → 0.5. A 0.7 floor
    rejects this, which is exactly the behaviour we want (orthogonal
    isn't similar)."""
    embed = MockEmbeddingClient({"disc": [1.0, 0.0], "sig": [0.0, 1.0]}, dim=2)
    sim = EmbeddingSimilarity(embed)
    score = sim(_occ("sig"), _cluster("c", "disc"))
    assert 0.4 <= score <= 0.6
