"""Trust ladder (operator 2026-07-10: "90% of the time when I test what was claimed, the claim is
false" — because the check compiles, it doesn't RUN the behavior). For a RUNTIME/behavioral goal,
"done" now requires a REPRODUCTION test (fails on the break, passes on the fix, wired into the
check), and the org must have run the check GREEN itself. The closure states EXACTLY what was
proven so the commit history stays honest:
  • repro + org-green            → VERIFIED via reproduction (done; human still merges this phase)
  • build-only, no repro         → NOT verified (stays visible; auto-iterates for a repro)
Generic — keys off the GOAL wording + the worker's REPRO block, no project specifics."""

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
from app.orchestrator import _REPRO_CLAUSE, Orchestrator
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
    return orch, orch.chat, db


def _clean_remote():
    """A healthy remote: a landed PR, a pure-addition diff (a new test file — no removals), and no
    sibling PRs. Enough for _finish_effort's PR-open + disclosure + apply/sibling notes."""
    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        if "/compare/" in p:
            return httpx.Response(200, json={"ahead_by": 2, "behind_by": 0, "commits": [],
                "files": [{"filename": "tests/AtlasLoadTest.cs", "status": "added",
                           "additions": 42, "deletions": 0, "patch": "+atlas load test"}]})
        if "/branches/" in p:
            return httpx.Response(200, json={"commit": {"sha": "headsha123456789000"}})
        if p.endswith("/pulls") and request.method == "POST":
            return httpx.Response(201, json={"number": 21, "html_url": "https://x/pull/21"})
        if p.endswith("/pulls"):
            return httpx.Response(200, json=[])
        if p.count("/") == 3:
            return httpx.Response(200, json={"default_branch": "main"})
        return httpx.Response(404)
    return httpx.MockTransport(handler)


_REPRO_OUTPUT = (
    "Fixed the atlas load path in the editor.\n"
    "REPRO:\n"
    "EXERCISES: loads the 'atlas' atlas the way opening a Game Profile does\n"
    "BEFORE: FAIL - Atlas 'atlas' is not loaded and couldn't be loaded from resources/atlas\n"
    "AFTER: PASS - atlas loaded, 128 sprites, cursor present\n"
    "WIRED: `dotnet test Murder.Editor.Tests --filter AtlasLoad` added to the project check")


async def test_behavioral_goal_with_repro_and_org_green_is_verified(db_url, tmp_path):
    orch, chat, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("app", "https://github.com/devonpveller/app")
        orch._gh_transport = _clean_remote()
        eid, _c, _r = await orch.router.open_effort("editor-atlas", project="app")
        await orch.charters.set_goal(
            eid, "the editor throws this at runtime when clicking Game Profile", created_by="po")
        orch._org_verified[eid] = "headsha123456789000"       # the org ran the check GREEN itself
        delivery = BranchDelivery(verifiable=True, exists=True, ahead=2, files_changed=3,
                                  head_sha="headsha123456789000", branch="agent/editor-atlas")
        res = SimpleNamespace(status="done", output=_REPRO_OUTPUT)
        await orch._finish_effort(eid, res, delivery=delivery)
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "Verified via reproduction" in msgs
        assert "VERIFIED via reproduction" in msgs             # the done_word
        assert "confirmed it passes now" in msgs               # honest about what it did/didn't prove
        async with orch.db.session_factory() as s:
            e = await s.get(Effort, eid)
        assert e.lifecycle == "done"                           # genuinely verified → done (you merge)
    finally:
        await db.dispose()


async def test_behavioral_goal_build_only_is_not_verified_and_stays_open(db_url, tmp_path):
    orch, chat, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("app", "https://github.com/devonpveller/app")
        orch._gh_transport = _clean_remote()
        eid, _c, _r = await orch.router.open_effort("editor-atlas2", project="app")
        await orch.charters.set_goal(
            eid, "the cursor is missing and the editor crashes when I click a menu", created_by="po")
        # org_verified NOT set + NO repro block in the report = build-only
        delivery = BranchDelivery(verifiable=True, exists=True, ahead=2, files_changed=3,
                                  head_sha="h2", branch="agent/editor-atlas2")
        res = SimpleNamespace(status="done", output="Made it compile. Looks fixed to me.")
        await orch._finish_effort(eid, res, delivery=delivery)
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "NOT verified" in msgs
        assert "reproduction test" in msgs.lower()             # demands the repro
        assert "VERIFIED via reproduction" not in msgs
        async with orch.db.session_factory() as s:
            e = await s.get(Effort, eid)
        assert e.lifecycle != "done"                           # unverified symptom stays visible
    finally:
        await db.dispose()


def test_repro_clause_demands_a_failing_test_wired_into_the_check():
    """The worker instruction for a behavioral goal demands the un-gameable proof shape."""
    c = _REPRO_CLAUSE.lower()
    assert "automated test" in c and "fails" in c              # reproduce as a failing test
    assert "wire" in c and "regression" in c                   # folded into the check permanently
    assert "before:" in c and "after:" in c                    # the RED->GREEN evidence block
    assert "never fake" in c or "unautomatable" in c           # honesty on the un-automatable residue
