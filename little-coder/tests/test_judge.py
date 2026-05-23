"""The judge — cluster minting from the unassigned pool (design §5.2,
§10.1, Chapter 3 §3e).

These tests drive the judge against a MockChatClient: prompt assembly,
response parsing, schema enforcement (baseline_covers required), bounds
checks (signal_indices non-overlap, in-range), and the "no minting from
a thin pool" rule. The actual LLM behavior isn't tested here — that's
verified by the operator's dry-run pass (design §13, open item #2).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from littlecoder.clusters import Occurrence
from littlecoder.judge import (
    ClusterProposal,
    Judge,
    JudgeOutput,
    build_messages,
    parse_response,
)
from littlecoder.llm import ChatResponse, LlmError, MockChatClient


def _occ(text: str, task_id: str = "01J0000000000000000000A", kind: str = "test_failure"):
    return Occurrence(
        task_id=task_id,
        ts="2026-05-23T00:00:00.000Z",
        lang="rust",
        task_shape="bugfix",
        repo="https://github.com/x/y",
        signal_text=text,
        source_kind=kind,
    )


# --- prompt assembly ----------------------------------------------------


def test_build_messages_includes_founding_knowledge(tmp_path):
    fk = tmp_path / "principles.md"
    fk.write_text("Single Responsibility — each function does ONE thing.", encoding="utf-8")
    msgs = build_messages(
        [_occ("borrow checker")],
        lang="rust",
        task_shape="bugfix",
        founding_knowledge_paths=[fk],
    )
    assert msgs[0].role == "system"
    assert "META judge" in msgs[0].content
    assert "baseline_covers" in msgs[0].content
    # User message carries the founding knowledge inlined.
    assert "principles.md" in msgs[1].content
    assert "Single Responsibility" in msgs[1].content
    assert "borrow checker" in msgs[1].content


def test_build_messages_handles_missing_knowledge_files(tmp_path):
    """Missing files are skipped without raising — the judge still gets a
    pool, even if the baseline can't be inlined."""
    msgs = build_messages(
        [_occ("x")],
        lang="rust",
        task_shape="bugfix",
        founding_knowledge_paths=[tmp_path / "does-not-exist.md"],
    )
    assert "no founding-knowledge files" in msgs[1].content


def test_build_messages_indexes_pool_from_zero():
    msgs = build_messages(
        [_occ("a"), _occ("b"), _occ("c")],
        lang="rust",
        task_shape="bugfix",
        founding_knowledge_paths=[],
    )
    user = msgs[1].content
    assert "[0]" in user and "[1]" in user and "[2]" in user
    assert "POOL SIZE: 3" in user


# --- response parsing ---------------------------------------------------


def test_parse_response_round_trips_valid_output():
    payload = {
        "clusters": [
            {
                "label": "lifetime errors",
                "discriminator": "borrow checker + lifetime annotations",
                "signal_indices": [0, 2],
                "baseline_covers": False,
                "reasoning": "both signals mention borrow checker",
                "not_other_types": "tried to argue these are distinct — couldn't",
            }
        ],
        "pool_too_small": False,
        "pool_too_noisy": False,
    }
    output = parse_response(json.dumps(payload))
    assert len(output.clusters) == 1
    assert output.clusters[0].baseline_covers is False
    assert output.clusters[0].signal_indices == [0, 2]


def test_parse_response_strips_json_code_fence():
    """LLMs sometimes ignore 'JSON only' and wrap in ```json … ```; the
    parser tolerates it."""
    payload = '```json\n{"clusters": [], "pool_too_small": true, "pool_too_noisy": false}\n```'
    output = parse_response(payload)
    assert output.pool_too_small is True
    assert output.clusters == []


def test_parse_response_rejects_non_json():
    with pytest.raises(LlmError, match="not JSON"):
        parse_response("this is just prose")


