"""Pending-decision hygiene (operator 2026-07-08): a bare `approve` listed 14 items — 11 of them
merge gates for PRs that no longer existed — burying the ONE decision that actually blocked work
(the frozen effort) at the end of the wall. Two mechanisms: (1) merge gates are RECONCILED
against the remote (a gate whose PR is closed/merged/gone is pruned); (2) a bare `approve`
prefers the BLOCKING decision — optional merge invites never hide it."""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from app.adapters.chat import FakeChatAdapter
from app.config import Settings
from app.db import Database
from app.modules.model_router import FakeModelClient
from app.orchestrator import Orchestrator
from app.worker.harness import FakeHarness

ROOT = Path(__file__).resolve().parents[1]


async def _orch(db_url, tmp_path):
    key = tmp_path / "app.pem"
    key.write_text("dummy")
    settings = Settings(
        _env_file=None, chat_adapter="fake",
        profiles_dir=str(ROOT / "profiles"), charters_dir=str(ROOT / "charters"),
        floor_dir=str(ROOT / "floor"), worker_instance_urls="http://w1:8090",
        max_concurrent_workers=1, database_url=db_url, project_survey_enabled=False,
        review_mode="off", plan_approval="off",
        github_app_id="1", github_app_owner="devonpveller",
        github_app_private_key_path=str(key),
    )
    db = Database(db_url)
    orch = Orchestrator(settings, db, FakeChatAdapter(),
                        model_client=FakeModelClient(), harness=FakeHarness())
    await orch.setup()
    return orch, orch.chat, db


def _pulls_remote(open_by_repo: dict[str, list[int]]):
    """GET /repos/{o}/{r}/pulls?state=open → the given numbers per repo name."""
    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        if p.endswith("/pulls") and request.method == "GET":
            repo = p.split("/repos/", 1)[1].rsplit("/", 1)[0]
            nums = open_by_repo.get(repo, [])
            return httpx.Response(200, json=[{"number": n} for n in nums])
        return httpx.Response(404)
    return httpx.MockTransport(handler)


async def test_stale_merge_gates_are_pruned_against_the_remote(db_url, tmp_path):
    orch, chat, db = await _orch(db_url, tmp_path)
    try:
        orch._gh_transport = _pulls_remote({"devonpveller/murder": [8]})
        for mid, pr in (("merge-effort-old-one", 3), ("merge-effort-old-two", 5),
                        ("merge-effort-live", 8)):
            orch._pending_merge[mid] = {"repo": "https://github.com/devonpveller/murder",
                                        "pr_number": pr, "effort_id": mid[6:]}
        cands = await orch._pending_decisions()
        assert "merge-effort-live" in cands                     # the real open PR keeps its gate
        assert "merge-effort-old-one" not in cands and "merge-effort-old-two" not in cands
        assert set(orch._pending_merge) == {"merge-effort-live"}
    finally:
        await db.dispose()


async def test_unreadable_remote_prunes_nothing(db_url, tmp_path):
    orch, chat, db = await _orch(db_url, tmp_path)
    try:
        orch._gh_transport = httpx.MockTransport(lambda r: httpx.Response(500))
        orch._pending_merge["merge-effort-x"] = {
            "repo": "https://github.com/devonpveller/murder", "pr_number": 3}
        cands = await orch._pending_decisions()
        assert "merge-effort-x" in cands                        # fail-open: never drop on a hiccup
    finally:
        await db.dispose()


async def test_bare_approve_prefers_the_blocking_decision_over_merge_invites(db_url, tmp_path):
    """One held plan + two live merge gates → a bare `approve` resolves the BLOCKING item and
    says the merge invites are still there — never a 14-item wall."""
    orch, chat, db = await _orch(db_url, tmp_path)
    try:
        orch._gh_transport = _pulls_remote({"devonpveller/murder": [8, 9]})
        orch._pending_merge["merge-effort-a"] = {
            "repo": "https://github.com/devonpveller/murder", "pr_number": 8}
        orch._pending_merge["merge-effort-b"] = {
            "repo": "https://github.com/devonpveller/murder", "pr_number": 9}
        from app.schemas import LifecyclePlan
        mgmt = await orch.mgmt_channel_id()
        orch._pending_lifecycle["plan-port"] = {
            "channel_id": mgmt, "root": "r", "plan": LifecyclePlan(goal="port it", steps=[])}
        await orch._handle_command("approve", mgmt, "t")
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "plan-port" in msgs and "blocks work" in msgs
        assert "merge invite" in msgs                           # the optional items acknowledged
        assert "which?" not in msgs.lower()                     # no disambiguation wall
    finally:
        await db.dispose()
