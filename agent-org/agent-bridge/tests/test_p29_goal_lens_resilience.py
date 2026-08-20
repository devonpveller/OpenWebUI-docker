"""P29 — goal-lens resilience: a BOUNDED focused retry that completes, and no infinite incomplete loop.

The goal_alignment lens exhausts its budget on a matured product and produces no report → swept=False.
F27.1 retried once with the SAME exhaustive prompt (same wall). F29.1 makes the retry a FOCUSED
goal-coverage re-check (a mechanical wrapper on the operator's §6.5 prompt) and allows up to
`goal_lens_retries` attempts, so it completes. F29.2 bounds repeated incomplete sweeps: after
`incomplete_sweep_cap` consecutive swept=False rounds it ESCALATES instead of looping (gym-026).

Fakes + mocked GitHub.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import httpx

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
_STUB = "All 44 tests pass. Now let me do manual CLI testing."   # too short → not a lens report
_REPORT = (
    "The tool stores todos in todos.json and supports add and list. Each todo has a title and a due "
    "date. There is no way to mark a todo complete, and no delete path. Running it with no arguments "
    "prints an argparse traceback rather than usage text. The add command accepts any string for --due "
    "without validating the format, so an unparseable date is stored and silently excluded from every "
    "later filter. Ids are len(items)+1, reused after a deletion, and there is no way to edit an item.")


async def _orch(db_url, tmp_path=None, *, github=False, **overrides):
    kwargs = dict(
        _env_file=None, chat_adapter="fake",
        profiles_dir=str(ROOT / "profiles"), charters_dir=str(ROOT / "charters"),
        floor_dir=str(ROOT / "floor"), worker_instance_urls="http://w1:8090",
        max_concurrent_workers=1, database_url=db_url, project_survey_enabled=False,
        review_mode="off", plan_approval="off", qa_gate="report", drain_loop=True,
        drain_tier_walk=False, drain_plan_split=True,
    )
    if github and tmp_path is not None:
        key = tmp_path / "app.pem"
        key.write_text("dummy")
        kwargs.update(github_app_id="1", github_app_owner="devonpveller",
                      github_app_private_key_path=str(key))
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


# ── F29.1 — the focused, bounded retry ────────────────────────────────────────
async def test_focused_retry_recovers_the_sweep(db_url):
    """Goal lens truncates on the exhaustive pass; the FOCUSED retry reports → the round is swept."""
    orch, db = await _orch(db_url)
    try:
        eid, chan, root = await _effort(orch)
        # full sweep: goal(stub), clean(report), proj(report); then focused retry: goal(report)
        orch.harness.output_queue.extend([_STUB, _REPORT, _REPORT, _REPORT])
        for _ in range(3):
            orch.models._client.queue_text("none")   # gap + clean + proj extraction
        r = await orch._drain_round(eid, chan, root, REPO, _delivery())
        assert await orch._event_count(eid, "goal_lens_retry") == 1
        assert r["swept"] is True
    finally:
        await _shutdown(orch, db)


async def test_the_retry_is_focused_but_the_first_pass_is_not(db_url):
    """The bounding wrapper reaches the prompt ONLY on the retry — the operator's §6.5 pass is untouched."""
    orch, db = await _orch(db_url)
    try:
        eid, chan, root = await _effort(orch)
        orch.harness.output_queue.extend([_STUB, _REPORT, _REPORT, _REPORT])
        for _ in range(3):
            orch.models._client.queue_text("none")
        await orch._drain_round(eid, chan, root, REPO, _delivery())
        prompts = [w["prompt"] for w in orch.harness.wakes]
        focused = [p for p in prompts if "FOCUSED re-check" in p]
        assert len(focused) == 1                       # exactly the one retry pass is focused
        # the three initial lens passes are the exhaustive (un-wrapped) prompt
        assert sum("FOCUSED re-check" not in p for p in prompts) == len(_LENSES)
    finally:
        await _shutdown(orch, db)


async def test_focused_retries_are_bounded(db_url):
    """If the goal lens keeps failing, the retry fires at most `goal_lens_retries` times, then stops."""
    orch, db = await _orch(db_url, goal_lens_retries=2)
    try:
        eid, chan, root = await _effort(orch)
        # full sweep goal(stub), clean(report), proj(report); then 2 focused retries both stub
        orch.harness.output_queue.extend([_STUB, _REPORT, _REPORT, _STUB, _STUB])
        for _ in range(2):
            orch.models._client.queue_text("none")     # clean + proj extraction (no gap: goal missing)
        r = await orch._drain_round(eid, chan, root, REPO, _delivery())
        assert await orch._event_count(eid, "goal_lens_retry") == 2   # bounded
        assert r["swept"] is False
    finally:
        await _shutdown(orch, db)


# ── F29.2 — repeated incomplete sweeps escalate, they don't loop ──────────────
def _remote():
    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        if "/compare/" in p:
            return httpx.Response(200, json={"ahead_by": 1, "behind_by": 0, "commits": [],
                "files": [{"filename": "todo.py", "status": "added",
                           "additions": 40, "deletions": 0, "patch": "+todo"}]})
        if "/branches/" in p:
            return httpx.Response(200, json={"commit": {"sha": "headsha123456789000"}})
        if p.endswith("/pulls") and request.method == "POST":
            return httpx.Response(201, json={"number": 9, "html_url": "https://x/pull/9"})
        if p.endswith("/pulls"):
            return httpx.Response(200, json=[])
        if p.count("/") == 3:
            return httpx.Response(200, json={"default_branch": "main"})
        return httpx.Response(404)
    return httpx.MockTransport(handler)


async def test_repeated_incomplete_sweeps_escalate(db_url, tmp_path):
    """Two consecutive incomplete sweeps (cap=2) escalate to the human instead of churning forever."""
    orch, db = await _orch(db_url, tmp_path, github=True, incomplete_sweep_cap=2, goal_lens_retries=1)
    try:
        await orch.projects.add("gym", "https://github.com/devonpveller/gym")
        eid, _c, _r = await orch.router.open_effort("todo-product", project="gym")
        await orch.charters.set_goal(eid, GOAL, created_by="po")
        orch._gh_transport = _remote()
        delivery = BranchDelivery(verifiable=True, exists=True, ahead=1, files_changed=1,
                                  head_sha="headsha123456789000", branch=f"agent/{eid}")
        res = SimpleNamespace(status="done", output="done")
        # Each _finish_effort → drain round with the goal lens ALWAYS failing (stub for full + retry).
        for _round in range(2):
            orch.harness.output_queue.extend([_STUB, _REPORT, _REPORT, _STUB])   # goal fails both passes
            orch.models._client.queue_text("none")     # clean extraction
            orch.models._client.queue_text("none")     # proj extraction
            await orch._finish_effort(eid, res, delivery=delivery)
        assert await orch._event_count(eid, "incomplete_sweep_escalated") == 1   # fired once at the cap
    finally:
        await _shutdown(orch, db)
