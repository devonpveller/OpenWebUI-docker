"""P10 — THE DRAIN LOOP: objective lenses, gap-derived tasks, propagation to zero.

The org used to run out of work before the project was done: it QA'd once or twice, hit a hard
`n >= 2` cap, and stopped — with no task queue and no notion of "next item", so "nothing left to
do" was an ACCIDENT OF AN EMPTY MODEL REPLY rather than a computed fact. These tests hold the
replacement to its acceptance criteria (docs/P10-the-drain-loop.md):

  P10.1  three lenses, fresh every round, no "nothing" affordance, no verdict framing, and the
         goal WITHHELD from the goal-alignment lens (the structural debias)
  P10.2  gap analysis = objective report x THIS SCOPE's goal -> plainly-stated tasks
  P10.3  a content-addressed queue, so a re-derived gap is not a duplicate
  P10.4  termination on ZERO PROPAGATION, not on a cap or a model's "none"
  P10.5  a fresh implementer that never plans its own completed work
  P10.6  scopes nest, complete bottom-up, and reopen on a neighbour's seam defect

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
from app.orchestrator import (
    _LENS_CLEAN_CODE, _LENS_GOAL_ALIGNMENT, _LENS_PROJECT_DOCUMENTATION, _LENSES,
    Orchestrator, _plain_tasks,
)
from app.worker.harness import FakeHarness

ROOT = Path(__file__).resolve().parents[1]

REPO = "https://github.com/acme/gym.git"
GOAL = "a todo CLI that adds, lists, completes and deletes todos with a due date"

_REPORT = (
    "The tool stores todos in todos.json and supports add and list. Each todo has a title and a "
    "due date. There is no way to mark a todo complete, and no delete path. Running it with no "
    "arguments prints an argparse traceback."
)


async def _orch(db_url, *, drain_loop=True, qa_gate="report", tier_walk=True, plan_split=True,
                round_cap=40):
    settings = Settings(
        _env_file=None, chat_adapter="fake",
        profiles_dir=str(ROOT / "profiles"), charters_dir=str(ROOT / "charters"),
        floor_dir=str(ROOT / "floor"), worker_instance_urls="http://w1:8090",
        max_concurrent_workers=1, database_url=db_url, project_survey_enabled=False,
        review_mode="off", plan_approval="off", qa_gate=qa_gate, drain_loop=drain_loop,
        drain_tier_walk=tier_walk, drain_plan_split=plan_split, drain_round_cap=round_cap,
    )
    db = Database(db_url)
    orch = Orchestrator(settings, db, FakeChatAdapter(),
                        model_client=FakeModelClient(), harness=FakeHarness())
    await orch.setup()
    return orch, orch.chat, orch.harness, db


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


async def _effort(orch, *, goal=GOAL, slug="gym"):
    await orch.projects.add(slug, REPO)
    eid, chan, root = await orch.router.open_effort("feat", project=slug)
    await orch.charters.set_goal(eid, goal, created_by="po")
    return eid, chan, root


def _delivery():
    return BranchDelivery(branch="agent/feat", exists=True, ahead=1, head_sha="abc1234567")


# ══════════════════════════════════════════════════════════════════════════════
# P10.1 — the three standing lenses, de-biased
# ══════════════════════════════════════════════════════════════════════════════
async def test_all_three_lenses_are_issued_every_round(db_url):
    orch, _chat, harness, db = await _orch(db_url)
    try:
        eid, chan, root = await _effort(orch)
        for _ in _LENSES:
            harness.output_queue.append(_REPORT)
        reports = await orch._lens_sweep(eid, chan, root, REPO, _delivery(), round_no=1)
        assert len(harness.wakes) == 3
        prompts = [w["prompt"] for w in harness.wakes]
        # the operator's prompts, VERBATIM
        assert any(_LENS_GOAL_ALIGNMENT in p for p in prompts)
        assert any(_LENS_CLEAN_CODE in p for p in prompts)
        assert any(_LENS_PROJECT_DOCUMENTATION in p for p in prompts)
        assert set(reports) == {"goal_alignment", "clean_code", "project_documentation"}
    finally:
        await _shutdown(orch, db)


async def test_no_nothing_affordance_and_no_verdict_framing(db_url):
    """A prompt that sanctions "nothing" gets told "nothing" (§6.5): gym-008's functional lens
    returned "no defects" on a codebase where another lens found real ones. So no lens may offer a
    way to say none, and none may be asked to grade to a bar."""
    orch, _chat, harness, db = await _orch(db_url)
    try:
        eid, chan, root = await _effort(orch)
        for _ in _LENSES:
            harness.output_queue.append(_REPORT)
        await orch._lens_sweep(eid, chan, root, REPO, _delivery(), round_no=1)
        for w in harness.wakes:
            p = w["prompt"]
            assert "VERDICT" not in p
            assert "none" not in p.lower()
            assert "grade" not in p.lower()
    finally:
        await _shutdown(orch, db)


async def test_goal_alignment_lens_is_never_told_the_goal(db_url):
    """THE structural debias: a goal in an observation prompt invites the model to reason TOWARD it
    and declare it met. The report meets the goal in P10.2, not here."""
    orch, _chat, harness, db = await _orch(db_url)
    try:
        eid, chan, root = await _effort(orch)
        for _ in _LENSES:
            harness.output_queue.append(_REPORT)
        await orch._lens_sweep(eid, chan, root, REPO, _delivery(), round_no=1)
        for w in harness.wakes:
            assert GOAL not in w["prompt"]
            for phrase in ("todo CLI", "due date", "completes and deletes"):
                assert phrase not in w["prompt"]
    finally:
        await _shutdown(orch, db)


async def test_every_lens_runs_read_only_in_a_fresh_session(db_url):
    orch, _chat, harness, db = await _orch(db_url)
    try:
        eid, chan, root = await _effort(orch)
        for _ in _LENSES:
            harness.output_queue.append(_REPORT)
        await orch._lens_sweep(eid, chan, root, REPO, _delivery(), round_no=1)
        sids = [w.get("session_id") for w in harness.wakes]
        assert len(set(sids)) == 3                    # three DISTINCT sessions
        assert all(s and "~lens" in s for s in sids)  # none is the effort's working session
        for w in harness.wakes:
            assert "CHANGE NOTHING" in w["prompt"]
            assert "git checkout -f agent/feat" in w["prompt"]
    finally:
        await _shutdown(orch, db)


async def test_three_lens_reports_are_persisted_per_round(db_url):
    orch, _chat, harness, db = await _orch(db_url)
    try:
        eid, chan, root = await _effort(orch)
        for _ in _LENSES:
            harness.output_queue.append(_REPORT)
        await orch._lens_sweep(eid, chan, root, REPO, _delivery(), round_no=1)
        from sqlalchemy import select

        from app.models import LensReport
        async with db.session_factory() as s:
            rows = (await s.execute(select(LensReport))).scalars().all()
        assert len(rows) == 3
        assert {r.lens for r in rows} == {"goal_alignment", "clean_code", "project_documentation"}
        assert all(r.round_no == 1 and r.effort_id == eid and r.body for r in rows)
    finally:
        await _shutdown(orch, db)


async def test_reports_are_never_fed_back_into_a_later_lens(db_url):
    """Each sweep must be INDEPENDENT — the propagation count is only meaningful if a round is not
    primed by the last one's findings."""
    orch, _chat, harness, db = await _orch(db_url)
    try:
        eid, chan, root = await _effort(orch)
        marker = "ROUND-ONE-REPORT-MARKER"
        for _ in _LENSES:
            harness.output_queue.append(marker + " " + _REPORT)
        await orch._lens_sweep(eid, chan, root, REPO, _delivery(), round_no=1)
        harness.wakes.clear()
        for _ in _LENSES:
            harness.output_queue.append(_REPORT)
        await orch._lens_sweep(eid, chan, root, REPO, _delivery(), round_no=2)
        assert harness.wakes and all(marker not in w["prompt"] for w in harness.wakes)
    finally:
        await _shutdown(orch, db)


