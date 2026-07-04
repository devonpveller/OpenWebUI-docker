"""Tier-2 advisor: a design/architecture question the operator wants DISCUSSED gets a research-
grounded, CITED answer in-thread (not a one-shot local guess). When research is unavailable it
degrades to a clearly-labelled UNGROUNDED local answer — honest, never a silent uncited guess. The
whole flow is NL-first (model → OperatorIntent(kind=advisory) → governed handler). Fakes only."""

from __future__ import annotations

import asyncio
from pathlib import Path

from app.adapters.chat import FakeChatAdapter
from app.config import Settings
from app.db import Database
from app.modules.grounding import FakeGrounding
from app.modules.model_router import FakeModelClient
from app.orchestrator import Orchestrator
from app.schemas import AdvisoryAnswer, OperatorIntent
from app.worker.harness import FakeHarness

ROOT = Path(__file__).resolve().parents[1]


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
    return orch, orch.chat, db


async def _drain(orch):
    for _ in range(8):
        if not orch._bg_tasks:
            return
        await asyncio.gather(*list(orch._bg_tasks), return_exceptions=True)


# ── grounded path: research runs, a cited answer lands in-thread ─────────────
async def test_advisory_posts_grounded_cited_answer(db_url):
    orch, chat, db = await _orch(db_url)
    try:
        orch.grounding = FakeGrounding(advice=AdvisoryAnswer(
            grounded=True,
            answer="Model each component as its own fork tracking one upstream; compose via submodules.",
            sources=["Git Submodules — https://git-scm.com/book/en/v2/Git-Tools-Submodules"]))
        orch.models._client.queue_structured(
            OperatorIntent(kind="advisory", reply="Good question — let me research that."))
        mgmt = await orch.mgmt_channel_id()
        await orch.nl_intake(
            "what's the industry standard for composing several upstream repos?", mgmt, thread_id="t")
        await _drain(orch)
        msgs = [p["message"] for p in chat.posted]
        assert any("Researching that" in m for m in msgs)                    # immediate ack
        assert any("compose via submodules" in m for m in msgs)              # grounded answer posted
        assert any("Sources" in m and "git-scm.com" in m for m in msgs)      # citations included
        assert orch.grounding.advice_calls                                   # research WAS called
    finally:
        await db.dispose()


# ── research down → labelled ungrounded local answer, NO fabricated citations ─
async def test_advisory_falls_back_labelled_when_research_unavailable(db_url):
    orch, chat, db = await _orch(db_url)
    try:
        orch.grounding = FakeGrounding(advice=AdvisoryAnswer(grounded=False))   # research unreachable
        orch.models._client.queue_structured(
            OperatorIntent(kind="advisory", reply="Let me look into that."))
        orch.models._client.queue_text("Fork each component, submodule it; use NuGet for packages.")
        mgmt = await orch.mgmt_channel_id()
        await orch.nl_intake(
            "how should I structure multiple engine dependencies?", mgmt, thread_id="t")
        await _drain(orch)
        msgs = [p["message"] for p in chat.posted]
        assert any("unverified" in m.lower() and "Fork each component" in m for m in msgs)
        assert not any("**Sources**" in m for m in msgs)                     # never fabricate citations
    finally:
        await db.dispose()


# ── advisory OFF → the message degrades to a plain conversational reply, no research ─
async def test_advisory_disabled_falls_through_to_reply(db_url):
    orch, chat, db = await _orch(db_url, advisory_enabled=False)
    try:
        orch.grounding = FakeGrounding()
        orch.models._client.queue_structured(
            OperatorIntent(kind="advisory", reply="Here's a quick thought on that."))
        mgmt = await orch.mgmt_channel_id()
        await orch.nl_intake("what's the best way to do X?", mgmt, thread_id="t")
        await _drain(orch)
        msgs = [p["message"] for p in chat.posted]
        assert any("Here's a quick thought" in m for m in msgs)              # posts the reply directly
        assert not any("Researching that" in m for m in msgs)               # no research kicked off
        assert not orch.grounding.advice_calls
    finally:
        await db.dispose()
