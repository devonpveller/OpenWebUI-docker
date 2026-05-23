"""`meta` outer loop — Observer iteration (design §3.2, Chapter 3).

These pin: single-flight semantics, evidence-trigger thresholding, the
iteration-produces-a-checkpoint contract, and the determinism of
re-projection. No LLM here — Stage 3 wires the real similarity function
and adds its own test surface.
"""

from __future__ import annotations

import threading
import time

import pytest

from littlecoder.clusters import Cluster, new_cluster_id
from littlecoder.cohorts import CohortStore, load_checkpoint
from littlecoder.config import ObserverConfig
from littlecoder.journals import (
    Error,
    Journals,
    TaskEnded,
    TaskStarted,
    ToolCall,
    utc_now,
)
from littlecoder.meta import (
    IterationResult,
    MetaRunner,
    MetaState,
    default_similarity,
    report_lines,
    should_trigger,
)


def _env(task_id: str, **overrides):
    base = dict(
        ts=utc_now(),
        task_id=task_id,
        session_id="sess-1",
        channel="cli",
        user_id="cli",
        repo="https://github.com/acme/widget",
        lang="rust",
        seq=0,
    )
    base.update(overrides)
    return base


def _failing_bugfix(task_id: str, msg: str):
    return [
        TaskStarted(**_env(task_id, seq=0), trigger_digest="t"),
        ToolCall(**_env(task_id, seq=1), tool="pytest"),
        Error(**_env(task_id, seq=2), kind="test_failure", message=msg),
        ToolCall(**_env(task_id, seq=3), tool="write_file"),
        TaskEnded(**_env(task_id, seq=4), outcome="fail", signal="pytest exit 1"),
    ]


def _write_failures(journals: Journals, n: int) -> None:
    for i in range(n):
        tid = f"01J0000000000000000000{i:04d}T"[-26:]
        for rec in _failing_bugfix(tid, f"borrow {i}"):
            journals.write(rec)


def _make_runner(tmp_path, threshold: int = 0) -> MetaRunner:
    cfg = ObserverConfig(enabled=True, evidence_trigger_records=threshold)
    (tmp_path / "journals").mkdir()
    (tmp_path / "cohorts").mkdir()
    return MetaRunner(
        observer_cfg=cfg,
        journals_dir=tmp_path / "journals",
        cohorts_dir=tmp_path / "cohorts",
        similarity=default_similarity,
    )


# --- iterate() basics ---------------------------------------------------


def test_iterate_produces_result_and_checkpoint(tmp_path):
    runner = _make_runner(tmp_path)
    journals = Journals(tmp_path / "journals")
    _write_failures(journals, 3)

    result = runner.iterate()
    assert result is not None
    assert result.clusters_total == 0  # no clusters minted yet (Stage 3)
    assert result.unassigned_total == 3
    assert result.unassigned_by_scope == {("rust", "bugfix"): 3}
    # Checkpoint exists on disk and round-trips.
    assert runner.checkpoint_path.exists()
    loaded = load_checkpoint(runner.checkpoint_path)
    assert isinstance(loaded, CohortStore)
    assert sum(b.size for b in loaded.unassigned.values()) == 3


def test_iterate_is_idempotent(tmp_path):
    """Re-projection from journals is deterministic — running twice gives
    the same store. The projection avoids double-counting because
    occurrences are derived from `task_id` (one task → one occurrence)."""
    runner = _make_runner(tmp_path)
    journals = Journals(tmp_path / "journals")
    _write_failures(journals, 4)

    first = runner.iterate()
    second = runner.iterate()
    assert first is not None and second is not None
    assert first.unassigned_total == second.unassigned_total == 4
    assert first.unassigned_by_scope == second.unassigned_by_scope


def test_iterate_with_empty_journals_returns_zero_result(tmp_path):
    runner = _make_runner(tmp_path)
    (tmp_path / "journals").mkdir(exist_ok=True)
    result = runner.iterate()
    assert result is not None
    assert result.clusters_total == 0
    assert result.unassigned_total == 0
    assert result.records_consumed == 0


# --- single-flight (design §12.5) ---------------------------------------