# ══════════════════════════════════════════════════════════════════════════════
# P10.2 — gap analysis
# ══════════════════════════════════════════════════════════════════════════════
async def test_a_report_omitting_a_goal_component_yields_a_task_naming_it(db_url):
    orch, _chat, _harness, db = await _orch(db_url)
    try:
        eid, _chan, _root = await _effort(orch)
        orch.models._client.queue_text(
            "add a command to mark a todo complete\nadd a delete command\n"
            "print usage help when run with no arguments")
        tasks = await orch._gap_analysis(eid, _REPORT, GOAL)
        assert len(tasks) == 3
        assert any("complete" in t for t in tasks) and any("delete" in t for t in tasks)
        # the goal entered HERE and only here
        call = orch.models._client.calls[-1]
        assert GOAL in call["user"] and _REPORT in call["user"]
    finally:
        await _shutdown(orch, db)


async def test_a_report_evidencing_every_component_yields_zero_tasks(db_url):
    """A legitimate, countable zero — the quantity termination depends on."""
    orch, _chat, _harness, db = await _orch(db_url)
    try:
        eid, _chan, _root = await _effort(orch)
        orch.models._client.queue_text("none")
        assert await orch._gap_analysis(eid, _REPORT, GOAL) == []
    finally:
        await _shutdown(orch, db)


def test_task_bodies_carry_no_rationale():
    """Tasks are stated PLAINLY. A small model reasons worse than a frontier one, so we ask it for
    LESS reasoning — rationale lives in the report, not in the task handed to a worker."""
    tasks = _plain_tasks(
        "1. add a delete command because users cannot remove todos\n"
        "2. print usage help when run with no arguments\n"
        "- validate the due date so that an invalid date cannot silently filter nothing")
    assert tasks == ["add a delete command",
                     "print usage help when run with no arguments",
                     "validate the due date"]
    for t in tasks:
        for word in ("because", "so that", "in order to"):
            assert word not in t
    assert _plain_tasks("none") == [] and _plain_tasks("") == []


def test_a_temporal_since_is_not_mistaken_for_rationale():
    """"since" is far more often temporal than causal in a task body — truncating a real task is a
    worse failure than leaving one rationale clause attached."""
    assert _plain_tasks("add a filter for todos due since a given date") == [
        "add a filter for todos due since a given date"]