def test_parse_response_rejects_missing_baseline_covers():
    """baseline_covers is REQUIRED (locked decision #17). A judge that
    omits it isn't doing its job — fail loud, don't default."""
    bad = {
        "clusters": [
            {
                "label": "x",
                "discriminator": "y",
                "signal_indices": [0],
                # baseline_covers missing
            }
        ]
    }
    with pytest.raises(LlmError, match="schema"):
        parse_response(json.dumps(bad))


def test_parse_response_rejects_empty_label():
    bad = {
        "clusters": [
            {
                "label": "",
                "discriminator": "y",
                "signal_indices": [0],
                "baseline_covers": False,
            }
        ]
    }
    with pytest.raises(LlmError, match="schema"):
        parse_response(json.dumps(bad))


# --- Judge.mint_clusters ------------------------------------------------


def _judge_with_response(payload: dict) -> tuple[Judge, MockChatClient]:
    chat = MockChatClient([ChatResponse(content=json.dumps(payload), finish_reason="stop")])
    judge = Judge(chat=chat, founding_knowledge_paths=[], min_pool_size=3)
    return judge, chat


def test_mint_skips_thin_pool_without_calling_llm():
    """Below `min_pool_size`, the judge doesn't fire at all — saves
    inference budget and avoids spurious clusters."""
    chat = MockChatClient([])  # no canned response — LLM must NOT be called
    judge = Judge(chat=chat, founding_knowledge_paths=[], min_pool_size=3)
    result = judge.mint_clusters([_occ("a"), _occ("b")], lang="rust", task_shape="bugfix")
    assert result.new_clusters == []
    assert result.consumed == []
    assert result.raw_output.pool_too_small is True
    assert chat.calls == []


def test_mint_returns_new_clusters_with_fresh_ids():
    payload = {
        "clusters": [
            {
                "label": "lifetime errors",
                "discriminator": "borrow checker",
                "signal_indices": [0, 1],
                "baseline_covers": False,
                "reasoning": "r",
                "not_other_types": "n",
            }
        ],
        "pool_too_small": False,
        "pool_too_noisy": False,
    }
    judge, _ = _judge_with_response(payload)
    pool = [_occ("borrow x"), _occ("borrow y"), _occ("trait z")]
    result = judge.mint_clusters(pool, lang="rust", task_shape="bugfix")
    assert len(result.new_clusters) == 1
    cluster = result.new_clusters[0]
    assert cluster.label == "lifetime errors"
    assert cluster.discriminator == "borrow checker"
    assert cluster.lang == "rust"
    assert cluster.task_shape == "bugfix"
    assert len(cluster.cluster_id) == 16  # 64-bit hex from new_cluster_id
    assert len(result.consumed) == 2


def test_mint_enforces_no_overlap_across_proposals():
    """If two proposals both claim index 0, only the FIRST gets it. The
    second silently drops the duplicate (an LLM mistake at the margin
    shouldn't abort the whole iteration)."""
    payload = {
        "clusters": [
            {
                "label": "Aa",
                "discriminator": "borrow",
                "signal_indices": [0, 1],
                "baseline_covers": False,
            },
            {
                "label": "Bb",
                "discriminator": "trait",
                "signal_indices": [0, 2],  # overlap on 0
                "baseline_covers": False,
            },
        ],
        "pool_too_small": False,
        "pool_too_noisy": False,
    }
    judge, _ = _judge_with_response(payload)
    pool = [_occ("x"), _occ("y"), _occ("z")]
    result = judge.mint_clusters(pool, lang="rust", task_shape="bugfix")
    # Both clusters minted, but the consumed-occurrence set is size 3:
    # cluster A claims [0,1], cluster B claims [2] (0 was already taken).
    assert len(result.new_clusters) == 2
    consumed_texts = {o.signal_text for o in result.consumed}
    assert consumed_texts == {"x", "y", "z"}