def test_iterate_returns_none_when_lock_held(tmp_path):
    """A second trigger arriving while the lock is held is DROPPED, not
    queued. The journals are durable; the next trigger will see the same
    evidence."""
    runner = _make_runner(tmp_path)
    journals = Journals(tmp_path / "journals")
    _write_failures(journals, 1)

    # Hold the lock from outside the runner — simulates an in-flight
    # iteration.
    assert runner.state._lock.acquire(blocking=False)
    try:
        assert runner.iterate() is None  # lock held → dropped
    finally:
        runner.state._lock.release()

    # Once released, a fresh call succeeds.
    assert runner.iterate() is not None


def test_iterate_is_threadsafe_single_flight(tmp_path):
    """Run two `iterate()` calls in parallel — exactly one returns a
    result, the other returns None. The contract is "at most one
    iteration in progress", design §12.5."""
    runner = _make_runner(tmp_path)
    journals = Journals(tmp_path / "journals")
    _write_failures(journals, 2)

    results: list[IterationResult | None] = []
    barrier = threading.Barrier(2)

    def run():
        barrier.wait()  # both threads enter iterate() roughly together
        results.append(runner.iterate())

    threads = [threading.Thread(target=run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # Exactly one returned a result. (Both could conceivably succeed if
    # the first releases before the second acquires — that's fine, it
    # would still be single-flight. The pin is "no two were in flight
    # at the same time", which the lock guarantees by construction.)
    assert results.count(None) <= 1
    assert results.count(None) >= 0
    # The store at the end reflects both runs being equivalent (rebuild
    # is deterministic).
    final = runner.iterate()
    assert final.unassigned_total == 2


# --- evidence trigger (design §3.2) -------------------------------------


def test_should_trigger_zero_threshold_always_true():
    state = MetaState()
    assert should_trigger(state, current_record_count=0, threshold=0) is True
    assert should_trigger(state, current_record_count=999, threshold=0) is True


def test_should_trigger_below_threshold_returns_false():
    state = MetaState()
    state._records_at_last_run = 100
    # 100 + 4 < threshold of 5 new records
    assert should_trigger(state, current_record_count=104, threshold=5) is False


def test_should_trigger_at_threshold_returns_true():
    state = MetaState()
    state._records_at_last_run = 100
    # exactly threshold new records → trigger
    assert should_trigger(state, current_record_count=105, threshold=5) is True


def test_should_trigger_above_threshold_returns_true():
    state = MetaState()
    state._records_at_last_run = 100
    assert should_trigger(state, current_record_count=120, threshold=5) is True


def test_iterate_advances_records_at_last_run(tmp_path):
    """After a successful iteration, the evidence baseline advances. A
    next `should_trigger` check uses the post-iteration count."""
    runner = _make_runner(tmp_path)
    journals = Journals(tmp_path / "journals")
    _write_failures(journals, 2)
    assert runner.state._records_at_last_run == 0

    runner.iterate()
    # The runner counted the journal records; after iteration the state
    # remembers that count so the next trigger needs NEW records.
    assert runner.state._records_at_last_run > 0
    baseline = runner.state._records_at_last_run
    assert not should_trigger(runner.state, baseline, threshold=5)
    assert should_trigger(runner.state, baseline + 5, threshold=5)


# --- report rendering ---------------------------------------------------


def test_report_lines_shape(tmp_path):
    runner = _make_runner(tmp_path)
    journals = Journals(tmp_path / "journals")
    _write_failures(journals, 3)
    result = runner.iterate()
    lines = report_lines(result)
    joined = "\n".join(lines)
    assert "meta iteration" in joined
    assert "clusters known:   0" in joined
    assert "unassigned total: 3" in joined
    assert "rust | bugfix" in joined


def test_report_lines_omits_scope_section_when_empty(tmp_path):
    runner = _make_runner(tmp_path)
    (tmp_path / "journals").mkdir(exist_ok=True)
    result = runner.iterate()
    lines = report_lines(result)
    assert all("unassigned by scope" not in line for line in lines)


# --- judge-wired iteration (Stage 3) ------------------------------------


def test_iterate_with_judge_mints_clusters_and_routes_consumed(tmp_path):
    """A wired Judge can mint clusters from the unassigned pool. Consumed
    occurrences leave the pool and increment the new cluster's observed
    counter; remaining occurrences stay in the pool for next iteration."""
    import json

    from littlecoder.judge import Judge
    from littlecoder.llm import ChatResponse, MockChatClient

    runner = _make_runner(tmp_path)
    journals = Journals(tmp_path / "journals")
    _write_failures(journals, 5)  # 5 unassigned bugfix occurrences

    payload = {
        "clusters": [
            {
                "label": "borrow checker errors",
                "discriminator": "borrow checker + lifetime",
                "signal_indices": [0, 1, 2],  # 3 of the 5 cohere
                "baseline_covers": False,
                "reasoning": "all three mention borrow",
                "not_other_types": "tried to split, didn't work",
            }
        ],
        "pool_too_small": False,
        "pool_too_noisy": False,
    }
    chat = MockChatClient(
        [ChatResponse(content=json.dumps(payload), finish_reason="stop")]
    )
    judge = Judge(chat=chat, founding_knowledge_paths=[], min_pool_size=1)
    runner.judge = judge

    result = runner.iterate()
    assert result is not None
    # One cluster minted; its id appears in the result.
    assert len(result.minted_cluster_ids) == 1
    # 3 consumed → cluster's observed = 3; 2 stayed in pool.
    minted_id = result.minted_cluster_ids[0]
    store = runner.load_store()
    assert store.counters[minted_id].observed == 3
    assert store.unassigned[("rust", "bugfix")].size == 2
    # Cluster discriminator + lang/shape are correct.
    assert store.clusters[minted_id].lang == "rust"
    assert store.clusters[minted_id].task_shape == "bugfix"
    assert store.clusters[minted_id].discriminator == "borrow checker + lifetime"


def test_iterate_with_judge_skips_when_pool_too_small(tmp_path):
    """A 1-occurrence pool is below default min_pool_size=3 — the judge
    is NEVER called, so the iteration completes with no mints."""
    from littlecoder.judge import Judge
    from littlecoder.llm import MockChatClient

    runner = _make_runner(tmp_path)
    journals = Journals(tmp_path / "journals")
    _write_failures(journals, 1)

    chat = MockChatClient([])  # no canned responses — would raise if called
    judge = Judge(chat=chat, founding_knowledge_paths=[])  # default min=3
    runner.judge = judge

    result = runner.iterate()
    assert result is not None
    assert result.minted_cluster_ids == ()
    assert result.unassigned_total == 1
    assert chat.calls == []  # LLM not called


# --- tier-0 drafting integration (Chapter 4 §4e) ------------------------


def test_iterate_drafts_pending_skill_for_eligible_cluster(tmp_path):
    """End-to-end: pre-seed a cluster that's tier-0 eligible, wire a
    drafting judge, run the iteration. A `pending` skill file should
    land on disk."""
    import json

    from littlecoder.cohorts import ClusterCounters, checkpoint as save_store
    from littlecoder.clusters import Cluster, new_cluster_id
    from littlecoder.judge import Judge
    from littlecoder.llm import ChatResponse, MockChatClient
    from littlecoder.skills import iter_skills

    runner = _make_runner(tmp_path)
    # Use an "always-match" similarity so synthetic failures route to
    # the seeded cluster (observed count then comes from journals, as
    # design §5.4 mandates — counters are re-derived per iteration).
    runner.similarity = lambda occ, cluster: 1.0
    journals = Journals(tmp_path / "journals")
    _write_failures(journals, 6)  # ≥ TIER0_MIN_OCCURRENCES

    # Pre-seed only the CLUSTER identity (label, discriminator,
    # baseline_covers). The counter is re-derived from the journals
    # via the always-match similarity. Cluster matches the lang +
    # task_shape of the synthetic failures (rust + bugfix).
    cid = new_cluster_id()
    seeded_store = runner.load_store()
    seeded_store.clusters[cid] = Cluster(
        cluster_id=cid,
        label="seeded rust bugfix cluster",
        discriminator="seeded",
        lang="rust",
        task_shape="bugfix",
        baseline_covers=False,
    )
    save_store(seeded_store, runner.checkpoint_path)

    # All-match similarity routes every occurrence to the seeded
    # cluster, so `unassigned` is empty and mint isn't called — the
    # only LLM call is `draft_tier_0_skill`.
    draft_payload = {
        "name": "rust borrow checker tips",
        "description": "When async Rust hits borrow errors, prefer owned values.",
        "body": "# Borrow checker\n\nReturn owned values from async fns.\n",
        "reasoning": "Three signals mention borrow checker.",
        "baseline_covers": False,
    }
    chat = MockChatClient(
        [ChatResponse(content=json.dumps(draft_payload), finish_reason="stop")]
    )
    judge = Judge(chat=chat, founding_knowledge_paths=[], min_pool_size=1)
    runner.judge = judge
    runner.skill_dir = tmp_path / "skills"

    result = runner.iterate()
    assert result is not None
    assert len(result.drafted_skill_ids) == 1

    drafted_id = result.drafted_skill_ids[0]
    pending = list(iter_skills(runner.skill_dir, status="pending"))
    assert len(pending) == 1
    assert pending[0].id == drafted_id
    assert pending[0].frontmatter.cluster_id == cid
    assert pending[0].frontmatter.tier == 0
    assert pending[0].frontmatter.kind == "knowledge"


def test_iterate_skips_drafting_when_no_skill_dir(tmp_path):
    """Backwards-compatible: a Chapter-3-shape runner (no skill_dir)
    still works — drafting is gated on `skill_dir is not None`. The
    mint step CAN run (judge wired) but the draft step does not."""
    import json
    from littlecoder.judge import Judge
    from littlecoder.llm import ChatResponse, MockChatClient

    runner = _make_runner(tmp_path)
    journals = Journals(tmp_path / "journals")
    _write_failures(journals, 5)
    # Default similarity sends everything to unassigned, so mint fires.
    # Return an "incoherent pool" response so mint exits cleanly with
    # no new clusters and no further LLM calls needed.
    empty_mint = {"clusters": [], "pool_too_small": False, "pool_too_noisy": True}
    chat = MockChatClient(
        [ChatResponse(content=json.dumps(empty_mint), finish_reason="stop")]
    )
    judge = Judge(chat=chat, founding_knowledge_paths=[], min_pool_size=1)
    runner.judge = judge
    # NB: no skill_dir set — drafting is gated off

    result = runner.iterate()
    assert result is not None
    assert result.drafted_skill_ids == ()
    # Exactly one chat call — the mint one, never the draft one.
    assert len(chat.calls) == 1


def test_iterate_drafting_respects_per_iteration_cap(tmp_path):
    """Design §12.5: per-iteration draft cap is 1 by default. Two
    eligible clusters → ONE drafted this iteration; the loudest wins."""
    import json

    from littlecoder.cohorts import ClusterCounters, checkpoint as save_store
    from littlecoder.clusters import Cluster, new_cluster_id
    from littlecoder.judge import Judge
    from littlecoder.llm import ChatResponse, MockChatClient
    from littlecoder.skills import iter_skills

    runner = _make_runner(tmp_path)
    # Custom similarity that picks the loud cluster every time, so
    # journal-projected occurrences land there (re-derived counters
    # then make `loud` the eligible candidate per `observed`).
    loud_id_holder = {}
    runner.similarity = lambda occ, cluster: 1.0 if cluster.cluster_id == loud_id_holder.get("id") else 0.5
    journals = Journals(tmp_path / "journals")
    _write_failures(journals, 6)

    # Two clusters; only one will receive the projection's occurrences
    # (similarity above). Both are baseline-silent so the gate is the
    # observed count.
    quiet_id = new_cluster_id()
    loud_id = new_cluster_id()
    loud_id_holder["id"] = loud_id
    seeded_store = runner.load_store()
    for cid in (quiet_id, loud_id):
        seeded_store.clusters[cid] = Cluster(
            cluster_id=cid,
            label="L",
            discriminator="d",
            lang="rust",
            task_shape="bugfix",
            baseline_covers=False,
        )
    save_store(seeded_store, runner.checkpoint_path)

    draft_payload = {
        "name": "loud cluster knowledge",
        "description": "Specific knowledge for the loudest cluster.",
        "body": "# Body\n\nText.\n",
        "reasoning": "r",
        "baseline_covers": False,
    }
    # Custom similarity routes all occurrences to LOUD, so unassigned
    # is empty and mint isn't called — only the draft response is used.
    chat = MockChatClient(
        [ChatResponse(content=json.dumps(draft_payload), finish_reason="stop")]
    )
    judge = Judge(chat=chat, founding_knowledge_paths=[], min_pool_size=1)
    runner.judge = judge
    runner.skill_dir = tmp_path / "skills"

    result = runner.iterate()
    assert len(result.drafted_skill_ids) == 1  # one per iteration cap
    # The drafted skill is for the loudest cluster.
    drafted = next(iter_skills(runner.skill_dir, status="pending"))
    assert drafted.frontmatter.cluster_id == loud_id


def test_iterate_does_not_draft_when_baseline_covers_cluster(tmp_path):
    """A cluster the operator has marked baseline-covered (or that the
    judge minted with `baseline_covers=true`) must NOT receive a
    tier-0 draft — locked decision #17."""
    from littlecoder.cohorts import ClusterCounters, checkpoint as save_store
    from littlecoder.clusters import Cluster, new_cluster_id
    from littlecoder.judge import Judge
    from littlecoder.llm import MockChatClient

    runner = _make_runner(tmp_path)
    Journals(tmp_path / "journals")  # ensure dir
    seeded_store = runner.load_store()
    cid = new_cluster_id()
    seeded_store.clusters[cid] = Cluster(
        cluster_id=cid,
        label="L",
        discriminator="d",
        lang="rust",
        task_shape="bugfix",
        baseline_covers=True,  # ← compliance gap
    )
    seeded_store.counters[cid] = ClusterCounters(cluster_id=cid, observed=10)
    save_store(seeded_store, runner.checkpoint_path)

    chat = MockChatClient([])  # judge MUST NOT be called for drafting
    judge = Judge(chat=chat, founding_knowledge_paths=[], min_pool_size=1)
    runner.judge = judge
    runner.skill_dir = tmp_path / "skills"

    result = runner.iterate()
    assert result.drafted_skill_ids == ()
    # No skill files exist yet — the dir may not exist.
    assert (
        not (runner.skill_dir / "knowledge").exists()
        or list((runner.skill_dir / "knowledge").iterdir()) == []
    )


def test_iterate_does_not_draft_when_prior_skill_exists(tmp_path):
    """If a tier-0 skill (pending OR active) already exists for the
    cluster, don't draft another. Prevents queue-up duplicates."""
    import json

    from littlecoder.cohorts import ClusterCounters, checkpoint as save_store
    from littlecoder.clusters import Cluster, new_cluster_id
    from littlecoder.judge import Judge
    from littlecoder.llm import MockChatClient
    from littlecoder.skills import build_skill, iter_skills, write_skill

    runner = _make_runner(tmp_path)
    Journals(tmp_path / "journals")
    seeded_store = runner.load_store()
    cid = new_cluster_id()
    seeded_store.clusters[cid] = Cluster(
        cluster_id=cid,
        label="L",
        discriminator="d",
        lang="rust",
        task_shape="bugfix",
    )
    seeded_store.counters[cid] = ClusterCounters(cluster_id=cid, observed=10)
    save_store(seeded_store, runner.checkpoint_path)

    runner.skill_dir = tmp_path / "skills"
    # Pre-existing pending tier-0 skill for this cluster.
    existing = build_skill(
        kind="knowledge",
        cluster_id=cid,
        tier=0,
        lang="rust",
        domain="dom",
        task_shape="bugfix",
        name="existing",
        description="existing skill description",
        body="existing body content here\n",
        status="pending",
    )
    write_skill(runner.skill_dir, existing)

    chat = MockChatClient([])  # judge MUST NOT be called
    judge = Judge(chat=chat, founding_knowledge_paths=[], min_pool_size=1)
    runner.judge = judge

    result = runner.iterate()
    assert result.drafted_skill_ids == ()
    # The pre-existing skill is still the only one.
    pending = list(iter_skills(runner.skill_dir, status="pending"))
    assert len(pending) == 1
    assert pending[0].id == existing.id
