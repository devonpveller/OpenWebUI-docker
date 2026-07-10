"""Blocker / feasibility elevation (operator 2026-07-07): the worker plainly said "the standalone
build fails because ../MonoGame isn't present in this workspace" and the PM ignored every word and
barked "no branch landed, commit + push". A real PM HEARS a stated constraint and elevates it with
an actionable next step, keeping the effort open — never steamrolls it. And the WORKSPACE must be
made sufficient: a composition fix runs in the HOST context where the build can actually run."""

from __future__ import annotations

import base64
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

LIVE_REPORT = (
    "All three fixes are in place and pushed.\n"
    "Note: The standalone build fails because ../MonoGame/ (the sibling submodule in "
    "monogame-engine) isn't present in this workspace — this is expected per the composition "
    "context. The code-level fixes were verified against the vendored MonoGame fork source via "
    "GitHub API inspection.")


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
    return orch, orch.chat, orch.harness, db


def _stack_remote(bumped: dict, *, sub_landed=True):
    gitmodules = base64.b64encode(
        b'[submodule "vendor/murder"]\n\tpath = vendor/murder\n'
        b'\turl = https://github.com/devonpveller/murder\n').decode()

    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        if p.endswith("/contents/.gitmodules"):
            return httpx.Response(200, json={"content": gitmodules})
        if p.endswith("/contents"):
            return httpx.Response(200, json=[{"name": "vendor", "type": "dir"}])
        if "/compare/" in p:
            return httpx.Response(200, json={
                "ahead_by": 1, "behind_by": 0, "commits": [],
                "files": [{"filename": "src/Murder/Game.cs", "additions": 2, "deletions": 2}]})
        if "/contents/src/Murder/Game.cs" in p:
            return httpx.Response(200, json={"type": "file", "sha": "aa"})
        if "/branches/" in p:
            if not sub_landed and "/murder/" in p:
                return httpx.Response(404, json={"message": "Not Found"})
            bumped["reads"] = bumped.get("reads", 0) + 1
            sha = "prehead000000" if bumped["reads"] == 1 else "newhead1234567890"
            return httpx.Response(200, json={"commit": {"sha": sha}})
        if p.endswith("/git/ref/heads/main"):
            return httpx.Response(200, json={"object": {"sha": "eng_base"}})
        if p.endswith("/git/commits/eng_base"):
            return httpx.Response(200, json={"tree": {"sha": "eng_tree"}})
        if p.endswith("/git/trees") and request.method == "POST":
            bumped["tree"] = json.loads(request.content)
            return httpx.Response(201, json={"sha": "eng_newtree"})
        if p.endswith("/git/commits") and request.method == "POST":
            return httpx.Response(201, json={"sha": "eng_newcommit"})
        if p.endswith("/git/refs") and request.method == "POST":
            return httpx.Response(201, json={})
        if p.endswith("/pulls") and request.method == "POST":
            return httpx.Response(201, json={"number": 9, "html_url": "https://x/pull/9"})
        if p.endswith("/pulls") and request.method == "GET":
            return httpx.Response(200, json=[])
        if p.count("/") == 3:
            return httpx.Response(200, json={"default_branch": "main"})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


async def _drain(orch):
    import asyncio
    for _ in range(20):
        if not orch._bg_tasks:
            return
        await asyncio.gather(*list(orch._bg_tasks), return_exceptions=True)


