"""P28 — the off-theme filter is DETERMINISTIC (design §6.5/§6.6/§11), replacing P26's LLM verdict.

Telling git-meta NOISE apart from product WORK is a mechanical distinction (one rewrites git history,
the other changes product code), so it must be deterministic — not an LLM "is this off-theme?" verdict,
which INVERTED under a real load (gym-026: pruned 10/10 real product tasks while keeping commit-hygiene).
`_GIT_META_RE` matches the git artifact (commit / SHA / merge / rebase / bisect / git history) → the
task becomes a CONSTRAINT (keeping §6.6's misalignment→constraint plumbing); everything else stays a
task. Goal-relevance is NOT decided here — that stays reasoning (the goal_alignment lens + gap analysis).

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
from app.worker.harness import FakeHarness

ROOT = Path(__file__).resolve().parents[1]
REPO = "https://github.com/acme/gym.git"
GOAL = "a todo CLI that adds, lists, completes and deletes todos with a due date"

# The actual gym-026 strings the LLM sort got exactly backwards.
_GIT_META = [
    "Add a verification result to the first commit message",
    "Split the second commit into smaller logical units to enable bisecting",
    "Reduce the second commit body to the charter-recommended 1-3 lines",
    "Add bodies to empty merge commits",
    "Split commit 67c0b95 into smaller focused commits",
    "Annotate duplicate commits b40a91c and ad19ce3",
    "Add explanation to revert commit be15dec",
]
_PRODUCT = [
    "Change save_items exception handling to catch only Exception instead of BaseException",
    "Handle IsADirectoryError in JsonFileStore.load() when the database path is a directory",
    "Fix _repl_argv_add to correctly parse misaligned flag/value boundaries",
    "Validate priority and id types in _sanitize_items to reject invalid values",
    "Document the silent-dropping behavior of _sanitize_items in the module docstring",
    "Validate add command to reject empty todo text",
    "Route REPL command handler errors to stderr instead of stdout",
]


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
    from app.modules.capabilities import BranchDelivery
    return BranchDelivery(branch="agent/feat", exists=True, ahead=1, head_sha="abc1234567")


# ── the deterministic classifier ──────────────────────────────────────────────
def test_git_meta_is_pruned_and_product_work_is_kept():
    """The exact inversion the LLM sort got wrong: commit/SHA tasks → off-theme; code tasks → kept."""
    orch = Orchestrator.__new__(Orchestrator)   # no setup needed; the classifier is pure
    cands = [("clean_code", b) for b in _GIT_META] + [("clean_code", b) for b in _PRODUCT]
    kept, off = orch._sort_off_theme(cands)
    assert sorted(b for _l, b in off) == sorted(_GIT_META)      # every git-meta task pruned
    assert sorted(b for _l, b in kept) == sorted(_PRODUCT)      # every product task kept


def test_a_decimal_number_is_not_mistaken_for_a_commit_sha():
    orch = Orchestrator.__new__(Orchestrator)
    kept, off = orch._sort_off_theme([("clean_code", "set the default port to 1234567")])
    assert len(kept) == 1 and off == []       # all-decimal ⇒ not a SHA ⇒ kept


def test_empty_candidates_are_a_noop():
    orch = Orchestrator.__new__(Orchestrator)
    assert orch._sort_off_theme([]) == ([], [])


# ── off_theme constraints do NOT pollute the CDCL failure preamble ────────────
async def test_off_theme_constraint_is_excluded_from_the_retry_preamble(db_url):
    orch, db = await _orch(db_url)
    try:
        eid, _c, _r = await _effort(orch)
        await orch._record_constraint(eid, "the undo command crashes on an empty stack", kind="failure")
        await orch._record_constraint(eid, "rewrite the body of commit b3de9e3", kind="off_theme")
        ctx = await orch._constraints_context(eid)
        assert "undo command crashes" in ctx
        assert "rewrite the body" not in ctx
    finally:
        await _shutdown(orch, db)


# ── the drain wiring end-to-end ───────────────────────────────────────────────
async def test_drain_round_prunes_git_meta_to_a_constraint_and_out_of_the_count(db_url):
    """A round deriving one product gap and one git-meta task counts only the product task; the
    git-meta one is recorded as an `off_theme` constraint and never queued — deterministically, with
    no LLM call in the loop."""
    orch, db = await _orch(db_url)
    try:
        eid, chan, root = await _effort(orch)
        for _ in _LENSES:
            orch.harness.output_queue.append(
                "The tool stores todos in todos.json and supports add and list. There is no delete "
                "path, and running with no arguments prints an argparse traceback rather than usage "
                "text. The add command accepts any string for --due without validating the format, so "
                "an unparseable date is stored and silently excluded from every later filter. Ids are "
                "len(items)+1, reused after a deletion, and there is no way to edit an item's text.")
        # model calls: gap_analysis, clean_code tasks, project_documentation tasks. NO sort call (P28).
        orch.models._client.queue_text(
            "add a delete command\nsplit the scaffold commit b3de9e3 into testable commits")
        orch.models._client.queue_text("none")     # clean_code
        orch.models._client.queue_text("none")     # project_documentation
        r = await orch._drain_round(eid, chan, root, REPO, _delivery())

        assert r["new_tasks"] == 1                                  # only the product gap counted
        assert await orch._event_count(eid, "off_theme_pruned") == 1
        off = [c for c in await orch._list_constraints(eid) if c["kind"] == "off_theme"]
        assert len(off) == 1 and "commit" in off[0]["body"].lower()
        open_bodies = [t["body"] for t in await orch.list_open_tasks(effort_id=eid)]
        assert any("delete command" in b for b in open_bodies)
        assert not any("scaffold commit" in b for b in open_bodies)   # pruned, never queued
    finally:
        await _shutdown(orch, db)