async def test_gap_analysis_uses_the_scope_goal_not_the_project_goal(db_url):
    """Scope is the constraint that makes gap analysis tractable for a small model (§4)."""
    orch, _chat, harness, db = await _orch(db_url)
    try:
        eid, chan, root = await _effort(orch)
        node = await orch.add_scope_node("gym", "storage layer",
                                         "persist todos atomically to todos.json")
        await orch._attach_effort_to_scope(node, eid)
        for _ in _LENSES:
            harness.output_queue.append(_REPORT)
        orch.models._client.queue_text("write todos.json atomically via a temp file and rename")
        orch.models._client.queue_text("none")
        orch.models._client.queue_text("none")
        await orch._drain_round(eid, chan, root, REPO, _delivery())
        gap_call = orch.models._client.calls[0]
        assert "persist todos atomically" in gap_call["user"]
        assert GOAL not in gap_call["user"]
    finally:
        await _shutdown(orch, db)


# ══════════════════════════════════════════════════════════════════════════════
# P10.3 — the scoped task queue
# ══════════════════════════════════════════════════════════════════════════════
async def test_rederiving_the_same_gap_produces_no_new_row(db_url):
    """Content addressing is what makes the count honest: without it every round would
    re-propagate its predecessors' findings and zero would be unreachable."""
    orch, _chat, _harness, db = await _orch(db_url)
    try:
        eid, _chan, _root = await _effort(orch)
        first = await orch.add_task("add a delete command", project_slug="gym", effort_id=eid,
                                    round_no=1)
        again = await orch.add_task("add a delete command", project_slug="gym", effort_id=eid,
                                    round_no=2)
        assert first[1] is True and again[1] is False
        assert first[0] == again[0]
        assert len(await orch.list_open_tasks(effort_id=eid)) == 1
        # ...and it stays stamped with the round that actually discovered it
        assert await orch.count_new_tasks(1, effort_id=eid) == 1
        assert await orch.count_new_tasks(2, effort_id=eid) == 0
    finally:
        await _shutdown(orch, db)


async def test_count_new_tasks_returns_only_this_rounds_rows(db_url):
    orch, _chat, _harness, db = await _orch(db_url)
    try:
        eid, _chan, _root = await _effort(orch)
        await orch.add_task("task one", project_slug="gym", effort_id=eid, round_no=1)
        await orch.add_task("task two", project_slug="gym", effort_id=eid, round_no=1)
        await orch.add_task("task three", project_slug="gym", effort_id=eid, round_no=2)
        assert await orch.count_new_tasks(1, effort_id=eid) == 2
        assert await orch.count_new_tasks(2, effort_id=eid) == 1
        assert await orch.count_new_tasks(3, effort_id=eid) == 0
    finally:
        await _shutdown(orch, db)


async def test_close_task_drains_the_queue_and_rederivation_reopens_it(db_url):
    """A closed task re-derived by a later INDEPENDENT sweep is still-outstanding work — reopen it,
    but never count it as new information (or a task the implementer keeps failing would
    re-propagate forever and the loop could not terminate)."""
    orch, _chat, _harness, db = await _orch(db_url)
    try:
        eid, _chan, _root = await _effort(orch)
        tid, _new = await orch.add_task("add a delete command", project_slug="gym", effort_id=eid,
                                        round_no=1)
        assert await orch.close_task(tid) is True
        assert await orch.list_open_tasks(effort_id=eid) == []
        _tid2, is_new = await orch.add_task("add a delete command", project_slug="gym",
                                            effort_id=eid, round_no=2)
        assert is_new is False                                  # not new information
        assert len(await orch.list_open_tasks(effort_id=eid)) == 1   # but genuinely open again
        assert await orch.count_new_tasks(2, effort_id=eid) == 0
    finally:
        await _shutdown(orch, db)


# ══════════════════════════════════════════════════════════════════════════════
# P10.4 — propagation-count termination
# ══════════════════════════════════════════════════════════════════════════════
async def _round(orch, harness, eid, chan, root, gap_lines: str):
    """One drain round with a stubbed lens sweep + stubbed gap/task extraction."""
    for _ in _LENSES:
        harness.output_queue.append(_REPORT)
    orch.models._client.queue_text(gap_lines)   # goal_alignment -> gap analysis
    orch.models._client.queue_text("none")      # clean_code
    orch.models._client.queue_text("none")      # project_documentation
    return await orch._drain_round(eid, chan, root, REPO, _delivery())


async def test_a_scope_completes_after_exactly_three_rounds_of_2_then_1_then_0(db_url):
    """The plan's assertion verbatim: a stubbed lens returning 2 gaps, then 1, then 0 completes
    after exactly 3 rounds — and the runaway cap is never reached."""
    orch, _chat, harness, db = await _orch(db_url, tier_walk=False)
    try:
        eid, chan, root = await _effort(orch)
        r1 = await _round(orch, harness, eid, chan, root,
                          "add a delete command\nprint usage help with no arguments")
        assert r1["round"] == 1 and r1["new_tasks"] == 2
        await orch._drain_iterate(eid, r1["open_tasks"], r1["round"])

        r2 = await _round(orch, harness, eid, chan, root, "validate the due date format")
        assert r2["round"] == 2 and r2["new_tasks"] == 1
        await orch._drain_iterate(eid, r2["open_tasks"], r2["round"])

        r3 = await _round(orch, harness, eid, chan, root, "none")
        assert r3["round"] == 3 and r3["new_tasks"] == 0    # ZERO PROPAGATION -> complete
        assert r3["capped"] is False
        assert r3["open_tasks"] == []                       # and the queue drained
        assert "complete" in r3["note"].lower()
        assert await orch._event_count(eid, "drain_round") == 3
    finally:
        await _shutdown(orch, db)


