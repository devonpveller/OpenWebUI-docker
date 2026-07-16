"""P8 #1 — CLOSURE INVARIANT (2026-07-16 gym: `effort-gym-004b-todo-product` closed "done —
read-only, nothing to publish" while its own audit read `effort_published: 3`,
`delivery_pr_opened: 0`, `qa_evaluation: 0`, `develop_integration: 0` — a complete, green,
62-test product, entirely undelivered, and nothing noticed for hours). The PM may not claim
"done" on a LANDED delivery unless the effort's own audit proves the gates that should have run
actually did; a missing gate refuses the close with an honest "I could not deliver". A genuine
read-only/no-changes completion has no delivery and no gates to assert. Fakes + mocked GitHub."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import httpx

from app.adapters.chat import FakeChatAdapter
from app.config import Settings
from app.db import Database
from app.models import Effort
from app.modules.capabilities import BranchDelivery
from app.modules.model_router import FakeModelClient
from app.orchestrator import Orchestrator
from app.worker.harness import FakeHarness

ROOT = Path(__file__).resolve().parents[1]
REPO = "https://github.com/devonpveller/gym"


async def _orch(db_url, tmp_path, *, invariant=True, github=True, **overrides):
    kwargs = dict(
        _env_file=None, chat_adapter="fake",
        profiles_dir=str(ROOT / "profiles"), charters_dir=str(ROOT / "charters"),
        floor_dir=str(ROOT / "floor"), worker_instance_urls="http://w1:8090",
        max_concurrent_workers=1, database_url=db_url, project_survey_enabled=False,
        review_mode="off", plan_approval="off", closure_invariant=invariant,
    )
    if github:
        key = tmp_path / "app.pem"
        key.write_text("dummy")
        kwargs.update(github_app_id="1", github_app_owner="devonpveller",
                      github_app_private_key_path=str(key))
    kwargs.update(overrides)
    settings = Settings(**kwargs)
    db = Database(db_url)
    orch = Orchestrator(settings, db, FakeChatAdapter(),
                        model_client=FakeModelClient(), harness=FakeHarness())
    await orch.setup()
    return orch, orch.chat, db


def _remote(*, pr_opens: bool):
    """A remote where the branch verifies as LANDED (pure-addition diff) and the PR endpoint
    either works (201) or fails the way the gym did (422, no existing PR to fall back to)."""
    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        if "/compare/" in p:
            return httpx.Response(200, json={"ahead_by": 1, "behind_by": 0, "commits": [],
                "files": [{"filename": "todo.py", "status": "added",
                           "additions": 40, "deletions": 0, "patch": "+todo"}]})
        if "/branches/" in p:
            return httpx.Response(200, json={"commit": {"sha": "headsha123456789000"}})
        if p.endswith("/pulls") and request.method == "POST":
            if pr_opens:
                return httpx.Response(201, json={"number": 5, "html_url": "https://x/pull/5"})
            return httpx.Response(422, json={"message": "Validation Failed: no common ancestor"})
        if p.endswith("/pulls"):
            return httpx.Response(200, json=[])
        if p.count("/") == 3:
            return httpx.Response(200, json={"default_branch": "main"})
        return httpx.Response(404)
    return httpx.MockTransport(handler)


async def _landed_effort(orch, name):
    await orch.projects.add("gym", REPO)
    eid, _c, _r = await orch.router.open_effort(name, project="gym")
    await orch.charters.set_goal(eid, "add a due-date field to the todo tool", created_by="po")
    delivery = BranchDelivery(verifiable=True, exists=True, ahead=1, files_changed=1,
                              head_sha="headsha123456789000", branch=f"agent/{eid}")
    res = SimpleNamespace(status="done", output="Built the feature; 62/62 tests green.")
    return eid, delivery, res


async def _lifecycle(orch, eid):
    async with orch.db.session_factory() as s:
        e = await s.get(Effort, eid)
    return e.lifecycle


# ── the invariant: a landed delivery whose PR never opened must NOT close done ──
async def test_landed_delivery_without_pr_never_closes_done(db_url, tmp_path):
    orch, chat, db = await _orch(db_url, tmp_path)
    try:
        eid, delivery, res = await _landed_effort(orch, "todo-product")
        orch._gh_transport = _remote(pr_opens=False)          # POST /pulls → 422, the gym failure
        await orch._finish_effort(eid, res, delivery=delivery)
        assert await _lifecycle(orch, eid) != "done"          # the false done is refused
        assert await orch._event_count(eid, "closure_invariant_failed") == 1
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "could **not** deliver" in msgs                # honest, not "done"
        assert "no delivery PR" in msgs                       # names the exact missing gate
        assert "worker finished" not in msgs                  # the clean closure never posted
    finally:
        await db.dispose()


async def test_happy_path_with_all_gates_still_closes_clean(db_url, tmp_path):
    orch, chat, db = await _orch(db_url, tmp_path)
    try:
        eid, delivery, res = await _landed_effort(orch, "todo-clean")
        orch._gh_transport = _remote(pr_opens=True)
        await orch._finish_effort(eid, res, delivery=delivery)
        assert await orch._event_count(eid, "delivery_pr_opened") == 1
        assert await orch._event_count(eid, "closure_invariant_failed") == 0
        assert await _lifecycle(orch, eid) == "done"
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "worker finished" in msgs and "/pull/5" in msgs
    finally:
        await db.dispose()


async def test_read_only_no_changes_completion_has_no_gates_to_assert(db_url, tmp_path):
    """A genuine read-only completion has no delivery — the invariant must NOT hold it hostage
    to gates that were never supposed to run (the plan's gotcha)."""
    orch, chat, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("gym", REPO)
        eid, _c, _r = await orch.router.open_effort("survey", project="gym")
        await orch.charters.set_goal(eid, "inventory the repo layout and report", created_by="po")
        # remote where nothing landed (branch 404s) so the no-changes re-verify stays no-changes
        orch._gh_transport = httpx.MockTransport(lambda r: httpx.Response(404))
        deliv = BranchDelivery(no_changes=True)
        res = SimpleNamespace(status="done", output="NO CHANGES: read-only survey, answer above.")
        await orch._finish_effort(eid, res, delivery=deliv)
        assert await _lifecycle(orch, eid) == "done"
        assert await orch._event_count(eid, "closure_invariant_failed") == 0
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "no changes" in msgs
    finally:
        await db.dispose()


async def test_invariant_off_keeps_the_old_close(db_url, tmp_path):
    """AO_CLOSURE_INVARIANT off (the field default) — the pre-P8 behaviour is unchanged, per the
    house rule that new config fields default off."""
    orch, chat, db = await _orch(db_url, tmp_path, invariant=False)
    try:
        eid, delivery, res = await _landed_effort(orch, "todo-legacy")
        orch._gh_transport = _remote(pr_opens=False)
        await orch._finish_effort(eid, res, delivery=delivery)
        assert await _lifecycle(orch, eid) == "done"          # old behaviour: closes anyway
        assert await orch._event_count(eid, "closure_invariant_failed") == 0
    finally:
        await db.dispose()


# ── the per-gate conditions (unit level) ─────────────────────────────────────
async def test_qa_gap_is_asserted_only_when_qa_gate_is_on(db_url, tmp_path):
    """qa_gate != off and a delivery landed ⇒ the audit must show a qa_evaluation. No GitHub App
    here, so the PR/develop gates (which need the App) are not asserted."""
    orch, _chat, db = await _orch(db_url, tmp_path, github=False, qa_gate="report")
    try:
        await orch.projects.add("gym", REPO)
        eid, _c, _r = await orch.router.open_effort("qa-gap", project="gym")
        gaps = await orch._closure_invariant_gaps(eid)
        assert len(gaps) == 1 and "no QA evaluation" in gaps[0]
        await orch.audit.log("qa_evaluation", effort_id=eid, payload={"defects": 0})
        assert await orch._closure_invariant_gaps(eid) == []
    finally:
        await db.dispose()


async def test_develop_integration_attempt_satisfies_the_invariant(db_url, tmp_path):
    """'Attempted' is the invariant, not success: a failed/conflicted integration that leaves its
    develop_integration event (even ok=False) satisfies the gate; a silent void does not."""
    orch, _chat, db = await _orch(db_url, tmp_path, develop_integration=True, qa_gate="off")
    try:
        await orch.projects.add("gym", REPO)
        eid, _c, _r = await orch.router.open_effort("integ", project="gym")
        await orch.audit.log("delivery_pr_opened", effort_id=eid, payload={"pr": 5})
        orch._pending_merge[f"merge-{eid}"] = {"repo": REPO, "pr_number": 5, "effort_id": eid}
        gaps = await orch._closure_invariant_gaps(eid)
        assert len(gaps) == 1 and "no develop integration" in gaps[0]
        # an integration ATTEMPT that fails to even seed `develop` now leaves its audit trace…
        orch._gh_transport = httpx.MockTransport(lambda r: httpx.Response(404))
        d = BranchDelivery(verifiable=True, exists=True, ahead=1, head_sha="abc",
                           branch=f"agent/{eid}")
        note = await orch._integrate_to_develop(eid, REPO, d)
        assert "Couldn't prepare" in note
        assert await orch._event_count(eid, "develop_integration") == 1
        # …and the invariant reads that as "attempted" — the gap clears
        assert await orch._closure_invariant_gaps(eid) == []
    finally:
        await db.dispose()
