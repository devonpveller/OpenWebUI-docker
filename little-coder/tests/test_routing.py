"""Tier-2 routing rules (design §5.8, Chapter 5 §5b).

Tests cover: YAML round-trip, atomic write, staged-freeze gate (both
knowledge-gap and compliance-gap paths), deterministic exploration
scheduler, and the rule matcher.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from littlecoder.clusters import Cluster
from littlecoder.cohorts import ClusterCounters
from littlecoder.routing import (
    DEFAULT_EXPLORATION_RATE,
    FreezeVerdict,
    RoutingDecision,
    RoutingRule,
    RoutingRuleFormatError,
    build_rule,
    evaluate,
    explore_this_task,
    iter_rules,
    list_rules,
    matches_task,
    new_rule_id,
    parse_rule,
    serialize_rule,
    staged_freeze_allows,
    write_rule,
)


def _rule(**overrides):
    base = dict(
        cluster_id="cl0001",
        lang="rust",
        task_shape="bugfix",
        action="use_reasoning_model",
        status="active",
    )
    base.update(overrides)
    return build_rule(**base)


# --- build_rule / parse_rule round-trip --------------------------------


def test_build_rule_defaults_pending_status():
    """Tier-2 NEVER auto-merges. Drafts land pending by default."""
    rule = build_rule(
        cluster_id="cl0001",
        lang="rust",
        task_shape="bugfix",
        action="skip_planner",
    )
    assert rule.status == "pending"


def test_round_trip_through_yaml():
    rule = _rule()
    text = serialize_rule(rule)
    again = parse_rule(text)
    assert again.id == rule.id
    assert again.action == rule.action
    assert again.cluster_id == rule.cluster_id


def test_invalid_action_rejected():
    with pytest.raises(RoutingRuleFormatError, match="action"):
        _rule(action="invented_action")


def test_invalid_yaml_rejected():
    with pytest.raises(RoutingRuleFormatError, match="YAML"):
        parse_rule(": : not yaml :")


def test_non_mapping_yaml_rejected():
    with pytest.raises(RoutingRuleFormatError, match="mapping"):
        parse_rule("- a\n- b\n")


# --- atomic write -------------------------------------------------------


def test_write_rule_lands_in_routing_subdir(tmp_path):
    rule = _rule()
    target = write_rule(tmp_path, rule)
    assert target.parent.name == "routing"
    assert target.name == f"{rule.id}.yaml"
    assert target.exists()


def test_write_rule_is_atomic(tmp_path, monkeypatch):
    """`.tmp` + rename, same discipline as skills."""
    rule = _rule()
    seen = {"tmp_existed": False}
    real_replace = os.replace

    def spy_replace(src, dst):
        assert str(src).endswith(".tmp")
        assert Path(src).exists()
        seen["tmp_existed"] = True
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", spy_replace)
    write_rule(tmp_path, rule)
    assert seen["tmp_existed"]


def test_iter_rules_skips_tmp_and_corrupt(tmp_path):
    rule = _rule()
    write_rule(tmp_path, rule)
    # Drop a stale .tmp and a corrupt .yaml.
    (tmp_path / "routing" / "stray.yaml.tmp").write_text("garbage", encoding="utf-8")
    (tmp_path / "routing" / "corrupt.yaml").write_text(": : :", encoding="utf-8")
    found = list(iter_rules(tmp_path))
    assert [r.id for r in found] == [rule.id]


def test_list_rules_defaults_to_active(tmp_path):
    write_rule(tmp_path, _rule(status="active"))
    write_rule(tmp_path, _rule(status="retired"))
    write_rule(tmp_path, _rule(status="pending"))
    assert len(list_rules(tmp_path)) == 1  # only active


# --- staged-freeze (design §5.8) ---------------------------------------


def _c(cid="c1", *, baseline_covers=False):
    return Cluster(
        cluster_id=cid, label="L", discriminator="d",
        lang="rust", task_shape="bugfix", baseline_covers=baseline_covers,
    )


def test_freeze_blocks_knowledge_cluster_without_tier_0():
    v = staged_freeze_allows(
        _c(), ClusterCounters("c1", observed=100), prior={"c1": {1}}
    )
    assert v.allowed is False
    assert "tier" in v.reason


def test_freeze_blocks_knowledge_cluster_without_tier_1():
    v = staged_freeze_allows(
        _c(), ClusterCounters("c1", observed=100), prior={"c1": {0}}
    )
    assert v.allowed is False


def test_freeze_allows_knowledge_cluster_with_both_tiers_and_enough_evidence():
    v = staged_freeze_allows(
        _c(),
        ClusterCounters("c1", observed=50),
        prior={"c1": {0, 1}},
    )
    assert v.allowed is True
    assert "cleared" in v.reason


def test_freeze_compliance_cluster_only_needs_tier_1():
    """Locked #17 — baseline-covered clusters skip tier-0. Tier-1 alone
    is enough prior, plus enough observed evidence."""
    v = staged_freeze_allows(
        _c(baseline_covers=True),
        ClusterCounters("c1", observed=50),
        prior={"c1": {1}},
    )
    assert v.allowed is True


def test_freeze_blocks_when_observed_too_low():
    v = staged_freeze_allows(
        _c(),
        ClusterCounters("c1", observed=5),  # below 20
        prior={"c1": {0, 1}},
    )
    assert v.allowed is False
    assert "insufficient" in v.reason


def test_freeze_blocks_when_tier_2_already_shipped():
    v = staged_freeze_allows(
        _c(),
        ClusterCounters("c1", observed=100),
        prior={"c1": {0, 1, 2}},
    )
    assert v.allowed is False
    assert "already live" in v.reason


# --- exploration scheduler (design §5.8) ------------------------------


def test_exploration_rate_0_never_explores():
    rule = _rule(exploration_rate=0.0)
    for tid in (f"task-{i}" for i in range(100)):
        assert explore_this_task(rule, tid) is False


def test_exploration_rate_1_always_explores():
    rule = _rule(exploration_rate=1.0)
    for tid in (f"task-{i}" for i in range(100)):
        assert explore_this_task(rule, tid) is True


def test_exploration_is_deterministic_per_task():
    rule = _rule(exploration_rate=0.5)
    for tid in ("a", "b", "c"):
        assert explore_this_task(rule, tid) == explore_this_task(rule, tid)


def test_exploration_rate_approximately_correct():
    """At 5% over 10000 tasks, expect ~500 to explore. Allow ±30%
    tolerance to account for sampling noise on the hash."""
    rule = _rule(exploration_rate=0.05)
    explored = sum(
        1 for i in range(10000) if explore_this_task(rule, f"task-{i}")
    )
    assert 350 <= explored <= 650, f"got {explored} explored, expected ~500"


def test_different_rules_explore_on_different_tasks():
    """Two rules with different ids → different exploration patterns
    (the rule id is mixed into the hash so they don't move in lockstep)."""
    rule_a = build_rule(
        rule_id="aaaaaaaaaaaaaaaa",
        cluster_id="cl01", lang="rust", task_shape="bugfix",
        action="use_reasoning_model", exploration_rate=0.5,
    )
    rule_b = build_rule(
        rule_id="bbbbbbbbbbbbbbbb",
        cluster_id="cl01", lang="rust", task_shape="bugfix",
        action="use_reasoning_model", exploration_rate=0.5,
    )
    disagreements = sum(
        1 for i in range(500)
        if explore_this_task(rule_a, f"t-{i}") != explore_this_task(rule_b, f"t-{i}")
    )
    # At 50% each with independent hashes, ~50% disagreement expected.
    assert 200 < disagreements < 300


# --- matchers + evaluate ----------------------------------------------


def test_matches_task_exact_lang_and_shape():
    rule = _rule(lang="rust", task_shape="bugfix")
    assert matches_task(rule, lang="rust", task_shape="bugfix") is True
    assert matches_task(rule, lang="python", task_shape="bugfix") is False
    assert matches_task(rule, lang="rust", task_shape="refactor") is False


def test_matches_task_lang_wildcard():
    rule = _rule(lang="*", task_shape="bugfix")
    assert matches_task(rule, lang="rust", task_shape="bugfix") is True
    assert matches_task(rule, lang="python", task_shape="bugfix") is True


def test_matches_task_tool_when_set():
    rule = build_rule(
        cluster_id="cl01", lang="rust", task_shape="bugfix",
        action="use_fast_model", tool="pytest",
    )
    assert matches_task(rule, lang="rust", task_shape="bugfix", tool="pytest")
    assert not matches_task(rule, lang="rust", task_shape="bugfix", tool="cargo")
    assert not matches_task(rule, lang="rust", task_shape="bugfix", tool=None)


def test_evaluate_composes_applied_and_explored():
    """The planner-process calls `evaluate` with all active rules; it
    sees which actions to apply + which were intentionally skipped."""
    r1 = build_rule(
        rule_id="rule1xxxxxxxxxxx",
        cluster_id="cl01", lang="rust", task_shape="bugfix",
        action="use_reasoning_model", exploration_rate=0.0,
    )
    r2 = build_rule(
        rule_id="rule2xxxxxxxxxxx",
        cluster_id="cl01", lang="rust", task_shape="bugfix",
        action="skip_planner", exploration_rate=1.0,  # always explore (skip)
    )
    r3 = build_rule(
        rule_id="rule3xxxxxxxxxxx",
        cluster_id="cl01", lang="python", task_shape="bugfix",
        action="use_fast_model", exploration_rate=0.0,
    )
    decision = evaluate(
        [r1, r2, r3],
        task_id="task-1",
        lang="rust",
        task_shape="bugfix",
    )
    # r3 doesn't match. r1 applies. r2 matches but explored → skipped.
    assert decision.applied_actions == ("use_reasoning_model",)
    assert set(decision.matched_rule_ids) == {"rule1xxxxxxxxxxx", "rule2xxxxxxxxxxx"}
    assert decision.explored_rule_ids == ("rule2xxxxxxxxxxx",)
