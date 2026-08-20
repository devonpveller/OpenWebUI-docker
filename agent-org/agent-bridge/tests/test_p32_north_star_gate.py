"""P32 (§6.6.1) — the North-Star alignment gate, judged over the round's whole GROUP.

gym-035 ran ~13h / 2109 events on off-North-Star work — packaging/linting/version, commit-history
rewrites, corner-cases no real user hits. The gate checks each round's generated task list against the
ORIGINAL prompt. Crucially it judges the list AS A GROUP, not task-by-task: an enabling/scaffolding task
is a tangent in isolation but essential to an aligned group, so isolation would falsely prune it and the
aligned work it enables could never land (operator, 2026-07-29). So a candidate is constrained only when
it serves NO part of the North Star EVEN given the rest of the group. Context-isolated (North Star + the
list only) = the anti-mirror; fails OPEN. Fakes only.
"""

from __future__ import annotations

from pathlib import Path

from app.adapters.chat import FakeChatAdapter
from app.config import Settings
from app.db import Database
from app.modules.model_router import FakeModelClient
from app.orchestrator import Orchestrator
from app.schemas import AlignmentVerdict
from app.worker.harness import FakeHarness

ROOT = Path(__file__).resolve().parents[1]
REPO = "https://github.com/acme/gym.git"
NORTH = "a polished todo CLI a real person would enjoy — add, list, complete, delete, with due dates"


async def _orch(db_url, **over):
    kwargs = dict(
        _env_file=None, chat_adapter="fake",
        profiles_dir=str(ROOT / "profiles"), charters_dir=str(ROOT / "charters"),
        floor_dir=str(ROOT / "floor"), worker_instance_urls="http://w1:8090",
        max_concurrent_workers=1, database_url=db_url, project_survey_enabled=False,
        review_mode="off", plan_approval="off", north_star_gate=True,
    )
    kwargs.update(over)
    db = Database(db_url)
    orch = Orchestrator(Settings(**kwargs), db, FakeChatAdapter(),
                        model_client=FakeModelClient(), harness=FakeHarness())
    await orch.setup()
    return orch, db


async def _effort(orch, goal=NORTH):
    await orch.projects.add("gym", REPO)
    eid, chan, root = await orch.router.open_effort("feat", project="gym")
    await orch.charters.set_goal(eid, goal, created_by="po")
    return eid, chan, root


def _cands(*bodies):
    return [("goal_alignment", b) for b in bodies]


# ── the North Star is the ORIGINAL prompt, not an auto-iteration suffix ───────
async def test_north_star_is_the_original_prompt(db_url):
    orch, db = await _orch(db_url)
    try:
        eid, _c, _r = await _effort(orch, goal=f"{NORTH}\n\nITERATION 2/2 (automatic): fix the one defect")
        assert await orch._north_star(eid) == NORTH
    finally:
        await db.dispose()


# ── the GROUP judgment: keep the enabling task, constrain only the real tangent ─
async def test_group_keeps_the_enabling_task_and_flags_only_the_tangent(db_url):
    orch, db = await _orch(db_url)
    try:
        eid, _c, _r = await _effort(orch)
        cands = _cands(
            "Implement the delete command",
            "Add a _normalize_priority helper that maps low/medium/high to an order index",  # enabling
            "Add pyproject.toml packaging metadata")                                         # tangent
        # the group verdict flags ONLY index 3 — the enabling helper (2) is judged aligned in context
        orch.models._client.queue_structured(
            AlignmentVerdict(off_north_star=[3], rationale="packaging serves no part of the goal"))
        kept, off = await orch._sort_off_north_star(eid, cands)
        assert [b for _l, b in kept] == [
            "Implement the delete command",
            "Add a _normalize_priority helper that maps low/medium/high to an order index"]
        assert [b for _l, b in off] == ["Add pyproject.toml packaging metadata"]
        # the checker saw the WHOLE GROUP + the North Star, and NOTHING else (context isolation)
        user = orch.models._client.calls[-1]["user"]
        assert NORTH in user and "_normalize_priority" in user and "packaging" in user
        assert "ITERATION" not in user
    finally:
        await db.dispose()


async def test_nothing_flagged_keeps_the_whole_group(db_url):
    orch, db = await _orch(db_url)
    try:
        eid, _c, _r = await _effort(orch)
        cands = _cands("Implement delete", "Add due-date validation")
        orch.models._client.queue_structured(AlignmentVerdict(off_north_star=[], rationale="all aligned"))
        kept, off = await orch._sort_off_north_star(eid, cands)
        assert kept == cands and off == []
    finally:
        await db.dispose()


async def test_out_of_range_index_is_ignored_keeps_all(db_url):
    orch, db = await _orch(db_url)
    try:
        eid, _c, _r = await _effort(orch)
        cands = _cands("Implement delete", "Add search")
        orch.models._client.queue_structured(AlignmentVerdict(off_north_star=[9, 0]))  # both invalid
        kept, off = await orch._sort_off_north_star(eid, cands)
        assert kept == cands and off == []
    finally:
        await db.dispose()


async def test_fail_open_on_a_model_hiccup(db_url):
    orch, db = await _orch(db_url)
    try:
        eid, _c, _r = await _effort(orch)
        cands = _cands("Implement delete", "Add pyproject.toml")
        orch.models._client.queue_raise(RuntimeError("model down"))
        kept, off = await orch._sort_off_north_star(eid, cands)
        assert kept == cands and off == []   # a hiccup must never prune real work (the P26 lesson)
    finally:
        await db.dispose()


async def test_gate_off_is_a_noop_with_no_model_call(db_url):
    orch, db = await _orch(db_url, north_star_gate=False)
    try:
        eid, _c, _r = await _effort(orch)
        cands = _cands("Add pyproject.toml packaging metadata")
        before = len(orch.models._client.calls)
        kept, off = await orch._sort_off_north_star(eid, cands)
        assert kept == cands and off == [] and len(orch.models._client.calls) == before  # no model call
    finally:
        await db.dispose()
