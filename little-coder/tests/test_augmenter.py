"""Per-task augmenter (design §7.4, Chapter 4).

The augmenter is pure: similarity + cohort-proven are injected. These
tests pin each step of the pipeline (tag filter, embedding rank, budget
fit, tiebreaker) plus the contract that the result is auditable enough
for the §8.4 in-context assertion (rejected skills carry reasons).
"""

from __future__ import annotations

import pytest

from littlecoder.augmenter import (
    RejectedSkill,
    SkillSelection,
    TaskRequest,
    estimate_tokens,
    select,
    skill_token_cost,
)
from littlecoder.skills import build_skill


def _skill(
    *,
    skill_id: str | None = None,
    lang: str = "rust",
    domain: str = "async",
    task_shape: str = "bugfix",
    tool: str = "*",
    description: str = "When async Rust hits the borrow checker, prefer owning over borrowing.",
    body: str = "# How to fix it\n\n...",
    tier: int = 0,
    kind: str = "knowledge",
    status: str = "active",
):
    return build_skill(
        kind=kind,
        cluster_id="cl0001",
        tier=tier,
        lang=lang,
        domain=domain,
        task_shape=task_shape,
        tool=tool,
        name="lifetime-help",
        description=description,
        body=body,
        skill_id=skill_id,
        status=status,
    )


def _request(**overrides) -> TaskRequest:
    base = dict(
        lang="rust",
        domain="async",
        task_shape="bugfix",
        prompt="fix the borrow checker error in my async fn",
    )
    base.update(overrides)
    return TaskRequest(**base)


def _const_sim(score: float):
    """Similarity stub returning a constant score for every pair."""
    return lambda q, a: score


def _by_text(scores_by_text: dict[str, float]):
    """Similarity stub keyed on the description text — lets a single
    test set per-skill scores deterministically."""
    return lambda q, a: scores_by_text.get(a, 0.0)


# --- estimate_tokens / skill_token_cost ---------------------------------


def test_estimate_tokens_rounds_up():
    """Conservative tokenization — never under-count the budget."""
    assert estimate_tokens("a") == 1  # 1 char → 1 token (rounded up)
    assert estimate_tokens("a" * 4) == 1  # 4 chars → exactly 1 token
    assert estimate_tokens("a" * 5) == 2  # 5 chars → round up to 2


def test_skill_token_cost_adds_overhead():
    """Each selected skill costs body tokens + a wrapping overhead."""
    skill = _skill(body="hello")  # 5 chars → 2 tokens body + 20 overhead
    assert skill_token_cost(skill) == 22


# --- tag filter (§7.4 step 1) -------------------------------------------


def test_filter_keeps_exact_lang_domain_taskshape():
    skill = _skill(lang="rust", domain="async", task_shape="bugfix")
    result = select([skill], _request(), _const_sim(0.9))
    assert result.selected_ids == [skill.id]
    assert result.rejected == []


def test_filter_rejects_lang_mismatch():
    skill = _skill(lang="python")
    result = select([skill], _request(lang="rust"), _const_sim(1.0))
    assert result.selected == []
    assert len(result.rejected) == 1
    assert "lang mismatch" in result.rejected[0].reason


def test_filter_rejects_domain_mismatch():
    skill = _skill(domain="fs")
    result = select([skill], _request(domain="async"), _const_sim(1.0))
    assert result.selected == []
    assert "domain mismatch" in result.rejected[0].reason


def test_filter_rejects_task_shape_mismatch():
    skill = _skill(task_shape="refactor")
    result = select([skill], _request(task_shape="bugfix"), _const_sim(1.0))
    assert result.selected == []
    assert "task_shape mismatch" in result.rejected[0].reason


def test_wildcard_in_skill_matches_any_lang():
    """A `lang="*"` skill is language-agnostic (e.g. "use git log to
    orient", which the operator might author as a tier-0 entry across
    every language)."""
    skill = _skill(lang="*", domain="*", task_shape="*")
    for lang in ("rust", "python", "go"):
        result = select([skill], _request(lang=lang, domain="any", task_shape="bugfix"), _const_sim(0.9))
        assert result.selected_ids == [skill.id], f"failed for lang={lang}"