async def test_termination_is_not_the_old_n_ge_2_cap(db_url):
    """`_auto_iterate`'s hard `n >= 2` stopped for a reason unrelated to whether the work was
    finished. The drain runs a 4th round while work remains."""
    orch, _chat, harness, db = await _orch(db_url, tier_walk=False)
    try:
        eid, chan, root = await _effort(orch)
        for i in range(4):
            r = await _round(orch, harness, eid, chan, root, f"task discovered in round {i + 1}")
            assert r["new_tasks"] == 1, f"round {i + 1} should still propagate work"
            assert await orch._drain_iterate(eid, r["open_tasks"], r["round"]) is True
        assert await orch._event_count(eid, "drain_round") == 4
    finally:
        await _shutdown(orch, db)


async def test_the_round_cap_is_a_runaway_guard_not_a_completion(db_url):
    """Hitting the cap must never read as "done" — it says, in terms, that the scope is NOT
    finished."""
    orch, _chat, harness, db = await _orch(db_url, tier_walk=False, round_cap=2)
    try:
        eid, chan, root = await _effort(orch)
        await _round(orch, harness, eid, chan, root, "task one")
        await _round(orch, harness, eid, chan, root, "task two")
        r3 = await _round(orch, harness, eid, chan, root, "task three")
        assert r3["capped"] is True and r3["new_tasks"] == 0
        assert "not** finished" in r3["note"] or "not finished" in r3["note"]
        assert await orch._event_count(eid, "drain_round_capped") == 1
    finally:
        await _shutdown(orch, db)


async def test_drain_off_leaves_the_legacy_qa_path_untouched(db_url):
    orch, _chat, harness, db = await _orch(db_url, drain_loop=False)
    try:
        eid, chan, root = await _effort(orch)
        harness.output_queue.append(
            "WORKS: it works.\nDEFECTS: none\nFOLLOWUPS: none\nVERDICT: fine.")
        note, defects = await orch._qa_evaluation(eid, chan, root, REPO, _delivery())
        assert len(harness.wakes) == 1 and defects == []
        assert "QA evaluation" in note
        assert await orch._event_count(eid, "drain_round") == 0
    finally:
        await _shutdown(orch, db)


# ══════════════════════════════════════════════════════════════════════════════
# P10.5 — plan / implement split
# ══════════════════════════════════════════════════════════════════════════════
async def test_implementer_session_differs_from_the_planner_session(db_url):
    orch, _chat, harness, db = await _orch(db_url, tier_walk=False)
    try:
        eid, chan, root = await _effort(orch)
        r1 = await _round(orch, harness, eid, chan, root, "add a delete command")
        harness.wakes.clear()
        harness.output_queue.append("1. edit todo.py: add a `delete` subcommand calling remove().")
        assert await orch._drain_iterate(eid, r1["open_tasks"], r1["round"]) is True
        planner = [w for w in harness.wakes if "~plan" in (w.get("session_id") or "")]
        assert len(planner) == 1
        assert "CHANGE NOTHING" in planner[0]["prompt"]
        # the implementer runs in the effort's own session, ROTATED by the drain round — so it is
        # neither the planner's session nor the session that just declared the goal met
        implementer_session = await orch._session_for(eid)
        assert implementer_session != planner[0]["session_id"]
        assert implementer_session != eid          # rotated away from the pre-drain session
    finally:
        await _shutdown(orch, db)


async def test_implementer_gets_the_plan_and_tasks_and_not_the_whole_goal(db_url):
    """gym-008: `_auto_iterate` re-sent the ENTIRE original goal to the worker that had just
    satisfied it — asking it to plan work it had just done — and got an empty plan."""
    orch, _chat, harness, db = await _orch(db_url, tier_walk=False)
    try:
        eid, chan, root = await _effort(orch)
        r1 = await _round(orch, harness, eid, chan, root,
                          "add a delete command\nprint usage help with no arguments")
        harness.output_queue.append("1. edit todo.py: add a `delete` subcommand.")
        assert await orch._drain_iterate(eid, r1["open_tasks"], r1["round"]) is True
        brief = orch._iterate_after[eid]
        assert "add a delete command" in brief and "print usage help" in brief
        assert "delete` subcommand" in brief                     # the plan rode along
        assert GOAL not in brief                                 # ...but never the whole goal
        assert "ITERATION" not in brief                          # nor the old evolved-goal shape
    finally:
        await _shutdown(orch, db)


async def test_the_root_scope_does_not_smuggle_the_whole_goal_back_in(db_url):
    """With the tier walk ON — the DEPLOYED configuration — an undecomposed root scope's text IS
    the effort's goal. Injecting it as "your scope" would restate the whole goal to the
    implementer, which is exactly the construction that produced gym-008's empty plan. There is no
    sibling to be bounded away from at a root, so a root brief is tasks + plan only."""
    orch, _chat, harness, db = await _orch(db_url, tier_walk=True)
    try:
        eid, chan, root = await _effort(orch)
        r1 = await _round(orch, harness, eid, chan, root, "add a delete command")
        assert r1["scope_node_id"] is not None                  # the tree IS live
        assert (await orch._scope_node(r1["scope_node_id"]))["depth"] == 0
        harness.output_queue.append("plan: add a delete subcommand")
        await orch._drain_iterate(eid, r1["open_tasks"], r1["round"])
        brief = orch._iterate_after[eid]
        assert "add a delete command" in brief
        assert GOAL not in brief
        assert "YOUR SCOPE" not in brief
    finally:
        await _shutdown(orch, db)


