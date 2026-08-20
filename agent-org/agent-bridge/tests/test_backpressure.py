"""Inference backpressure resilience. The shared single-GPU llm-queue sheds requests (429/503)
when a batch job saturates it; a bridge model call retries with backoff, and if still shed the PO
says so HONESTLY ('model's busy, one moment') instead of the misleading 'couldn't parse'. Fakes
only — the fake client raises queued exceptions to simulate the shed."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.adapters.chat import FakeChatAdapter
from app.config import Settings
from app.db import Database
from app.modules.model_router import ModelBackpressureError
from app.modules.model_router import FakeModelClient
from app.orchestrator import Orchestrator
from app.schemas import OperatorIntent
from app.worker.harness import FakeHarness

ROOT = Path(__file__).resolve().parents[1]

# The queue's real shed error shape — `_is_backpressure` must classify it as backpressure.
SHED = "Error code: 503 - llm-queue at hard connection cap (128); shedding load. (queue_connections_exhausted)"


async def _orch(db_url):
    settings = Settings(
        _env_file=None, chat_adapter="fake",
        profiles_dir=str(ROOT / "profiles"), charters_dir=str(ROOT / "charters"),
        floor_dir=str(ROOT / "floor"), worker_instance_urls="http://w1:8090",
        max_concurrent_workers=1, database_url=db_url, project_survey_enabled=False,
        review_mode="off", plan_approval="off",
        model_backpressure_retries=3, model_backpressure_base_delay_s=0.0,
        model_backpressure_max_delay_s=0.0,
    )
    db = Database(db_url)
    orch = Orchestrator(settings, db, FakeChatAdapter(),
                        model_client=FakeModelClient(), harness=FakeHarness())
    await orch.setup()
    return orch, orch.chat, db


# ── model router: retries on shed, raises the typed error once exhausted ─────
async def test_router_retries_then_succeeds(db_url):
    orch, chat, db = await _orch(db_url)
    try:
        # 2 sheds (< 3 retries) then a valid response → the call should ultimately succeed.
        orch.models._client.queue_raise(Exception(SHED), times=2)
        orch.models._client.queue_structured(OperatorIntent(kind="chitchat", reply="hi"))
        out = await orch.models.structured("po", "sys", "user", OperatorIntent)
        assert out.reply == "hi"
    finally:
        await db.dispose()


async def test_router_raises_backpressure_after_exhausting_retries(db_url):
    orch, chat, db = await _orch(db_url)
    try:
        orch.models._client.queue_raise(Exception(SHED), times=10)  # never clears
        with pytest.raises(ModelBackpressureError):
            await orch.models.structured("po", "sys", "user", OperatorIntent)
    finally:
        await db.dispose()


async def test_non_backpressure_error_is_not_retried_or_masked(db_url):
    orch, chat, db = await _orch(db_url)
    try:
        # A genuine error (not 429/503) must propagate as-is, not become ModelBackpressureError.
        orch.models._client.queue_raise(ValueError("bad schema"), times=1)
        with pytest.raises(ValueError):
            await orch.models.structured("po", "sys", "user", OperatorIntent)
    finally:
        await db.dispose()


# ── nl_intake: honest 'saturated' message on shed, NOT 'couldn't parse' ──────
async def test_nl_intake_backpressure_gives_honest_message(db_url):
    orch, chat, db = await _orch(db_url)
    try:
        orch.models._client.queue_raise(Exception(SHED), times=10)  # stays saturated
        mgmt = await orch.mgmt_channel_id()
        await orch.nl_intake("clone MonoGame into this repo maintaining upstream", mgmt,
                             user_id="op", thread_id="t1")
        msgs = [p["message"] for p in chat.posted]
        assert any("saturated" in m and "didn't lose your message" in m for m in msgs)
        assert not any("couldn't parse" in m for m in msgs)   # the misleading message is gone
    finally:
        await db.dispose()


async def test_nl_intake_recovers_after_transient_shed(db_url):
    orch, chat, db = await _orch(db_url)
    try:
        # one shed then a real classification → the PO answers normally, no 'saturated' message
        orch.models._client.queue_raise(Exception(SHED), times=1)
        orch.models._client.queue_structured(
            OperatorIntent(kind="chitchat", reply="Here's how I'd approach that."))
        mgmt = await orch.mgmt_channel_id()
        await orch.nl_intake("how would you set up the fork?", mgmt, user_id="op", thread_id="t1")
        msgs = [p["message"] for p in chat.posted]
        assert any("how I'd approach" in m for m in msgs)
        assert not any("saturated" in m for m in msgs)
    finally:
        await db.dispose()
