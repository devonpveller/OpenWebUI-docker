"""PM delivery verification (governance §4.2 / F8; UX-FLOW Stage 5→6). A worker's pi turn ending
`done` is NOT delivery — the PM must INDEPENDENTLY verify the effort's branch actually landed on the
remote (a checkable acceptance signal, read via the GitHub App — the deterministic floor, not the
worker's self-report). On verified non-delivery the PM re-engages ONCE, then escalates; it never
rubber-stamps a `done` that didn't deliver. Fakes + a mocked GitHub; no real network."""

from __future__ import annotations

from pathlib import Path

import httpx

from app.adapters.chat import FakeChatAdapter
from app.config import Settings
from app.db import Database
from app.modules.capabilities import read_branch_delivery
from app.modules.github_app import FakeGitHubApp
from app.modules.model_router import FakeModelClient
from app.orchestrator import Orchestrator
from app.worker.harness import FakeHarness

ROOT = Path(__file__).resolve().parents[1]


# ── read_branch_delivery: the checkable signal (branch exists + ahead of base) ──
def _remote(*, branch_status=200, ahead=1, default_branch="main"):
    """A MockTransport answering repo-meta / branch / compare for one repo."""

    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        if p.endswith("/compare/" + default_branch + "...agent/effort-wire") or "/compare/" in p:
            return httpx.Response(200, json={"ahead_by": ahead, "behind_by": 0})
        if "/branches/" in p:
            if branch_status == 200:
                return httpx.Response(200, json={"name": "agent/effort-wire",
                                                 "commit": {"sha": "abcdef1234567890"}})
            return httpx.Response(branch_status, json={"message": "Not Found"})
        if p.count("/") == 3:  # /repos/{owner}/{repo}
            return httpx.Response(200, json={"default_branch": default_branch})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


async def test_read_branch_delivery_landed():
    d = await read_branch_delivery(
        FakeGitHubApp(owner="devonpveller"),
        "https://github.com/devonpveller/Docker-Game", "agent/effort-wire",
        transport=_remote(branch_status=200, ahead=3))
    assert d.verifiable and d.exists and d.ahead == 3 and d.landed
    assert d.head_sha == "abcdef1234567890"    # FULL sha (needed to bump a submodule pointer)


async def test_read_branch_delivery_missing_branch():
    d = await read_branch_delivery(
        FakeGitHubApp(owner="devonpveller"),
        "https://github.com/devonpveller/Docker-Game", "agent/effort-wire",
        transport=_remote(branch_status=404))
    assert d.verifiable and not d.exists and not d.landed      # verifiably absent


async def test_read_branch_delivery_empty_branch_is_not_landed():
    # branch exists but 0 commits over base — the worker committed NOTHING; not a real delivery.
    d = await read_branch_delivery(
        FakeGitHubApp(owner="devonpveller"),
        "https://github.com/devonpveller/Docker-Game", "agent/effort-wire",
        transport=_remote(branch_status=200, ahead=0))
    assert d.verifiable and d.exists and d.ahead == 0 and not d.landed


async def test_read_branch_delivery_unverifiable_for_other_owner():
    # the App can only read its own account — a repo under a different owner is unverifiable.
    d = await read_branch_delivery(
        FakeGitHubApp(owner="me"), "https://github.com/someoneelse/repo", "agent/effort-wire",
        transport=_remote())
    assert not d.verifiable and not d.landed


# ── the orchestrator loop: publish → verify → re-engage → escalate/finish ──────
async def _orch(db_url, tmp_path):
    key = tmp_path / "app.pem"
    key.write_text("dummy")   # github_app_enabled = id set + key file present (Fake never reads it)
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


async def test_verified_landed_reports_done_with_branch(db_url, tmp_path):
    orch, chat, harness, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("game", "https://github.com/devonpveller/Docker-Game")
        eid, chan, root = await orch.router.open_effort("wire", project="game")
        orch._gh_transport = _remote(branch_status=200, ahead=2)
        await orch.delegate(eid, chan, root, "wire the build", plan_steps=["do the work"])
        # 1 step wake + 1 publish wake
        assert len(harness.wakes) == 2
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "verified on the remote" in msgs and "agent/effort-wire" in msgs
        assert "finished (**done**)" in msgs
        assert await _lifecycle(orch, eid) == "done"
    finally:
        await db.dispose()


async def test_nondelivery_reengages_once_then_escalates(db_url, tmp_path):
    orch, chat, harness, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("game", "https://github.com/devonpveller/Docker-Game")
        eid, chan, root = await orch.router.open_effort("wire", project="game")
        orch._gh_transport = _remote(branch_status=404)          # branch NEVER lands
        await orch.delegate(eid, chan, root, "wire the build", plan_steps=["do the work"])
        # 1 step + publish + ONE firm re-engage publish + the read-only goal STATE CHECK
        # (self-recovery: an already-holds goal closes as no-op; here it answers nothing
        # useful, so the escalation below still fires) = 4 wakes
        assert len(harness.wakes) == 4
        assert any("NOT PUBLISHED" in w["prompt"] for w in harness.wakes)   # firm re-engage went out
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "explicit commit + push" in msgs                  # PM announced the re-engage
        assert "did not land" in msgs or "not verify the change landed" in msgs
        assert "finished (**done**)" not in msgs                 # NOT rubber-stamped done
        assert await _lifecycle(orch, eid) != "done"             # stays visible in /status
    finally:
        await db.dispose()


