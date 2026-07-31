"""P33 (2026-07-30, gym-037) — the alignment gates run on the GAP candidates, NOT on `derived`.

gym-037 proved the North-Star gate (P32) was wired to the WRONG list. `derived` holds only goal-gaps
(`_gap_analysis`) and DEFECTs (`_tasks_from_lens`) — genuine, product-serving correctness work by
construction — yet the interpretive gate AMPUTATED it: the audit shows `off_north_star_pruned kept:0`
in rounds 3 AND 4, pruning the `load_items` crash fix, duplicate-ID detection, exception hygiene and
date validation as "off-North-Star" (the P26 "an LLM grading an LLM over-prunes real work" failure P28
was built to retire). Meanwhile the ACTUAL off-North-Star tangents (linting/packaging config, a SOLID
refactor, commit-message conventions) arrived GAP-graded and, gated nowhere, were dispatched as wasted
rounds.

P33 moves BOTH gates off `derived` and onto the GAP-candidate list — the deterministic git-meta filter
(P28) and the interpretive North-Star group gate (P32) — so correctness/goal work is immune to
amputation while the polish tangents are constrained where they actually live.

Fakes only.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from app.adapters.chat import FakeChatAdapter
from app.config import Settings
from app.db import Database
from app.modules.model_router import FakeModelClient
from app.orchestrator import _LENSES, Orchestrator
from app.schemas import AlignmentVerdict
from app.worker.harness import FakeHarness

ROOT = Path(__file__).resolve().parents[1]
REPO = "https://github.com/acme/gym.git"
GOAL = "a polished todo CLI a real person would enjoy — add, list, complete, delete, with due dates"


async def _orch(db_url, **overrides):
    kwargs = dict(
        _env_file=None, chat_adapter="fake",
        profiles_dir=str(ROOT / "profiles"), charters_dir=str(ROOT / "charters"),
        floor_dir=str(ROOT / "floor"), worker_instance_urls="http://w1:8090",
        max_concurrent_workers=1, database_url=db_url, project_survey_enabled=False,
        review_mode="off", plan_approval="off", qa_gate="report", drain_loop=True,
        drain_tier_walk=False, drain_plan_split=True, north_star_gate=True,
    )
    kwargs.update(overrides)
    db = Database(db_url)
    orch = Orchestrator(Settings(**kwargs), db, FakeChatAdapter(),
                        model_client=FakeModelClient(), harness=FakeHarness())
    await orch.setup()
    return orch, db


async def _shutdown(orch, db):
    if orch._bg_tasks:
        await asyncio.gather(*list(orch._bg_tasks))
    for t in (orch._capacity_task, orch._stall_task, orch._reaper_task):
        if t is not None:
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
    await db.dispose()


async def _effort(orch):
    await orch.projects.add("gym", REPO)
    eid, chan, root = await orch.router.open_effort("feat", project="gym")
    await orch.charters.set_goal(eid, GOAL, created_by="po")
    return eid, chan, root


def _delivery():
    from app.modules.capabilities import BranchDelivery
    return BranchDelivery(branch="agent/feat", exists=True, ahead=1, head_sha="abc1234567")


def _feed_lens_reports(orch):
    """One report per lens so all three lenses 'report' and `swept` is true. Long enough to pass
    `_is_lens_report` (extraction itself is faked via `queue_text`, so the exact content is inert)."""
    for _ in _LENSES:
        orch.harness.output_queue.append(
            "The tool stores todos in todos.json and supports add and list. load_items() crashes with "
            "a TypeError on a non-dict element in the JSON array (for example json.dump([1,2,3])), "
            "because the sanitisation loop skips such entries but still returns them. The add command "
            "accepts empty text, creating meaningless entries, and there is no linting or packaging "
            "configuration. The due-before filter accepts unparseable date strings and compares them "
            "lexicographically, silently returning wrong results instead of rejecting the input.")


# ── the CRITICAL fix: a correctness gap in `derived` is NEVER touched by the gate ──
async def test_derived_correctness_work_is_never_amputated(db_url):
    """gym-037's core failure: the crash fix was pruned as off-North-Star. With the gate off `derived`
    the fix is queued, no `off_north_star_pruned` fires, and the gate is not even CONSULTED for
    derived work — proven by leaving a 'flag everything' verdict queued and unconsumed."""
    orch, db = await _orch(db_url)
    try:
        eid, chan, root = await _effort(orch)
        _feed_lens_reports(orch)
        orch.models._client.queue_text("Fix load_items crash on non-dict database items")  # goal gap
        orch.models._client.queue_text("none")   # clean_code — no GAP
        orch.models._client.queue_text("none")   # project_documentation — no GAP
        # A hostile verdict that would prune the crash fix if the gate ever saw `derived`.
        orch.models._client.queue_structured(AlignmentVerdict(off_north_star=[1]))

        r = await orch._drain_round(eid, chan, root, REPO, _delivery())

        assert r["new_tasks"] == 1                                        # the crash fix counted
        assert await orch._event_count(eid, "off_north_star_pruned") == 0  # nothing amputated
        # The gate never ran (no GAP candidates) — the hostile verdict sits unconsumed.
        assert not any(c["kind"] == "structured" for c in orch.models._client.calls)
        open_bodies = [t["body"] for t in await orch.list_open_tasks(effort_id=eid)]
        assert any("load_items crash" in b for b in open_bodies)
        assert not [c for c in await orch._list_constraints(eid) if c["kind"] == "off_north_star"]
    finally:
        await _shutdown(orch, db)


# ── the gate now guards where the tangents actually live: the GAP list ─────────
async def test_gap_tangent_is_pruned_by_the_moved_north_star_gate(db_url):
    orch, db = await _orch(db_url)
    try:
        eid, chan, root = await _effort(orch)
        _feed_lens_reports(orch)
        orch.models._client.queue_text("Fix load_items crash on non-dict database items")   # goal gap
        orch.models._client.queue_text(                                                      # a GAP tangent
            "GAP: Provide linting configuration and dependency management file")
        orch.models._client.queue_text("none")                                               # project_documentation
        # The gate runs on the GAP list (one candidate) and flags it.
        orch.models._client.queue_structured(AlignmentVerdict(off_north_star=[1]))

        r = await orch._drain_round(eid, chan, root, REPO, _delivery())

        assert r["new_tasks"] == 1                                        # only the counted goal gap
        assert await orch._event_count(eid, "off_north_star_pruned") == 1
        off = [c for c in await orch._list_constraints(eid) if c["kind"] == "off_north_star"]
        assert len(off) == 1 and "linting" in off[0]["body"].lower()
        open_bodies = [t["body"] for t in await orch.list_open_tasks(effort_id=eid)]
        assert any("load_items crash" in b for b in open_bodies)          # real work survives
        assert not any("linting" in b.lower() for b in open_bodies)       # tangent pruned, never queued
    finally:
        await _shutdown(orch, db)


# ── a commit-message GAP is pruned deterministically (P28 git-meta) on the GAP path ──
async def test_commit_message_gap_is_pruned_by_deterministic_git_meta(db_url):
    orch, db = await _orch(db_url)
    try:
        eid, chan, root = await _effort(orch)
        _feed_lens_reports(orch)
        orch.models._client.queue_text("Fix the due-before filter to reject invalid dates")  # goal gap
        orch.models._client.queue_text(                                                      # git-meta GAP
            "GAP: Record acceptance criteria in the commit messages")
        orch.models._client.queue_text("none")                                               # project_documentation
        # No verdict queued: after the deterministic filter removes the only GAP, the North-Star
        # gate has nothing to judge and never calls the model.

        r = await orch._drain_round(eid, chan, root, REPO, _delivery())

        assert r["new_tasks"] == 1
        assert await orch._event_count(eid, "off_theme_pruned") == 1
        assert not any(c["kind"] == "structured" for c in orch.models._client.calls)  # deterministic only
        off = [c for c in await orch._list_constraints(eid) if c["kind"] == "off_theme"]
        assert len(off) == 1 and "commit" in off[0]["body"].lower()
        open_bodies = [t["body"] for t in await orch.list_open_tasks(effort_id=eid)]
        assert any("due-before" in b for b in open_bodies)
        assert not any("commit message" in b.lower() for b in open_bodies)
    finally:
        await _shutdown(orch, db)
