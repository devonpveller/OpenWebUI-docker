"""Workspace provenance (operator 2026-07-16: "worker workspaces should be wiped each time the task
changes … unless we can deterministically determine dependencies … could use project name and git
commit id").

Live root cause: the worker daemon only wipes `/workspace` on a PROJECT switch, so consecutive
efforts on the SAME project reused a days-old checkout. It accumulated every prior effort's branches,
sat on a stale base, and pushed branches with NO common ancestor to the live `main` →
`compare → 404`, `POST /pulls → 422`. Two complete, green products could never be delivered — and a
reopened effort's worker even read the PREVIOUS round's finished branch and reported "all phases
complete — no changes", closing hollow with nothing published.

Rule: reuse ONLY when the task is provably identical (same effort + same repo → the base we cloned
— and, P8 #3, the same BASE COMMIT when the caller states one); otherwise WIPE. A fresh clone at
task start always carries the CURRENT base commit, which is the determinism the commit-id check
exists for. P8 #3 additionally makes provenance a first-class claim: the worker is HANDED the
expected base in its brief (it can't discover it — proxied git) and asserts before working;
published claims are stamped with the base they were built against. Fakes only."""

from __future__ import annotations

from pathlib import Path

import httpx
from sqlalchemy import select

from app.adapters.chat import FakeChatAdapter
from app.config import Settings
from app.db import Database
from app.models import Effort, Event
from app.modules.model_router import FakeModelClient
from app.orchestrator import Orchestrator
from app.worker.harness import FakeHarness

ROOT = Path(__file__).resolve().parents[1]
REPO = "https://github.com/acme/gym.git"
GH_REPO = "https://github.com/devonpveller/gym"
BASE_SHA = "ba5e000000000000000000000000000000000000"


async def _orch(db_url, tmp_path=None, *, github=False):
    kwargs = dict(
        _env_file=None, chat_adapter="fake",
        profiles_dir=str(ROOT / "profiles"), charters_dir=str(ROOT / "charters"),
        floor_dir=str(ROOT / "floor"), worker_instance_urls="http://w1:8090",
        max_concurrent_workers=1, database_url=db_url, project_survey_enabled=False,
        review_mode="off", plan_approval="off",
    )
    if github:
        key = tmp_path / "app.pem"
        key.write_text("dummy")
        kwargs.update(github_app_id="1", github_app_owner="devonpveller",
                      github_app_private_key_path=str(key))
    settings = Settings(**kwargs)
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


# ── P8 #3: the BASE COMMIT is part of the task's identity ────────────────────
async def test_a_moved_base_forces_a_reclone(db_url):
    """Same effort + same repo is NOT enough when the caller states the base it expects: a moved
    base (a merge landed / the arena swapped `main`) means the old checkout's lineage is dead —
    branches pushed off it have no common ancestor with the live main and can never deliver."""
    orch, harness, db = await _orch(db_url)
    try:
        await orch.projects.add("gym", REPO)
        eid, chan, root = await orch.router.open_effort("one", project="gym")
        await orch.router.wake(eid, role="worker-default", thread_id=root, channel_id=chan,
                               instruction="x", repo=REPO, expected_base="base-1")
        assert harness.focus_calls[-1]["fresh"] is True                   # first anchor
        await orch.router.wake(eid, role="worker-default", thread_id=root, channel_id=chan,
                               instruction="x", repo=REPO, expected_base="base-1")
        assert harness.focus_calls[-1]["fresh"] is False                  # same base → reuse
        # a mid-effort follow-up (publish/QA) that states NO base still reuses the checkout
        await orch.router.wake(eid, role="worker-default", thread_id=root, channel_id=chan,
                               instruction="x", repo=REPO)
        assert harness.focus_calls[-1]["fresh"] is False
        # the base MOVED → the checkout is dead history → wipe + re-clone
        await orch.router.wake(eid, role="worker-default", thread_id=root, channel_id=chan,
                               instruction="x", repo=REPO, expected_base="base-2")
        assert harness.focus_calls[-1]["fresh"] is True, "a moved base must re-clone"
    finally:
        await db.dispose()


