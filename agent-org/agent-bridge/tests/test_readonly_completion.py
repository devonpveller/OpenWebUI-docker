"""Read-only/investigation completion (live miss: the worker CORRECTLY reported `NO CHANGES:
read-only investigation` and the PM ignored its own protocol — force-marched publish → firm
re-engage → escalated 'the change did not land' for a task that was never meant to change anything).
The `NO CHANGES:` reply is a LEGITIMATE completion: the worker's ANSWER is the deliverable. Plus the
answer-truncation fix: long answers are chunked, never chopped mid-diagram at 1500 chars."""

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


def _no_branch_remote():
    """Remote where the effort branch never exists (a read-only task pushes nothing)."""
    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        if "/branches/" in p:
            return httpx.Response(404, json={"message": "Not Found"})
        if p.count("/") == 3:
            return httpx.Response(200, json={"default_branch": "main"})
        return httpx.Response(404)
    return httpx.MockTransport(handler)


async def _lifecycle(orch, eid):
    from app.models import Effort
    async with orch.db.session_factory() as s:
        e = await s.get(Effort, eid)
        return e.lifecycle if e else None


async def test_no_changes_on_first_publish_finishes_done_without_reengage(db_url, tmp_path):
    orch, chat, harness, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("murder", "https://github.com/devonpveller/murder")
        eid, chan, root = await orch.router.open_effort("investigate", project="murder")
        orch._gh_transport = _no_branch_remote()
        # step output, then the publish wake replies NO CHANGES (the worker's protocol reply)
        harness.output_queue = ["Answer: the canonical structure is …",
                                "NO CHANGES: read-only investigation, zero modifications."]
        await orch.delegate(eid, chan, root, "investigate the repo — read-only", plan_steps=["look"])
        assert len(harness.wakes) == 2                          # NO firm re-engage
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "finished (**done**)" in msgs
        assert "read-only task" in msgs and "answer" in msgs.lower()
        assert "did not land" not in msgs and "Re-dispatching" not in msgs   # never escalated
        assert await _lifecycle(orch, eid) == "done"
    finally:
        await db.dispose()


async def test_no_changes_on_firm_reengage_finishes_done_not_escalated(db_url, tmp_path):
    """The LIVE sequence: first publish narrated (no NO CHANGES marker) → verify 404 → firm
    re-engage → worker replies `NO CHANGES:` → must finish DONE, not escalate 'did not land'."""
    orch, chat, harness, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("murder", "https://github.com/devonpveller/murder")
        eid, chan, root = await orch.router.open_effort("investigate", project="murder")
        orch._gh_transport = _no_branch_remote()
        harness.output_queue = ["the answer …", "skipped git steps per instruction",
                                "NO CHANGES: read-only investigation task."]
        await orch.delegate(eid, chan, root, "investigate — read-only", plan_steps=["look"])
        assert len(harness.wakes) == 3                          # step + publish + firm re-engage
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "finished (**done**)" in msgs and "read-only task" in msgs
        assert "did not land" not in msgs                       # NOT escalated as undelivered
        assert await _lifecycle(orch, eid) == "done"
    finally:
        await db.dispose()


async def test_long_answers_are_chunked_not_chopped(db_url, tmp_path):
    """Live miss: a structure diagram was cut mid-tree at the old 1500-char cap. Long answers must
    arrive whole, split across thread replies."""
    orch, chat, harness, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("murder", "https://github.com/devonpveller/murder")
        eid, chan, root = await orch.router.open_effort("investigate", project="murder")
        orch._gh_transport = _no_branch_remote()
        harness.answer_text = ("HEAD-" + "x" * 5000 + "-TAIL")   # ~5KB answer
        harness.output_queue = ["ans", "NO CHANGES: read-only."]
        await orch.delegate(eid, chan, root, "investigate — read-only", plan_steps=["look"])
        joined = "".join(p["message"] for p in chat.posted)
        assert "HEAD-" in joined and "-TAIL" in joined           # BOTH ends arrived (not chopped)
        assert any("continued" in p["message"] for p in chat.posted)   # chunked into replies
    finally:
        await db.dispose()