def test_mint_drops_out_of_range_indices():
    """A pool of 3 with a claim on index 99 → the 99 is dropped, the
    other claims stand."""
    payload = {
        "clusters": [
            {
                "label": "Aa",
                "discriminator": "dd",
                "signal_indices": [0, 99],
                "baseline_covers": False,
            }
        ],
        "pool_too_small": False,
        "pool_too_noisy": False,
    }
    judge, _ = _judge_with_response(payload)
    pool = [_occ("x"), _occ("y"), _occ("z")]
    result = judge.mint_clusters(pool, lang="rust", task_shape="bugfix")
    assert len(result.new_clusters) == 1
    assert len(result.consumed) == 1
    assert result.consumed[0].signal_text == "x"


def test_mint_proposal_with_only_invalid_indices_drops():
    """A proposal that claims only out-of-range / duplicate indices ⇒
    no cluster minted from it (would-be empty cluster). Same iteration
    can still mint other proposals."""
    payload = {
        "clusters": [
            {"label": "Aa", "discriminator": "dd", "signal_indices": [99, 100], "baseline_covers": False},
            {"label": "Bb", "discriminator": "dd", "signal_indices": [0], "baseline_covers": True},
        ],
        "pool_too_small": False,
        "pool_too_noisy": False,
    }
    judge, _ = _judge_with_response(payload)
    pool = [_occ("x")]
    # min_pool_size is 3 by default; relax for this test.
    judge.min_pool_size = 1
    result = judge.mint_clusters(pool, lang="rust", task_shape="bugfix")
    assert len(result.new_clusters) == 1
    assert result.new_clusters[0].label == "Bb"


def test_mint_preserves_baseline_covers_per_proposal():
    """The `baseline_covers` flag flows through to `JudgeOutput` (and the
    operator surface reads it from `MintingResult.raw_output`)."""
    payload = {
        "clusters": [
            {"label": "Knowledge gap", "discriminator": "dd", "signal_indices": [0], "baseline_covers": False},
            {"label": "Compliance gap", "discriminator": "dd2", "signal_indices": [1], "baseline_covers": True},
        ],
        "pool_too_small": False,
        "pool_too_noisy": False,
    }
    judge, _ = _judge_with_response(payload)
    judge.min_pool_size = 1
    pool = [_occ("a"), _occ("b")]
    result = judge.mint_clusters(pool, lang="rust", task_shape="bugfix")
    flags = [c.baseline_covers for c in result.raw_output.clusters]
    assert flags == [False, True]
    labels = [c.label for c in result.new_clusters]
    assert "Knowledge gap" in labels and "Compliance gap" in labels


def test_mint_truncates_oversized_pool():
    """A pool larger than `max_pool_size` is windowed — the judge sees
    the prefix; the rest stays in the pool for next iteration."""
    payload = {"clusters": [], "pool_too_small": False, "pool_too_noisy": True}
    chat = MockChatClient(
        [ChatResponse(content=json.dumps(payload), finish_reason="stop")]
    )
    judge = Judge(
        chat=chat, founding_knowledge_paths=[], min_pool_size=1, max_pool_size=5
    )
    pool = [_occ(f"sig-{i}") for i in range(20)]
    result = judge.mint_clusters(pool, lang="rust", task_shape="bugfix")
    assert chat.calls
    # The user message saw only 5 items, not 20.
    assert "POOL SIZE: 5" in chat.calls[0][1].content


def test_judge_aborts_when_chat_raises_llmerror():
    """A transport-level failure in the LLM client surfaces as LlmError —
    `meta.iterate` will treat this as 'iteration failed, defer'. The
    judge does NOT swallow it (design §12.10: nothing fails open)."""

    class BoomChat:
        def chat(self, *args, **kwargs):
            raise LlmError("backend down")

    judge = Judge(chat=BoomChat(), founding_knowledge_paths=[], min_pool_size=1)
    pool = [_occ("x"), _occ("y"), _occ("z")]
    with pytest.raises(LlmError, match="backend down"):
        judge.mint_clusters(pool, lang="rust", task_shape="bugfix")


# --- tier-0 drafting (Chapter 4 §4e) ------------------------------------