async def test_dispatched_tasks_are_closed_so_they_are_not_reworked_forever(db_url):
    orch, _chat, harness, db = await _orch(db_url, tier_walk=False)
    try:
        eid, chan, root = await _effort(orch)
        r1 = await _round(orch, harness, eid, chan, root, "add a delete command")
        harness.output_queue.append("plan")
        await orch._drain_iterate(eid, r1["open_tasks"], r1["round"])
        assert await orch.list_open_tasks(effort_id=eid) == []
    finally:
        await _shutdown(orch, db)


# ══════════════════════════════════════════════════════════════════════════════
# P10.6 — the tier walk
# ══════════════════════════════════════════════════════════════════════════════
async def test_a_child_completing_marks_the_parent_for_reevaluation(db_url):
    orch, _chat, _harness, db = await _orch(db_url)
    try:
        eid, _chan, _root = await _effort(orch)
        parent = await orch.add_scope_node("gym", "todo cli", "the whole todo CLI")
        kids = await orch.decompose_scope(parent, [("storage layer", "persist todos to disk"),
                                                   ("cli layer", "parse arguments and print")])
        assert len(kids) == 2
        back = await orch._complete_scope(kids[0], eid)
        assert back is not None and back["id"] == parent
        assert (await orch._scope_node(kids[0]))["status"] == "done"
        assert await orch._event_count(eid, "scope_completed") == 1
        assert await orch._event_count(eid, "scope_reevaluate") == 1
    finally:
        await _shutdown(orch, db)


async def test_a_scope_with_open_tasks_does_not_complete(db_url):
    """Completion needs the queue DRAINED as well as the sweep silent."""
    orch, _chat, _harness, db = await _orch(db_url)
    try:
        eid, _chan, _root = await _effort(orch)
        node = await orch.add_scope_node("gym", "storage layer", "persist todos to disk")
        await orch.add_task("write atomically", project_slug="gym", scope_node_id=node,
                            effort_id=eid, round_no=1)
        assert await orch._complete_scope(node, eid) is None
        assert (await orch._scope_node(node))["status"] == "open"
    finally:
        await _shutdown(orch, db)


async def test_a_parent_seam_defect_reopens_the_child_and_writes_the_task_there(db_url):
    """"Complete" is a CURRENT state, not a terminal one — a neighbour's later sweep can reopen a
    finished scope. That is the integration check."""
    orch, _chat, harness, db = await _orch(db_url)
    try:
        eid, chan, root = await _effort(orch)
        parent = await orch.add_scope_node("gym", "todo cli", "the whole todo CLI")
        kids = await orch.decompose_scope(parent, [("storage layer", "persist todos to disk"),
                                                   ("export layer", "write CSV exports")])
        await orch._attach_effort_to_scope(parent, eid)
        assert await orch._complete_scope(kids[0], eid) is not None
        assert (await orch._scope_node(kids[0]))["status"] == "done"
        # the parent's sweep sees the assembled product and finds a defect the CHILD owns
        for _ in _LENSES:
            harness.output_queue.append(_REPORT)
        orch.models._client.queue_text("make the storage layer fsync before returning")
        orch.models._client.queue_text("none")
        orch.models._client.queue_text("none")
        r = await orch._drain_round(eid, chan, root, REPO, _delivery())
        assert r["new_tasks"] == 1
        assert (await orch._scope_node(kids[0]))["status"] == "open"      # reopened
        child_tasks = await orch.list_open_tasks(scope_node_id=kids[0])
        assert len(child_tasks) == 1 and "fsync" in child_tasks[0]["body"]  # written THERE
        assert await orch.list_open_tasks(scope_node_id=parent) == []       # not kept by the parent
        assert await orch._event_count(eid, "scope_reopened") == 1
    finally:
        await _shutdown(orch, db)


async def test_a_worker_brief_never_contains_a_siblings_detail(db_url):
    """Withholding the rest of the tree is the mechanism, not an oversight (§4)."""
    orch, _chat, harness, db = await _orch(db_url)
    try:
        eid, chan, root = await _effort(orch)
        parent = await orch.add_scope_node("gym", "todo cli", "the whole todo CLI")
        kids = await orch.decompose_scope(
            parent,
            [("storage layer", "persist todos to a json file on disk"),
             ("reporting layer", "render weekly burndown charts from the todo history")])
        await orch._attach_effort_to_scope(kids[0], eid)
        r1 = await _round(orch, harness, eid, chan, root, "write todos atomically")
        harness.output_queue.append("plan: use a temp file and rename")
        await orch._drain_iterate(eid, r1["open_tasks"], r1["round"])
        brief = orch._iterate_after[eid]
        assert "persist todos to a json file" in brief          # its OWN scope
        assert "burndown charts" not in brief                   # ...and not its sibling's
        assert "reporting layer" not in brief
        assert "ESCALATE" in brief                              # the border, named
    finally:
        await _shutdown(orch, db)


