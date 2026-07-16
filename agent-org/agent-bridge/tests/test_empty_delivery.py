"""Empty-delivery gate (live 2026-07-05, PR #4): a branch can land AHEAD-BY-N with ZERO net file
changes — the worker fixed code inside a vendored submodule checkout, re-pointed the gitlink back
to a published commit (dodging the reachability gate), and delivered an engine branch whose
commits cancel out. `ahead >= 1` counted commits, not substance → an EMPTY PR claiming the fix.
The gate: files_changed == 0 on a landed branch ⇒ re-engage the affine worker once with the
submodule-aware remedy → re-verify → NO CHANGES / state-check / escalate. No PR ever opens for an
empty delivery. Run RED against pre-fix code as proof, GREEN after."""

from __future__ import annotations

from pathlib import Path

import httpx

from app.adapters.chat import FakeChatAdapter
from app.config import Settings
from app.db import Database
from app.modules.model_router import FakeModelClient
from app.orchestrator import Orchestrator
from app.worker.harness import FakeHarness

ROOT = Path(__file__).resolve().parents[1]


async def _orch(db_url, tmp_path):
    key = tmp_path / "app.pem"
    key.write_text("dummy")
    settings = Settings(
        _env_file=None, chat_adapter="fake",
        profiles_dir=str(ROOT / "profiles"), charters_dir=str(ROOT / "charters"),
        floor_dir=str(ROOT / "floor"), worker_instance_urls="http://w1:8090",
        max_concurrent_workers=1, database_url=db_url, project_survey_enabled=False,
        review_mode="off", plan_approval="off",
        github_app_id="1", github_app_owner="devonpveller",
        github_app_private_key_path=str(key),
    )
    db = Database(db_url)
    orch = Orchestrator(settings, db, FakeChatAdapter(),
                        model_client=FakeModelClient(), harness=FakeHarness())
    await orch.setup()
    return orch, orch.chat, orch.harness, db


