"""Worker-health reliability: a wedged (409 busy) or unreachable worker must not trap an effort. The
router QUARANTINES the bad worker and, when the effort can be re-cloned (a repo is set), RE-DISPATCHES
it on a healthy worker; a repo-less follow-up (publish) can't be moved, so it quarantines + raises and
the caller's verify/re-engage handles it. Fixes the infinite-409-retry / idle-GPU stuck loop."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from app.adapters.chat import FakeChatAdapter
from app.config import Settings
from app.db import Database
from app.models import WorkerInstance
from app.modules.model_router import FakeModelClient
from app.orchestrator import Orchestrator
from app.worker.harness import FakeHarness

ROOT = Path(__file__).resolve().parents[1]


async def _orch(db_url, *, workers="http://w1:8090,http://w2:8090", cap=2):
    settings = Settings(
        _env_file=None, chat_adapter="fake",
        profiles_dir=str(ROOT / "profiles"), charters_dir=str(ROOT / "charters"),
        floor_dir=str(ROOT / "floor"), worker_instance_urls=workers,
        max_concurrent_workers=cap, database_url=db_url, project_survey_enabled=False,
        review_mode="off", plan_approval="off",
    )
    db = Database(db_url)
    orch = Orchestrator(settings, db, FakeChatAdapter(),
                        model_client=FakeModelClient(), harness=FakeHarness())
    await orch.setup()
    return orch, db


async def test_wedged_worker_quarantined_and_rerouted(db_url):
    orch, db = await _orch(db_url)
    try:
        orch.harness.busy_urls = {"http://w1:8090"}      # worker-1's daemon is wedged (409)
        eid, chan, root = await orch.router.open_effort("reroute")
        result = await orch.router.wake(
            eid, "worker-default", thread_id=root, channel_id=chan, session_id=eid,
            instruction="do the work", repo="https://github.com/acme/widget")
        assert result is not None and result.ok           # completed despite the wedge
        # it re-dispatched onto worker-2 (re-cloning there)
        assert any(w["base_url"] == "http://w2:8090" for w in orch.harness.wakes)
        async with orch.db.session_factory() as s:
            w1 = await s.get(WorkerInstance, "worker-1")
        assert w1.quarantined_until is not None            # the bad worker is quarantined
    finally:
        await db.dispose()


async def test_unreachable_worker_also_reroutes(db_url):
    orch, db = await _orch(db_url)
    try:
        orch.harness.down_urls = {"http://w1:8090"}        # worker-1 unreachable (transport error)
        eid, chan, root = await orch.router.open_effort("down")
        result = await orch.router.wake(
            eid, "worker-default", thread_id=root, channel_id=chan, session_id=eid,
            instruction="do the work", repo="https://github.com/acme/widget")
        assert result is not None and result.ok
        assert any(w["base_url"] == "http://w2:8090" for w in orch.harness.wakes)
    finally:
        await db.dispose()


async def test_repoless_wake_on_wedged_worker_quarantines_and_raises(db_url):
    """A publish/follow-up wake (repo=None) can't be moved to another worker (the workspace lives on
    the wedged one), so it quarantines the worker and RAISES — the caller (publish) treats that as a
    failed self-report and verification arbitrates. It must NOT silently 'succeed' or loop."""
    orch, db = await _orch(db_url, workers="http://w1:8090", cap=1)
    try:
        orch.harness.busy_urls = {"http://w1:8090"}
        eid, chan, root = await orch.router.open_effort("pub")
        with pytest.raises(httpx.HTTPStatusError):
            await orch.router.wake(
                eid, "worker-default", thread_id=root, channel_id=chan, session_id=eid,
                instruction="publish", repo=None)
        async with orch.db.session_factory() as s:
            w1 = await s.get(WorkerInstance, "worker-1")
        assert w1.quarantined_until is not None
    finally:
        await db.dispose()


async def test_all_workers_wedged_parks_via_nocapacity(db_url):
    """When every worker is quarantined, acquire raises NoCapacity (not an infinite loop) — the
    orchestrator then PARKS the effort and auto-resumes when a worker frees / a quarantine lapses."""
    from app.modules.scheduler import NoCapacityError
    orch, db = await _orch(db_url, workers="http://w1:8090,http://w2:8090", cap=2)
    try:
        orch.harness.busy_urls = {"http://w1:8090", "http://w2:8090"}   # both wedged
        eid, chan, root = await orch.router.open_effort("allbad")
        with pytest.raises(NoCapacityError):
            await orch.router.wake(
                eid, "worker-default", thread_id=root, channel_id=chan, session_id=eid,
                instruction="do it", repo="https://github.com/acme/widget")
        # both got quarantined on the way to exhaustion
        async with orch.db.session_factory() as s:
            w1 = await s.get(WorkerInstance, "worker-1")
            w2 = await s.get(WorkerInstance, "worker-2")
        assert w1.quarantined_until is not None and w2.quarantined_until is not None
    finally:
        await db.dispose()
