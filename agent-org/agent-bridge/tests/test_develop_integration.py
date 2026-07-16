"""Develop-branch integration (operator 2026-07-15, reviewing gym PR#2-5: "the PRs should be
separate as they are now, but they should be MERGED INTO DEVELOPMENT" — the org left N parallel
PRs off main and never converged them into one product "like an actual project"). Each ACCEPTED
delivery is folded into a `develop` branch that accumulates the whole product, with one standing
`develop → default` PR as the human gate. Fakes + mocked GitHub."""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from app.adapters.chat import FakeChatAdapter
from app.config import Settings
from app.db import Database
from app.modules.capabilities import BranchDelivery, ensure_branch
from app.modules.github_app import FakeGitHubApp
from app.modules.model_router import FakeModelClient
from app.orchestrator import Orchestrator
from app.worker.harness import FakeHarness

ROOT = Path(__file__).resolve().parents[1]
REPO = "https://github.com/devonpveller/ai-orchestration-gym"


# ── the ensure_branch capability ──────────────────────────────────────────────
async def test_ensure_branch_creates_when_absent():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        if p.endswith("/git/ref/heads/develop") and request.method == "GET":
            return httpx.Response(404)                                  # develop absent
        if p == "/repos/devonpveller/ai-orchestration-gym":
            return httpx.Response(200, json={"default_branch": "main"})
        if p.endswith("/git/ref/heads/main"):
            return httpx.Response(200, json={"object": {"sha": "mainsha00000"}})
        if p.endswith("/git/refs") and request.method == "POST":
            seen["ref"] = json.loads(request.content)
            return httpx.Response(201, json={})
        return httpx.Response(404)

    res = await ensure_branch(FakeGitHubApp(owner="devonpveller"), REPO, "develop",
                              transport=httpx.MockTransport(handler))
    assert res.ok and "created" in res.summary
    assert seen["ref"] == {"ref": "refs/heads/develop", "sha": "mainsha00000"}


async def test_ensure_branch_idempotent_when_present():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/git/ref/heads/develop"):
            return httpx.Response(200, json={"object": {"sha": "devsha"}})
        return httpx.Response(500)                                       # nothing else may be hit

    res = await ensure_branch(FakeGitHubApp(owner="devonpveller"), REPO, "develop",
                              transport=httpx.MockTransport(handler))
    assert res.ok and "already exists" in res.summary


# ── the orchestrator integration step ──────────────────────────────────────────
async def _orch(db_url, tmp_path, *, develop_integration=True):
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
        develop_integration=develop_integration,
    )
    db = Database(db_url)
    orch = Orchestrator(settings, db, FakeChatAdapter(),
                        model_client=FakeModelClient(), harness=FakeHarness())
    await orch.setup()
    return orch, db


def _integ_handler(state: dict, *, conflict=False):
    def handler(request: httpx.Request) -> httpx.Response:
        p, m = request.url.path, request.method
        if p.endswith("/git/ref/heads/develop") and m == "GET":
            return httpx.Response(200, json={"object": {"sha": "devsha"}})   # develop exists
        if p.endswith("/merges") and m == "POST":
            state["merge"] = json.loads(request.content)
            if conflict:
                return httpx.Response(409, json={"message": "Merge conflict"})
            return httpx.Response(201, json={"sha": "mergedsha000"})
        if p.endswith("/pulls") and m == "POST":
            state["prod_pr"] = json.loads(request.content)
            return httpx.Response(201, json={"number": 9,
                                             "html_url": REPO + "/pull/9"})
        if p == "/repos/devonpveller/ai-orchestration-gym":
            return httpx.Response(200, json={"default_branch": "main"})
        return httpx.Response(404)
    return handler


async def test_accepted_delivery_is_merged_into_develop_with_product_pr(db_url, tmp_path):
    orch, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("gym", REPO)
        eid, _c, _r = await orch.router.open_effort("feat", project="gym")
        state: dict = {}
        orch._gh_transport = httpx.MockTransport(_integ_handler(state))
        d = BranchDelivery(branch="agent/feat", verifiable=True, exists=True, ahead=1, head_sha="abc123")
        note = await orch._integrate_to_develop(eid, REPO, d)
        # the effort branch was folded into develop, and the standing develop→default PR opened
        assert state["merge"]["base"] == "develop" and state["merge"]["head"] == "agent/feat"
        assert state["prod_pr"]["head"] == "develop" and state["prod_pr"]["base"] == "main"
        assert "Integrated into `develop`" in note and "Whole-product PR" in note
        assert "/pull/9" in note
        assert await orch._event_count(eid, "develop_integration") == 1
    finally:
        await db.dispose()


async def test_conflict_is_surfaced_not_forced(db_url, tmp_path):
    orch, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("gym", REPO)
        eid, _c, _r = await orch.router.open_effort("clash", project="gym")
        state: dict = {}
        orch._gh_transport = httpx.MockTransport(_integ_handler(state, conflict=True))
        d = BranchDelivery(branch="agent/clash", verifiable=True, exists=True, ahead=1, head_sha="def456")
        note = await orch._integrate_to_develop(eid, REPO, d)
        assert "Not integrated into `develop`" in note and "manual" in note
        assert "prod_pr" not in state                       # no product PR forced on a conflict
    finally:
        await db.dispose()


async def test_integration_off_is_a_noop(db_url, tmp_path):
    orch, db = await _orch(db_url, tmp_path, develop_integration=False)
    try:
        await orch.projects.add("gym", REPO)
        eid, _c, _r = await orch.router.open_effort("x", project="gym")
        orch._gh_transport = httpx.MockTransport(lambda r: httpx.Response(500))  # must not be hit
        d = BranchDelivery(branch="agent/x", verifiable=True, exists=True, ahead=1, head_sha="a")
        assert await orch._integrate_to_develop(eid, REPO, d) == ""
    finally:
        await db.dispose()