def _remote(*, heal_after: int | None = None):
    """Branch exists, ahead=2. Compare reports files=[] (empty diff) until `heal_after` compare
    calls have happened; then a real file appears (the re-engaged worker published the fix).
    The branch HEAD moves after the first read (the worker's push) so the stale-head gate —
    which fires first — correctly sees a fresh delivery."""
    state = {"compares": 0, "branch_reads": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        if "/compare/" in p:
            state["compares"] += 1
            healed = heal_after is not None and state["compares"] > heal_after
            files = ([{"filename": "src/Game.cs", "additions": 3, "deletions": 3}]
                     if healed else [])
            return httpx.Response(200, json={
                "ahead_by": 2, "behind_by": 0,
                "commits": [{"commit": {"message": "fix OnExiting"}}], "files": files})
        if "/contents/src/Game.cs" in p:
            return httpx.Response(200, json={"type": "file", "sha": "aa"})
        if "/branches/" in p:
            state["branch_reads"] += 1
            sha = ("pre_dispatch_head_000000" if state["branch_reads"] == 1
                   else "feedbead12345678")
            return httpx.Response(200, json={"commit": {"sha": sha}})
        if p.endswith("/pulls") and request.method == "POST":
            return httpx.Response(201, json={
                "number": 9, "html_url": "https://github.com/devonpveller/Engine/pull/9"})
        if p.endswith("/pulls") and request.method == "GET":
            return httpx.Response(200, json=[])
        if p.count("/") == 3:
            return httpx.Response(200, json={"default_branch": "main"})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def _remote_real_changes():
    """Branch exists AHEAD with REAL file changes (files_changed > 0) from the start — a genuine
    landed delivery, even if the worker's final turn claims NO CHANGES."""
    state = {"branch_reads": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        if "/compare/" in p:
            return httpx.Response(200, json={
                "ahead_by": 3, "behind_by": 0,
                "commits": [{"commit": {"message": "add features"}}],
                "files": [{"filename": "todo.py", "additions": 120, "deletions": 2},
                          {"filename": "tests/test_todo.py", "additions": 200, "deletions": 0}]})
        if "/branches/" in p:
            state["branch_reads"] += 1
            sha = "pre_dispatch_head_000000" if state["branch_reads"] == 1 else "cafef00d12345678"
            return httpx.Response(200, json={"commit": {"sha": sha}})
        if p.endswith("/pulls") and request.method == "POST":
            return httpx.Response(201, json={
                "number": 12, "html_url": "https://github.com/devonpveller/Engine/pull/12"})
        if p.endswith("/pulls") and request.method == "GET":
            return httpx.Response(200, json=[])
        if p.count("/") == 3:
            return httpx.Response(200, json={"default_branch": "main"})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


async def _lifecycle(orch, effort_id):
    from app.models import Effort
    async with orch.db.session_factory() as s:
        e = await s.get(Effort, effort_id)
        return e.lifecycle if e else None


async def test_no_changes_over_a_real_branch_delivers_not_readonly_closes(db_url, tmp_path):
    """2026-07-16 gym: a complete, 62-test product closed 'done — read-only, nothing to publish'
    because a final NO CHANGES turn (with self-reported REPRO:/AFTER: PASS on a behavioral goal)
    masked the LANDED branch — so it never opened a PR, ran QA, or integrated to develop. A
    no_changes delivery whose branch actually has real changes ahead of main must go through the
    DELIVERY pipeline (a PR), never a hollow read-only close."""
    orch, chat, harness, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("engine", "https://github.com/devonpveller/Engine")
        eid, chan, root = await orch.router.open_effort("full", project="engine")
        orch._gh_transport = _remote_real_changes()
        harness.output_queue = ["did work", "published",
                                "NO CHANGES: everything was already committed on the branch"]
        await orch.delegate(eid, chan, root,
                            "add delete and edit commands to the todo CLI", plan_steps=["work"])
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "PR opened for review" in msgs, "a landed branch with real changes must be delivered"
        assert "read-only task" not in msgs, "must NOT close read-only over a real branch"
        assert await _lifecycle(orch, eid) == "done"
    finally:
        await db.dispose()


async def test_empty_diff_reengaged_worker_publishes_real_fix(db_url, tmp_path):
    orch, chat, harness, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("engine", "https://github.com/devonpveller/Engine")
        eid, chan, root = await orch.router.open_effort("fix", project="engine")
        orch._gh_transport = _remote(heal_after=2)      # pre-dispatch + first verify empty, then the fix lands
        await orch.delegate(eid, chan, root, "fix the override", plan_steps=["work"])
        prompts = " ".join(w["prompt"] for w in harness.wakes)
        assert "ZERO file changes" in prompts, "the worker was never told its delivery is empty"
        assert "submodule" in prompts.lower()           # the remedy names the classic cause
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "finished (**done**)" in msgs and "PR opened for review" in msgs
        assert await _lifecycle(orch, eid) == "done"
    finally:
        await db.dispose()


async def test_empty_diff_forever_escalates_and_opens_no_pr(db_url, tmp_path):
    orch, chat, harness, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("engine", "https://github.com/devonpveller/Engine")
        eid, chan, root = await orch.router.open_effort("hollow", project="engine")
        orch._gh_transport = _remote(heal_after=None)   # never heals
        await orch.delegate(eid, chan, root, "fix the override", plan_steps=["work"])
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "zero net file changes" in msgs          # the escalation says WHY
        assert "PR opened for review" not in msgs, "an EMPTY PR was still opened"
        assert "finished (**done**)" not in msgs
        assert await _lifecycle(orch, eid) != "done"
    finally:
        await db.dispose()


async def test_empty_diff_with_no_changes_protocol_closes_noop(db_url, tmp_path):
    orch, chat, harness, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("engine", "https://github.com/devonpveller/Engine")
        eid, chan, root = await orch.router.open_effort("noop", project="engine")
        orch._gh_transport = _remote(heal_after=None)
        harness.output_queue = ["did work", "published",
                                "NO CHANGES: the override was already correct on this branch"]
        await orch.delegate(eid, chan, root, "fix the override", plan_steps=["work"])
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "finished (**done**)" in msgs            # legitimate no-op completion
        assert "PR opened for review" not in msgs       # nothing to promote
        assert await _lifecycle(orch, eid) == "done"
    finally:
        await db.dispose()


# ── LIVE 2026-07-07: an empty-workspace run "delivered" yesterday's stale branch ──
def _stale_branch(*, heal_after: int | None = None):
    """The branch pre-exists at a FIXED head; after `heal_after` branch reads the head changes
    (the re-engaged worker pushed real commits)."""
    state = {"reads": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        if "/branches/" in p:
            state["reads"] += 1
            healed = heal_after is not None and state["reads"] > heal_after
            sha = "new_commit_after_reengage_00" if healed else "stale_head_from_yesterday_0"
            return httpx.Response(200, json={"commit": {"sha": sha}})
        if "/compare/" in p:
            return httpx.Response(200, json={
                "ahead_by": 2, "behind_by": 0, "commits": [],
                "files": [{"filename": "src/x.cs", "additions": 1, "deletions": 1}]})
        if "/contents/src/x.cs" in p:
            return httpx.Response(200, json={"type": "file", "sha": "aa"})
        if p.endswith("/pulls") and request.method == "POST":
            return httpx.Response(201, json={"number": 9, "html_url": "https://x/pull/9"})
        if p.endswith("/pulls") and request.method == "GET":
            return httpx.Response(200, json=[])
        if p.count("/") == 3:
            return httpx.Response(200, json={"default_branch": "main"})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


async def test_stale_head_never_counts_as_delivery(db_url, tmp_path):
    """Branch pre-exists, the run pushes nothing → the head is unchanged → re-engage with the
    plain truth; still stale → escalate, no PR, not done."""
    orch, chat, harness, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("engine", "https://github.com/devonpveller/Engine")
        eid, chan, root = await orch.router.open_effort("resurrect", project="engine")
        orch._gh_transport = _stale_branch(heal_after=None)      # head never moves
        await orch.delegate(eid, chan, root, "fix the thing", plan_steps=["work"])
        prompts = " ".join(w["prompt"] for w in harness.wakes)
        assert "NOTHING NEW WAS DELIVERED" in prompts
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "no new commits" in msgs and "PR opened for review" not in msgs
        assert await _lifecycle(orch, eid) != "done"
    finally:
        await db.dispose()


async def test_behavioral_goal_stale_branch_no_changes_is_not_closed_done(db_url, tmp_path):
    """LIVE 2026-07-11 (the false-done the operator distrusts): a REOPENED behavioral effort whose
    branch pre-existed (stale head) — the re-engaged worker replied "NO CHANGES: already published,
    nothing to change" and the stale-recovery path FALSE-closed it 'done — verified'. A behavioral-
    symptom goal must NEVER close done on a bare no-op (doing nothing can't fix a live symptom); it
    falls through to the honest 'delivered nothing new — not done, no PR' escalation instead. RED on
    the pre-gate code (line closed done), GREEN after. Behavioral detection keys off the GOAL, not
    any project specifics."""
    orch, chat, harness, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("engine", "https://github.com/devonpveller/Engine")
        eid, chan, root = await orch.router.open_effort("atlas-runtime", project="engine")
        goal = ("the editor throws at runtime when Game Profile is clicked: the atlas is not "
                "loaded, and the cursor is missing")
        await orch.charters.set_goal(eid, goal, created_by="po")
        orch._gh_transport = _stale_branch(heal_after=None)      # branch pre-exists, never heals
        # step → publish → stale re-engage says NO CHANGES → state-check says STATE MISSING
        harness.output_queue = ["did the work", "published the branch",
                                "NO CHANGES: already correct on this branch, nothing to change",
                                "STATE MISSING: the atlas.json still fails to load"]
        await orch.delegate(eid, chan, root, goal, plan_steps=["work"])
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "finished (**done**)" not in msgs, "a behavioral no-op FALSE-closed done"
        assert "PR opened for review" not in msgs
        assert await _lifecycle(orch, eid) != "done"
    finally:
        await db.dispose()


async def test_stale_head_healed_by_reengage_proceeds(db_url, tmp_path):
    orch, chat, harness, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("engine", "https://github.com/devonpveller/Engine")
        eid, chan, root = await orch.router.open_effort("resurrect2", project="engine")
        # pre-dispatch read (1) + post-publish verify (2) see the stale head; after the
        # re-engage the worker's push moves it
        orch._gh_transport = _stale_branch(heal_after=2)
        await orch.delegate(eid, chan, root, "fix the thing", plan_steps=["work"])
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "finished (**done**)" in msgs and "PR opened for review" in msgs
        assert await _lifecycle(orch, eid) == "done"
    finally:
        await db.dispose()
