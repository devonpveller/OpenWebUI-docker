"""Post-delivery QA / exploratory evaluation (operator 2026-07-15, reviewing gym PR#2: the
delivery passed its own tests + read well, but was frustrating to USE — no help systems, and a
SEPARATE little-coder QA pass found a page of gaps "that could've been caught with a simple QA").
A differently-goaled agent exercises the running product and reports DEFECTS (in scope → fixable)
vs FOLLOWUPS (out of scope → the operator's call). Fakes only."""

from __future__ import annotations

import asyncio
from pathlib import Path

from app.adapters.chat import FakeChatAdapter
from app.config import Settings
from app.db import Database
from app.modules.capabilities import BranchDelivery
from app.modules.model_router import FakeModelClient
from app.orchestrator import Orchestrator, _qa_block, _qa_items
from app.worker.harness import FakeHarness

ROOT = Path(__file__).resolve().parents[1]

_QA_REPLY = (
    "WORKS: adds and lists todos with a due date, and --due-before filters as intended.\n"
    "DEFECTS:\n"
    "1. `python3 todo.py` with no args prints a bare argparse traceback, no usage help.\n"
    "2. --due accepts 'not-a-date' silently, so --due-before then filters nothing.\n"
    "FOLLOWUPS:\n"
    "1. no delete command to remove a todo.\n"
    "2. list output is unsorted.\n"
    "VERDICT: works for the happy path but rough for a new user — no help, weak input validation."
)


