"""DELIVERY-PIPELINE D1 (PR = the promotion artifact that makes branch work VISIBLE) + D4 (the
human-gated merge: operator approves — plain "merge it" or `approve merge-…` — and the bridge merges
via the host API; no auto-merge). Audit fix for the live complaint: 'the branching wasn't communicated
by the PM' — work landed on branches with no PR, invisible in GitHub's UI. Fakes + mocked GitHub."""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from app.adapters.chat import FakeChatAdapter
from app.config import Settings
from app.db import Database
from app.modules.capabilities import merge_pull_request, open_pull_request
from app.modules.github_app import FakeGitHubApp
from app.modules.model_router import FakeModelClient
from app.orchestrator import _HELP, Orchestrator
from app.worker.harness import FakeHarness

ROOT = Path(__file__).resolve().parents[1]


# ── the capabilities ─────────────────────────────────────────────────────────
async def test_open_pull_request_creates_pr():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        if p == "/repos/devonpveller/murder":
            return httpx.Response(200, json={"default_branch": "main"})
        if p.endswith("/pulls") and request.method == "POST":
            seen["pr"] = json.loads(request.content)
            return httpx.Response(201, json={"number": 7, "html_url": "https://github.com/devonpveller/murder/pull/7"})
        return httpx.Response(404)

    res = await open_pull_request(
        FakeGitHubApp(owner="devonpveller"), "https://github.com/devonpveller/murder",
        "agent/effort-x", title="agent: x", body="intent…",
        transport=httpx.MockTransport(handler))
    assert res.ok and res.url.endswith("/pull/7") and res.detail == "7"
    assert seen["pr"]["head"] == "agent/effort-x" and seen["pr"]["base"] == "main"


async def test_open_pull_request_existing_is_idempotent():
    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        if p == "/repos/devonpveller/murder":
            return httpx.Response(200, json={"default_branch": "main"})
        if p.endswith("/pulls") and request.method == "POST":
            return httpx.Response(422, json={"message": "A pull request already exists"})
        if p.endswith("/pulls") and request.method == "GET":
            return httpx.Response(200, json=[{"number": 3, "html_url": "https://github.com/devonpveller/murder/pull/3"}])
        return httpx.Response(404)

    res = await open_pull_request(
        FakeGitHubApp(owner="devonpveller"), "https://github.com/devonpveller/murder",
        "agent/effort-x", title="t", body="b", transport=httpx.MockTransport(handler))
    assert res.ok and "already open" in res.summary and res.detail == "3"


async def test_merge_pull_request_success_and_unmergeable():
    def ok_handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "PUT" and request.url.path.endswith("/pulls/7/merge")
        assert json.loads(request.content)["merge_method"] == "merge"   # --no-ff equivalent
        return httpx.Response(200, json={"merged": True})

    res = await merge_pull_request(FakeGitHubApp(owner="devonpveller"),
                                   "https://github.com/devonpveller/murder", 7,
                                   transport=httpx.MockTransport(ok_handler))
    assert res.ok and "merged" in res.summary

    res2 = await merge_pull_request(FakeGitHubApp(owner="devonpveller"),
                                    "https://github.com/devonpveller/murder", 8,
                                    transport=httpx.MockTransport(lambda r: httpx.Response(405, json={})))
    assert not res2.ok and "isn't mergeable" in res2.summary


# ── the orchestrator flow ────────────────────────────────────────────────────
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


def _delivery_handler(merged: dict):
    """Remote where the branch verifies as landed AND PR + merge endpoints work; compare carries
    commits + files so the PR body can be DESCRIPTIVE."""
    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        if "/branches/" in p:
            return httpx.Response(200, json={"commit": {"sha": "abc123def4567890"}})
        if "/compare/" in p:
            return httpx.Response(200, json={
                "ahead_by": 1,
                "commits": [{"commit": {"message": "Replace NuGet ref with vendored project reference\n\ndetails"}}],
                "files": [{"filename": "src/Murder/Murder.csproj", "additions": 1, "deletions": 1}],
            })
        if p.endswith("/pulls") and request.method == "POST":
            merged["pr_body"] = json.loads(request.content).get("body", "")
            return httpx.Response(201, json={"number": 12, "html_url": "https://github.com/devonpveller/Docker-Game/pull/12"})
        if p.endswith("/pulls/12/merge") and request.method == "PUT":
            merged["hit"] = True
            return httpx.Response(200, json={"merged": True})
        if p.count("/") == 3:
            return httpx.Response(200, json={"default_branch": "main"})
        return httpx.Response(404)
    return handler