def test_wildcard_in_request_matches_any_skill():
    """A `request.lang="*"` (genuinely unknown lang at trigger time)
    should match any skill. Cross-scope clustering is forbidden in
    §5.5, but the augmenter sees a derived task — when lang is unknown
    we don't punish the augmenter for it."""
    skill = _skill(lang="rust")
    result = select([skill], _request(lang="*"), _const_sim(0.9))
    assert result.selected_ids == [skill.id]


def test_tool_filter_optional_in_request():
    """`request.tool is None` skips the tool axis entirely. With a tool
    set, only matching or wildcard-tool skills pass."""
    rust_skill = _skill(tool="pytest")
    result_unset = select([rust_skill], _request(), _const_sim(0.9))
    assert result_unset.selected_ids == [rust_skill.id]

    result_match = select([rust_skill], _request(tool="pytest"), _const_sim(0.9))
    assert result_match.selected_ids == [rust_skill.id]

    result_mismatch = select([rust_skill], _request(tool="cargo"), _const_sim(0.9))
    assert result_mismatch.selected == []
    assert "tool mismatch" in result_mismatch.rejected[0].reason


def test_filter_is_case_insensitive_on_lang():
    """Journals have drifted between `Rust` and `rust` historically — be
    forgiving on the common axes."""
    skill = _skill(lang="Rust")
    result = select([skill], _request(lang="rust"), _const_sim(0.9))
    assert result.selected_ids == [skill.id]


# --- embedding rank (§7.4 step 2) ---------------------------------------


def test_rank_orders_higher_score_first():
    a = _skill(skill_id="aaaaaaaaaaaaaaaa", description="async lifetimes")
    b = _skill(skill_id="bbbbbbbbbbbbbbbb", description="trait bounds")
    c = _skill(skill_id="cccccccccccccccc", description="error handling")
    sim = _by_text(
        {"async lifetimes": 0.9, "trait bounds": 0.4, "error handling": 0.7}
    )
    result = select([a, b, c], _request(), sim)
    # a (0.9) > c (0.7) > b (0.4)
    assert result.selected_ids == [a.id, c.id, b.id]


def test_rank_uses_skill_description_not_body():
    """Crucial — the augmenter ranks against the description (judge-
    authored for retrieval), not the body. A long body shouldn't get
    weighted more than a short one with a precise description."""
    long_body = _skill(
        skill_id="11111111aaaaaaaa",
        description="trait bounds",
        body="x" * 4000,  # very long
    )
    short_body = _skill(
        skill_id="22222222bbbbbbbb",
        description="async lifetimes",
        body="ok",
    )
    sim = _by_text({"async lifetimes": 0.9, "trait bounds": 0.5})
    result = select([long_body, short_body], _request(), sim, token_budget=10000)
    # short_body wins on score (description match), not on body size
    assert result.selected_ids[0] == short_body.id


# --- budget fit (§7.4 step 3) -------------------------------------------


def test_budget_drops_lowest_when_over():
    """Greedy fill from top of the sort; when the next addition would
    exceed budget, that skill is rejected with `over budget` reason."""
    big = _skill(skill_id="big0000000000000", body="x" * 4000)  # ~1020 tokens
    small = _skill(skill_id="small00000000000", body="x" * 80, description="d2")
    sim = _by_text({big.frontmatter.description: 0.95, "d2": 0.4})
    # Budget tight enough that only the small one (after the big one
    # fits) might or might not survive. Big takes ~1020; budget 1000
    # rejects big, accepts small.
    result = select([big, small], _request(), sim, token_budget=1000)
    assert result.selected_ids == [small.id]
    assert any("over budget" in r.reason for r in result.rejected)


def test_budget_accounting_is_accurate():
    """`total_tokens` is the sum of `skill_token_cost` for selected
    skills; never exceeds budget."""
    a = _skill(skill_id="aaaaaaaaaaaaaaaa", body="a" * 400)  # 100+20 = 120
    b = _skill(skill_id="bbbbbbbbbbbbbbbb", body="b" * 400, description="d2")
    sim = _by_text({a.frontmatter.description: 0.9, "d2": 0.5})
    result = select([a, b], _request(), sim, token_budget=500)
    assert result.total_tokens <= result.budget
    assert result.total_tokens == sum(skill_token_cost(s) for s in result.selected)


# --- tiebreaker (cohort-proven, tier NOT a tiebreaker) ------------------