from littlecoder.clusters import Cluster
from littlecoder.cohorts import ClusterCounters
from littlecoder.judge import DraftOutput, parse_draft_response


def _cluster(cid="aaaaaaaaaaaaaaaa", *, baseline_covers=False):
    return Cluster(
        cluster_id=cid,
        label="async lifetime errors",
        discriminator="borrow checker on async fns",
        lang="rust",
        task_shape="bugfix",
        baseline_covers=baseline_covers,
    )


def _draft_payload(**overrides) -> dict:
    base = {
        "name": "async lifetime errors in rust",
        "description": (
            "When the agent writes async Rust and the borrow checker complains "
            "about captured references, prefer owning over borrowing."
        ),
        "body": "# Async lifetimes\n\nReturn owned values from async fns.\n",
        "reasoning": "Three signals mention borrow checker on async; fix is consistent.",
        "baseline_covers": False,
    }
    base.update(overrides)
    return base


def test_parse_draft_response_accepts_canonical_shape():
    output = parse_draft_response(json.dumps(_draft_payload()))
    assert isinstance(output, DraftOutput)
    assert output.baseline_covers is False
    assert output.body.startswith("# Async")


def test_parse_draft_response_rejects_missing_description():
    bad = _draft_payload()
    del bad["description"]
    with pytest.raises(LlmError, match="schema"):
        parse_draft_response(json.dumps(bad))


def test_parse_draft_response_rejects_too_short_body():
    """`min_length=10` on body — the judge can't ship an empty knowledge
    entry."""
    with pytest.raises(LlmError, match="schema"):
        parse_draft_response(json.dumps(_draft_payload(body="x")))


def test_parse_draft_response_strips_code_fence():
    payload = "```json\n" + json.dumps(_draft_payload()) + "\n```"
    output = parse_draft_response(payload)
    assert output.name == "async lifetime errors in rust"


def test_draft_tier_0_skill_returns_drafted_output():
    """Happy path — judge writes a tier-0 entry; meta materializes."""
    chat = MockChatClient([ChatResponse(content=json.dumps(_draft_payload()), finish_reason="stop")])
    judge = Judge(chat=chat, founding_knowledge_paths=[], min_pool_size=1)
    counter = ClusterCounters("aaaaaaaaaaaaaaaa", observed=8)
    result = judge.draft_tier_0_skill(_cluster(), counter, ["borrow checker", "borrow checker", "lifetime mismatch"])
    assert result.escaped_to_compliance is False
    assert result.output is not None
    assert result.output.name == "async lifetime errors in rust"


def test_draft_tier_0_skill_escapes_when_judge_says_baseline_covers():
    """The judge sees signals + founding knowledge in context; if it
    realises baseline DOES cover the cluster after all, it sets
    `baseline_covers: true`. Meta must NOT ship a tier-0 entry."""
    payload = _draft_payload(baseline_covers=True)
    chat = MockChatClient([ChatResponse(content=json.dumps(payload), finish_reason="stop")])
    judge = Judge(chat=chat, founding_knowledge_paths=[], min_pool_size=1)
    counter = ClusterCounters("aaaaaaaaaaaaaaaa", observed=8)
    result = judge.draft_tier_0_skill(_cluster(), counter, ["x", "y"])
    assert result.escaped_to_compliance is True
    assert result.output is None


def test_draft_tier_0_skill_includes_founding_knowledge_in_prompt(tmp_path):
    """Locked decision #17 — founding knowledge MUST be in context so
    the judge can second-guess the baseline-covers flag."""
    fk = tmp_path / "environment.md"
    fk.write_text("# Environment\n\nThe agent runs in open-terminal.", encoding="utf-8")
    chat = MockChatClient([ChatResponse(content=json.dumps(_draft_payload()), finish_reason="stop")])
    judge = Judge(chat=chat, founding_knowledge_paths=[fk], min_pool_size=1)
    counter = ClusterCounters("aaaaaaaaaaaaaaaa", observed=8)
    judge.draft_tier_0_skill(_cluster(), counter, ["x"])
    # The call's messages should carry the founding knowledge.
    fk_visible = any(
        "Environment" in msg.content for msg in chat.calls[0]
    )
    assert fk_visible