async def test_verified_done_opens_pr_and_registers_merge_gate(db_url, tmp_path):
    orch, chat, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("game", "https://github.com/devonpveller/Docker-Game")
        eid, chan, root = await orch.router.open_effort("wire", project="game")
        state: dict = {}
        orch._gh_transport = httpx.MockTransport(_delivery_handler(state))
        await orch.delegate(eid, chan, root, "wire the build", plan_steps=["work"])
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "PR opened for review" in msgs and "/pull/12" in msgs
        assert "merge it" in msgs and "only changes when you merge" in msgs   # the model, explained
        assert f"merge-{eid}" in orch._pending_merge                          # D4 gate registered
        assert orch._pending_merge[f"merge-{eid}"]["pr_number"] == 12
        # the PR BODY is descriptive of the delivery (commits + files + verification) — NOT chat
        # coaching (that belongs in Mattermost; the live PR read like a message meant for chat).
        body = state["pr_body"]
        assert "Replace NuGet ref with vendored project reference" in body    # commit subject
        assert "src/Murder/Murder.csproj" in body                             # files touched
        assert "verified on the remote" in body
        assert "Mattermost" not in body and "merge it" not in body            # no chat plumbing
    finally:
        await db.dispose()


async def test_nl_merge_it_with_nothing_pending_answers_deterministically(db_url, tmp_path):
    """'Merge it' with no pending merges must get an honest deterministic answer — not fall through
    to the model (which asked 'what exactly should I merge?' live, right after it had merged)."""
    orch, chat, db = await _orch(db_url, tmp_path)
    try:
        mgmt = await orch.mgmt_channel_id()
        await orch.nl_intake("Merge it", mgmt, thread_id="t")
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "Nothing is awaiting a merge" in msgs and "already merged" in msgs
    finally:
        await db.dispose()


async def test_nl_merge_it_merges_the_single_pending_pr(db_url, tmp_path):
    orch, chat, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("game", "https://github.com/devonpveller/Docker-Game")
        eid, chan, root = await orch.router.open_effort("wire", project="game")
        merged: dict = {}
        orch._gh_transport = httpx.MockTransport(_delivery_handler(merged))
        await orch.delegate(eid, chan, root, "wire", plan_steps=["work"])
        assert f"merge-{eid}" in orch._pending_merge
        mgmt = await orch.mgmt_channel_id()
        await orch.nl_intake("merge it", mgmt, thread_id="t")                 # plain language (D4)
        assert merged.get("hit") is True                                      # the API merge ran
        assert f"merge-{eid}" not in orch._pending_merge                      # consumed
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "merged" in msgs
    finally:
        await db.dispose()


async def test_abort_merge_leaves_pr_open(db_url, tmp_path):
    orch, chat, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("game", "https://github.com/devonpveller/Docker-Game")
        eid, chan, root = await orch.router.open_effort("wire", project="game")
        merged: dict = {}
        orch._gh_transport = httpx.MockTransport(_delivery_handler(merged))
        await orch.delegate(eid, chan, root, "wire", plan_steps=["work"])
        mgmt = await orch.mgmt_channel_id()
        await orch.handle_event({"id": "d1", "channel_id": mgmt,
                                 "message": f"abort merge-{eid}", "is_bot": False, "ts": 2})
        assert merged.get("hit") is None                                      # NOT merged
        assert f"merge-{eid}" not in orch._pending_merge
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "stays open" in msgs
    finally:
        await db.dispose()


def test_help_explains_the_delivery_model():
    """Audit fix: the branch-based delivery model must be EXPLAINED, not implicit."""
    assert "agent/<effort-id>" in _HELP
    assert "never changes until you merge" in _HELP
    assert "merge it" in _HELP


