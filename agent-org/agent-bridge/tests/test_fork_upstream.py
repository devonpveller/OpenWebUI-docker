"""Fork/upstream onboarding (D0.f). A fork project carries a parent `upstream_url`; the bridge
re-bakes it as a read-only `upstream` remote on every worker focus (the workspace is ephemeral, so
the persistent Project record is the source of truth — surviving wipes + rebuilds). Fakes only."""

from __future__ import annotations

import asyncio
from pathlib import Path

from app.adapters.chat import FakeChatAdapter
from app.config import Settings
from app.db import Database
from app.modules.audit_sink import AuditSink
from app.modules.egress import EgressAllowlist
from app.modules.model_router import FakeModelClient
from app.modules.projects import ProjectRegistry
from app.orchestrator import Orchestrator
from app.schemas import OperatorIntent, ReadinessVerdict
from app.worker.harness import FakeHarness

ROOT = Path(__file__).resolve().parents[1]

FORK = "https://github.com/profnovice/MonoGame.git"
PARENT = "https://github.com/MonoGame/MonoGame.git"


async def _orch(db_url, **over):
    settings = Settings(
        _env_file=None, chat_adapter="fake",
        profiles_dir=str(ROOT / "profiles"), charters_dir=str(ROOT / "charters"),
        floor_dir=str(ROOT / "floor"), worker_instance_urls="http://w1:8090",
        max_concurrent_workers=1, database_url=db_url, project_survey_enabled=False,
        review_mode="off", plan_approval="off", **over,
    )
    db = Database(db_url)
    orch = Orchestrator(settings, db, FakeChatAdapter(),
                        model_client=FakeModelClient(), harness=FakeHarness())
    await orch.setup()
    return orch, orch.chat, orch.harness, db


async def _drain(orch):
    if orch._bg_tasks:
        await asyncio.gather(*orch._bg_tasks)


# ── registry: upstream stored + its host feeds egress ────────────────────────
async def test_registry_stores_upstream_and_host_in_egress(db, settings):
    reg = ProjectRegistry(db, AuditSink(db, settings))
    await reg.add("mono", FORK, upstream_url=PARENT)
    assert await reg.upstream_for("mono") == PARENT
    # both the fork host AND the parent host are in hosts() so egress survives every re-render
    hosts = await reg.hosts()
    assert "github.com" in hosts
    eg = EgressAllowlist(db, AuditSink(db, settings), reg)
    assert await eg.is_allowed(PARENT)          # parent reachable for `git fetch upstream`

    # a non-fork add leaves upstream None
    await reg.add("plain", "https://gitlab.com/x/y.git")
    assert await reg.upstream_for("plain") is None


# ── /project add --upstream: onboards a fork + allows both hosts + lists it ──
async def test_project_add_upstream_command(db_url):
    orch, chat, harness, db = await _orch(db_url)
    try:
        mgmt = await orch.mgmt_channel_id()
        await orch.handle_event(
            {"id": "c1", "channel_id": mgmt,
             "message": f"/project add mono {FORK} --upstream {PARENT}",
             "is_bot": False, "ts": 1})
        p = await orch.projects.resolve("mono")
        assert p["upstream_url"] == PARENT
        assert "github.com" in await orch.egress.hosts()
        assert any("fork of" in msg["message"] for msg in chat.posted)
        # /project list surfaces the upstream
        chat.posted.clear()
        await orch.handle_event(
            {"id": "c2", "channel_id": mgmt, "message": "/project list", "is_bot": False, "ts": 2})
        assert any("upstream" in msg["message"] and "MonoGame" in msg["message"] for msg in chat.posted)
    finally:
        await db.dispose()


async def test_project_add_upstream_flag_anywhere_in_args(db_url):
    # `--upstream` may precede the positionals; `--upstream=<url>` form also works.
    orch, chat, harness, db = await _orch(db_url)
    try:
        mgmt = await orch.mgmt_channel_id()
        await orch.handle_event(
            {"id": "c1", "channel_id": mgmt,
             "message": f"/project add --upstream={PARENT} mono {FORK}",
             "is_bot": False, "ts": 1})
        p = await orch.projects.resolve("mono")
        assert p["repo_url"].endswith("profnovice/MonoGame.git") and p["upstream_url"] == PARENT
    finally:
        await db.dispose()


