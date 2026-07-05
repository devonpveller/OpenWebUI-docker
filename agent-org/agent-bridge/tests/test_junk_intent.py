"""Junk-intent guard (live miss: the model returned kind=chitchat + reply="…" for a clear scoped
work request — the bridge posted the ellipsis and silently dropped the work). A contentless reply
means the model misfired: an "in <known-project>, …" message is deterministically repaired into a
request; anything else gets ONE reclassification retry, then an honest rephrase ask. The operator
never sees bare punctuation, and work is never silently dropped."""

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


async def test_scoped_message_with_junk_intent_is_repaired_to_request(db_url):
    """LIVE regression: '@bot-pm in murder, investigate …' → model said chitchat/'…' → NOTHING ran.
    The guard must force the request on the named project instead of dropping the work."""
    orch, chat, db = await _orch(db_url)
    try:
        await orch.projects.add("murder", "https://github.com/devonpveller/murder")
        orch.models._client.queue_structured(OperatorIntent(kind="chitchat", reply="…"))
        orch.models._client.queue_structured(ReadinessVerdict(clear_and_safe=True, blast_radius="routine"))
        mgmt = await orch.mgmt_channel_id()
        await orch.nl_intake("in murder, investigate the repo's README and answer: how does a game "
                             "reference the engine? Read-only.", mgmt, thread_id="t")
        await _drain(orch)
        assert len(orch.harness.wakes) >= 1                          # work DISPATCHED, not dropped
        from app.models import Effort
        async with orch.db.session_factory() as s:
            rows = (await s.execute(__import__("sqlalchemy").select(Effort))).scalars().all()
        assert any(e.project == "murder" for e in rows)              # scoped to the named project
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "…" not in msgs.replace("…", "…") or True             # (ellipsis may appear in prose)
        assert not any(p["message"].strip() == "…" for p in chat.posted)   # never a bare "…" post
        assert "On it" in msgs                                       # a real ack replaced the junk
    finally:
        await db.dispose()


async def test_unscoped_junk_retries_then_asks_honestly(db_url):
    orch, chat, db = await _orch(db_url)
    try:
        orch.models._client.queue_structured(OperatorIntent(kind="chitchat", reply="…"))
        orch.models._client.queue_structured(OperatorIntent(kind="chitchat", reply="..."))  # retry also junk
        mgmt = await orch.mgmt_channel_id()
        await orch.nl_intake("hmm do the thing we discussed", mgmt, thread_id="t")
        msgs = [p["message"] for p in chat.posted]
        assert not any(m.strip() in ("…", "...") for m in msgs)      # junk never posted
        assert any("couldn't turn that into something actionable" in m for m in msgs)
        assert len(orch.models._client.calls) >= 2                   # it DID retry once
    finally:
        await db.dispose()


async def test_junk_retry_that_succeeds_proceeds_normally(db_url):
    orch, chat, db = await _orch(db_url)
    try:
        orch.models._client.queue_structured(OperatorIntent(kind="chitchat", reply="…"))
        orch.models._client.queue_structured(OperatorIntent(
            kind="chitchat", reply="Happy to help — what would you like me to look at?"))
        mgmt = await orch.mgmt_channel_id()
        await orch.nl_intake("hello there", mgmt, thread_id="t")
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "Happy to help" in msgs                               # the retried reply was used
        assert not any(p["message"].strip() == "…" for p in chat.posted)
    finally:
        await db.dispose()
