"""Stage-5 governance wiring: scope grant (P5.1) + role authority (P5.2), the learning loop
(P6.4/6.5), and the lateral-concern (P4.8) + A→B hand-off (P5.4) primitives. Fakes only."""

from __future__ import annotations

from pathlib import Path

from app.adapters.chat import FakeChatAdapter
from app.config import Settings
from app.db import Database
from app.modules.model_router import FakeModelClient
from app.orchestrator import Orchestrator
from app.schemas import MonitorVerdict, ReviewVerdict
from app.worker.harness import FakeHarness

ROOT = Path(__file__).resolve().parents[1]


async def _orch(db_url, *, review_mode="off", plan_approval="off"):
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


# ── P5.1/5.2: scope granted + role approved on dispatch ──────────────────────
async def test_scope_and_role_authorized_on_dispatch(db_url):
    orch, chat, harness, db = await _orch(db_url)
    try:
        eid, chan, root = await orch.router.open_effort("s")
        await orch.delegate(eid, chan, root, "do it")
        assert await orch.scope.authorized("worker-default", "read")
        assert await orch.scope.authorized("worker-default", "write")
        assert not await orch.scope.authorized("worker-default", "push")   # irreversible = human-only
        assert await orch.scope.is_role_approved("worker-default")          # role catalog (P5.2)
    finally:
        await db.dispose()


# ── P6.4/6.5: a review flag recurring across ≥2 efforts surfaces a pattern ────
async def test_recurring_review_flag_surfaces_pattern(db_url):
    orch, chat, harness, db = await _orch(db_url, review_mode="all")
    try:
        for name in ("e-one", "e-two"):
            eid, chan, root = await orch.router.open_effort(name)
            orch.models._client.queue_structured(MonitorVerdict(deviates=False))
            orch.models._client.queue_structured(
                ReviewVerdict(verdict="flag", lens="ethics", findings=["same recurring issue"]))
            await orch.delegate(eid, chan, root, "goal")
        sug = orch.chat.channels["suggestions"]
        assert any(
            p["channel_id"] == sug and "pattern" in p["message"].lower() for p in orch.chat.posted
        )
        # both efforts were frozen by the flag (pause-until-cleared)
        assert await orch.gate.can_dispatch("effort-e-two") is False
    finally:
        await db.dispose()


# ── P4.8: a lateral concern surfaces on the bus + is storm-exempt ────────────
async def test_lateral_concern_surfaces_and_is_storm_exempt(db_url):
    orch, chat, harness, db = await _orch(db_url)
    try:
        eid, chan, root = await orch.router.open_effort("lat")
        orch.s.wake_storm_max = 0            # any WORK wake would trip the cap
        await orch.raise_lateral_concern(eid, "worker-default", "this touches the auth module")
        assert any(
            "lateral concern" in p["message"].lower() and p.get("thread_id") == root
            for p in chat.posted
        )
        # the brake wake is EXEMPT — it does not trip the work wake-storm cap
        assert await orch.router.wake_storm_tripped(eid) is False
    finally:
        await db.dispose()


# ── P5.4: hand-off resolves the last owner (or surfaces to PM when unresolved) ─
async def test_handoff_surfaces_when_owner_unresolved(db_url):
    orch, chat, harness, db = await _orch(db_url)
    try:
        eid, chan, root = await orch.router.open_effort("ho")
        owner = await orch.hand_off(eid, "src/x.py", workspace="/nonexistent-workspace")
        assert owner is None                # no git repo → unresolved
        assert any("hand-off" in p["message"].lower() for p in chat.posted)
    finally:
        await db.dispose()
