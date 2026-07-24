"""P26 — the North Star sorts generation: aligned→task, off-theme→constraint (design §6.6).

gym-024 delivered a complete product PR but its propagation count plateaued at 2-4 for rounds because
off-theme commit-hygiene ("split the scaffold commit", "add bodies to merge commits") reached the
COUNTED queue — via a mis-graded DEFECT out of `_tasks_from_lens` AND `goal_alignment` gap analysis
mapping a git-history observation to a "gap". P26 sorts the whole derived list against the North Star
(the original/product goal): a candidate that advances the PRODUCT stays a task; an off-theme one
becomes a CONSTRAINT that narrows the path (§6.6), never a counted task. Fail-safe: unsure ⇒ keep.

Fakes only.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from app.adapters.chat import FakeChatAdapter
from app.config import Settings
from app.db import Database
from app.modules.capabilities import BranchDelivery
from app.modules.model_router import FakeModelClient
from app.orchestrator import _LENSES, Orchestrator
from app.worker.harness import FakeHarness

ROOT = Path(__file__).resolve().parents[1]
REPO = "https://github.com/acme/gym.git"
GOAL = "a todo CLI that adds, lists, completes and deletes todos with a due date"

# A substantial lens report (the P11.5 substance floor rejects a stub).
_REPORT = (
    "The tool stores todos in todos.json and supports add and list. There is no way to mark a todo "
    "complete, and no delete path. Running with no arguments prints an argparse traceback. The add "
    "command accepts any string for --due without validating it, so an unparseable date is stored "
    "verbatim and silently excluded from every later filter. Ids are len(items)+1, reused after a "
    "deletion. There is no interactive mode, no search, and no way to edit an item's text."
)


async def _orch(db_url, **overrides):
    kwargs = dict(
        _env_file=None, chat_adapter="fake",
        profiles_dir=str(ROOT / "profiles"), charters_dir=str(ROOT / "charters"),
        floor_dir=str(ROOT / "floor"), worker_instance_urls="http://w1:8090",
        max_concurrent_workers=1, database_url=db_url, project_survey_enabled=False,
        review_mode="off", plan_approval="off", qa_gate="report", drain_loop=True,
        drain_tier_walk=False, drain_plan_split=True,
    )
    kwargs.update(overrides)
    settings = Settings(**kwargs)
    db = Database(db_url)
    orch = Orchestrator(settings, db, FakeChatAdapter(),
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
    return BranchDelivery(branch="agent/feat", exists=True, ahead=1, head_sha="abc1234567")


# ── the sort itself ───────────────────────────────────────────────────────────
async def test_off_theme_candidate_is_pruned_and_product_work_is_kept(db_url):
    orch, db = await _orch(db_url)
    try:
        eid, _c, _r = await _effort(orch)
        cands = [("goal_alignment", "add a delete command"),
                 ("project_documentation", "split the scaffold commit b3de9e3 into smaller commits")]
        orch.models._client.queue_text("2")           # #2 is off-theme (git-history housekeeping)
        kept, off = await orch._sort_off_theme(eid, cands, GOAL)
        assert [b for _l, b in kept] == ["add a delete command"]
        assert [b for _l, b in off] == ["split the scaffold commit b3de9e3 into smaller commits"]
    finally:
        await _shutdown(orch, db)


async def test_sort_fails_safe_on_an_empty_goal_without_calling_the_model(db_url):
    orch, db = await _orch(db_url)
    try:
        eid, _c, _r = await _effort(orch)
        cands = [("clean_code", "add a delete command")]
        kept, off = await orch._sort_off_theme(eid, cands, "")   # no goal → short-circuit, no call
        assert kept == cands and off == []
    finally:
        await _shutdown(orch, db)


async def test_sort_keeps_everything_on_none(db_url):
    orch, db = await _orch(db_url)
    try:
        eid, _c, _r = await _effort(orch)
        cands = [("clean_code", "add a delete command"), ("clean_code", "validate the due date")]
        orch.models._client.queue_text("none")
        kept, off = await orch._sort_off_theme(eid, cands, GOAL)
        assert kept == cands and off == []
    finally:
        await _shutdown(orch, db)


async def test_sort_ignores_out_of_range_indices(db_url):
    """A model that names a number past the list must not silently drop or crash — keep all."""
    orch, db = await _orch(db_url)
    try:
        eid, _c, _r = await _effort(orch)
        cands = [("clean_code", "add a delete command")]
        orch.models._client.queue_text("5\n9")
        kept, off = await orch._sort_off_theme(eid, cands, GOAL)
        assert kept == cands and off == []
    finally:
        await _shutdown(orch, db)


# ── off_theme constraints do NOT pollute the CDCL failure preamble ─────────────
async def test_off_theme_constraint_is_excluded_from_the_retry_preamble(db_url):
    orch, db = await _orch(db_url)
    try:
        eid, _c, _r = await _effort(orch)
        await orch._record_constraint(eid, "the undo command crashes on an empty stack", kind="failure")
        await orch._record_constraint(eid, "rewrite the body of commit b3de9e3", kind="off_theme")
        ctx = await orch._constraints_context(eid)
        assert "undo command crashes" in ctx          # a real dead end the worker walked
        assert "rewrite the body" not in ctx           # off_theme narrows GENERATION, not a retry
    finally:
        await _shutdown(orch, db)


# ── the drain wiring end-to-end ───────────────────────────────────────────────
async def test_drain_round_prunes_off_theme_to_a_constraint_and_out_of_the_count(db_url):
    """THE gym-024 fix. A round that derives one product gap and one off-theme (commit-hygiene) task
    counts only the product task; the off-theme one is recorded as an `off_theme` constraint and never
    reaches the queue, so `new_tasks` measures theme progress."""
    orch, db = await _orch(db_url)
    try:
        eid, chan, root = await _effort(orch)
        for _ in _LENSES:
            orch.harness.output_queue.append(_REPORT)
        # model calls, in order: gap_analysis, clean_code tasks, project_documentation tasks, off-theme sort
        orch.models._client.queue_text(
            "add a delete command\nsplit the scaffold commit b3de9e3 into testable commits")
        orch.models._client.queue_text("none")         # clean_code
        orch.models._client.queue_text("none")         # project_documentation
        orch.models._client.queue_text("2")            # sort: candidate #2 (split-commit) is off-theme
        r = await orch._drain_round(eid, chan, root, REPO, _delivery())

        assert r["new_tasks"] == 1                      # only the product gap counted
        assert await orch._event_count(eid, "off_theme_pruned") == 1
        off = [c for c in await orch._list_constraints(eid) if c["kind"] == "off_theme"]
        assert len(off) == 1 and "split the scaffold commit" in off[0]["body"]
        open_bodies = [t["body"] for t in await orch.list_open_tasks(effort_id=eid)]
        assert any("delete command" in b for b in open_bodies)
        assert not any("scaffold commit" in b for b in open_bodies)   # pruned, never queued
    finally:
        await _shutdown(orch, db)