# ── NL PR request = operator-plane capability, NEVER a worker task (live miss) ─
def _pr_repo_handler(state: dict, *, repos_with_branch: set[str]):
    """Mock GitHub: branch exists on `repos_with_branch`; PR + merge endpoints record calls."""
    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        repo = p.split("/repos/devonpveller/")[-1].split("/")[0] if "/repos/" in p else ""
        if "/branches/" in p:
            return (httpx.Response(200, json={"commit": {"sha": "feedbeef1234"}})
                    if repo in repos_with_branch else httpx.Response(404, json={}))
        if "/compare/" in p:
            return httpx.Response(200, json={"ahead_by": 1})
        if p.endswith("/pulls") and request.method == "POST":
            n = state.setdefault("n", 0) + 1
            state["n"] = n
            state.setdefault("prs", []).append(repo)
            return httpx.Response(201, json={"number": n, "html_url": f"https://github.com/devonpveller/{repo}/pull/{n}"})
        if "/merge" in p and request.method == "PUT":
            state.setdefault("merged", []).append(repo)
            return httpx.Response(200, json={"merged": True})
        if p.count("/") == 3:
            return httpx.Response(200, json={"default_branch": "main"})
        return httpx.Response(404)
    return handler


async def test_nl_create_pr_with_premerge_is_operator_plane_not_a_worker(db_url, tmp_path):
    """LIVE regression: 'Create a PR for agent/X into main. if the merge looks clean, proceed with
    merge.' must be handled by the BRIDGE (D1 open + pre-authorized D4 merge) — NOT classified as a
    coding request that dispatches a worker (which can't open PRs) into the sandbox."""
    orch, chat, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("murder", "https://github.com/devonpveller/murder")
        await orch.projects.add("monogame-engine", "https://github.com/devonpveller/MonoGame-Engine")
        state: dict = {}
        orch._gh_transport = httpx.MockTransport(
            _pr_repo_handler(state, repos_with_branch={"murder", "MonoGame-Engine"}))
        mgmt = await orch.mgmt_channel_id()
        await orch.nl_intake(
            "Create a PR for agent/effort-modify-directory-build-p into main. "
            "if the merge looks clean, proceed with merge.", mgmt, thread_id="t")
        assert not orch.harness.wakes                       # NO worker dispatched
        assert sorted(state.get("prs", [])) == ["MonoGame-Engine", "murder"]   # PR per repo w/ branch
        assert sorted(state.get("merged", [])) == ["MonoGame-Engine", "murder"]  # pre-cleared merge ran
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "merged" in msgs and "pre-cleared" in msgs
    finally:
        await db.dispose()


async def test_nl_create_pr_without_merge_registers_gate(db_url, tmp_path):
    orch, chat, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("murder", "https://github.com/devonpveller/murder")
        state: dict = {}
        orch._gh_transport = httpx.MockTransport(_pr_repo_handler(state, repos_with_branch={"murder"}))
        mgmt = await orch.mgmt_channel_id()
        await orch.nl_intake("please open a PR for agent/effort-x", mgmt, thread_id="t")
        assert not orch.harness.wakes
        assert state.get("prs") == ["murder"] and not state.get("merged")     # opened, NOT merged
        assert any(k.startswith("merge-") for k in orch._pending_merge)       # D4 gate registered
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "merge it" in msgs                                             # NL invitation
    finally:
        await db.dispose()


async def test_mgmt_thread_reply_inherits_effort_project(db_url, tmp_path):
    """LIVE regression: a request sent as a reply in an effort's #mgmt conversation must resolve to
    THAT effort's project — not fall to the sandbox."""
    orch, chat, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("monogame-engine", "https://github.com/devonpveller/MonoGame-Engine")
        eid, chan, root = await orch.router.open_effort("wire", project="monogame-engine")
        orch._effort_mgmt_thread[eid] = "mgmt-thread-1"     # the conversation the summary lives in
        slug = await orch._resolve_project_slug(None, None, effort_name="do-a-thing",
                                                thread_id="mgmt-thread-1")
        assert slug == "monogame-engine"                    # context inherited, not sandbox
        assert await orch._resolve_project_slug(None, None, effort_name="do-a-thing") == "sandbox"
    finally:
        await db.dispose()