async def test_nondelivery_then_reengage_lands_finishes_done(db_url, tmp_path):
    orch, chat, harness, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("game", "https://github.com/devonpveller/Docker-Game")
        eid, chan, root = await orch.router.open_effort("wire", project="game")
        # branch missing on the FIRST verify, present on the re-check (the re-engage worked).
        state = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            p = request.url.path
            if "/compare/" in p:
                return httpx.Response(200, json={"ahead_by": 1, "behind_by": 0})
            if "/branches/" in p:
                state["n"] += 1
                if state["n"] == 1:
                    return httpx.Response(404, json={"message": "Not Found"})
                return httpx.Response(200, json={"commit": {"sha": "deadbeefcafe"}})
            if p.count("/") == 3:
                return httpx.Response(200, json={"default_branch": "main"})
            return httpx.Response(404)

        orch._gh_transport = httpx.MockTransport(handler)
        await orch.delegate(eid, chan, root, "wire the build", plan_steps=["do the work"])
        assert len(harness.wakes) == 3                           # step + publish + firm re-engage
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "Re-dispatching" in msgs                          # PM caught it + re-engaged
        assert "verified on the remote" in msgs and "finished (**done**)" in msgs
        assert await _lifecycle(orch, eid) == "done"             # landed on the retry → done
    finally:
        await db.dispose()


async def test_unverifiable_repo_reports_selfreport_labelled_unverified(db_url, tmp_path):
    orch, chat, harness, db = await _orch(db_url, tmp_path)
    try:
        # repo under a DIFFERENT owner — the App can't read it, so the PM can't independently verify.
        await orch.projects.add("ext", "https://github.com/someoneelse/thing")
        eid, chan, root = await orch.router.open_effort("wire", project="ext")
        orch._gh_transport = _remote()   # unused (owner mismatch short-circuits before any HTTP)
        await orch.delegate(eid, chan, root, "wire the build", plan_steps=["do the work"])
        assert len(harness.wakes) == 2                           # no re-engage — nothing to verify
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "could **not" in msgs and "independently verify" in msgs
        assert "finished (**done**)" in msgs                     # done, but honestly labelled
        assert await _lifecycle(orch, eid) == "done"
    finally:
        await db.dispose()


async def test_publish_reauths_origin_with_current_token(db_url, tmp_path):
    """LIVE regression ("expired token in origin"): the publish wake must pass repo + a CURRENT
    project token so the daemon NOOP-refocuses (work preserved) and re-bakes origin's auth — the
    token embedded at clone time is short-lived and dies before a long task's push."""
    orch, chat, harness, db = await _orch(db_url, tmp_path)
    try:
        # repo under the App's account → _project_token returns the App installation token
        await orch.projects.add("game", "https://github.com/devonpveller/Docker-Game")
        eid, chan, root = await orch.router.open_effort("wire", project="game")
        orch._gh_transport = _remote(branch_status=200, ahead=1)
        await orch.delegate(eid, chan, root, "wire the build", plan_steps=["do the work"])
        # set_project ran at least twice: the initial focus AND the publish re-auth — both w/ a token
        assert len(harness.focus_calls) >= 2
        publish_focus = harness.focus_calls[-1]
        assert publish_focus["repo"] == "https://github.com/devonpveller/Docker-Game"
        assert publish_focus["token"] == "ghs_faketoken"     # a CURRENT App token, not none
    finally:
        await db.dispose()


# ── Phase 1: intent-anchored completion (DELIVERY-PIPELINE §1 — PM judges vs the intent) ──
async def test_intent_named_projects_excludes_own_target(db_url, tmp_path):
    orch, chat, harness, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("monogame-engine", "https://github.com/devonpveller/MonoGame-Engine")
        await orch.projects.add("murder", "https://github.com/devonpveller/murder")
        # names both, effort targets murder → only monogame-engine is "named but not targeted"
        named = await orch._intent_named_projects("in monogame-engine, wire murder to build", "murder")
        assert named == ["monogame-engine"]                      # longest-first; own target excluded
        # when the effort targets the named engine, nothing extra is flagged
        assert await orch._intent_named_projects("in monogame-engine, wire it", "monogame-engine") == []
    finally:
        await db.dispose()


async def test_scope_mismatch_flags_partly_done_not_done(db_url, tmp_path):
    """The effort's branch landed on `murder`, but the operator also named `monogame-engine` — which
    this effort didn't touch. Completion must be a SCOPE-FLAGGED 'partly done' (stays in /status), not
    a clean 'done' that hides the untouched stated target."""
    orch, chat, harness, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("murder", "https://github.com/devonpveller/murder")
        eid, chan, root = await orch.router.open_effort("wire", project="murder")
        orch._gh_transport = _remote(branch_status=200, ahead=2)      # the murder branch DID land
        orch._effort_intent_scope[eid] = ["monogame-engine"]          # operator named the engine too
        await orch.delegate(eid, chan, root, "wire murder in monogame-engine", plan_steps=["work"])
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "Scope check" in msgs and "monogame-engine" in msgs and "not done" in msgs
        assert "partly done" in msgs and "finished (**done**)" not in msgs
        assert await _lifecycle(orch, eid) != "done"                  # stays visible — intent unmet
    finally:
        await db.dispose()
