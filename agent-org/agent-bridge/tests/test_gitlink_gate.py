"""DELIVERY-PIPELINE gitlink-reachability gate (live 2026-07-05): a worker committed inside its
vendored submodule checkout (`vendor/MonoGame`), bumped the superproject pointer, and published
ONLY the superproject branch — the branch then referenced submodule commit `ac3a830b…` that never
reached `devonpveller/MonoGame`, so the operator's `git submodule update --init --recursive` died
with `fatal: remote error: upload-pack: not our ref`. Delivery verification said "landed" (branch
exists + ahead) and invited a merge of a branch NO ONE ELSE CAN BUILD. The gate: for every gitlink
the branch CHANGED, verify the referenced commit exists on the submodule's remote; if not,
re-engage the affine worker ONCE with the exact per-path remedy, then escalate — never a false
'done'. Run RED against pre-fix code as proof, GREEN after."""

from __future__ import annotations

from pathlib import Path

import httpx

from app.adapters.chat import FakeChatAdapter
from app.config import Settings
from app.db import Database
from app.modules.capabilities import read_broken_gitlinks
from app.modules.github_app import FakeGitHubApp
from app.modules.model_router import FakeModelClient
from app.orchestrator import Orchestrator
from app.worker.harness import FakeHarness

ROOT = Path(__file__).resolve().parents[1]

BAD_SHA = "ac3a830b185acb1d0bc486cc002485df3a367abe"
GOOD_SHA = "c591fdd8238c991e818427ffdb9999dd419296a9"