# ══════════════════════════════════════════════════════════════════════════════
# FALSE-GREEN GUARDS — an adversarial review of the first build found these reachable paths
# where the loop reported "complete" for a reason that had nothing to do with the product.
# Each one is the failure the plan exists to eliminate, so each gets a test.
# ══════════════════════════════════════════════════════════════════════════════
async def test_a_sweep_that_could_not_run_is_not_a_clean_sweep(db_url):
    """If no lens produces a report, `new_tasks` is zero for a reason unrelated to the product.
    Reporting that as completion is an absence of OUTPUT read as an absence of WORK."""
    orch, _chat, harness, db = await _orch(db_url, tier_walk=False)
    try:
        eid, chan, root = await _effort(orch)
        harness.output_queue.extend(["", "", ""])       # three lenses, no output
        r = await orch._drain_round(eid, chan, root, REPO, _delivery())
        assert r["swept"] is False and r["new_tasks"] == 0
        assert "could not run" in r["note"]
        assert "complete" not in r["note"].lower().replace("not** a", "")
    finally:
        await _shutdown(orch, db)


async def test_no_worker_capacity_parks_instead_of_reporting_complete(db_url):
    """A saturated pool means the sweep DIDN'T HAPPEN. Swallowing NoCapacityError would turn the
    pre-existing park-and-resume contract into a silent "scope complete" (trap 3: a round needs
    four worker slots)."""
    from app.modules.scheduler import NoCapacityError

    orch, _chat, harness, db = await _orch(db_url, tier_walk=False)
    try:
        eid, chan, root = await _effort(orch)

        async def _no_capacity(*a, **kw):
            raise NoCapacityError("pool saturated")

        orch.router.wake = _no_capacity
        try:
            await orch._drain_round(eid, chan, root, REPO, _delivery())
        except NoCapacityError:
            pass                     # propagated: delegate parks the effort and resumes it later
        else:
            raise AssertionError("NoCapacityError was swallowed — the effort would close as "
                                 "complete on a sweep that never ran")
    finally:
        await _shutdown(orch, db)


async def test_a_reopened_task_keeps_the_loop_running(db_url):
    """Round N derives gap G; the implementer fails to land it; round N+1 re-derives G, which is
    NOT new information but IS outstanding work. Dispatching only on `new_tasks` would close the
    effort as complete with its own queue visibly non-empty."""
    orch, _chat, harness, db = await _orch(db_url, tier_walk=False)
    try:
        eid, chan, root = await _effort(orch)
        r1 = await _round(orch, harness, eid, chan, root, "add a delete command")
        harness.output_queue.append("plan")
        await orch._drain_iterate(eid, r1["open_tasks"], r1["round"])
        assert await orch.list_open_tasks(effort_id=eid) == []     # handed over
        # the implementer failed: the next INDEPENDENT sweep sees the same gap
        r2 = await _round(orch, harness, eid, chan, root, "add a delete command")
        assert r2["new_tasks"] == 0                                # not new information...
        assert len(r2["open_tasks"]) == 1                          # ...but genuinely open work
        assert "Not complete" in r2["note"]
        assert "found nothing further to do" not in r2["note"]
    finally:
        await _shutdown(orch, db)


async def test_two_efforts_on_one_project_do_not_share_a_scope_or_a_goal(db_url):
    """A per-project root node would hand every effort the same node: the newest steals
    `effort_id` while the node's `scope` stays the first effort's goal, so effort B's gap analysis
    would run against effort A's goal."""
    orch, _chat, _harness, db = await _orch(db_url)
    try:
        eid_a, _c, _r = await _effort(orch, goal="build the storage layer")
        await orch.projects.add("gym", REPO)
        eid_b, _c2, _r2 = await orch.router.open_effort("feat2", project="gym")
        await orch.charters.set_goal(eid_b, "build the reporting layer", created_by="po")
        node_a = await orch._ensure_scope_node(eid_a)
        node_b = await orch._ensure_scope_node(eid_b)
        assert node_a != node_b
        assert "storage" in (await orch._scope_node(node_a))["scope"]
        assert "reporting" in (await orch._scope_node(node_b))["scope"]
        assert (await orch._scope_node(node_a))["effort_id"] == eid_a
    finally:
        await _shutdown(orch, db)


async def test_the_same_task_body_on_two_efforts_does_not_collide(db_url):
    """Without per-owner addressing the second effort's task would fold onto the first effort's
    row — invisible to its own queue and its own count, so it would complete on a phantom zero."""
    orch, _chat, _harness, db = await _orch(db_url)
    try:
        eid_a, _c, _r = await _effort(orch)
        await orch.projects.add("gym", REPO)
        eid_b, _c2, _r2 = await orch.router.open_effort("feat2", project="gym")
        a = await orch.add_task("add a delete command", project_slug="gym", effort_id=eid_a,
                                round_no=1)
        b = await orch.add_task("add a delete command", project_slug="gym", effort_id=eid_b,
                                round_no=1)
        assert a[0] != b[0] and a[1] is True and b[1] is True
        assert len(await orch.list_open_tasks(effort_id=eid_a)) == 1
        assert len(await orch.list_open_tasks(effort_id=eid_b)) == 1
        assert await orch.count_new_tasks(1, effort_id=eid_b) == 1
    finally:
        await _shutdown(orch, db)


def test_a_list_marker_strip_does_not_eat_real_leading_digits():
    assert _plain_tasks("2FA login support must be added") == ["2FA login support must be added"]
    assert _plain_tasks("1. add a delete command") == ["add a delete command"]
    assert _plain_tasks("- add a delete command") == ["add a delete command"]


