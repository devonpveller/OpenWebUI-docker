"""Workspace provenance (operator 2026-07-16: "worker workspaces should be wiped each time the task
changes … unless we can deterministically determine dependencies … could use project name and git
commit id").

Live root cause: the worker daemon only wipes `/workspace` on a PROJECT switch, so consecutive
efforts on the SAME project reused a days-old checkout. It accumulated every prior effort's branches,
sat on a stale base, and pushed branches with NO common ancestor to the live `main` →
`compare → 404`, `POST /pulls → 422`. Two complete, green products could never be delivered — and a
reopened effort's worker even read the PREVIOUS round's finished branch and reported "all phases
complete — no changes", closing hollow with nothing published.

Rule: reuse ONLY when the task is provably identical (same effort + same repo → the base we cloned);
otherwise WIPE. A fresh clone at task start always carries the CURRENT base commit, which is the
determinism the commit-id check exists for. Fakes only."""

from __future__ import annotations

from pathlib import Path

from app.adapters.chat import FakeChatAdapter
from app.config import Settings
from app.db import Database
from app.models import Effort
from app.modules.model_router import FakeModelClient
from app.orchestrator import Orchestrator
from app.worker.harness import FakeHarness

ROOT = Path(__file__).resolve().parents[1]
REPO = "https://github.com/acme/gym.git"


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
    return orch, orch.harness, db


async def _focus(orch, eid, chan, root):
    await orch.router.wake(eid, role="worker-default", thread_id=root, channel_id=chan,
                           instruction="do the thing", repo=REPO)


async def test_first_focus_of_a_task_clones_fresh(db_url):
    orch, harness, db = await _orch(db_url)
    try:
        await orch.projects.add("gym", REPO)
        eid, chan, root = await orch.router.open_effort("one", project="gym")
        await _focus(orch, eid, chan, root)
        assert harness.focus_calls[-1]["fresh"] is True, "a task's first focus must clone fresh"
    finally:
        await db.dispose()


async def test_same_effort_reuses_so_its_branch_survives_across_turns(db_url):
    """The ONLY provably-identical case: the same effort's later turns. Wiping here would destroy
    the worker's in-progress branch between turns."""
    orch, harness, db = await _orch(db_url)
    try:
        await orch.projects.add("gym", REPO)
        eid, chan, root = await orch.router.open_effort("one", project="gym")
        await _focus(orch, eid, chan, root)
        await _focus(orch, eid, chan, root)
        assert harness.focus_calls[-1]["fresh"] is False, "same effort+repo must REUSE its checkout"
    finally:
        await db.dispose()


async def test_a_different_effort_on_the_same_project_wipes(db_url):
    """The core fix: the task changed, so the workspace must NOT inherit the previous effort's
    branches or its stale base — even though the project is identical."""
    orch, harness, db = await _orch(db_url)
    try:
        await orch.projects.add("gym", REPO)
        eid1, c1, r1 = await orch.router.open_effort("one", project="gym")
        await _focus(orch, eid1, c1, r1)
        eid2, c2, r2 = await orch.router.open_effort("two", project="gym")
        await _focus(orch, eid2, c2, r2)
        assert harness.focus_calls[-1]["fresh"] is True, "a new task must never inherit a checkout"
    finally:
        await db.dispose()


async def test_reopened_effort_wipes_its_stale_round(db_url):
    """A reopen is a NEW round whose base has moved (a swap/merge landed). Reusing the old checkout
    is how a worker ends up reading the PREVIOUS round's finished branch and reporting "no changes"."""
    orch, harness, db = await _orch(db_url)
    try:
        await orch.projects.add("gym", REPO)
        eid, chan, root = await orch.router.open_effort("one", project="gym")
        await _focus(orch, eid, chan, root)
        assert harness.focus_calls[-1]["fresh"] is True
        await _focus(orch, eid, chan, root)
        assert harness.focus_calls[-1]["fresh"] is False          # same round → reuse
        async with orch.db.session_factory() as s:                # close it, then reopen the slug
            e = await s.get(Effort, eid)
            e.lifecycle = "done"
            await s.commit()
        await orch.router.open_effort("one", project="gym")        # → effort_reopened
        await _focus(orch, eid, chan, root)
        assert harness.focus_calls[-1]["fresh"] is True, "a reopened round must re-clone off the base"
    finally:
        await db.dispose()


async def test_failed_focus_never_claims_provenance(db_url):
    """If the clone failed the workspace state is UNKNOWN — the next focus must wipe, never assume
    the checkout is good (a masked clone failure is how a void workspace got worked on before)."""
    orch, harness, db = await _orch(db_url)
    try:
        await orch.projects.add("gym", REPO)
        eid, chan, root = await orch.router.open_effort("one", project="gym")
        harness.set_project_fails = "fatal: could not read from remote"
        await _focus(orch, eid, chan, root)
        harness.set_project_fails = ""
        await _focus(orch, eid, chan, root)
        assert harness.focus_calls[-1]["fresh"] is True, "unknown workspace state must re-clone"
    finally:
        await db.dispose()
