"""P4.0 reconciliation (operator 2026-07-07): a "port the whole engine" prompt was classified
`cross_effort` → dry-run REQUIRED → the effort dead-ended waiting for a `/dry-run pass` command
nobody runs, so the PM "got nothing done". For a BRANCH-ISOLATED code effort with a runnable
build, the org now performs the rehearsal ITSELF (the isolated agent branch never reaches `main`
without the D4 human merge; the org's own build is the rehearsal) — no operator command. A
genuinely `irreversible` act still waits for a human, but "proceed" (NL) releases even that."""

from __future__ import annotations

import asyncio
from pathlib import Path

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


async def _drain(orch, rounds=12):
    for _ in range(rounds):
        if not orch._bg_tasks:
            return
        await asyncio.gather(*list(orch._bg_tasks), return_exceptions=True)


async def test_cross_effort_code_effort_auto_rehearses_no_command(db_url, tmp_path):
    """cross_effort + repo + a build → the org auto-satisfies the dry-run and DISPATCHES, instead
    of dead-ending on `/dry-run pass`."""
    orch, chat, harness, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("murder", "https://github.com/devonpveller/murder")
        await orch.projects.set_check("murder", "dotnet build Murder.sln")
        eid, chan, root = await orch.router.open_effort("port", project="murder")
        await orch.charters.set_goal(eid, "port the whole thing", created_by="po")
        await orch.exec_gate.set_risk(eid, "cross_effort")          # dry-run REQUIRED
        assert (await orch.exec_gate.status(eid))["dry_run_status"] == "required"
        await orch.delegate(eid, chan, root, "port the whole thing", plan_steps=["work"])
        await _drain(orch)
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "High blast radius" in msgs and "isolated agent branch" in msgs
        assert "held before execution" not in msgs                  # NOT dead-ended
        assert "/dry-run" not in msgs                               # no command demanded
        assert (await orch.exec_gate.status(eid))["dry_run_status"] == "passed"
        assert harness.wakes, "the worker was actually dispatched"
    finally:
        await db.dispose()


async def test_irreversible_still_holds_for_a_human(db_url, tmp_path):
    """An `irreversible` classification is NOT auto-rehearsed — a human should look first."""
    orch, chat, harness, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("murder", "https://github.com/devonpveller/murder")
        await orch.projects.set_check("murder", "dotnet build Murder.sln")
        eid, chan, root = await orch.router.open_effort("danger", project="murder")
        await orch.charters.set_goal(eid, "delete everything", created_by="po")
        await orch.exec_gate.set_risk(eid, "irreversible")
        await orch.delegate(eid, chan, root, "delete everything", plan_steps=["work"])
        await _drain(orch)
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "held before execution" in msgs and "proceed" in msgs.lower()
        assert not harness.wakes, "an irreversible effort must not auto-dispatch"
    finally:
        await db.dispose()


async def test_no_build_check_falls_back_to_human_gate(db_url, tmp_path):
    """cross_effort but NO runnable build → nothing to rehearse with → keep the human gate."""
    orch, chat, harness, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("murder", "https://github.com/devonpveller/murder")   # no check_cmd
        eid, chan, root = await orch.router.open_effort("nocheck", project="murder")
        await orch.charters.set_goal(eid, "broad change", created_by="po")
        await orch.exec_gate.set_risk(eid, "cross_effort")
        await orch.delegate(eid, chan, root, "broad change", plan_steps=["work"])
        await _drain(orch)
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "held before execution" in msgs
        assert not harness.wakes
    finally:
        await db.dispose()


async def test_nl_proceed_releases_the_hold_and_dispatches(db_url, tmp_path):
    """The residual held case is NL-resolvable: "proceed" clears the dry-run and dispatches —
    no `/dry-run` command."""
    orch, chat, harness, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("murder", "https://github.com/devonpveller/murder")
        eid, chan, root = await orch.router.open_effort("await-go", project="murder")
        await orch.charters.set_goal(eid, "the risky thing", created_by="po")
        await orch.exec_gate.set_risk(eid, "irreversible")          # held for a human
        mgmt = await orch.mgmt_channel_id()
        await orch.nl_intake("proceed", mgmt, thread_id="t")
        await _drain(orch)
        assert (await orch.exec_gate.status(eid))["dry_run_status"] == "passed"
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "Go-ahead received" in msgs
        assert harness.wakes, "proceed must actually dispatch the held effort"
    finally:
        await db.dispose()


async def test_bare_proceed_without_held_effort_is_not_stolen(db_url, tmp_path):
    """"proceed" with nothing held falls through to normal intake (doesn't hijack the turn)."""
    orch, chat, harness, db = await _orch(db_url, tmp_path)
    try:
        handled = await orch._nl_proceed_execution("proceed", await orch.mgmt_channel_id(), "t")
        assert handled is False
    finally:
        await db.dispose()
