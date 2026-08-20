"""Stage-5 governed execution loop (P4.1-4.7 + P3.7 + P6.4): per-step checkpoints, differently-
goaled review, sampled monitor, and flag/deviation → freeze + escalate. Fakes only."""

from __future__ import annotations

import asyncio
from pathlib import Path

from app.adapters.chat import FakeChatAdapter
from app.config import Settings
from app.db import Database
from app.modules.model_router import FakeModelClient
from app.orchestrator import Orchestrator
from app.schemas import MonitorVerdict, OperatorIntent, Plan, ReadinessVerdict, ReviewVerdict
from app.worker.harness import FakeHarness

ROOT = Path(__file__).resolve().parents[1]


async def _orch(db_url, *, review_mode="all", plan_approval="off"):
    settings = Settings(
        _env_file=None, chat_adapter="fake",
        profiles_dir=str(ROOT / "profiles"), charters_dir=str(ROOT / "charters"),
        floor_dir=str(ROOT / "floor"), worker_instance_urls="http://w1:8090",
        max_concurrent_workers=1, database_url=db_url, project_survey_enabled=False,
        review_mode=review_mode, plan_approval=plan_approval,
    )
    db = Database(db_url)
    orch = Orchestrator(settings, db, FakeChatAdapter(),
                        model_client=FakeModelClient(), harness=FakeHarness())
    await orch.setup()
    return orch, orch.chat, orch.harness, db


async def _drain(orch):
    if orch._bg_tasks:
        await asyncio.gather(*orch._bg_tasks)


# ── clean deliverable → monitor pass + review pass → done ────────────────────
async def test_heavy_effort_runs_monitor_and_review_then_done(db_url):
    orch, chat, harness, db = await _orch(db_url)  # review_mode=all → routine efforts are heavy
    try:
        mgmt = await orch.mgmt_channel_id()
        orch.models._client.queue_structured(OperatorIntent(kind="request", effort_name="feat", reply="ok"))
        orch.models._client.queue_structured(ReadinessVerdict(clear_and_safe=True, blast_radius="routine"))
        orch.models._client.queue_structured(MonitorVerdict(deviates=False))
        orch.models._client.queue_structured(ReviewVerdict(verdict="pass", lens="ethics"))
        await orch.handle_event(
            {"id": "r1", "channel_id": mgmt, "message": "add a feature", "is_bot": False, "ts": 1})
        await _drain(orch)
        assert len(harness.wakes) == 1
        assert await orch.gate.can_dispatch("effort-feat") is True         # not frozen
        assert any("finished (**done**)" in p["message"] for p in chat.posted)
        # a checkpoint was created + cleared (the enforced halt, P4.1/4.2)
        from app.models import Checkpoint
        async with orch.db.session_factory() as s:
            cp = await s.get(Checkpoint, "effort-feat:cp1")
        assert cp is not None and cp.status == "cleared"
    finally:
        await db.dispose()


# ── review FLAGS → effort frozen + CONCERN + checkpoint stays flagged (P4.6/§3) ─
async def test_review_flag_freezes_and_escalates(db_url):
    orch, chat, harness, db = await _orch(db_url)
    try:
        mgmt = await orch.mgmt_channel_id()
        orch.models._client.queue_structured(OperatorIntent(kind="request", effort_name="risky", reply="ok"))
        orch.models._client.queue_structured(ReadinessVerdict(clear_and_safe=True, blast_radius="routine"))
        orch.models._client.queue_structured(MonitorVerdict(deviates=False))
        orch.models._client.queue_structured(
            ReviewVerdict(verdict="flag", lens="ethics", findings=["drops the safety constraint"]))
        await orch.handle_event(
            {"id": "r2", "channel_id": mgmt, "message": "rank for engagement", "is_bot": False, "ts": 1})
        await _drain(orch)
        assert await orch.gate.can_dispatch("effort-risky") is False        # frozen on the flag
        assert any("CONCERN" in p["message"] and p["channel_id"] == mgmt for p in chat.posted)
        from app.models import Checkpoint
        async with orch.db.session_factory() as s:
            cp = await s.get(Checkpoint, "effort-risky:cp1")
        assert cp is not None and cp.status == "flagged"                    # blocking, not cleared
    finally:
        await db.dispose()


# ── monitor DEVIATES → frozen before review is even reached (P3.7) ───────────
async def test_monitor_deviation_freezes(db_url):
    orch, chat, harness, db = await _orch(db_url)
    try:
        mgmt = await orch.mgmt_channel_id()
        orch.models._client.queue_structured(OperatorIntent(kind="request", effort_name="drift", reply="ok"))
        orch.models._client.queue_structured(ReadinessVerdict(clear_and_safe=True, blast_radius="routine"))
        orch.models._client.queue_structured(
            MonitorVerdict(deviates=True, trigger="deviation", level="steering",
                           rationale="output ignores the stated constraint"))
        await orch.handle_event(
            {"id": "r3", "channel_id": mgmt, "message": "summarize tickets", "is_bot": False, "ts": 1})
        await _drain(orch)
        assert await orch.gate.can_dispatch("effort-drift") is False        # monitor froze it
    finally:
        await db.dispose()