def test_prose_asserting_there_is_no_work_never_becomes_a_task():
    """A content-addressed task made from re-worded commentary would count as NEW every round, so
    the propagation count could never reach zero — this breaks termination itself."""
    assert _plain_tasks("The codebase is fine, no issues were found.") == []
    assert _plain_tasks("Tasks:\nadd a delete command") == ["add a delete command"]
    for nothing in ("none", "None.", "none found", "- none", "nothing to do", "N/A"):
        assert _plain_tasks(nothing) == [], nothing


async def test_a_generic_title_token_does_not_claim_every_task(db_url):
    """A child titled "cli layer" reducing to ["layer"] would claim every task mentioning a layer;
    and an AMBIGUOUS finding must stay with the parent, because mis-routing is worse."""
    orch, _chat, _harness, db = await _orch(db_url)
    try:
        _eid, _chan, _root = await _effort(orch)
        parent = await orch.add_scope_node("gym", "todo cli", "the whole todo CLI")
        kids = await orch.decompose_scope(parent, [("cli layer", "argument parsing"),
                                                   ("storage layer", "persist to disk")])
        assert await orch._seam_owner(parent, "fix the layer") is None       # generic → nobody
        assert await orch._seam_owner(parent, "the storage layer must fsync") == kids[1]
        # "port" must not match "reporting"; an unrelated finding stays with the parent
        assert await orch._seam_owner(parent, "improve the reporting output") is None
    finally:
        await _shutdown(orch, db)


async def test_a_parent_does_not_work_an_owned_childs_tasks(db_url):
    """A parent reaching into its children's insides is the encapsulation break the tree exists to
    prevent — a seam-routed task belongs to the child's OWNER, not this dispatch. The rule turns on
    there actually BEING another owner: an unowned child has nobody else to do the work, which is
    what `test_a_decomposing_round_still_dispatches_the_work_it_derived` pins down."""
    orch, _chat, harness, db = await _orch(db_url)
    try:
        eid, chan, root = await _effort(orch)
        await orch.projects.add("gym", REPO)
        other, _c, _r = await orch.router.open_effort("other", project="gym")
        parent = await orch.add_scope_node("gym", "todo cli", "the whole todo CLI")
        kids = await orch.decompose_scope(parent, [("storage layer", "persist to disk")])
        await orch._attach_effort_to_scope(parent, eid)
        await orch._attach_effort_to_scope(kids[0], other)        # a REAL other owner
        for _ in _LENSES:
            harness.output_queue.append(_REPORT)
        orch.models._client.queue_text("make the storage layer fsync before returning")
        orch.models._client.queue_text("none")
        orch.models._client.queue_text("none")
        r = await orch._drain_round(eid, chan, root, REPO, _delivery())
        assert r["new_tasks"] == 1
        assert r["open_tasks"] == []                                  # not the parent's to work
        assert len(await orch.list_open_tasks(scope_node_id=kids[0])) == 1
    finally:
        await _shutdown(orch, db)


async def test_a_parent_does_not_complete_over_an_unfinished_child(db_url):
    """A tier whose findings all seam-routed DOWN has an empty queue of its own while the work it
    discovered is still outstanding below it. Completing on that would report the whole subtree
    done on the strength of the parent having handed its work away."""
    orch, _chat, _harness, db = await _orch(db_url)
    try:
        eid, _chan, _root = await _effort(orch)
        parent = await orch.add_scope_node("gym", "todo cli", "the whole todo CLI")
        kids = await orch.decompose_scope(parent, [("storage", "persist to disk")])
        await orch.add_task("write atomically", project_slug="gym", scope_node_id=kids[0],
                            effort_id=eid, round_no=1)
        assert await orch.list_open_tasks(scope_node_id=parent) == []   # the parent's own queue IS empty
        assert await orch._complete_scope(parent, eid) is None
        assert (await orch._scope_node(parent))["status"] == "open"
    finally:
        await _shutdown(orch, db)


async def test_completion_bubbles_all_the_way_up_the_tree(db_url):
    """"child complete → parent complete → … → project" is a WALK, not a single hop. Without the
    recursion the tree completes one tier and the project is never reached."""
    orch, _chat, _harness, db = await _orch(db_url)
    try:
        eid, _chan, _root = await _effort(orch)
        root_node = await orch.add_scope_node("gym", "todo cli", "the whole todo CLI")
        mids = await orch.decompose_scope(root_node, [("storage", "persist to disk")])
        leaves = await orch.decompose_scope(mids[0], [("json writer", "serialise to json")])
        await orch._complete_scope(leaves[0], eid)
        assert (await orch._scope_node(leaves[0]))["status"] == "done"
        assert (await orch._scope_node(mids[0]))["status"] == "done"      # bubbled
        assert (await orch._scope_node(root_node))["status"] == "done"    # ...to the top
    finally:
        await _shutdown(orch, db)


