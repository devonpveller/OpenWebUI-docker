"""P3.8 Stage-1 anchor — cached read-only project survey feeds the readiness gate so it reasons
from the actual codebase instead of guessing. Fakes only (no repo clone / GPU)."""

from __future__ import annotations

import asyncio
from pathlib import Path

from app.adapters.chat import FakeChatAdapter
from app.config import Settings
from app.db import Database
from app.modules.audit_sink import AuditSink
from app.modules.governance_gate import GovernanceGate
from app.modules.model_router import FakeModelClient
from app.modules.project_context import ProjectContext
from app.modules.router import Router
from app.modules.scheduler import Scheduler
from app.orchestrator import Orchestrator
from app.schemas import OperatorIntent, ReadinessVerdict
from app.worker.harness import FakeHarness

ROOT = Path(__file__).resolve().parents[1]


# ── ProjectContext cache ─────────────────────────────────────────────────────
async def test_project_context_caches_survey():
    calls: list[str] = []

    async def survey(repo: str) -> str:
        calls.append(repo)
        return f"summary::{repo}"

    pc = ProjectContext(survey, enabled=True)
    a = await pc.ensure("proj", "git://x")
    b = await pc.ensure("proj", "git://x")
    assert a == "summary::git://x" and b == a
    assert calls == ["git://x"]  # surveyed once, then cached


async def test_project_context_skips_without_repo_or_disabled():
    async def boom(repo: str) -> str:  # must not be called
        raise AssertionError("survey should not run")

    assert await ProjectContext(boom, enabled=True).ensure("p", "") == ""      # no repo
    assert await ProjectContext(boom, enabled=False).ensure("p", "git://x") == ""  # disabled


async def test_project_context_caches_failed_survey():
    calls: list[str] = []

    async def boom(repo: str) -> str:
        calls.append(repo)
        raise RuntimeError("no worker")

    pc = ProjectContext(boom, enabled=True)
    assert await pc.ensure("proj", "git://x") == ""
    assert await pc.ensure("proj", "git://x") == ""   # not retried every request
    assert len(calls) == 1


# ── P8 #5: the survey cache is keyed by the BASE COMMIT ─────────────────────
async def test_survey_cache_keyed_by_base_commit():
    """Same base ⇒ one survey shared across efforts; base moved ⇒ re-survey once; a caller that
    states no base reuses the current map. (2026-07-16: fresh-wiped workspaces made "clean" mean
    "blind" — 26 read-only calls to re-discover a tiny template; the map is the fix, and it must
    track the base or it becomes the stale-context poison it replaces.)"""
    calls: list[str] = []

    async def survey(repo: str) -> str:
        calls.append(repo)
        return f"map-{len(calls)}"

    pc = ProjectContext(survey, enabled=True)
    a = await pc.ensure("proj", "git://x", base_sha="base-1")     # effort 1: fresh clone
    b = await pc.ensure("proj", "git://x", base_sha="base-1")     # effort 2: same base
    assert a == b == "map-1" and len(calls) == 1                  # ONE survey, shared
    c = await pc.ensure("proj", "git://x", base_sha="base-2")     # the base MOVED
    assert c == "map-2" and len(calls) == 2                       # re-surveyed once
    d = await pc.ensure("proj", "git://x")                        # baseless caller → current map
    assert d == "map-2" and len(calls) == 2


async def test_survey_taken_without_a_base_is_reused():
    """A map surveyed before any base was known (the pre-P8 shape) keeps serving — a stated base
    must not force a pointless re-survey when we have no base to compare against."""
    calls: list[str] = []

    async def survey(repo: str) -> str:
        calls.append(repo)
        return "the-map"

    pc = ProjectContext(survey, enabled=True)
    assert await pc.ensure("proj", "git://x") == "the-map"                 # no base recorded
    assert await pc.ensure("proj", "git://x", base_sha="b1") == "the-map"  # reused, not re-run
    assert len(calls) == 1
    assert pc.get("proj") == "the-map"


# ── router.survey_project ────────────────────────────────────────────────────
async def _router(db, settings):
    audit = AuditSink(db, settings)
    gate = GovernanceGate(db, audit)
    sched = Scheduler(db, gate, audit, 1)
    await sched.register("w1", "http://w1:8090")
    harness = FakeHarness()
    return Router(db, settings, gate, sched, harness, FakeChatAdapter(), audit), harness


async def test_router_survey_focuses_repo_and_returns_summary(db, settings):
    r, harness = await _router(db, settings)
    out = await r.survey_project("git://repo")
    assert out == "ok"                                        # FakeHarness worker output
    assert harness.projects["http://w1:8090"] == "git://repo"  # repo focused before survey
    assert harness.wakes and harness.wakes[0]["session_id"] == "survey-git-repo"


async def test_router_survey_empty_repo_is_noop(db, settings):
    r, harness = await _router(db, settings)
    assert await r.survey_project("") == ""
    assert not harness.wakes


# ── end-to-end: the readiness gate receives the project summary ──────────────
async def test_readiness_gate_anchored_to_project_summary(db_url):
    settings = Settings(
        _env_file=None, chat_adapter="fake",
        profiles_dir=str(ROOT / "profiles"), charters_dir=str(ROOT / "charters"),
        floor_dir=str(ROOT / "floor"), worker_instance_urls="http://w1:8090",
        max_concurrent_workers=1, database_url=db_url, default_repo="git://acme",
    )
    db = Database(db_url)
    orch = Orchestrator(settings, db, FakeChatAdapter(),
                        model_client=FakeModelClient(), harness=FakeHarness())
    await orch.setup()
    # inject a fake survey so the anchor is deterministic (no real clone).
    orch.project_context = ProjectContext(
        lambda repo: _canned("Python/FastAPI project; utilities live in app/utils; pytest"),
        enabled=True,
    )
    try:
        mgmt = await orch.mgmt_channel_id()
        orch.models._client.queue_structured(
            OperatorIntent(kind="request", effort_name="thing", reply="ok"))
        orch.models._client.queue_structured(
            ReadinessVerdict(clear_and_safe=True, blast_radius="routine"))
        await orch.handle_event(
            {"id": "a1", "channel_id": mgmt, "message": "add a util", "is_bot": False, "ts": 1})
        # the readiness (planner) call carried the surveyed project summary in its WORKSPACE block.
        planner_calls = [c for c in orch.models._client.calls if "WORKSPACE" in (c.get("user") or "")]
        assert planner_calls, "readiness gate was not called with a workspace block"
        assert "utilities live in app/utils" in planner_calls[0]["user"]
        if orch._bg_tasks:
            await asyncio.gather(*orch._bg_tasks)
    finally:
        await db.dispose()


async def _canned(text: str) -> str:
    return text