def test_tiebreaker_cohort_proven_wins_at_equal_score():
    """Two skills tied on score; cohort-proven wins. This is the design
    §7.4 step-3 tiebreaker — production-truth evidence over recency."""
    proven = _skill(skill_id="proven0000000000", description="d-proven")
    unproven = _skill(skill_id="unproven00000000", description="d-unproven")
    sim = _by_text({"d-proven": 0.8, "d-unproven": 0.8})  # tied
    # Default body is 25 tokens (5 body + 20 overhead). Budget below
    # that rejects both — but the rejected-order reflects the
    # tiebreaker so the §8.4 audit can read it.
    is_proven = lambda sid: sid == proven.id
    result = select(
        [unproven, proven],
        _request(),
        sim,
        token_budget=24,  # below 25, neither fits
        is_cohort_proven=is_proven,
    )
    assert result.selected == []
    rejected_ids = [r.skill_id for r in result.rejected if r.score is not None]
    assert rejected_ids == [proven.id, unproven.id]

    # Now with a budget that fits exactly one — proven wins.
    result_fits = select(
        [unproven, proven],
        _request(),
        sim,
        token_budget=25,  # exactly one 25-token skill fits
        is_cohort_proven=is_proven,
    )
    assert result_fits.selected_ids == [proven.id]


def test_tier_is_NOT_a_tiebreaker_on_its_own():
    """A loosely-matched tier-1 skill should LOSE to a well-matched
    tier-0 skill. Tier governs production discipline, not runtime
    selection — locked in design §7.4 step 3."""
    tier0_well = _skill(skill_id="tier0wellmatched", tier=0, description="d-tight")
    tier1_loose = _skill(skill_id="tier1loosem00000", tier=1, kind="tool", description="d-loose")
    sim = _by_text({"d-tight": 0.95, "d-loose": 0.45})
    result = select([tier1_loose, tier0_well], _request(), sim, token_budget=1000)
    # tier-0 with tighter match wins, ahead of tier-1 with looser match.
    assert result.selected_ids[0] == tier0_well.id


def test_higher_score_beats_cohort_proven_at_lower_score():
    """Cohort-proven is a TIEBREAKER, not a multiplier. A non-proven
    skill with a higher similarity score still wins on score."""
    proven_low = _skill(skill_id="proven0000000000", description="d-low")
    unproven_high = _skill(skill_id="unproven00000000", description="d-high")
    sim = _by_text({"d-low": 0.5, "d-high": 0.9})
    is_proven = lambda sid: sid == proven_low.id
    result = select(
        [proven_low, unproven_high],
        _request(),
        sim,
        token_budget=25,  # fits exactly one 25-token skill
        is_cohort_proven=is_proven,
    )
    assert result.selected_ids == [unproven_high.id]


# --- §8.4 in-context assertion ------------------------------------------


def test_includes_method_for_assertion():
    """The §8.4 contract: validation queries the SkillSelection to see
    whether the artifact-under-test was actually inlined for the task.
    A `False` here means the gate measured nothing."""
    a = _skill(skill_id="aaaaaaaaaaaaaaaa")
    b = _skill(skill_id="bbbbbbbbbbbbbbbb", description="d2")
    sim = _by_text({a.frontmatter.description: 0.9, "d2": 0.4})
    result = select([a, b], _request(), sim, token_budget=10000)
    assert result.includes(a.id) is True
    assert result.includes(b.id) is True
    assert result.includes("never-selected") is False


def test_rejected_reasons_are_specific_per_axis():
    """Rejection reasons name the failing axis, so an operator (or the
    §8.4 audit) can see WHY an artifact didn't land."""
    s = _skill(lang="python")  # rejected on lang
    result = select([s], _request(lang="rust"), _const_sim(0.9))
    assert any(
        r.skill_id == s.id and "lang mismatch" in r.reason for r in result.rejected
    )


# --- empty inputs -------------------------------------------------------


def test_empty_library_returns_empty_selection():
    result = select([], _request(), _const_sim(1.0))
    assert result.selected == []
    assert result.rejected == []
    assert result.total_tokens == 0


def test_zero_budget_rejects_everything_above_overhead():
    """Even a tiny skill costs at least the overhead; budget=0 rejects
    everything but the rejection reason makes it clear."""
    s = _skill(body="x")
    result = select([s], _request(), _const_sim(0.9), token_budget=0)
    assert result.selected == []
    assert any("over budget" in r.reason for r in result.rejected)
