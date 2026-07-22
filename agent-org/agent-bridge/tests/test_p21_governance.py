"""P21 governance fixes — F2a (deterministic risk class) + F2b (firewalled dev auto-approve).

Evidence: gym-018/019. F2a: `blast_radius` was an UNINSTRUCTED schema field sampled at temp 0.3, so
the byte-identical goal classified `routine` (gym-017) vs `cascading_refactor` (gym-018/019) — a
coin-flip governance decision that cost ~4.9h of plan-approval idle. F2b: a time-boxed autonomous
window may auto-clear ONLY the dev-scale plan-proceed gate — never a §3 hard-gate. Fakes only.
"""

from __future__ import annotations

from pathlib import Path

from app.adapters.chat import FakeChatAdapter
from app.config import Settings
from app.db import Database
from app.models import Effort
from app.modules.model_router import FakeModelClient
from app.modules.planner import _READINESS_SYS
from app.orchestrator import Orchestrator
from app.schemas import ReadinessVerdict
from app.worker.harness import FakeHarness

ROOT = Path(__file__).resolve().parents[1]
FAR_FUTURE = "2099-01-01T00:00:00+00:00"
FAR_PAST = "2000-01-01T00:00:00+00:00"


async def _orch(db_url, *, auto_window=""):
    settings = Settings(
        _env_file=None, chat_adapter="fake",
        profiles_dir=str(ROOT / "profiles"), charters_dir=str(ROOT / "charters"),
        floor_dir=str(ROOT / "floor"), worker_instance_urls="http://w1:8090",
        max_concurrent_workers=1, database_url=db_url, project_survey_enabled=False,
        review_mode="off", plan_approval="risky", plan_auto_approve_until=auto_window,
    )
    db = Database(db_url)
    orch = Orchestrator(settings, db, FakeChatAdapter(),
                        model_client=FakeModelClient(), harness=FakeHarness())
    await orch.setup()
    return orch, db


async def _effort_with_risk(orch, eid, risk):
    async with orch.db.session_factory() as s:
        s.add(Effort(id=eid, name=eid, channel_id=await orch.mgmt_channel_id(),
                     root_post_id=f"root-{eid}", state="active", lifecycle="open"))
        await s.commit()
    await orch.exec_gate.set_risk(eid, risk)


# ── F2a — the risk class is now a CONTRACT, not a coin-flip ────────────────────
async def test_readiness_prompt_and_schema_carry_risk_criteria(db_url):
    """The field was uninstructed. It now carries explicit criteria in BOTH the schema field and
    the prompt, and both anchor a greenfield feature-add to `routine` (the gym-018/019 over-rating)."""
    assert "blast_radius" in _READINESS_SYS and "cascading_refactor" in _READINESS_SYS
    assert "greenfield" in _READINESS_SYS.lower() and "routine" in _READINESS_SYS
    desc = ReadinessVerdict.model_fields["blast_radius"].description or ""
    assert "routine" in desc and "cascading_refactor" in desc and "greenfield" in desc.lower()


async def test_readiness_gate_runs_at_temperature_zero(db_url):
    """A governance decision must be deterministic, not sampled. The readiness/risk gate overrides
    the planner profile's 0.3 to 0.0 for this structured call (the profile is unchanged elsewhere)."""
    orch, db = await _orch(db_url)
    try:
        orch.models._client.queue_structured(
            ReadinessVerdict(clear_and_safe=True, blast_radius="routine"))
        await orch.planner.readiness_gate("effort-x", "add a delete command to the todo CLI", "")
        last = orch.models._client.calls[-1]
        assert last["kind"] == "structured"
        assert last["temperature"] == 0.0
    finally:
        await db.dispose()


# ── F2b — the autonomous-window auto-approve, FIREWALLED ───────────────────────
async def test_auto_approve_is_off_by_default(db_url):
    """No window granted → the plan gate holds for the human, even for a dev-scale change."""
    orch, db = await _orch(db_url)                       # plan_auto_approve_until = "" (off)
    try:
        await _effort_with_risk(orch, "e-default", "cascading_refactor")
        assert await orch._plan_auto_approvable("e-default") is False
    finally:
        await db.dispose()


async def test_auto_approve_only_cascading_refactor_inside_the_window(db_url):
    """THE FIREWALL. Inside an active window, ONLY a `cascading_refactor` (a wide but REVERSIBLE dev
    change) auto-proceeds. `irreversible` and `cross_effort` are NEVER auto-approved — they stay the
    human's, per §1 (the human holds the irreversible gates) and the paper's dropped-signal rule."""
    orch, db = await _orch(db_url, auto_window=FAR_FUTURE)
    try:
        await _effort_with_risk(orch, "e-cascading", "cascading_refactor")
        await _effort_with_risk(orch, "e-irreversible", "irreversible")
        await _effort_with_risk(orch, "e-crosseffort", "cross_effort")
        assert await orch._plan_auto_approvable("e-cascading") is True     # dev-scale, reversible
        assert await orch._plan_auto_approvable("e-irreversible") is False  # FIREWALL
        assert await orch._plan_auto_approvable("e-crosseffort") is False   # FIREWALL
    finally:
        await db.dispose()


async def test_auto_approve_fails_safe_on_an_expired_window(db_url):
    """A lapsed window reverts to the human — fail SAFE, never fail open."""
    orch, db = await _orch(db_url, auto_window=FAR_PAST)
    try:
        await _effort_with_risk(orch, "e-expired", "cascading_refactor")
        assert await orch._plan_auto_approvable("e-expired") is False
    finally:
        await db.dispose()


async def test_auto_approve_fails_safe_on_an_unparseable_window(db_url):
    """A malformed grant must not silently auto-approve — it reverts to the human."""
    orch, db = await _orch(db_url, auto_window="not-a-timestamp")
    try:
        await _effort_with_risk(orch, "e-bad", "cascading_refactor")
        assert await orch._plan_auto_approvable("e-bad") is False
    finally:
        await db.dispose()