# --- tier-1 drafting (Chapter 4 §4e + §5.7) ----------------------------


from littlecoder.judge import Tier1DraftOutput, parse_tier_1_response


def _tier1_payload(**overrides) -> dict:
    base = {
        "kind": "tool_craft",
        "name": "always pass --no-pager to git in headless contexts",
        "description": "Enforce git --no-pager so paging never blocks the agent.",
        "body": "# git --no-pager\n\nUse git --no-pager log instead of git log.\n",
        "argument_for_tool_craft": "the gap is in the agent's flag choice",
        "argument_for_plan_slot": "could add a plan step but flag fix is tighter",
        "argument_for_pick": "tool-craft is the more direct fix",
        "baseline_covers": False,
    }
    base.update(overrides)
    return base


def test_parse_tier_1_accepts_tool_craft_or_plan_slot():
    out_tc = parse_tier_1_response(json.dumps(_tier1_payload(kind="tool_craft")))
    out_ps = parse_tier_1_response(json.dumps(_tier1_payload(kind="plan_slot")))
    assert out_tc.kind == "tool_craft"
    assert out_ps.kind == "plan_slot"


def test_parse_tier_1_rejects_unknown_kind():
    bad = _tier1_payload(kind="invented")
    with pytest.raises(LlmError, match="kind must be"):
        parse_tier_1_response(json.dumps(bad))


def test_draft_tier_1_skill_returns_output():
    chat = MockChatClient(
        [ChatResponse(content=json.dumps(_tier1_payload()), finish_reason="stop")]
    )
    judge = Judge(chat=chat, founding_knowledge_paths=[], min_pool_size=1)
    counter = ClusterCounters("aaaaaaaaaaaaaaaa", observed=30)
    result = judge.draft_tier_1_skill(_cluster(), counter, ["s1", "s2", "s3"])
    assert result.escaped_to_compliance is False
    assert result.output is not None
    assert result.output.kind == "tool_craft"


def test_draft_tier_1_escapes_to_compliance_when_baseline_covers():
    payload = _tier1_payload(baseline_covers=True)
    chat = MockChatClient(
        [ChatResponse(content=json.dumps(payload), finish_reason="stop")]
    )
    judge = Judge(chat=chat, founding_knowledge_paths=[], min_pool_size=1)
    counter = ClusterCounters("aaaaaaaaaaaaaaaa", observed=30)
    result = judge.draft_tier_1_skill(_cluster(), counter, ["s"])
    assert result.escaped_to_compliance is True
    assert result.output is None


# --- tier-3 §6 justification gate (Chapter 5 §5c) ----------------------


from littlecoder.judge import (
    InterventionRecord,
    Tier3Justification,
    parse_justification_response,
)


def _justify_payload(**overrides) -> dict:
    base = {
        "cluster_persistence": "cluster has persisted across tier-0 and tier-1 with rate unchanged at 0.20/task",
        "interventions_tried": [
            {
                "skill_id": "skill-abc",
                "tier": 0,
                "kind": "knowledge",
                "why_failed": "agent ignored the guidance under load",
            },
            {
                "skill_id": "skill-def",
                "tier": 1,
                "kind": "tool_craft",
                "why_failed": "tool-craft only catches one shape; cluster has multiple",
            },
        ],
        "no_skill_argument": (
            "No additional skill could close this: the cluster manifests in "
            "the agent's INNER LOOP control flow — specifically in how the "
            "fast-path planner decides to skip the test-running step. No "
            "knowledge entry, tool-craft, plan-slot, or routing rule can "
            "alter the inner loop's decision-tree shape from outside."
        ),
        "proposed_change": "modify the planner's fast-path to consult a routing-rule-evaluator before skipping test-running",
        "code_surface": "node",
        "expected_effect": "the cluster's failure rate drops because planner now consults routing rules before skipping",
        "refused": False,
        "refusal_reason": "",
    }
    base.update(overrides)
    return base