# ── a multi-step plan runs each step as its own checkpoint ───────────────────
# ── Stage-3 plan-approval gate: HOLD until the operator approves (P3.9) ───────
async def test_plan_approval_gate_holds_until_approved(db_url):
    orch, chat, harness, db = await _orch(db_url, plan_approval="always", review_mode="off")
    try:
        mgmt = await orch.mgmt_channel_id()
        orch.models._client.queue_structured(OperatorIntent(kind="request", effort_name="planned", reply="ok"))
        orch.models._client.queue_structured(ReadinessVerdict(clear_and_safe=True, blast_radius="routine"))
        orch.models._client.queue_structured(
            Plan(intent_thread="i", feature_overview="adds X",
                 implementation_steps=["do a", "do b"], estimate="~1h"))
        await orch.handle_event(
            {"id": "p1", "channel_id": mgmt, "message": "build X", "is_bot": False, "ts": 1})
        await _drain(orch)
        # HELD — no worker dispatched; plan presented + pending; effort plan_status=draft
        assert len(harness.wakes) == 0
        assert "effort-planned" in orch._pending_plan
        assert any("Plan for" in p["message"] for p in chat.posted)
        assert await orch.planner.plan_status("effort-planned") == "draft"

        # operator approves → dispatch with the plan's TWO steps (light path since review off)
        await orch.handle_event(
            {"id": "p2", "channel_id": mgmt, "message": "approve effort-planned", "is_bot": False, "ts": 2})
        await _drain(orch)
        assert len(harness.wakes) == 2
        assert "effort-planned" not in orch._pending_plan
        assert await orch.planner.plan_status("effort-planned") == "approved"
    finally:
        await db.dispose()


async def test_operator_api_approve_reaches_the_control_surface(db_url):
    """2026-07-15 (iteration-2's very first gate): `approve <effort>` sent through POST /nl —
    which calls nl_intake directly — bypassed handle_event's control surface, so the PO MODEL
    narrated "Approved. Dispatching…" while the plan stayed `draft` (a false-ack at the operator
    API). The control grammar now applies inside nl_intake itself: every inlet honors
    decision/kill/slash commands exactly like chat."""
    orch, chat, harness, db = await _orch(db_url, plan_approval="always", review_mode="off")
    try:
        mgmt = await orch.mgmt_channel_id()
        orch.models._client.queue_structured(
            OperatorIntent(kind="request", effort_name="api-2-approved", reply="ok"))
        orch.models._client.queue_structured(
            ReadinessVerdict(clear_and_safe=True, blast_radius="routine"))
        orch.models._client.queue_structured(
            Plan(intent_thread="i", feature_overview="adds X",
                 implementation_steps=["do a"], estimate="~1h"))
        await orch.handle_event(
            {"id": "n1", "channel_id": mgmt, "message": "build X (iteration 2)",
             "is_bot": False, "ts": 1})
        await _drain(orch)
        assert len(harness.wakes) == 0                        # held at the plan gate
        # approve arrives via the OPERATOR API inlet (nl_intake direct), id contains a DIGIT
        await orch.nl_intake("approve effort-api-2-approved", mgmt, user_id="operator-api")
        await _drain(orch)
        assert len(harness.wakes) == 1                        # actually dispatched
        assert await orch.planner.plan_status("effort-api-2-approved") == "approved"
    finally:
        await db.dispose()


async def test_plan_abort_does_not_dispatch(db_url):
    orch, chat, harness, db = await _orch(db_url, plan_approval="always", review_mode="off")
    try:
        mgmt = await orch.mgmt_channel_id()
        orch.models._client.queue_structured(OperatorIntent(kind="request", effort_name="nope", reply="ok"))
        orch.models._client.queue_structured(ReadinessVerdict(clear_and_safe=True, blast_radius="routine"))
        orch.models._client.queue_structured(
            Plan(intent_thread="i", feature_overview="X", implementation_steps=["a"], estimate="?"))
        await orch.handle_event(
            {"id": "a1", "channel_id": mgmt, "message": "build nope", "is_bot": False, "ts": 1})
        await _drain(orch)
        await orch.handle_event(
            {"id": "a2", "channel_id": mgmt, "message": "abort effort-nope", "is_bot": False, "ts": 2})
        await _drain(orch)
        assert len(harness.wakes) == 0                      # never dispatched
        assert "effort-nope" not in orch._pending_plan
    finally:
        await db.dispose()


async def test_multi_step_plan_runs_each_step_as_checkpoint(db_url):
    orch, chat, harness, db = await _orch(db_url)
    try:
        eid, chan, root = await orch.router.open_effort("multi")
        # two steps, each: monitor pass + review pass
        for _ in range(2):
            orch.models._client.queue_structured(MonitorVerdict(deviates=False))
            orch.models._client.queue_structured(ReviewVerdict(verdict="pass", lens="ethics"))
        await orch.delegate(eid, chan, root, "big goal", plan_steps=["step one", "step two"])
        assert len(harness.wakes) == 2                                      # one wake per step
        from app.models import Checkpoint
        async with orch.db.session_factory() as s:
            cp1 = await s.get(Checkpoint, f"{eid}:cp1")
            cp2 = await s.get(Checkpoint, f"{eid}:cp2")
        assert cp1.status == "cleared" and cp2.status == "cleared"
        assert any("finished (**done**)" in p["message"] for p in chat.posted)
    finally:
        await db.dispose()