async def _orch(db_url, *, qa_gate="report", qa_code_review=False):
    settings = Settings(
        _env_file=None, chat_adapter="fake",
        profiles_dir=str(ROOT / "profiles"), charters_dir=str(ROOT / "charters"),
        floor_dir=str(ROOT / "floor"), worker_instance_urls="http://w1:8090",
        max_concurrent_workers=1, database_url=db_url, project_survey_enabled=False,
        review_mode="off", plan_approval="off", qa_gate=qa_gate,
        qa_code_review=qa_code_review,
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


# ── the section parsers ───────────────────────────────────────────────────────
def test_qa_block_isolates_a_section():
    assert "no args prints" in _qa_block(_QA_REPLY, "DEFECTS")
    assert "delete command" in _qa_block(_QA_REPLY, "FOLLOWUPS")
    # a section stops at the next header — DEFECTS must not swallow FOLLOWUPS/VERDICT
    assert "delete command" not in _qa_block(_QA_REPLY, "DEFECTS")
    assert "happy path" in _qa_block(_QA_REPLY, "VERDICT")


def test_qa_items_splits_and_honours_none():
    items = _qa_items(_qa_block(_QA_REPLY, "DEFECTS"))
    assert len(items) == 2 and items[0].startswith("`python3 todo.py`")
    assert _qa_items("none") == [] and _qa_items("None.") == [] and _qa_items("") == []


# ── the evaluation method ──────────────────────────────────────────────────────
async def test_qa_evaluation_runs_the_product_and_reports(db_url):
    orch, chat, harness, db = await _orch(db_url)
    try:
        await orch.projects.add("gym", "https://github.com/acme/gym.git")
        eid, chan, root = await orch.router.open_effort("feat", project="gym")
        await orch.charters.set_goal(eid, "add a due-date field and --due-before filter",
                                     created_by="po")
        harness.output_queue.append(_QA_REPLY)
        d = BranchDelivery(branch="agent/feat", exists=True, ahead=1, head_sha="abc1234567")
        note, defects = await orch._qa_evaluation(
            eid, chan, root, "https://github.com/acme/gym.git", d)
        # the QA turn checked out the DELIVERED branch and was told to change nothing
        assert any("git checkout -f agent/feat" in w["prompt"] and "CHANGE NOTHING" in w["prompt"]
                   for w in harness.wakes)
        assert len(defects) == 2 and "no args" in defects[0]
        assert "Defects (in scope" in note and "Follow-ups (out of scope" in note
        assert "delete command" in note and "happy path" in note      # verdict carried
        assert await orch._event_count(eid, "qa_evaluation") == 1
    finally:
        await _shutdown(orch, db)


async def test_qa_gate_off_skips_the_pass(db_url):
    orch, chat, harness, db = await _orch(db_url, qa_gate="off")
    try:
        await orch.projects.add("gym", "https://github.com/acme/gym.git")
        eid, chan, root = await orch.router.open_effort("feat", project="gym")
        d = BranchDelivery(branch="agent/feat", exists=True, ahead=1, head_sha="abc")
        note, defects = await orch._qa_evaluation(
            eid, chan, root, "https://github.com/acme/gym.git", d)
        assert note == "" and defects == [] and len(harness.wakes) == 0
    finally:
        await _shutdown(orch, db)


_CODE_REVIEW_REPLY = (
    "WORKS: clear function names and clean test isolation.\n"
    "DEFECTS:\n"
    "1. none of the 7 functions have docstrings, and there are no type hints on any signature.\n"
    "2. load_items() calls sys.exit(1) from the data layer instead of raising — untestable and "
    "kills the process.\n"
    "3. tests import via sys.path.insert with no __init__.py — a packaging hack.\n"
    "FOLLOWUPS:\n"
    "1. a plugin architecture for commands would open/closed-ify build_parser.\n"
    "VERDICT: reads clean but under-documented — no docstrings or type hints, exits from a data layer."
)


async def test_qa_code_review_lens_runs_and_merges_defects(db_url):
    """With AO_QA_CODE_REVIEW on, a SECOND differently-goaled reviewer reads the source; its
    code-quality defects merge into the same defect list that drives iterate (operator's 4th
    evaluation: SOLID / naming / docstrings / type hints)."""
    orch, chat, harness, db = await _orch(db_url, qa_code_review=True)
    try:
        await orch.projects.add("gym", "https://github.com/acme/gym.git")
        eid, chan, root = await orch.router.open_effort("feat", project="gym")
        await orch.charters.set_goal(eid, "add a due-date field and --due-before filter",
                                     created_by="po")
        harness.output_queue.append(_QA_REPLY)              # lens 1: functional
        harness.output_queue.append(_CODE_REVIEW_REPLY)     # lens 2: code craftsmanship
        d = BranchDelivery(branch="agent/feat", exists=True, ahead=1, head_sha="abc1234567")
        note, defects = await orch._qa_evaluation(
            eid, chan, root, "https://github.com/acme/gym.git", d)
        # both lenses woke, and the code-review lens was told to READ THE SOURCE, not run it
        assert len(harness.wakes) == 2
        assert any("CODE REVIEW" in w["prompt"] and "docstring" in w["prompt"]
                   and "SOLID" in w["prompt"] for w in harness.wakes)
        # the union of both lenses' defects feeds iterate: 2 functional + 3 code-quality
        assert len(defects) == 5
        assert any("docstring" in x for x in defects) and any("no args" in x for x in defects)
        # both sections render in the note
        assert "Code review — craftsmanship" in note and "Code-quality defects" in note
        assert await orch._event_count(eid, "qa_evaluation") == 2
    finally:
        await _shutdown(orch, db)


async def test_qa_code_review_off_by_default(db_url):
    """Default (lens off): only the functional lens runs — one wake, one event — so the
    wake-counting delivery harness is unchanged."""
    orch, chat, harness, db = await _orch(db_url)
    try:
        await orch.projects.add("gym", "https://github.com/acme/gym.git")
        eid, chan, root = await orch.router.open_effort("feat", project="gym")
        harness.output_queue.append(_QA_REPLY)
        d = BranchDelivery(branch="agent/feat", exists=True, ahead=1, head_sha="abc")
        note, defects = await orch._qa_evaluation(
            eid, chan, root, "https://github.com/acme/gym.git", d)
        assert len(harness.wakes) == 1 and len(defects) == 2
        assert "Code review — craftsmanship" not in note
        assert await orch._event_count(eid, "qa_evaluation") == 1
    finally:
        await _shutdown(orch, db)


async def test_qa_clean_product_reports_no_defects(db_url):
    orch, chat, harness, db = await _orch(db_url)
    try:
        await orch.projects.add("gym", "https://github.com/acme/gym.git")
        eid, chan, root = await orch.router.open_effort("clean", project="gym")
        harness.output_queue.append(
            "WORKS: everything.\nDEFECTS: none\nFOLLOWUPS: none\nVERDICT: solid and usable.")
        d = BranchDelivery(branch="agent/clean", exists=True, ahead=1, head_sha="def")
        note, defects = await orch._qa_evaluation(
            eid, chan, root, "https://github.com/acme/gym.git", d)
        assert defects == []
        assert "exercised cleanly" in note and "solid and usable" in note
    finally:
        await _shutdown(orch, db)
