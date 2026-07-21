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

# A realistic lens report. Length matters: P11.5 requires a body to clear a substance floor before
# it counts as a report at all, because gym-009's goal_alignment lens produced 72- and 48-char
# narration stubs that were stored as findings and fed to gap analysis. A fixture shorter than a
# real report would test a path production can no longer take.
_REPORT = (
    "The tool stores todos in todos.json and supports add and list. Each todo has a title and a "
    "due date. There is no way to mark a todo complete, and no delete path. Running it with no "
    "arguments prints an argparse traceback rather than usage text.\n\n"
    "Storage is a single JSON array rewritten in full on every change, with no temp-file or "
    "rename step, so an interrupted write truncates the file. Loading does not guard against a "
    "missing 'due' key, so a record written by an older version raises KeyError on list.\n\n"
    "The add command accepts any string for --due without validating the format, so an unparseable "
    "date is stored verbatim and silently excluded from every later filter. Ids are assigned by "
    "taking len(items) + 1, which reuses an id after a deletion.\n\n"
    "There is no interactive mode, no search, and no way to edit an item's text once created."
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
    """One drain round with a stubbed lens sweep + stubbed gap/task extraction.

    P11.3 note: the round now asks the model to DECOMPOSE before it asks for gaps, so the model
    queue must account for that call or every later response shifts by one. `no split` yields
    fewer than two parts, so decomposition no-ops and the tree stays flat — which is what the
    tests below that aren't about the tier walk actually want."""
    for _ in _LENSES:
        harness.output_queue.append(_REPORT)
    if orch.s.drain_tier_walk:
        orch.models._client.queue_text("no split")   # decomposition -> <2 parts -> no-op
    orch.models._client.queue_text(gap_lines)   # goal_alignment -> gap analysis
    orch.models._client.queue_text("none")      # clean_code
    orch.models._client.queue_text("none")      # project_documentation
    return await orch._drain_round(eid, chan, root, REPO, _delivery())


async def _drain_queue(orch, eid, round_no, node=None):
    """P20 ONE TASK AT A TIME — dispatch every queued task in the scope, ONE implementer turn each,
    until the queue is empty. Production does this via `_finish_effort`'s pending-drain (dispatch
    the next single task, re-enter, sweep only when empty); the tests drive `_drain_round` /
    `_drain_iterate` directly, so they drain the round's queue here between sweeps."""
    for _ in range(50):                       # bound: a real round never queues 50; guards a slip
        opens = await orch._dispatchable_tasks(eid, node)
        if not opens:
            return
        await orch._drain_iterate(eid, opens, round_no)


async def test_a_scope_completes_after_exactly_three_rounds_of_2_then_1_then_0(db_url):
    """The plan's assertion verbatim: a stubbed lens returning 2 gaps, then 1, then 0 completes
    after exactly 3 rounds — and the runaway cap is never reached. P20: each round's tasks now
    dispatch one at a time, so the test drains the queue between sweeps; the SWEEP counts (2, 1, 0)
    and the termination are unchanged — those are `_drain_round`, not the dispatch cadence."""
    orch, _chat, harness, db = await _orch(db_url, tier_walk=False)
    try:
        eid, chan, root = await _effort(orch)
        r1 = await _round(orch, harness, eid, chan, root,
                          "add a delete command\nprint usage help with no arguments")
        assert r1["round"] == 1 and r1["new_tasks"] == 2
        await _drain_queue(orch, eid, r1["round"])          # drain BOTH, one at a time

        r2 = await _round(orch, harness, eid, chan, root, "validate the due date format")
        assert r2["round"] == 2 and r2["new_tasks"] == 1
        await _drain_queue(orch, eid, r2["round"])          # drain the 1

        r3 = await _round(orch, harness, eid, chan, root, "none")
        assert r3["round"] == 3 and r3["new_tasks"] == 0    # ZERO PROPAGATION -> complete
        assert r3["capped"] is False
        assert r3["open_tasks"] == []                       # and the queue drained
        assert "complete" in r3["note"].lower()
        assert await orch._event_count(eid, "drain_round") == 3
    finally:
        await _shutdown(orch, db)


async def test_drain_iterate_dispatches_one_task_and_leaves_the_rest_queued(db_url):
    """P20 — ONE task per implementer turn (ORCHESTRATION-DESIGN §4/§5). Given three open tasks, a
    single `_drain_iterate` dispatches exactly ONE (closing it `dispatched`) and leaves the other
    two queued for their own turns. Handing a worker the whole queue is the multi-task turn that
    "don't typically work reliably" — gym-018's worker hung trying to do 6 in one pass."""
    orch, _chat, harness, db = await _orch(db_url, tier_walk=False)
    try:
        eid, chan, root = await _effort(orch)
        r1 = await _round(orch, harness, eid, chan, root,
                          "add a delete command\nadd an edit command\nvalidate the due date")
        assert r1["new_tasks"] == 3
        assert await orch._drain_iterate(eid, r1["open_tasks"], r1["round"]) is True
        still_open = await orch._dispatchable_tasks(eid, None)
        assert len(still_open) == 2                        # ONE dispatched, two remain queued
        brief = orch._iterate_after[eid]
        assert "delete" in brief                           # the first-derived task, dispatched
        assert "edit command" not in brief                 # a sibling — NOT in this turn's brief
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
        # P11.2: phrased as an evaluation, not a prohibition wrapped around an imperative
        assert "this is just evaluative" in planner[0]["prompt"]
        assert planner[0].get("plan_only") is True
        # the implementer runs in the effort's own session, ROTATED by the drain round — so it is
        # neither the planner's session nor the session that just declared the goal met
        implementer_session = await orch._session_for(eid)
        assert implementer_session != planner[0]["session_id"]
        assert implementer_session != eid          # rotated away from the pre-drain session
    finally:
        await _shutdown(orch, db)


async def test_implementer_gets_one_task_and_the_plan_not_the_whole_goal(db_url):
    """gym-008: `_auto_iterate` re-sent the ENTIRE original goal to the worker that had just
    satisfied it — asking it to plan work it had just done — and got an empty plan. P20: the brief
    now carries exactly ONE task (§4/§5 — the worker holds one bounded unit and is unaware of the
    bigger picture), never the sibling task and never the whole goal."""
    orch, _chat, harness, db = await _orch(db_url, tier_walk=False)
    try:
        eid, chan, root = await _effort(orch)
        r1 = await _round(orch, harness, eid, chan, root,
                          "add a delete command\nprint usage help with no arguments")
        harness.output_queue.append("1. edit todo.py: add a `delete` subcommand.")
        assert await orch._drain_iterate(eid, r1["open_tasks"], r1["round"]) is True
        brief = orch._iterate_after[eid]
        assert "add a delete command" in brief                   # the ONE dispatched task
        assert "print usage help" not in brief                   # the sibling stays queued (P20)
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
        # P11.3: the walk is BOTTOM-UP — a parent only becomes the working scope once its children
        # are done. That is exactly when a seam check is meaningful: the assembled product exists.
        for k in kids:
            assert await orch._complete_scope(k, eid) is not None
            assert (await orch._scope_node(k))["status"] == "done"
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
        # Assert the MEANING, not the wording: the note must say a sweep didn't happen and must
        # not read as completion. (P17 reworded this message when `swept` grew the goal_alignment
        # requirement; the original assertion pinned the exact phrase "could not run" and failed
        # on a behaviour-identical change.)
        assert "not** a clean sweep" in r["note"]
        assert "no lens produced a report" in r["note"].lower()
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
        # the child belongs to someone else, so THIS effort's working scope stays the parent
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
        # P11.3: decomposition runs FIRST, from the REPORTS — so it is the first model call.
        orch.models._client.queue_text(
            "commands :: the add/delete/complete subcommands\n"
            "storage :: writing and reading todos.json\n"
            "export :: rendering todos to other formats")
        orch.models._client.queue_text(
            "add a delete command\nadd a complete command\nvalidate the due date\n"
            "print usage help\nwrite todos atomically\nadd a csv export")
        orch.models._client.queue_text("none")
        orch.models._client.queue_text("none")
        r = await orch._drain_round(eid, chan, root, REPO, _delivery())
        # the round now WORKS a child scope; the tree hangs off its parent
        working = await orch._scope_node(r["scope_node_id"])
        assert working["depth"] == 1
        kids = await orch._scope_children(working["parent_id"])
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
        orch.models._client.queue_text(          # P11.3: decomposition is the FIRST model call
            "commands :: the add/delete/complete subcommands\n"
            "storage :: writing and reading todos.json\n"
            "export :: rendering todos to other formats")
        orch.models._client.queue_text(
            "add a delete command\nadd a complete command\nvalidate the due date\n"
            "print usage help\nwrite todos atomically\nadd a csv export")
        orch.models._client.queue_text("none")
        orch.models._client.queue_text("none")
        r = await orch._drain_round(eid, chan, root, REPO, _delivery())
        working = await orch._scope_node(r["scope_node_id"])
        assert len(await orch._scope_children(working["parent_id"])) == 3   # it DID decompose
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
        # P13.1: a parent dispatches ONLY its own scope's tasks. Neither child's work is its to do
        # — the foreign-owned one because someone else owns it, the unowned one because the walk
        # will select that scope and work it there. gym-011 blocked an effort by handing a worker
        # 12 tasks and a scope covering 5 of them.
        assert await orch._dispatchable_tasks(eid, parent) == []
        assert len(await orch.list_open_tasks(scope_node_id=kids[1])) == 1   # still queued
        # ...and when the walk selects that child, its task IS dispatchable
        assert [t["body"] for t in await orch._dispatchable_tasks(eid, kids[1])] == ["add csv export"]
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


# ══════════════════════════════════════════════════════════════════════════════
# P11 — THE SEQUENCE. gym-009 (2026-07-19) ran the drain loop with its steps in an order that
# contradicted what they claimed to do: a "plan" turn implemented, the implementer ran second and
# could only find work already done, and decomposition happened AFTER the analysis it was meant to
# scope. These pin the corrected order.
# ══════════════════════════════════════════════════════════════════════════════
from app.orchestrator import _is_lens_report  # noqa: E402


def test_a_truncated_lens_turn_is_not_a_report():
    """gym-009's goal_alignment lens produced no report in 3 of 3 rounds — its turn ended mid-flight
    and the last narration line was stored as the round's findings. Gap analysis then read a 72-char
    stub as evidence that a 5417-char goal was unmet and invented 12 tasks for shipped features."""
    assert _is_lens_report(
        "All 44 tests pass. Now let me do manual CLI testing to probe edge cases.") is False
    assert _is_lens_report("All 47 tests pass. Let me do manual CLI testing:") is False
    # length alone is not enough — a long preamble is still a preamble
    assert _is_lens_report("Now let me examine the codebase carefully. " * 20) is False
    # a genuine report passes
    assert _is_lens_report("The codebase is clean and well structured. " * 20) is True


async def test_a_truncated_lens_does_not_sweep_or_reach_gap_analysis(db_url):
    orch, _chat, harness, db = await _orch(db_url, tier_walk=False)
    try:
        eid, chan, root = await _effort(orch)
        for _ in _LENSES:
            harness.output_queue.append("All 44 tests pass. Now let me do manual CLI testing.")
        r = await orch._drain_round(eid, chan, root, REPO, _delivery())
        assert r["swept"] is False and r["new_tasks"] == 0
        assert "not** a clean sweep" in r["note"]                  # meaning, not wording
        assert await orch._event_count(eid, "lens_report_truncated") == 3
        assert await orch._event_count(eid, "gap_analysis") == 0   # never ran on a stub
    finally:
        await _shutdown(orch, db)


async def test_gap_analysis_is_told_the_report_is_the_authority_on_what_exists(db_url):
    """P11.4 — the question is 'what REMAINS', asked of a report that already observed what EXISTS.
    The old framing ('what the goal requires that the report does not evidence') restated the goal
    whenever the report was thin."""
    orch, _chat, _harness, db = await _orch(db_url)
    try:
        eid, _c, _r = await _effort(orch)
        orch.models._client.queue_text("none")
        await orch._gap_analysis(eid, _REPORT, GOAL)
        call = orch.models._client.calls[-1]
        sys_p = call["system"]
        assert "MAY ALREADY BE IMPLEMENTED" in sys_p
        assert "treat anything it describes as DONE" in sys_p
        assert "Do NOT list anything the report describes as existing" in sys_p
        assert "WHAT THE CODEBASE ALREADY DOES" in call["user"]
    finally:
        await _shutdown(orch, db)


async def test_decomposition_happens_before_gap_analysis(db_url):
    """THE gym-009 SEQUENCING DEFECT: `_maybe_decompose` ran after `_gap_analysis`, so the children
    could never inform the analysis that created them. Order is now sweep -> decompose -> SELECT a
    child (for DISPATCH) -> analyse. The child selection still governs which tasks run this round;
    what changed in P19 F19-redux is the goal gap analysis is HANDED — the product goal, mined once,
    not the child's goal mined per-scope (which N-plicated cross-scope findings and broke the
    termination count)."""
    orch, _chat, harness, db = await _orch(db_url, tier_walk=True)
    try:
        eid, chan, root = await _effort(orch)
        big = ("The tool stores todos in todos.json and supports add and list. " * 40)
        for _ in _LENSES:
            harness.output_queue.append(big)
        orch.models._client.queue_text(          # decomposition, from the REPORTS
            "storage :: persisting todos to todos.json\n"
            "commands :: the add/list/done subcommands\n"
            "output :: rendering todos to the terminal")
        orch.models._client.queue_text("write todos atomically")   # gap analysis (ONE call)
        orch.models._client.queue_text("none")
        orch.models._client.queue_text("none")
        r = await orch._drain_round(eid, chan, root, REPO, _delivery())
        node = await orch._scope_node(r["scope_node_id"])
        assert node["depth"] > 0, "the round must work a CHILD scope, not the root"
        assert node["effort_id"] == eid, "the child must be selectable by _ensure_scope_node"
        # the decomposition call came BEFORE the gap-analysis call
        kinds = [c["system"][:60] for c in orch.models._client.calls]
        assert "identify the distinct parts" in kinds[0]
        assert "report describing what a codebase" in kinds[1]
        # ...and gap analysis ran exactly ONCE (F19-redux: no per-scope fan-out), against the
        # PRODUCT goal — the whole-branch report is mined in a single pass.
        gap_calls = [c for c in orch.models._client.calls
                     if "report describing what a codebase" in c["system"]]
        assert len(gap_calls) == 1
        assert GOAL in gap_calls[0]["user"]
    finally:
        await _shutdown(orch, db)


async def test_the_drain_planner_is_evaluative_and_gated(db_url):
    """P11.2 — gym-009's planner did the whole implementation (6 min, 5 commits) before the
    implementer was dispatched. The three lenses stay read-only every round with no enforcement,
    because they ask for an assessment. The planner is now phrased the same way, plus plan_only."""
    orch, _chat, harness, db = await _orch(db_url, tier_walk=False)
    try:
        eid, chan, root = await _effort(orch)
        harness.output_queue.append("todo.py would need a delete subcommand; tests/ needs a case.")
        out = await orch._drain_plan(eid, "- add a delete command")
        assert out
        w = [x for x in harness.wakes if "~plan" in (x.get("session_id") or "")][0]
        assert w.get("plan_only") is True                  # the mechanical backstop
        p = w["prompt"]
        assert "this is just evaluative" in p              # the lens phrasing that actually works
        assert "write a short report" in p
        assert "implementation plan" not in p              # no imperative
        assert GOAL not in p                               # the goal stays out of an evaluation
    finally:
        await _shutdown(orch, db)


# ══════════════════════════════════════════════════════════════════════════════
# P13 — scope and gate coherence (gym-011, 2026-07-19)
# ══════════════════════════════════════════════════════════════════════════════
async def test_only_the_selected_scopes_tasks_are_dispatched(db_url):
    """gym-011 BLOCKED an effort here. `_drain_iterate` handed the worker 12 tasks and a 63-char
    scope covering 5 of them; the worker escalated the other 7 exactly as `_scope_context`
    prescribes, and the plan gate — judging against the GOAL — rejected that three times."""
    orch, _chat, _harness, db = await _orch(db_url)
    try:
        eid, _c, _r = await _effort(orch)
        parent = await orch._ensure_scope_node(eid)
        kids = await orch.decompose_scope(parent, [("storage", "persist todos to disk"),
                                                   ("history", "commit message quality")])
        for body in ("write todos atomically", "add a fsync on close"):
            await orch.add_task(body, project_slug="gym", scope_node_id=kids[0],
                                effort_id=eid, round_no=1)
        await orch.add_task("rewrite commit subjects", project_slug="gym",
                            scope_node_id=kids[1], effort_id=eid, round_no=1)
        got = [t["body"] for t in await orch._dispatchable_tasks(eid, kids[0])]
        assert got == ["write todos atomically", "add a fsync on close"]
        assert "rewrite commit subjects" not in got          # a sibling scope's work
        assert len(await orch.list_open_tasks(scope_node_id=kids[1])) == 1   # still queued
    finally:
        await _shutdown(orch, db)


def test_a_narration_stub_is_not_a_plan():
    """P13.3 — the third instance of one pattern: a turn ends on narration and a consumer
    adjudicates the fragment. gym-011 rejected finished work over `Final test run and commit:`."""
    from app.orchestrator import _is_plan_reply
    assert _is_plan_reply("Final test run and commit:") is False
    assert _is_plan_reply("ok") is False
    assert _is_plan_reply("Now let me examine the codebase. " * 20) is False
    # a STRUCTURED plan passes however short — plans are legitimately terser than lens reports
    assert _is_plan_reply("UNDERSTANDING: port it\nPLAN:\n1. edit parser.py") is True


async def test_only_defect_grade_findings_become_tasks(db_url):
    """P13.6 — an aesthetic lens asked "how could this be better?" always answers, so without a
    severity floor the propagation count has no fixed point and E5 is unmeasurable. gym-011 round
    1: 7 of 12 tasks were commit-message preferences."""
    orch, _chat, _harness, db = await _orch(db_url)
    try:
        eid, _c, _r = await _effort(orch)
        orch.models._client.queue_text(
            "DEFECT: add a guard for the missing due field\n"
            "PREFERENCE: rewrite commit subject lines to state intent\n"
            "PREFERENCE: restructure commit bodies into bullet points\n"
            "DEFECT: fix the crash when run with no arguments")
        tasks = await orch._tasks_from_lens(eid, "clean_code", "a report body")
        assert tasks == ["add a guard for the missing due field",
                         "fix the crash when run with no arguments"]
        assert await orch._event_count(eid, "lens_preferences_dropped") == 1
    finally:
        await _shutdown(orch, db)


async def test_a_round_of_pure_preference_propagates_zero(db_url):
    orch, _chat, _harness, db = await _orch(db_url)
    try:
        eid, _c, _r = await _effort(orch)
        orch.models._client.queue_text(
            "PREFERENCE: use narrative prose in commit bodies\n"
            "PREFERENCE: add cross-references between commits")
        assert await orch._tasks_from_lens(eid, "project_documentation", "a report body") == []
    finally:
        await _shutdown(orch, db)


# ══════════════════════════════════════════════════════════════════════════════
# P14 — escalation routing (gym-012, 2026-07-19: frozen `ambiguous_scope`)
# ══════════════════════════════════════════════════════════════════════════════
async def test_an_escalation_routes_to_the_sibling_scope_instead_of_freezing(db_url):
    """gym-012 did everything right — completed its in-scope task, escalated a test-assertions task
    that had been mis-filed into the persistence scope — and the org FROZE on `ambiguous_scope`
    with a four-child tree sitting right there. `_escalation_target` existed since P10.6; nothing
    connected an ESCALATE marker to it."""
    orch, _chat, _harness, db = await _orch(db_url)
    try:
        eid, _c, _r = await _effort(orch)
        root = await orch._ensure_scope_node(eid)
        kids = await orch.decompose_scope(root, [
            ("storage", "json data storage and persistence layer"),
            ("testing", "test suite assertions and output verification")])
        await orch._attach_effort_to_scope(kids[0], eid)   # the worker is in `storage`
        tid, _n = await orch.add_task(
            "Add assertions to filter tests verifying output content",
            project_slug="gym", scope_node_id=kids[0], effort_id=eid, round_no=1)
        await orch.close_task(tid)                        # closed at dispatch, as the drain does
        routed = await orch._route_escalation(
            eid, "ESCALATE: adding assertions to filter tests verifying output content is "
                 "outside my scope; a testing-scoped worker should handle it")
        assert routed == 1
        assert await orch._event_count(eid, "escalation_routed") == 1
        # re-filed into `testing`, and REOPENED — an escalated task is not done
        moved = await orch.list_open_tasks(scope_node_id=kids[1])
        assert len(moved) == 1 and "filter tests" in moved[0]["body"]
        assert await orch.list_open_tasks(scope_node_id=kids[0]) == []
    finally:
        await _shutdown(orch, db)


async def test_an_escalation_with_no_plausible_owner_still_reaches_a_human(db_url):
    """Do not make freeze unreachable — a genuine cross-project escalation must still elevate."""
    orch, _chat, _harness, db = await _orch(db_url)
    try:
        eid, _c, _r = await _effort(orch)
        root = await orch._ensure_scope_node(eid)
        await orch.decompose_scope(root, [("storage", "json persistence layer")])
        assert await orch._route_escalation(
            eid, "ESCALATE: the upstream vendor SDK is broken and needs a new licence key") == 0
    finally:
        await _shutdown(orch, db)


async def test_a_derived_task_is_filed_to_the_scope_that_owns_it(db_url):
    """P14.2 — gym-012 filed a test-assertions task into the DATA STORAGE scope because assignment
    followed SELECTION rather than content, which is why there was anything to escalate."""
    orch, _chat, harness, db = await _orch(db_url)
    try:
        eid, chan, root_post = await _effort(orch)
        root = await orch._ensure_scope_node(eid)
        kids = await orch.decompose_scope(root, [
            ("storage", "json data storage persistence atomic writes"),
            ("testing", "test suite assertions coverage verification")])
        await orch._attach_effort_to_scope(kids[0], eid)
        # `_best_scope_for` takes scope DICTS (as `_scope_children` returns), not ids
        cands = await orch._scope_children(root)
        assert await orch._best_scope_for(
            "add assertions to the test suite verifying coverage", cands) == kids[1]
        assert await orch._best_scope_for(
            "make the json persistence writes atomic", cands) == kids[0]
        # no clear owner -> no guess (the caller falls back to the selected scope)
        assert await orch._best_scope_for("rename a variable", cands) is None
    finally:
        await _shutdown(orch, db)


# ══════════════════════════════════════════════════════════════════════════════
# P15 — verification fidelity. The operator tested gym-013's delivery and found 5 bugs + 10 design
# gaps on a product the loop had declared complete at zero propagation. The loop counted correctly;
# the evidence had been softened twice before it was counted. Fixtures below are the operator's
# ACTUAL findings (2026-07-19).
# ══════════════════════════════════════════════════════════════════════════════
async def test_the_operators_findings_grade_as_defects_not_preferences(db_url):
    """Every one of these was seen by a lens in gym-013 and dropped as a preference; every one came
    back in the operator's report as a real bug or gap. `done`/`reopen` silently succeeding, the
    REPL discarding error output, empty text accepted, and an undocumentable data contract are the
    software behaving wrongly — not taste."""
    orch, _chat, _harness, db = await _orch(db_url)
    try:
        eid, _c, _r = await _effort(orch)
        orch.models._client.queue_text(
            "DEFECT: make done on an already-done item report that it was already done\n"
            "DEFECT: surface command errors in the REPL instead of discarding the return code\n"
            "DEFECT: reject empty todo text on add and edit\n"
            "DEFECT: replace the Dict[str, Any] item model with a documented TypedDict\n"
            "GAP: add a --sort flag for list output\n"
            "PREFERENCE: use f-strings consistently instead of mixing with percent formatting")
        tasks = await orch._tasks_from_lens(eid, "clean_code", "a report body")
        assert len(tasks) == 4, tasks
        assert any("already-done" in t for t in tasks)
        assert any("REPL" in t for t in tasks)
        assert any("TypedDict" in t for t in tasks)
        assert not any("f-strings" in t for t in tasks)      # preference stays out
        assert not any("--sort" in t for t in tasks)         # GAP is not a counted task
    finally:
        await _shutdown(orch, db)


async def test_a_gap_is_queued_but_never_counted(db_url):
    """P15.2's third grade. A GAP keeps required-but-not-malfunctioning work visible without
    reintroducing the non-terminating loop the floor was built to stop (gym-009: 21 -> 23 ->
    ascending)."""
    orch, _chat, harness, db = await _orch(db_url, tier_walk=False)
    try:
        eid, chan, root = await _effort(orch)
        for _ in _LENSES:
            harness.output_queue.append(_REPORT)
        orch.models._client.queue_text("none")                      # gap analysis
        orch.models._client.queue_text("GAP: add a --sort flag\nGAP: add overdue highlighting")
        orch.models._client.queue_text("none")
        r = await orch._drain_round(eid, chan, root, REPO, _delivery())
        assert r["new_tasks"] == 0, "a GAP must not increment the propagation count"
        queued = await orch.list_open_tasks(effort_id=eid)
        assert len(queued) == 2, "but it must still be queued and visible"
        assert {t["lens"] for t in queued} == {"gap"}
    finally:
        await _shutdown(orch, db)


async def test_a_lens_is_told_not_to_invent_its_own_bar(db_url):
    """P15.2b — the org's lens graded SOLID "Strong" where the operator graded 2/5, having supplied
    a scale qualifier the verbatim prompt never set ("for its scope", "at this scale"). That is
    verdict framing returning in the ANSWER after P10.1 removed it from the prompt."""
    orch, _chat, harness, db = await _orch(db_url)
    try:
        eid, chan, root = await _effort(orch)
        for _ in _LENSES:
            harness.output_queue.append(_REPORT)
        await orch._lens_sweep(eid, chan, root, REPO, _delivery(), round_no=1)
        for w in harness.wakes:
            p = w["prompt"]
            assert "criteria AS WRITTEN" in p
            assert "for its scope" in p          # named as a thing NOT to do
            assert "wrong at any size" in p
            assert "VERDICT" not in p            # P10.1 still holds
    finally:
        await _shutdown(orch, db)


async def test_the_implementer_is_told_to_record_why_in_the_commit(db_url):
    """P15.5 — the operator scored the history 5/10: intent clarity 5, context linking 2. The org
    holds the goal, the scenario and the acceptance check and passed none of them on."""
    orch, _chat, harness, db = await _orch(db_url, tier_walk=False)
    try:
        eid, chan, root = await _effort(orch)
        r1 = await _round(orch, harness, eid, chan, root, "add a delete command")
        harness.output_queue.append("plan")
        await orch._drain_iterate(eid, r1["open_tasks"], r1["round"])
        brief = orch._iterate_after[eid]
        assert "WHY before what" in brief
        assert "traceable" not in brief          # the brief instructs; it doesn't lecture
        assert "verification you ran" in brief
    finally:
        await _shutdown(orch, db)


# ══════════════════════════════════════════════════════════════════════════════
# P16 — a recovered turn starts from the last COMMITTED state (gym-014, 2026-07-20)
# ══════════════════════════════════════════════════════════════════════════════
async def test_a_dead_turns_uncommitted_edits_are_discarded(db_url):
    """gym-014: a worker abandoned mid-edit leaving `tests/test_todo.py` modified and the suite
    FAILING (2 errors). The recovery re-engaged onto that same tree, so the next worker inherited a
    break it had not caused, burned its turn on it, and abandoned too — three turns lost to one
    partial edit. Uncommitted work from a turn that DIED is wreckage, not work."""
    orch, _chat, _harness, db = await _orch(db_url)
    try:
        eid, _c, _r = await _effort(orch)
        seen = {}

        # P17 F6 — a KNOWN worker url must be cleaned on THAT worker. `exec_check` acquires
        # whichever worker is free, so the original went through it and, in gym-015, cleaned
        # worker-1 while worker-2 held the dirty tree. `run_check` takes the base url directly.
        async def _fake_run_check(base_url, command, timeout=120, **kw):
            seen["url"], seen["command"] = base_url, command
            return 0, " M tests/test_todo.py\n?? scratch.py\nDISCARD-DONE", False

        async def _must_not_run(*a, **kw):
            raise AssertionError("targeted discard must not go through the acquire path")

        orch.router.harness.run_check = _fake_run_check
        orch.router.exec_check = _must_not_run
        assert await orch._discard_uncommitted("http://w1:8090", eid) is True
        assert seen["url"] == "http://w1:8090"           # the worker that hung, not a free one
        # reverts tracked edits AND drops untracked files, scoped to the workspace
        assert "git checkout -- ." in seen["command"]
        assert "git clean -fd" in seen["command"]
        assert "reset --hard" not in seen["command"]     # proxy-illegal; never used
        assert await orch._event_count(eid, "stall_tree_discarded") == 1
    finally:
        await _shutdown(orch, db)


async def test_the_idle_path_with_no_worker_url_falls_back_to_the_pool(db_url):
    """P17 F6 — the idle-stall recovery implicates no specific daemon, so it has no url to target.
    That path must still clean (via the pool), just without attribution."""
    orch, _chat, _harness, db = await _orch(db_url)
    try:
        eid, _c, _r = await _effort(orch)
        seen = {}

        async def _fake_exec(effort_id, *, command, session_id, **kw):
            seen["command"] = command
            return 0, " M todo.py\nDISCARD-DONE", False

        async def _must_not_run(*a, **kw):
            raise AssertionError("no url is known — nothing to target")

        orch.router.exec_check = _fake_exec
        orch.router.harness.run_check = _must_not_run
        assert await orch._discard_uncommitted("", eid) is True
        assert "git clean -fd" in seen["command"]
    finally:
        await _shutdown(orch, db)


async def test_a_clean_tree_discards_nothing_and_is_not_audited(db_url):
    orch, _chat, _harness, db = await _orch(db_url)
    try:
        eid, _c, _r = await _effort(orch)

        async def _clean(base_url, command, timeout=120, **kw):
            return 0, "DISCARD-DONE", False

        orch.router.harness.run_check = _clean
        assert await orch._discard_uncommitted("http://w1:8090", eid) is False
        assert await orch._event_count(eid, "stall_tree_discarded") == 0
    finally:
        await _shutdown(orch, db)


async def test_a_cleanup_failure_never_blocks_the_recovery(db_url):
    """The discard serves the recovery; it must never be able to prevent one."""
    orch, _chat, _harness, db = await _orch(db_url)
    try:
        eid, _c, _r = await _effort(orch)

        async def _boom(*a, **kw):
            raise RuntimeError("worker unreachable")

        orch.router.harness.run_check = _boom
        assert await orch._discard_uncommitted("http://w1:8090", eid) is False
    finally:
        await _shutdown(orch, db)


def test_the_stall_escalation_does_not_assert_an_unchecked_cause():
    """It told the operator "something structural is blocking it (a repo, clone, or tool problem),
    not your code" — wrong on every count in gym-014, and it aimed attention at the wrong layer."""
    import inspect
    from app.orchestrator import Orchestrator
    src = inspect.getsource(Orchestrator)
    assert "Something structural is blocking it" not in src
    assert "I have NOT identified the cause" in src