def _gh_remote():
    """A GitHub remote for the FULL dispatch path: `main`'s head is the expected base; the
    effort's agent branch verifies as landed with real changes; PR opens."""
    state = {"agent_reads": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        if "/branches/agent/" in p:
            state["agent_reads"] += 1
            sha = ("pre_dispatch_head_000000" if state["agent_reads"] == 1
                   else "cafef00d1234567890")
            return httpx.Response(200, json={"commit": {"sha": sha}})
        if "/branches/" in p:                       # the default-branch head = the expected base
            return httpx.Response(200, json={"commit": {"sha": BASE_SHA}})
        if "/compare/" in p:
            return httpx.Response(200, json={
                "ahead_by": 1, "behind_by": 0,
                "commits": [{"commit": {"message": "work"}}],
                "files": [{"filename": "todo.py", "additions": 10, "deletions": 0}]})
        if p.endswith("/pulls") and request.method == "POST":
            return httpx.Response(201, json={"number": 3, "html_url": "https://x/pull/3"})
        if p.endswith("/pulls"):
            return httpx.Response(200, json=[])
        if p.count("/") == 3:
            return httpx.Response(200, json={"default_branch": "main"})
        return httpx.Response(404)
    return httpx.MockTransport(handler)


async def _last_event_payload(orch, eid, kind):
    async with orch.db.session_factory() as s:
        row = (await s.execute(
            select(Event).where(Event.kind == kind, Event.effort_id == eid)
            .order_by(Event.id.desc()).limit(1))).scalar_one_or_none()
    return row.payload if row is not None else None


async def test_dispatch_hands_the_worker_its_expected_base_and_stamps_claims(db_url, tmp_path):
    """P8 #3 end-to-end: the org reads the live default-branch head at dispatch, HANDS it to the
    worker in the brief with an assert-before-work demand (the worker can't discover it — proxied
    git), and stamps `base_sha` on the published/delivery claims — no claim without the base it
    was made against."""
    orch, harness, db = await _orch(db_url, tmp_path, github=True)
    try:
        await orch.projects.add("gym", GH_REPO)
        orch._gh_transport = _gh_remote()
        eid, chan, root = await orch.router.open_effort("one", project="gym")
        await orch.delegate(eid, chan, root, "add a --due flag to the todo tool")
        # the FIRST coding wake carries the expected base + the honest-stop protocol
        first = harness.wakes[0]["prompt"]
        assert "WORKSPACE PROVENANCE" in first and BASE_SHA in first
        assert "WORKSPACE STALE" in first                       # the honest-stop reply shape
        # the coding step's focus was anchored at that base (reuse keys on it now); the later
        # follow-up focuses (publish) state no base and reuse the same checkout
        anchors = [e["payload"].get("base_sha") for e in await orch.audit.replay(eid)
                   if e["kind"] == "worker_project_set"]
        assert anchors[0] == BASE_SHA
        # every claim states what it was built against
        assert (await _last_event_payload(orch, eid, "effort_published"))["base_sha"] == BASE_SHA
        assert (await _last_event_payload(orch, eid, "delivery_pr_opened"))["base_sha"] == BASE_SHA
    finally:
        await db.dispose()


async def test_fresh_clone_carries_the_orientation_map_and_shares_it_per_base(db_url, tmp_path):
    """P8 #5 — a wiped workspace must not mean a BLIND worker: the first coding turn carries the
    org's cached codebase survey, keyed by base commit, so two efforts on the same base cost ONE
    survey (a map lookup, not 26 blind reads)."""
    from app.modules.project_context import ProjectContext
    orch, harness, db = await _orch(db_url, tmp_path, github=True)
    calls: list[str] = []

    async def survey(repo: str) -> str:
        calls.append(repo)
        return "todo.py is the whole CLI; tests live in tests/test_todo.py (pytest)"

    orch.project_context = ProjectContext(survey, enabled=True)
    try:
        await orch.projects.add("gym", GH_REPO)
        orch._gh_transport = _gh_remote()
        eid, chan, root = await orch.router.open_effort("map-one", project="gym")
        await orch.delegate(eid, chan, root, "add a --due flag to the todo tool")
        first = harness.wakes[0]["prompt"]
        assert "PROJECT ORIENTATION" in first and "todo.py is the whole CLI" in first
        # a SECOND effort on the SAME base shares the map — one survey total
        eid2, chan2, root2 = await orch.router.open_effort("map-two", project="gym")
        await orch.delegate(eid2, chan2, root2, "add a --priority flag to the todo tool")
        assert len(calls) == 1, "two efforts on the same base must share ONE survey"
    finally:
        await db.dispose()


async def test_worker_stale_report_stops_work_and_invalidates_the_checkout(db_url, tmp_path):
    """P8 #3 — refuse to act on unprovenanced state: a worker that asserts BASE-MISMATCH stops
    honestly; the org must NOT continue the pipeline (no publish), must drop the workspace's
    provenance claim (next focus re-clones fresh), and must audit the refusal."""
    orch, harness, db = await _orch(db_url, tmp_path, github=True)
    try:
        await orch.projects.add("gym", GH_REPO)
        orch._gh_transport = _gh_remote()
        eid, chan, root = await orch.router.open_effort("stale", project="gym")
        harness.output_queue = [f"WORKSPACE STALE: HEAD not rooted on {BASE_SHA[:12]}"]
        await orch.delegate(eid, chan, root, "add a --due flag to the todo tool")
        assert len(harness.wakes) == 1, "work continued on a workspace the worker proved stale"
        pay = await _last_event_payload(orch, eid, "focus_failed")
        assert pay is not None and pay.get("reason") == "workspace_stale"
        assert pay.get("expected_base") == BASE_SHA
        assert orch.router._ws_focus == {}, "the stale checkout's provenance claim must be dropped"
        msgs = " ".join(p["message"] for p in orch.chat.posted)
        assert "DEAD history" in msgs and "re-clones fresh" in msgs
    finally:
        await db.dispose()