# ── the upstream is threaded to the worker's clone on dispatch ───────────────
async def test_upstream_threaded_to_worker_focus(db_url):
    orch, chat, harness, db = await _orch(db_url)
    try:
        await orch.projects.add("mono", FORK, upstream_url=PARENT)
        eid, chan, root = await orch.router.open_effort("port-shader", project="mono")
        assert await orch._effort_upstream(eid) == PARENT
        await orch.delegate(eid, chan, root, "port the shader")
        # set_project received the parent → the fork's `upstream` remote gets baked each focus
        assert PARENT in harness.upstreams.values()
        # and it still pushes only to the fork's origin branch (additive publish)
        assert any("git push -u origin agent/effort-port-shader" in w["prompt"] for w in harness.wakes)
    finally:
        await db.dispose()


# ── a PRIVATE parent resolves a read-scoped token by owner convention ────────
async def test_private_upstream_token_by_owner_convention(db_url, monkeypatch):
    monkeypatch.setenv("LC_MONOGAME_TOKEN", "ghp_ro_parent")
    orch, chat, harness, db = await _orch(db_url)
    try:
        # fork under my account, parent under the MonoGame org (a different owner)
        await orch.projects.add("mono", FORK, upstream_url=PARENT)
        eid, _c, _r = await orch.router.open_effort("x", project="mono")
        # the parent's read token comes from LC_<PARENT_OWNER>_TOKEN, NOT the fork's push token
        assert await orch._project_upstream_token(eid) == "ghp_ro_parent"
    finally:
        await db.dispose()


async def test_clone_failure_reads_as_clone_not_worker_failure(db_url):
    """A clone/set_project failure (private/missing repo the token can't reach) must surface as a
    clear CLONE problem — not a phantom 'worker ended error'. Regression for the stale-token case
    where every effort looked like a fast worker response with no dialogue."""
    orch, chat, harness, db = await _orch(db_url)
    harness.set_project_fails = "clone failed (exit 128): "     # GitHub auth-rejected private repo
    try:
        await orch.projects.add("priv", "https://github.com/me/private-repo.git")
        eid, chan, root = await orch.router.open_effort("do-x", project="priv")
        await orch.delegate(eid, chan, root, "do x")
        msgs = [p["message"] for p in chat.posted]
        assert any("clone" in m and "private or missing" in m for m in msgs)   # clear clone-auth guidance
        assert any("No worker was dispatched" in m for m in msgs)              # named as a clone problem
        assert not any("worker ended" in m for m in msgs)                      # NOT a phantom worker failure
    finally:
        await db.dispose()


async def test_non_fork_threads_no_upstream(db_url):
    orch, chat, harness, db = await _orch(db_url)
    try:
        await orch.projects.add("plain", "https://github.com/acme/app.git")
        eid, chan, root = await orch.router.open_effort("feat", project="plain")
        assert await orch._effort_upstream(eid) is None
        await orch.delegate(eid, chan, root, "do it")
        assert harness.upstreams == {}          # nothing baked for a non-fork
    finally:
        await db.dispose()


# ── NL onboarding of a fork (the operator describes it in plain language) ────
async def test_nl_onboards_fork_with_upstream(db_url):
    orch, chat, harness, db = await _orch(db_url)
    try:
        mgmt = await orch.mgmt_channel_id()
        orch.models._client.queue_structured(OperatorIntent(
            kind="chitchat", repo_url=FORK, upstream_url=PARENT, project="mono",
            reply="Setting up your fork."))
        await orch.handle_event(
            {"id": "n1", "channel_id": mgmt,
             "message": f"start a project on my fork {FORK}, upstream is {PARENT}",
             "is_bot": False, "ts": 1})
        p = await orch.projects.resolve("mono")
        assert p is not None and p["upstream_url"] == PARENT
        assert await orch.egress.is_allowed(PARENT)          # parent host allowlisted
        assert any("fork of" in msg["message"] for msg in chat.posted)
    finally:
        await db.dispose()