def test_parse_justification_accepts_full_argument():
    out = parse_justification_response(json.dumps(_justify_payload()))
    assert isinstance(out, Tier3Justification)
    assert out.refused is False
    assert out.code_surface == "node"
    assert len(out.interventions_tried) == 2


def test_parse_justification_rejects_bad_code_surface():
    bad = _justify_payload(code_surface="wasm")
    with pytest.raises(LlmError, match="code_surface"):
        parse_justification_response(json.dumps(bad))


def test_parse_justification_rejects_thin_no_skill_argument():
    """Design §6(3) is THE KEY GATE — a non-refused justification with
    a weak `no_skill_argument` must fail. We require ≥ 50 chars."""
    bad = _justify_payload(no_skill_argument="dunno")
    with pytest.raises(LlmError, match="no_skill_argument"):
        parse_justification_response(json.dumps(bad))


def test_parse_justification_accepts_refused_without_code_surface():
    """A REFUSED justification doesn't need code_surface — no change is
    being proposed."""
    payload = _justify_payload(
        refused=True,
        refusal_reason="a routing-rule could close this without touching code",
        code_surface="",  # blank is OK when refused
        no_skill_argument="",  # also blank — same reason
    )
    out = parse_justification_response(json.dumps(payload))
    assert out.refused is True
    assert out.refusal_reason.startswith("a routing-rule")


def test_justify_tier_3_returns_refused_when_judge_refuses():
    """The gate: refused means operator MUST NOT proceed; caller routes
    the cluster back to skill drafting."""
    payload = _justify_payload(
        refused=True,
        refusal_reason="skill could close this",
        code_surface="",
        no_skill_argument="",
    )
    chat = MockChatClient(
        [ChatResponse(content=json.dumps(payload), finish_reason="stop")]
    )
    judge = Judge(chat=chat, founding_knowledge_paths=[])
    result = judge.justify_tier_3(
        _cluster(),
        ClusterCounters("aaaaaaaaaaaaaaaa", observed=100),
        ["sig1", "sig2"],
        [{"skill_id": "skill-abc", "tier": 0, "kind": "knowledge", "status": "active"}],
    )
    assert result.refused is True
    assert result.justified is False


def test_justify_tier_3_full_justification_marks_justified():
    payload = _justify_payload()
    chat = MockChatClient(
        [ChatResponse(content=json.dumps(payload), finish_reason="stop")]
    )
    judge = Judge(chat=chat, founding_knowledge_paths=[])
    result = judge.justify_tier_3(
        _cluster(),
        ClusterCounters("aaaaaaaaaaaaaaaa", observed=100),
        ["sig1"],
        [{"skill_id": "x", "tier": 0, "kind": "knowledge", "status": "active"}],
    )
    assert result.refused is False
    assert result.justified is True
    assert result.justification.code_surface == "node"


def test_draft_tier_0_skill_windows_oversized_signal_sample():
    """`max_signals=16` (default) — caller can pass more; only the
    prefix lands in the prompt. Bounds prompt size."""
    chat = MockChatClient([ChatResponse(content=json.dumps(_draft_payload()), finish_reason="stop")])
    judge = Judge(chat=chat, founding_knowledge_paths=[], min_pool_size=1)
    counter = ClusterCounters("aaaaaaaaaaaaaaaa", observed=50)
    many_signals = [f"signal-{i}" for i in range(50)]
    judge.draft_tier_0_skill(_cluster(), counter, many_signals)
    # The user message includes signal-0 but NOT signal-20 (windowed to 16).
    user_msg = next(m for m in chat.calls[0] if m.role == "user").content
    assert "signal-0" in user_msg
    assert "signal-15" in user_msg
    assert "signal-20" not in user_msg
