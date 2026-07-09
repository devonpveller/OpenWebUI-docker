"""Advisory review for machine-verified efforts (operator 2026-07-07): a cross_effort port
froze at checkpoint cp1 because the heavy-path review flagged the worker's MID-WORK STATUS
message ("let me make all the changes then build") for "no implementation / no incremental
checks" — redundant with the org's own build gate + D4 human merge, and exactly the autonomous-
progress friction the operator flagged. For an effort whose correctness is machine-verified (its
project or vendoring host has a check_cmd) and whose delivery is branch-isolated, a review flag
is now ADVISORY (surfaced, learned-from) rather than a freeze. The MONITOR (off-task deviation)
stays a hard freeze — that's a safety signal a build can't catch."""

from __future__ import annotations

import asyncio
from pathlib import Path

from app.adapters.chat import FakeChatAdapter
from app.config import Settings
from app.db import Database
from app.modules.model_router import FakeModelClient
from app.schemas import ReviewVerdict
from app.worker.harness import FakeHarness
from app.orchestrator import Orchestrator

ROOT = Path(__file__).resolve().parents[1]


async def _orch(db_url, tmp_path, review_mode="risky"):
    key = tmp_path / "app.pem"
    key.write_text("dummy")
    settings = Settings(
        _env_file=None, chat_adapter="fake",
        profiles_dir=str(ROOT / "profiles"), charters_dir=str(ROOT / "charters"),
        floor_dir=str(ROOT / "floor"), worker_instance_urls="http://w1:8090",
        max_concurrent_workers=1, database_url=db_url, project_survey_enabled=False,
        review_mode=review_mode, plan_approval="off",
        github_app_id="1", github_app_owner="devonpveller",
        github_app_private_key_path=str(key),
    )
    db = Database(db_url)
    orch = Orchestrator(settings, db, FakeChatAdapter(),
                        model_client=FakeModelClient(), harness=FakeHarness())
    await orch.setup()
    return orch, orch.chat, orch.harness, db


def _flag(lens="correctness"):
    return ReviewVerdict(lens=lens, verdict="flag",
                         findings=["Deliverable contains no actual implementation."],
                         reasoning="batching without incremental checks")


async def test_review_flag_is_advisory_when_build_verified(db_url, tmp_path):
    orch, chat, harness, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("murder", "https://github.com/devonpveller/murder")
        await orch.projects.set_check("murder", "dotnet build Murder.sln")
        eid, _c, _r = await orch.router.open_effort("verified", project="murder")
        await orch.charters.set_goal(eid, "port it", created_by="po")
        cp_id = f"{eid}:cp1"
        await orch.stop_gates.add_checkpoint(cp_id, eid, "step 1", 1)

        # force the review to flag, and the monitor to NOT deviate
        async def fake_review(*a, **k):
            return [_flag("correctness"), _flag("scope")]
        orch.stop_gates.review = fake_review
        orch.monitor_sampled = lambda *a, **k: _noop_none()

        from types import SimpleNamespace
        ok = await orch._gate_deliverable(
            eid, SimpleNamespace(output="Let me make all the changes then build."), cp_id)
        assert ok is True, "a build-verified effort must not freeze on a review flag"
        assert await orch.stop_gates.may_proceed(cp_id) is True   # checkpoint force-cleared
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "advisory" in msgs.lower()                         # surfaced, not hidden
        from app.models import Effort
        async with orch.db.session_factory() as s:
            e = await s.get(Effort, eid)
        assert e.state != "frozen"
    finally:
        await db.dispose()


async def test_review_flag_still_freezes_without_a_build_check(db_url, tmp_path):
    orch, chat, harness, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("plain", "https://github.com/devonpveller/plain")  # no check_cmd
        eid, _c, _r = await orch.router.open_effort("unverified", project="plain")
        await orch.charters.set_goal(eid, "do it", created_by="po")
        cp_id = f"{eid}:cp1"
        await orch.stop_gates.add_checkpoint(cp_id, eid, "step 1", 1)

        async def fake_review(*a, **k):
            return [_flag("security")]
        orch.stop_gates.review = fake_review
        orch.monitor_sampled = lambda *a, **k: _noop_none()

        from types import SimpleNamespace
        ok = await orch._gate_deliverable(eid, SimpleNamespace(output="whatever"), cp_id)
        assert ok is False, "without a machine build gate the review must still freeze"
        assert await orch.stop_gates.may_proceed(cp_id) is False
    finally:
        await db.dispose()


async def test_approving_a_concern_auto_resumes_the_work(db_url, tmp_path):
    """Approving a concern MEANS continue — the effort re-dispatches its own work automatically,
    so the operator never has to say "approve" AND then "re-run it" (operator 2026-07-07)."""
    orch, chat, harness, db = await _orch(db_url, tmp_path)
    try:
        from app.schemas import Decision
        await orch.projects.add("murder", "https://github.com/devonpveller/murder")
        eid, _c, _r = await orch.router.open_effort("frozen", project="murder")
        await orch.charters.set_goal(eid, "the work", created_by="po")
        # freeze it (a concern), then approve → should re-dispatch
        from app.schemas import Concern, Trigger
        await orch.gate.freeze(eid, Trigger.deviation,
                               Concern(intent_thread=f"effort {eid}", what_surfaced="x",
                                       intent_of_change="y", pm_recommendation="z",
                                       blocked_efforts=[eid]))
        await orch.apply_operator_decision(eid, Decision(decision="approve"), actor_role="human")
        for _ in range(8):
            if not orch._bg_tasks:
                break
            await asyncio.gather(*list(orch._bg_tasks), return_exceptions=True)
        assert harness.wakes, "approve must resume the effort's work"
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "Resuming" in msgs
    finally:
        await db.dispose()


async def test_abort_does_not_resume(db_url, tmp_path):
    orch, chat, harness, db = await _orch(db_url, tmp_path)
    try:
        from app.schemas import Decision
        await orch.projects.add("murder", "https://github.com/devonpveller/murder")
        eid, _c, _r = await orch.router.open_effort("gone", project="murder")
        await orch.charters.set_goal(eid, "the work", created_by="po")
        from app.schemas import Concern, Trigger
        await orch.gate.freeze(eid, Trigger.deviation,
                               Concern(intent_thread=f"effort {eid}", what_surfaced="x",
                                       intent_of_change="y", pm_recommendation="z",
                                       blocked_efforts=[eid]))
        await orch.apply_operator_decision(eid, Decision(decision="abort"), actor_role="human")
        for _ in range(6):
            if not orch._bg_tasks:
                break
            await asyncio.gather(*list(orch._bg_tasks), return_exceptions=True)
        assert not harness.wakes, "an aborted effort must not resume"
    finally:
        await db.dispose()


async def _noop_none():
    return None