async def test_worker_stated_blocker_is_elevated_not_steamrolled(db_url, tmp_path):
    """THE live case: the worker names the workspace constraint; the PM must elevate it (open,
    actionable) instead of the mechanical 'no branch, commit + push'."""
    orch, chat, harness, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("monogame-engine", "https://github.com/devonpveller/Engine")
        await orch.projects.add("murder", "https://github.com/devonpveller/murder")
        orch._gh_transport = _stack_remote({})
        eid, chan, root = await orch.router.open_effort("fix-vendored", project="murder")
        await orch.charters.set_goal(eid, "make it build against vendored MonoGame", created_by="po")
        harness.output_queue = ["did work", LIVE_REPORT]
        await orch.delegate(eid, chan, root, "make it build", plan_steps=["work"])
        await _drain(orch)
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "raised a real CONSTRAINT" in msgs, "the blocker was not elevated"
        assert "not a worker failure" in msgs.lower() or "not** a worker failure" in msgs
        assert "host context" in msgs                      # the actionable remedy is named
        assert "commit + push" not in msgs and "explicit commit" not in msgs, \
            "the PM steamrolled instead of hearing the worker"
        from app.models import Effort
        async with orch.db.session_factory() as s:
            e = await s.get(Effort, eid)
        assert e.lifecycle != "done"                        # open, needs-attention
    finally:
        await db.dispose()


async def test_explicit_blocked_protocol_elevated_with_needs_and_feasible(db_url, tmp_path):
    orch, chat, harness, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("murder", "https://github.com/devonpveller/murder")
        orch._gh_transport = _stack_remote({})
        eid, chan, root = await orch.router.open_effort("proto", project="murder")
        await orch.charters.set_goal(eid, "do the thing", created_by="po")
        harness.output_queue = [
            "tried",
            "BLOCKED: the API differs and I lack the target SDK version\n"
            "NEEDS: the MonoGame 3.8.2 reference or the host engine repo\n"
            "FEASIBLE: unknown-because-I-cannot-see-the-target-API"]
        await orch.delegate(eid, chan, root, "do the thing", plan_steps=["work"])
        await _drain(orch)
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "What it needs" in msgs and "MonoGame 3.8.2" in msgs
        assert "Feasible as scoped" in msgs and "unknown" in msgs
    finally:
        await db.dispose()


async def test_run_in_host_context_dispatches_recursive_host_focus(db_url, tmp_path):
    """The workspace-sufficiency remedy: the work is re-run focused on the HOST, recursively, so
    the build can actually run — worker edits the vendored subdir in place and pushes its branch."""
    orch, chat, harness, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("monogame-engine", "https://github.com/devonpveller/Engine")
        await orch.projects.set_check("monogame-engine", "dotnet build vendor/murder/Murder.sln")
        await orch.projects.add("murder", "https://github.com/devonpveller/murder")
        bumped: dict = {}
        orch._gh_transport = _stack_remote(bumped)
        eid, chan, root = await orch.router.open_effort("hostwork", project="murder")
        await orch.charters.set_goal(eid, "fix the vendored build", created_by="po")
        harness.output_queue = ["fixed in vendor/murder, dotnet build succeeded 0 errors, pushed"]
        await orch._run_in_host_context(eid)
        await _drain(orch)
        # the focus was the HOST, recursively cloned
        focus = harness.focus_calls[-1]
        assert focus["repo"].startswith("https://github.com/devonpveller/Engine")
        assert focus["recurse_submodules"] is True
        prompts = " ".join(w["prompt"] for w in harness.wakes)
        assert "git push origin" in prompts and "vendor/murder" in prompts
        assert "git push origin" in prompts                 # publishes from the submodule
        # the murder branch verified → engine gitlink bumped (composition wired)
        assert bumped.get("tree"), "the host gitlink was not bumped after host-context work"
    finally:
        await db.dispose()


async def test_run_in_host_context_elevates_if_still_blocked(db_url, tmp_path):
    orch, chat, harness, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("monogame-engine", "https://github.com/devonpveller/Engine")
        await orch.projects.add("murder", "https://github.com/devonpveller/murder")
        orch._gh_transport = _stack_remote({})
        eid, chan, root = await orch.router.open_effort("stillblocked", project="murder")
        await orch.charters.set_goal(eid, "fix it", created_by="po")
        harness.output_queue = ["BLOCKED: even here the target API version is wrong\nNEEDS: SDK X\nFEASIBLE: no"]
        await orch._run_in_host_context(eid)
        await _drain(orch)
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "raised a real CONSTRAINT" in msgs           # honest escalation, even in host ctx
    finally:
        await db.dispose()
