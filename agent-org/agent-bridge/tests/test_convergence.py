"""Error-report CONVERGENCE (live 2026-07-05: the operator pasted the same `Game.OnExiting` build
error after every attempt — four deliveries, zero resolution). Three generic mechanisms make the
series converge through the org instead of through the operator's patience:

1. REQUIRED VERIFICATION — an error-report goal demands reproduce → fix → re-run → confirm the
   pasted errors are GONE before publishing (nobody but the operator had ever run the build).
2. PRIOR ATTEMPTS — a re-reported error carries the earlier efforts' branches + verified outcomes
   into the goal, so the next worker builds on (or consciously diverges from) what exists.
3. AUTO-WIRING — an intake-born delivery on a VENDORED project bumps the host's gitlink to the
   verified commit + opens the paired wiring PR (planner-path parity): the fix cannot reach the
   host build otherwise. Plan-owned efforts are excluded (no double-bump)."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import httpx

from app.adapters.chat import FakeChatAdapter
from app.config import Settings
from app.db import Database
from app.modules.model_router import FakeModelClient
from app.orchestrator import Orchestrator
from app.schemas import ReadinessVerdict
from app.worker.harness import FakeHarness

ROOT = Path(__file__).resolve().parents[1]

ERR = "'Game.OnExiting(object, EventArgs)': no suitable method found to override"


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


def _stack_remote(bumped: dict):
    """murder (vendored) + Engine (host) remotes: healthy branches/compares, engine .gitmodules
    vendoring murder, and the Git Data API for the gitlink bump (recorded into `bumped`)."""
    gitmodules = base64.b64encode(
        b'[submodule "vendor/murder"]\n\tpath = vendor/murder\n'
        b'\turl = https://github.com/devonpveller/murder\n').decode()

    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        if p.endswith("/contents/.gitmodules"):
            return httpx.Response(200, json={"content": gitmodules})
        if p.endswith("/contents"):
            return httpx.Response(200, json=[{"name": "vendor", "type": "dir"}])
        if "/compare/" in p:
            return httpx.Response(200, json={
                "ahead_by": 2, "behind_by": 0,
                "commits": [{"commit": {"message": "fix override"}}],
                "files": [{"filename": "src/Murder/Game.cs", "additions": 3, "deletions": 3}]})
        if "/contents/src/Murder/Game.cs" in p:
            return httpx.Response(200, json={"type": "file", "sha": "aa"})
        if "/branches/" in p:
            return httpx.Response(200, json={"commit": {"sha": "abc123def456789000000000"}})
        if p.endswith("/git/ref/heads/main"):
            return httpx.Response(200, json={"object": {"sha": "eng_base"}})
        if p.endswith("/git/commits/eng_base"):
            return httpx.Response(200, json={"tree": {"sha": "eng_tree"}})
        if p.endswith("/git/trees") and request.method == "POST":
            bumped["tree"] = json.loads(request.content)
            return httpx.Response(201, json={"sha": "eng_newtree"})
        if p.endswith("/git/commits") and request.method == "POST":
            return httpx.Response(201, json={"sha": "eng_newcommit"})
        if p.endswith("/git/refs") and request.method == "POST":
            bumped["ref"] = json.loads(request.content)
            return httpx.Response(201, json={})
        if p.endswith("/pulls") and request.method == "POST":
            n = 7 if "/murder/" in p else 8
            return httpx.Response(201, json={"number": n, "html_url": f"https://x/pull/{n}"})
        if p.endswith("/pulls") and request.method == "GET":
            return httpx.Response(200, json=[])
        if p.count("/") == 3:
            return httpx.Response(200, json={"default_branch": "main"})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


async def test_error_report_goal_gets_verification_and_attempt_history(db_url, tmp_path):
    orch, chat, harness, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("murder", "https://github.com/devonpveller/murder")
        orch._gh_transport = _stack_remote({})
        # a PRIOR attempt at the same error, with a delivered (unmerged) branch on the remote
        prior, chan0, _r0 = await orch.router.open_effort("fix-murder-build-errors",
                                                          project="murder")
        await orch.charters.set_goal(prior, f"when building Murder.sln:\n{ERR}\nfix it",
                                     created_by="po")
        # the RE-REPORT: same pasted error, fresh effort
        eid, chan, root = await orch.router.open_effort("fix-onexiting-again", project="murder")
        orch.models._client.queue_structured(
            ReadinessVerdict(clear_and_safe=True, blast_radius="routine"))
        await orch._intake_or_dispatch(
            eid, chan, root, f"same errors again:\n{ERR}\nMetadata file 'x' could not be found",
            reply_prefix="", mgmt_channel=chan)
        _, goal, _ = await orch.charters.current_goal(eid)
        assert "REQUIRED VERIFICATION" in goal, "no repro→fix→re-verify contract in the goal"
        assert "PRIOR ATTEMPTS" in goal, "the re-report carries no attempt history"
        assert f"agent/{prior}" in goal and "UNMERGED" in goal, \
            "history must name the prior branch + its verified outcome"
        assert "BUILD ON IT" in goal
    finally:
        await db.dispose()


async def test_intake_delivery_on_vendored_project_auto_wires_host(db_url, tmp_path):
    orch, chat, harness, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("monogame-engine", "https://github.com/devonpveller/Engine")
        await orch.projects.add("murder", "https://github.com/devonpveller/murder")
        bumped: dict = {}
        orch._gh_transport = _stack_remote(bumped)
        eid, chan, root = await orch.router.open_effort("fix-sig", project="murder")
        await orch.delegate(eid, chan, root, "fix the override", plan_steps=["work"])
        assert bumped.get("tree"), "the host gitlink was never bumped"
        assert bumped["tree"]["tree"][0]["path"] == "vendor/murder"
        assert bumped["tree"]["tree"][0]["sha"] == "abc123def456789000000000"
        assert bumped["ref"]["ref"] == f"refs/heads/agent/{eid}"
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "Wiring half" in msgs and "Merge BOTH halves" in msgs
    finally:
        await db.dispose()


async def test_plan_owned_effort_is_not_double_wired(db_url, tmp_path):
    orch, chat, harness, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("monogame-engine", "https://github.com/devonpveller/Engine")
        await orch.projects.add("murder", "https://github.com/devonpveller/murder")
        bumped: dict = {}
        orch._gh_transport = _stack_remote(bumped)
        eid, chan, root = await orch.router.open_effort("plan-owned", project="murder")
        orch._composition_managed.add(eid)          # a lifecycle plan owns the wiring
        try:
            await orch.delegate(eid, chan, root, "fix the override", plan_steps=["work"])
        finally:
            orch._composition_managed.discard(eid)
        assert not bumped.get("tree"), "intake auto-wiring must not double-bump a plan-owned effort"
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "Wiring half" not in msgs
    finally:
        await db.dispose()
