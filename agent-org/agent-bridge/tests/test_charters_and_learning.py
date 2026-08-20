"""P3 charters/floor/steering/goals + P6 learning-loop tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings
from app.modules.audit_sink import AuditSink
from app.modules.charters import Charters, FloorChangeDenied
from app.modules.learning_loop import LearningLoop

FLOOR_DIR = str(Path(__file__).resolve().parents[1] / "floor")
CHARTERS_DIR = str(Path(__file__).resolve().parents[1] / "charters")


async def _charters(db) -> Charters:
    settings = Settings(_env_file=None, floor_dir=FLOOR_DIR, charters_dir=CHARTERS_DIR)
    c = Charters(db, settings, AuditSink(db, settings))
    await c.seed_floor_from_disk()
    return c


async def test_floor_seeded_and_change_requires_human(db):
    c = await _charters(db)
    ver, content = await c.current_floor()
    assert ver == 1 and "Hard Rules" in content
    with pytest.raises(FloorChangeDenied):
        await c.set_floor("weakened floor", approved_by="pm")   # non-human rejected
    new_ver = await c.set_floor(content + "\n9. extra", approved_by="human")
    assert new_ver == 2


async def test_steering_updates_reach_next_read(db):
    c = await _charters(db)
    await c.set_steering("e1", "focus on the auth module only")
    assert "auth module" in await c.current_steering("e1")
    await c.set_steering("e1", "now also handle logout")
    assert "logout" in await c.current_steering("e1")  # latest wins


async def test_goal_constraints_inline_in_context(db):
    c = await _charters(db)
    await c.set_goal("e1", "rank for engagement WITHOUT surfacing flagged misinformation",
                     scope_slice="ranking service only")
    ctx = await c.build_context("e1", "worker-default")
    assert "WITHOUT surfacing flagged misinformation" in ctx   # constraint inside the goal
    assert "FLOOR" in ctx                                       # floor is always injected


async def test_canonical_objective_change_is_human(db):
    c = await _charters(db)
    v1 = await c.set_goal("e1", "objective A", created_by="pm")
    v2 = await c.set_goal("e1", "objective B (canonical)", created_by="human")
    assert v2 == v1 + 1


# ── learning loop (propose-not-dispose) ─────────────────────────────────────
async def _loop(db) -> LearningLoop:
    return LearningLoop(db, AuditSink(db, Settings(_env_file=None)))


async def test_suggestion_pool(db):
    loop = await _loop(db)
    await loop.add_suggestion("worker-1", "grant read access to shared config")
    pool = await loop.pool()
    assert len(pool) == 1 and pool[0]["worker"] == "worker-1"


async def test_pattern_surfaces_across_two_efforts(db):
    loop = await _loop(db)
    sig = "same-issue"
    assert await loop.observe(sig, "e1", "x") is None      # 1 effort -> noise
    pat = await loop.observe(sig, "e2", "x")               # 2nd effort -> surfaced
    assert pat is not None and pat.count == 2


async def test_propose_not_dispose(db):
    loop = await _loop(db)
    sig = "recurring"
    await loop.observe(sig, "e1", "x")
    await loop.observe(sig, "e2", "x")
    await loop.propose(sig, "tighten the worker charter")
    # only the human disposes
    with pytest.raises(PermissionError):
        await loop.dispose(sig, approve=True, actor="pm")
    assert await loop.dispose(sig, approve=True, actor="human") is True
