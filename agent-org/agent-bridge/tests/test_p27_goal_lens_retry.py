"""P27 — retry the goal lens; never close "done" on an incomplete sweep (design §6.5/§6.6).

The goal_alignment lens is the convergence single point of failure: without its report the round is
`swept=False`, gap analysis is skipped, and the effort used to deliver-and-close "done" on a
comparison that never happened (gym-020, gym-024 r9, gym-025 r1). F27.1 re-runs just that lens once
(stochastic truncation often recovers on a fresh session). F27.2: if it is STILL missing, the effort
may deliver a PR but must NOT close "done" — the closure marks needs-attention.

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
from app.models import Effort
from app.modules.capabilities import BranchDelivery
from app.modules.model_router import FakeModelClient
from app.orchestrator import Orchestrator
from app.worker.harness import FakeHarness

ROOT = Path(__file__).resolve().parents[1]
REPO = "https://github.com/acme/gym.git"
GOAL = "a todo CLI that adds, lists, completes and deletes todos with a due date"
_STUB = "All 44 tests pass. Now let me do manual CLI testing."   # too short → not a lens report
_REPORT = (
    "The tool stores todos in todos.json and supports add and list. Each todo has a title and a due "
    "date. There is no way to mark a todo complete, and no delete path. Running it with no arguments "
    "prints an argparse traceback rather than usage text. Storage is a single JSON array rewritten in "
    "full on every change, with no temp-file or rename step, so an interrupted write truncates the "
    "file. The add command accepts any string for --due without validating the format, so an "
    "unparseable date is stored verbatim and silently excluded from every later filter. Ids are "
    "assigned by taking len(items)+1, which reuses an id after a deletion. There is no interactive "
    "mode, no search, and no way to edit an item's text once created."
)


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


# ── F27.1 — the goal-lens retry ───────────────────────────────────────────────
async def test_goal_lens_retry_fires_and_recovers_the_sweep(db_url):
    """The goal lens truncates on the first attempt; the bounded retry re-runs it in a fresh session
    and it reports — so the round becomes `swept` instead of delivering on an uncompared sweep."""
    orch, db = await _orch(db_url)
    try:
        eid, chan, root = await _effort(orch)
        # sweep order is goal_alignment, clean_code, project_documentation; then the retry re-runs goal.
        orch.harness.output_queue.extend([_STUB, _REPORT, _REPORT, _REPORT])
        for _ in range(3):
            orch.models._client.queue_text("none")   # gap + clean + proj task extraction
        r = await orch._drain_round(eid, chan, root, REPO, _delivery())
        assert await orch._event_count(eid, "goal_lens_retry") == 1
        assert r["swept"] is True          # the retry recovered the goal comparison
    finally:
        await _shutdown(orch, db)


async def test_no_retry_when_the_goal_lens_reports_first_time(db_url):
    orch, db = await _orch(db_url)
    try:
        eid, chan, root = await _effort(orch)
        orch.harness.output_queue.extend([_REPORT, _REPORT, _REPORT])
        for _ in range(3):
            orch.models._client.queue_text("none")
        r = await orch._drain_round(eid, chan, root, REPO, _delivery())
        assert await orch._event_count(eid, "goal_lens_retry") == 0
        assert r["swept"] is True
    finally:
        await _shutdown(orch, db)


# ── F27.2 — an incomplete sweep never closes "done" ───────────────────────────
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
            return httpx.Response(201, json={"number": 7, "html_url": "https://x/pull/7"})
        if p.endswith("/pulls"):
            return httpx.Response(200, json=[])
        if p.count("/") == 3:
            return httpx.Response(200, json={"default_branch": "main"})
        return httpx.Response(404)
    return httpx.MockTransport(handler)


async def _lifecycle(orch, eid):
    async with orch.db.session_factory() as s:
        e = await s.get(Effort, eid)
    return e.lifecycle


async def test_incomplete_sweep_delivers_a_pr_but_does_not_close_done(db_url, tmp_path):
    """THE F27.2 invariant. The goal lens produces no report even after the retry, so the round is
    swept=False. The effort still opens its PR (review value), but it must NOT be certified 'done' —
    the card is needs-attention and the lifecycle is not 'done'."""
    orch, db = await _orch(db_url, tmp_path, github=True, goal_lens_retries=1)   # P29: pin 1 retry here
    try:
        await orch.projects.add("gym", "https://github.com/devonpveller/gym")   # on the App's account
        eid, _c, _r = await orch.router.open_effort("todo-product", project="gym")
        await orch.charters.set_goal(eid, "add a due-date field to the todo tool", created_by="po")
        orch._gh_transport = _remote()
        # every lens attempt (3 initial + 1 goal retry) truncates → goal_alignment never reports.
        orch.harness.output_queue.extend([_STUB, _STUB, _STUB, _STUB])
        delivery = BranchDelivery(verifiable=True, exists=True, ahead=1, files_changed=1,
                                  head_sha="headsha123456789000", branch=f"agent/{eid}")
        res = SimpleNamespace(status="done", output="Built the feature; tests green.")
        await orch._finish_effort(eid, res, delivery=delivery)

        assert await orch._event_count(eid, "goal_lens_retry") == 1
        assert await orch._event_count(eid, "delivery_pr_opened") == 1     # PR still opened for review
        assert await _lifecycle(orch, eid) != "done"                      # but NOT certified done
        msgs = " ".join(p["message"] for p in orch.chat.posted)
        assert "not certified converged" in msgs
    finally:
        await _shutdown(orch, db)
