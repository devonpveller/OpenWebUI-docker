"""No silent removals (operator 2026-07-09): a burn-down round DELETED a whole feature file
(MouseCursor.Sdl.cs, 181 lines) to clear SDL3 compile errors, and the org green-passed + merged
it without ever telling the operator — who only discovered it via a broken cursor and asked
"what other arbitrary removals happened just to get green?". A passing build never proves
functionality was preserved. Every delivery now DISCLOSES what it removed; a removal on a
fix/port goal (not a cleanup goal) is surfaced for review, not silently 'done'. Generic — any
project, any language."""

from __future__ import annotations

from pathlib import Path

import httpx

from app.adapters.chat import FakeChatAdapter
from app.config import Settings
from app.db import Database
from app.modules.model_router import FakeModelClient
from app.worker.harness import FakeHarness
from app.orchestrator import Orchestrator

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


def _remote_with_files(files: list[dict]):
    """A compare endpoint returning the given file entries (status/additions/deletions/patch)."""
    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        if "/compare/" in p:
            return httpx.Response(200, json={"ahead_by": 1, "behind_by": 0, "commits": [],
                                             "files": files})
        if "/branches/" in p:
            return httpx.Response(200, json={"commit": {"sha": "headsha1234567890"}})
        if p.endswith("/pulls") and request.method == "POST":
            return httpx.Response(201, json={"number": 1, "html_url": "https://x/pull/1"})
        if p.endswith("/pulls"):
            return httpx.Response(200, json=[])
        if p.count("/") == 3:
            return httpx.Response(200, json={"default_branch": "main"})
        return httpx.Response(404)
    return httpx.MockTransport(handler)


async def test_deleted_file_on_a_fix_goal_is_disclosed_and_flagged(db_url, tmp_path):
    orch, chat, harness, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("murder", "https://github.com/devonpveller/murder")
        orch._gh_transport = _remote_with_files([
            {"filename": "src/Murder.Editor/Core/Cursor/MouseCursor.Sdl.cs", "status": "removed",
             "additions": 0, "deletions": 181, "patch": "-public partial class MouseCursor {"},
            {"filename": "src/x.cs", "status": "modified", "additions": 3, "deletions": 2,
             "patch": "+ok"},
        ])
        eid, _c, _r = await orch.router.open_effort("port", project="murder")
        note, flag = await orch._removal_disclosure(
            eid, "agent/port", "fix the editor to launch under MonoGame")
        assert flag is True                                    # a fix goal that deletes → review
        assert "Removal review" in note
        assert "MouseCursor.Sdl.cs" in note
        assert "does NOT prove" in note                        # the honest caveat
    finally:
        await db.dispose()


async def test_removal_on_a_cleanup_goal_is_disclosed_but_not_flagged(db_url, tmp_path):
    orch, chat, harness, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("murder", "https://github.com/devonpveller/murder")
        orch._gh_transport = _remote_with_files([
            {"filename": "src/Dead.cs", "status": "removed", "additions": 0, "deletions": 90,
             "patch": "-x"},
        ])
        eid, _c, _r = await orch.router.open_effort("cleanup", project="murder")
        note, flag = await orch._removal_disclosure(
            eid, "agent/cleanup", "remove the dead legacy code paths")
        assert flag is False                                   # the operator ASKED to remove
        assert "as intended" in note and "Dead.cs" in note     # still disclosed
    finally:
        await db.dispose()


async def test_pure_additions_no_removal_note(db_url, tmp_path):
    orch, chat, harness, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("murder", "https://github.com/devonpveller/murder")
        orch._gh_transport = _remote_with_files([
            {"filename": "src/New.cs", "status": "added", "additions": 120, "deletions": 0,
             "patch": "+lots"},
        ])
        eid, _c, _r = await orch.router.open_effort("add", project="murder")
        note, flag = await orch._removal_disclosure(eid, "agent/add", "add a feature")
        assert note == "" and flag is False
    finally:
        await db.dispose()


async def test_gutted_method_body_flags_even_without_a_deleted_file(db_url, tmp_path):
    orch, chat, harness, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("murder", "https://github.com/devonpveller/murder")
        orch._gh_transport = _remote_with_files([
            {"filename": "src/Feature.cs", "status": "modified", "additions": 2, "deletions": 40,
             "patch": "-    void DoTheThing() {\n-      ...40 lines...\n+    void DoTheThing() {}"},
        ])
        eid, _c, _r = await orch.router.open_effort("port2", project="murder")
        note, flag = await orch._removal_disclosure(eid, "agent/port2", "port the feature")
        assert flag is True and "gutted" in note
    finally:
        await db.dispose()


def test_removal_summary_parses_compare(db_url=None, tmp_path=None):
    # unit: the capability itself, no orchestrator
    from app.modules.capabilities import read_removal_summary
    import asyncio as _a

    class _G:
        owner = "devonpveller"
        async def installation_token(self): return "t"
    files = [
        {"filename": "a/Gone.cs", "status": "removed", "additions": 0, "deletions": 50,
         "patch": "-public class Gone {"},
        {"filename": "a/Keep.cs", "status": "modified", "additions": 10, "deletions": 3,
         "patch": "+added\n-  private void Helper() {"},
    ]
    tr = _remote_with_files(files)
    out = _a.run(read_removal_summary(_G(), "https://github.com/devonpveller/murder",
                                      "agent/x", transport=tr))
    assert out["deleted_files"] == ["a/Gone.cs"]
    assert out["deletions"] == 53 and out["insertions"] == 10
    assert any("Gone" in s for s in out["removed_symbols"])