async def test_a_big_round_splits_the_scope_into_real_child_tiers(db_url):
    """`decompose_scope` had no production caller, which left P10.6 inert in exactly the way the
    plan said §4 was inert before this work. A round carrying more work than one bounded scope
    should hold is the evidence that the tier spans several concerns."""
    orch, _chat, harness, db = await _orch(db_url)
    try:
        eid, chan, root = await _effort(orch)
        for _ in _LENSES:
            harness.output_queue.append(_REPORT)
        orch.models._client.queue_text(
            "add a delete command\nadd a complete command\nvalidate the due date\n"
            "print usage help\nwrite todos atomically\nadd a csv export")
        orch.models._client.queue_text("none")
        orch.models._client.queue_text("none")
        orch.models._client.queue_text(
            "commands :: the add/delete/complete subcommands\n"
            "storage :: writing and reading todos.json\n"
            "export :: rendering todos to other formats")
        r = await orch._drain_round(eid, chan, root, REPO, _delivery())
        kids = await orch._scope_children(r["scope_node_id"])
        assert len(kids) == 3
        assert {k["title"] for k in kids} == {"commands", "storage", "export"}
        assert await orch._event_count(eid, "scope_decomposed_live") == 1
    finally:
        await _shutdown(orch, db)


async def test_a_decomposing_round_still_dispatches_the_work_it_derived(db_url):
    """THE WORST FAILURE MODE, caught in review. The round that decomposes a scope routes every
    task it just derived into the brand-new children. A naive "own node only" dispatch filter then
    returns EMPTY, the effort closes reporting "a full, independent lens sweep found nothing
    further to do", and all of its work sits unreachable one tier down — nothing is attached to
    those children, so nobody can ever pick it up."""
    orch, _chat, harness, db = await _orch(db_url)
    try:
        eid, chan, root = await _effort(orch)
        for _ in _LENSES:
            harness.output_queue.append(_REPORT)
        orch.models._client.queue_text(
            "add a delete command\nadd a complete command\nvalidate the due date\n"
            "print usage help\nwrite todos atomically\nadd a csv export")
        orch.models._client.queue_text("none")
        orch.models._client.queue_text("none")
        orch.models._client.queue_text(
            "commands :: the add/delete/complete subcommands\n"
            "storage :: writing and reading todos.json\n"
            "export :: rendering todos to other formats")
        r = await orch._drain_round(eid, chan, root, REPO, _delivery())
        assert len(await orch._scope_children(r["scope_node_id"])) == 3   # it DID decompose
        assert r["new_tasks"] == 6
        # ...and every derived task is still dispatchable by the effort that found it
        assert len(r["open_tasks"]) == 6
        assert "This scope is complete" not in r["note"]
        assert "found nothing further to do" not in r["note"]
    finally:
        await _shutdown(orch, db)


async def test_a_foreign_owners_scope_is_still_not_this_efforts_to_work(db_url):
    """The encapsulation rule survives the fix: a child owned by a DIFFERENT effort is a real
    other owner, and its tasks stay out of this dispatch."""
    orch, _chat, _harness, db = await _orch(db_url)
    try:
        eid, _chan, _root = await _effort(orch)
        await orch.projects.add("gym", REPO)
        other, _c, _r = await orch.router.open_effort("other", project="gym")
        parent = await orch._ensure_scope_node(eid)
        kids = await orch.decompose_scope(parent, [("storage", "persist to disk"),
                                                   ("export", "csv output")])
        await orch._attach_effort_to_scope(kids[0], other)     # someone else owns this one
        await orch.add_task("write atomically", project_slug="gym", scope_node_id=kids[0],
                            effort_id=eid, round_no=1)
        await orch.add_task("add csv export", project_slug="gym", scope_node_id=kids[1],
                            effort_id=eid, round_no=1)
        mine = await orch._dispatchable_tasks(eid, parent)
        assert [t["body"] for t in mine] == ["add csv export"]
    finally:
        await _shutdown(orch, db)


async def test_unowned_children_do_not_deadlock_the_parent_forever(db_url):
    """A child this effort's own decomposition created has no owner: nobody will ever complete it,
    so a strict "all children done" rule would block the parent until the runaway cap."""
    orch, _chat, _harness, db = await _orch(db_url)
    try:
        eid, _chan, _root = await _effort(orch)
        parent = await orch._ensure_scope_node(eid)
        kids = await orch.decompose_scope(parent, [("storage", "persist to disk"),
                                                   ("export", "csv output")])
        tid, _n = await orch.add_task("write atomically", project_slug="gym",
                                      scope_node_id=kids[0], effort_id=eid, round_no=1)
        assert await orch._complete_scope(parent, eid) is None      # work outstanding below
        await orch.close_task(tid)                                  # the effort worked it
        await orch._complete_scope(parent, eid)
        assert (await orch._scope_node(parent))["status"] == "done"
        for k in kids:
            assert (await orch._scope_node(k))["status"] == "done"
    finally:
        await _shutdown(orch, db)


async def test_a_small_round_does_not_split_the_scope(db_url):
    orch, _chat, harness, db = await _orch(db_url)
    try:
        eid, chan, root = await _effort(orch)
        r = await _round(orch, harness, eid, chan, root, "add a delete command")
        assert await orch._scope_children(r["scope_node_id"]) == []
    finally:
        await _shutdown(orch, db)


async def test_the_drain_attaches_an_effort_to_a_scope_node(db_url):
    """`ScopeNode` was built but INERT — nothing called it. The drain is what makes it live."""
    orch, _chat, harness, db = await _orch(db_url)
    try:
        eid, chan, root = await _effort(orch)
        r = await _round(orch, harness, eid, chan, root, "none")
        assert r["scope_node_id"] is not None
        node = await orch._scope_node(r["scope_node_id"])
        assert node["effort_id"] == eid and node["project_slug"] == "gym"
    finally:
        await _shutdown(orch, db)