def _engine_remote(*, gitlink_sha=BAD_SHA, sub_commit_status=None, heal_after=None):
    """A MockTransport for `devonpveller/Engine` whose branch changed the `vendor/Sub` gitlink to
    `gitlink_sha`, plus the `devonpveller/Sub` remote answering commit-reachability probes.
    `sub_commit_status`: fixed status for GET Sub/commits/<sha> (defaults: 422 for BAD_SHA, 200
    for GOOD_SHA). `heal_after`: after N probes, the commit becomes reachable (the re-engage
    pushed it)."""
    state = {"probes": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        if "/repos/devonpveller/Sub/commits/" in p:
            state["probes"] += 1
            if heal_after is not None and state["probes"] > heal_after:
                return httpx.Response(200, json={"sha": p.rsplit("/", 1)[-1]})
            if sub_commit_status is not None:
                return httpx.Response(sub_commit_status, json={"message": "?"})
            if p.endswith(BAD_SHA):
                return httpx.Response(422, json={"message": f"No commit found for SHA: {BAD_SHA}"})
            return httpx.Response(200, json={"sha": p.rsplit("/", 1)[-1]})
        if "/contents/vendor/Sub" in p:
            return httpx.Response(200, json={
                "type": "submodule", "name": "Sub", "path": "vendor/Sub",
                "sha": gitlink_sha,
                "submodule_git_url": "https://github.com/devonpveller/Sub",
            })
        if "/contents/src/game.cs" in p:
            return httpx.Response(200, json={"type": "file", "name": "game.cs", "sha": "aa11"})
        if "/compare/" in p:
            return httpx.Response(200, json={
                "ahead_by": 1, "behind_by": 0,
                "commits": [{"commit": {"message": "bump vendor/Sub"}}],
                "files": [{"filename": "vendor/Sub", "status": "modified"},
                          {"filename": "src/game.cs", "status": "modified"}],
            })
        if "/branches/" in p:
            return httpx.Response(200, json={"commit": {"sha": "feedbead12345678"}})
        if p.count("/") == 3:   # /repos/{owner}/{repo}
            return httpx.Response(200, json={"default_branch": "main"})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


# ── unit: read_broken_gitlinks ─────────────────────────────────────────────────
async def test_read_broken_gitlinks_flags_unreachable_pointer():
    broken = await read_broken_gitlinks(
        FakeGitHubApp(owner="devonpveller"),
        "https://github.com/devonpveller/Engine", "agent/effort-fix",
        transport=_engine_remote(gitlink_sha=BAD_SHA))
    assert broken == [{"path": "vendor/Sub", "sha": BAD_SHA, "submodule_repo": "devonpveller/Sub"}]


async def test_read_broken_gitlinks_clean_when_pointer_published():
    broken = await read_broken_gitlinks(
        FakeGitHubApp(owner="devonpveller"),
        "https://github.com/devonpveller/Engine", "agent/effort-fix",
        transport=_engine_remote(gitlink_sha=GOOD_SHA))
    assert broken == []


async def test_read_broken_gitlinks_fails_open_on_infra_errors():
    # a 500 from the submodule remote is NOT proof of a broken pointer — never block on infra
    broken = await read_broken_gitlinks(
        FakeGitHubApp(owner="devonpveller"),
        "https://github.com/devonpveller/Engine", "agent/effort-fix",
        transport=_engine_remote(gitlink_sha=BAD_SHA, sub_commit_status=500))
    assert broken == []


# ── the orchestrator loop: landed branch + broken gitlink → re-engage → escalate ──
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


async def _lifecycle(orch, effort_id):
    from app.models import Effort
    async with orch.db.session_factory() as s:
        e = await s.get(Effort, effort_id)
        return e.lifecycle if e else None


async def test_broken_gitlink_reengages_once_then_escalates(db_url, tmp_path):
    """Branch verifiably landed BUT points vendor/Sub at an unpushed commit forever → the PM must
    re-engage once with the per-path remedy and then escalate — not mark done, not invite a merge."""
    orch, chat, harness, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("engine", "https://github.com/devonpveller/Engine")
        eid, chan, root = await orch.router.open_effort("fix", project="engine")
        orch._gh_transport = _engine_remote(gitlink_sha=BAD_SHA)     # never heals
        await orch.delegate(eid, chan, root, "fix the build", plan_steps=["do the work"])
        prompts = " ".join(w["prompt"] for w in harness.wakes)
        assert "not our ref" in prompts or "do NOT exist on the submodule" in prompts, \
            "the worker was never told its published branch references an unpushed submodule commit"
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "vendor/Sub" in msgs and BAD_SHA[:10] in msgs         # precise, checkable escalation
        assert "finished (**done**)" not in msgs                     # never a false done
        assert await _lifecycle(orch, eid) != "done"
    finally:
        await db.dispose()


async def test_broken_gitlink_fixed_by_reengage_finishes_done(db_url, tmp_path):
    """The re-engaged worker publishes the submodule commit → the re-check passes → done."""
    orch, chat, harness, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("engine", "https://github.com/devonpveller/Engine")
        eid, chan, root = await orch.router.open_effort("fix", project="engine")
        # first reachability probe fails (unpushed), probes after the re-engage succeed
        orch._gh_transport = _engine_remote(gitlink_sha=BAD_SHA, heal_after=1)
        await orch.delegate(eid, chan, root, "fix the build", plan_steps=["do the work"])
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "finished (**done**)" in msgs
        assert await _lifecycle(orch, eid) == "done"
    finally:
        await db.dispose()


async def test_healthy_gitlink_change_is_not_blocked(db_url, tmp_path):
    """A submodule bump to a PUBLISHED commit sails through — the gate only bites on unreachable."""
    orch, chat, harness, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("engine", "https://github.com/devonpveller/Engine")
        eid, chan, root = await orch.router.open_effort("fix", project="engine")
        orch._gh_transport = _engine_remote(gitlink_sha=GOOD_SHA)
        await orch.delegate(eid, chan, root, "fix the build", plan_steps=["do the work"])
        assert len(harness.wakes) == 2                               # step + publish; NO re-engage
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "finished (**done**)" in msgs
        assert await _lifecycle(orch, eid) == "done"
    finally:
        await db.dispose()
