"""P4 stop-gates + differently-goaled review tests (governance §4.4/§4.5)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings
from app.modules.audit_sink import AuditSink
from app.modules.model_router import FakeModelClient, ModelRouter
from app.modules.profiles import ProfileRegistry
from app.modules.stop_gates import CheckpointBlocked, SameGoalReviewerError, StopGates
from app.schemas import Explanation, ExplanationCheck, ReviewVerdict

PROFILES_DIR = str(Path(__file__).resolve().parents[1] / "profiles")


async def _stack(db, fake: FakeModelClient):
    settings = Settings(_env_file=None, profiles_dir=PROFILES_DIR)
    audit = AuditSink(db, settings)
    profiles = ProfileRegistry(db, PROFILES_DIR)
    await profiles.load_from_disk()
    models = ModelRouter(settings, profiles, client=fake)
    return StopGates(db, models, audit)


def test_same_goal_reviewer_rejected():
    with pytest.raises(SameGoalReviewerError):
        StopGates.assert_differently_goaled("worker-default", "worker-default")
    with pytest.raises(SameGoalReviewerError):
        StopGates.assert_differently_goaled("worker-default", "pm")
    # a reviewer-<lens> profile is accepted
    StopGates.assert_differently_goaled("worker-default", "reviewer-ethics")


async def test_checkpoint_blocks_until_cleared(db):
    sg = await _stack(db, FakeModelClient())
    await sg.add_checkpoint("cp1", "e1", "phase-1", 1)
    assert await sg.may_proceed("cp1") is False
    with pytest.raises(CheckpointBlocked):
        await sg.assert_may_proceed("cp1")


async def test_risk_gates_lens_count(db):
    sg = await _stack(db, FakeModelClient())
    assert sg.lenses_for("routine") == ["ethics"]
    assert set(sg.lenses_for("irreversible")) == {"correctness", "security", "scope", "ethics"}


async def test_explanation_mismatch_flagged(db):
    fake = FakeModelClient()
    fake.queue_structured(ExplanationCheck(consistent=False, mismatch_detail="diff added a push"))
    sg = await _stack(db, fake)
    await sg.add_checkpoint("cp1", "e1", "phase-1", 1)
    check = await sg.submit_explanation(
        "cp1",
        Explanation(intent="add login", goal_as_understood="login only",
                    tradeoffs_hit="none", what_id_flag="none"),
        diff="+ git push origin main",
    )
    assert check.consistent is False


async def test_flagged_review_keeps_checkpoint_blocked(db):
    fake = FakeModelClient()
    # routine risk -> one ethics reviewer; queue a flag verdict.
    fake.queue_structured(ReviewVerdict(verdict="flag", lens="ethics", findings=["scope creep"]))
    sg = await _stack(db, fake)
    await sg.add_checkpoint("cp1", "e1", "phase-1", 1)
    verdicts = await sg.review("e1", "worker-default", "deliverable text", risk="routine",
                               checkpoint_id="cp1")
    cleared = await sg.clear_checkpoint("cp1", verdicts)
    assert cleared is False
    assert await sg.may_proceed("cp1") is False   # stays blocked on a flag


async def test_clean_review_clears_checkpoint(db):
    fake = FakeModelClient()
    fake.queue_structured(ReviewVerdict(verdict="pass", lens="ethics", findings=[]))
    sg = await _stack(db, fake)
    await sg.add_checkpoint("cp1", "e1", "phase-1", 1)
    verdicts = await sg.review("e1", "worker-default", "clean deliverable", risk="routine",
                               checkpoint_id="cp1")
    assert await sg.clear_checkpoint("cp1", verdicts) is True
    assert await sg.may_proceed("cp1") is True


async def test_deterministic_check_failure_flags(db):
    fake = FakeModelClient()
    fake.queue_structured(ReviewVerdict(verdict="pass", lens="ethics", findings=[]))
    sg = await _stack(db, fake)
    verdicts = await sg.review("e1", "worker-default", "d", risk="routine",
                               deterministic_checks={"tests": False, "lint": True})
    assert any(v.verdict == "flag" for v in verdicts)  # a failed test is a flag, LLM-independent
