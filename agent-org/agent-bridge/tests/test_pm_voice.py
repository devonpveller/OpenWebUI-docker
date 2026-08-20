"""PM communication voice (operator UX 2026-07-10). The operator prefers talking to Claude over the
bot-PM because the PM's replies read mechanical. Root cause: the operator-facing reply was a thin
BYPRODUCT of a ~15-kind intent-classification call on the weak local model, and every substantive
update was a hardcoded template — communication was never its own task. Fix: a DEDICATED synthesis
pass (charters/pm-voice.md) that renders the ground-truth facts in a clear, honest voice — decoupled
from classification, faithful to the facts, and failing SOFT to the deterministic reply so the
operator's turn is never swallowed. Fakes-only unit tests of the wiring + the facts contract."""

from __future__ import annotations

from pathlib import Path

from app.adapters.chat import FakeChatAdapter
from app.config import Settings
from app.db import Database
from app.modules.model_router import FakeModelClient
from app.orchestrator import Orchestrator
from app.schemas import OperatorIntent
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


async def test_status_turn_uses_synthesized_voice_not_the_thin_reply(db_url):
    """A conversational STATUS turn posts the SYNTHESIZED reply, not the thin classifier byproduct."""
    orch, chat, db = await _orch(db_url)
    try:
        orch.models._client.queue_structured(
            OperatorIntent(kind="status", reply="thin classifier line"))
        orch.models._client.queue_text(
            "Nothing's running right now. The murder fix landed as PR #12 and built green, but the "
            "crash you reported is runtime-only, so it still needs your check.")
        mgmt = await orch.mgmt_channel_id()
        await orch.nl_intake("how's it going?", mgmt, thread_id="t")
        posted = " ".join(p["message"] for p in chat.posted)
        assert "still needs your check" in posted           # the synthesized voice won
        assert "thin classifier line" not in posted          # not the byproduct reply
    finally:
        await db.dispose()


async def test_synthesis_fails_soft_to_deterministic_reply(db_url):
    """If synthesis yields nothing (model hiccup / GPU squeeze / junk), the turn keeps its
    deterministic reply — the operator's message is NEVER swallowed."""
    orch, chat, db = await _orch(db_url)
    try:
        orch.models._client.queue_structured(
            OperatorIntent(kind="status", reply="Here's the deterministic status."))
        # queue NO text → complete() returns "" → synth is junk → fall back to the reply
        mgmt = await orch.mgmt_channel_id()
        await orch.nl_intake("status?", mgmt, thread_id="t")
        posted = " ".join(p["message"] for p in chat.posted)
        assert "Here's the deterministic status." in posted
    finally:
        await db.dispose()


async def test_chitchat_is_not_synthesized(db_url):
    """Synthesis is scoped to SUBSTANTIVE conversational turns (status/question). A social chitchat
    turn keeps its simple reply — no needless synthesis call (or model spend) on 'thanks'."""
    orch, chat, db = await _orch(db_url)
    try:
        orch.models._client.queue_structured(
            OperatorIntent(kind="chitchat", reply="You're welcome!"))
        orch.models._client.queue_text("SYNTH SHOULD NOT APPEAR")
        mgmt = await orch.mgmt_channel_id()
        await orch.nl_intake("thanks!", mgmt, thread_id="t")
        posted = " ".join(p["message"] for p in chat.posted)
        assert "You're welcome!" in posted
        assert "SYNTH SHOULD NOT APPEAR" not in posted       # synthesis text never consumed
        assert not any(c.get("kind") == "complete" for c in orch.models._client.calls)
    finally:
        await db.dispose()


async def test_comm_facts_carries_honesty_relevant_evidence(db_url):
    """The GROUND-TRUTH facts handed to synthesis carry what the operator must know honestly: the
    PRs awaiting THEIR merge and the org's OWN latest build verdict (never a worker's self-claim)."""
    orch, chat, db = await _orch(db_url)
    try:
        eid, _c, _r = await orch.router.open_effort("demo")
        orch._pending_merge["merge-demo"] = {"repo": "https://github.com/x/y", "pr_number": 42}
        await orch.audit.log("org_build_check", effort_id=eid,
                             payload={"verdict": "fail", "errors": 3})
        efforts = await orch.gate.snapshot(open_only=True)
        status_map = await orch._effort_status_map(efforts)
        facts = await orch._comm_facts(efforts, status_map)
        assert "PR #42" in facts and "merge" in facts.lower()     # what needs the operator
        assert "fail" in facts and "3 error" in facts            # the org's own verdict, honest
    finally:
        await db.dispose()


async def test_pm_voice_empty_message_returns_nothing(db_url):
    orch, chat, db = await _orch(db_url)
    try:
        assert await orch._pm_voice("", "some facts", "history") == ""
    finally:
        await db.dispose()


async def test_voice_is_the_operator_tunable_charter_from_disk(db_url):
    """The voice is loaded from charters/pm-voice.md (operator-tunable, no code change), not the
    tiny built-in fallback."""
    orch, chat, db = await _orch(db_url)
    try:
        assert "operator" in orch._pm_voice_sys.lower()
        assert "never invent" in orch._pm_voice_sys.lower()      # the truth rule survived the load
        assert len(orch._pm_voice_sys) > 400                     # the real charter, not the fallback
    finally:
        await db.dispose()


async def test_comm_facts_names_the_current_branch_to_check(db_url):
    """The facts name the CURRENT branch per project (operator 2026-07-11: "the latest change-
    containing branch is what we focus on; older branches aren't progress") so "what do I check?"
    always has one answer."""
    orch, chat, db = await _orch(db_url)
    try:
        await orch.router.open_effort("the-fix", project="app")
        efforts = await orch.gate.snapshot(open_only=True)
        status_map = await orch._effort_status_map(efforts)
        facts = await orch._comm_facts(efforts, status_map)
        assert "CURRENT WORK" in facts and "WHAT TO CHECK" in facts
        assert "agent/effort-the-fix" in facts       # the branch to check is named unambiguously
        assert "app" in facts
    finally:
        await db.dispose()
