"""EXACT reproduction of the second live '…' (2026-07-05 01:41): the model returned an ACTIONABLE-
looking kind (request) but with a junk reply and NO effort_name — the request branch requires
effort_name so it skipped, everything fell through, and nl_intake's tail posted the HARDCODED
`reply or "…"`. Run RED against the pre-fix code as proof, GREEN after."""

from __future__ import annotations

import asyncio
from pathlib import Path

from app.adapters.chat import FakeChatAdapter
from app.config import Settings
from app.db import Database
from app.modules.model_router import FakeModelClient
from app.orchestrator import Orchestrator
from app.schemas import OperatorIntent, ReadinessVerdict
from app.worker.harness import FakeHarness

ROOT = Path(__file__).resolve().parents[1]

LIVE_MESSAGE = ("in murder, investigate the repo's README, docs and templates and answer: what's "
                "the canonical project structure for a game built with murder, and how does a game "
                "project reference the engine? Read-only — change nothing.")


async def _orch(db_url):
    settings = Settings(
        _env_file=None, chat_adapter="fake",
        profiles_dir=str(ROOT / "profiles"), charters_dir=str(ROOT / "charters"),
        floor_dir=str(ROOT / "floor"), worker_instance_urls="http://w1:8090",
        max_concurrent_workers=1, database_url=db_url, project_survey_enabled=False,
        review_mode="off", plan_approval="off",
    )
    db = Database(db_url)
    orch = Orchestrator(settings, db, FakeChatAdapter(),
                        model_client=FakeModelClient(), harness=FakeHarness())
    await orch.setup()
    return orch, orch.chat, db


async def _drain(orch):
    for _ in range(12):
        if not orch._bg_tasks:
            return
        await asyncio.gather(*list(orch._bg_tasks), return_exceptions=True)


async def test_live_repro_request_kind_junk_reply_no_effort_name(db_url):
    """kind=request + reply='…' + effort_name=None (a classification qwen demonstrably produces):
    must NOT post a bare '…' and must NOT drop the scoped work."""
    orch, chat, db = await _orch(db_url)
    try:
        await orch.projects.add("murder", "https://github.com/devonpveller/murder")
        orch.models._client.queue_structured(
            OperatorIntent(kind="request", reply="…", effort_name=None))
        orch.models._client.queue_structured(
            ReadinessVerdict(clear_and_safe=True, blast_radius="routine"))
        mgmt = await orch.mgmt_channel_id()
        await orch.nl_intake(LIVE_MESSAGE, mgmt, thread_id="t")
        await _drain(orch)
        bare = [p["message"] for p in chat.posted if p["message"].strip() in ("…", "...")]
        assert not bare, f"bare ellipsis was posted: {bare}"
        assert len(orch.harness.wakes) >= 1, "the scoped work was silently dropped"
    finally:
        await db.dispose()


async def test_live_repro_clarification_kind_junk_reply(db_url):
    """kind=clarification + junk reply + no effort_id — another fall-through shape; same contract."""
    orch, chat, db = await _orch(db_url)
    try:
        await orch.projects.add("murder", "https://github.com/devonpveller/murder")
        orch.models._client.queue_structured(
            OperatorIntent(kind="clarification", reply="…"))
        orch.models._client.queue_structured(
            ReadinessVerdict(clear_and_safe=True, blast_radius="routine"))
        mgmt = await orch.mgmt_channel_id()
        await orch.nl_intake(LIVE_MESSAGE, mgmt, thread_id="t")
        await _drain(orch)
        bare = [p["message"] for p in chat.posted if p["message"].strip() in ("…", "...")]
        assert not bare, f"bare ellipsis was posted: {bare}"
        assert len(orch.harness.wakes) >= 1, "the scoped work was silently dropped"
    finally:
        await db.dispose()


async def test_tail_never_posts_bare_ellipsis_even_unscoped(db_url):
    """Even with NO scoped project to repair to, a fall-through must end in an honest sentence,
    never the hardcoded '…'."""
    orch, chat, db = await _orch(db_url)
    try:
        # decision kind with no effort/decision fields → falls through every branch
        orch.models._client.queue_structured(OperatorIntent(kind="decision", reply="…"))
        orch.models._client.queue_structured(OperatorIntent(kind="decision", reply="…"))  # retry too
        mgmt = await orch.mgmt_channel_id()
        await orch.nl_intake("hmm okay do it", mgmt, thread_id="t")
        await _drain(orch)
        bare = [p["message"] for p in chat.posted if p["message"].strip() in ("…", "...")]
        assert not bare, f"bare ellipsis was posted: {bare}"
        assert any(len(p["message"].strip()) > 20 for p in chat.posted), "no honest reply was posted"
    finally:
        await db.dispose()
