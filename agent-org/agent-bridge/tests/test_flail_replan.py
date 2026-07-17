"""FLAIL GUARD -> fork -> plan-first re-ask (operator 2026-07-14: "too many thinking turns or
time iterating on read without editing anything is a good indicator to stop, fork from original
user prompt and re-ask in plan mode"). The daemon kills a read-without-edit coding turn with a
FLAIL-GUARD answer marker; the bridge then: records `flail_replanned` (which bumps the session
generation = the FORK — the flailing context is never re-entered), forces the plan gate on the
re-dispatch regardless of mode, and re-runs from the ORIGINAL goal. Once per effort; a second
flail escalates honestly. Fakes only."""

from __future__ import annotations

import asyncio
from pathlib import Path

from app.adapters.chat import FakeChatAdapter
from app.config import Settings
from app.db import Database
from app.modules.model_router import FakeModelClient
from app.orchestrator import Orchestrator
from app.worker.harness import FakeHarness

ROOT = Path(__file__).resolve().parents[1]

_FLAIL_OUT = ("FLAIL-GUARD: stopped the turn — 25 read-only tool calls with zero file edits. "
              "The approach wasn't converging; a fresh plan is needed.")


async def _orch(db_url, *, plan_gate="off", flail_guard=True):
    settings = Settings(
        _env_file=None, chat_adapter="fake",
        profiles_dir=str(ROOT / "profiles"), charters_dir=str(ROOT / "charters"),
        floor_dir=str(ROOT / "floor"), worker_instance_urls="http://w1:8090",
        max_concurrent_workers=1, database_url=db_url, project_survey_enabled=False,
        review_mode="off", plan_approval="off", worker_plan_gate=plan_gate,
        worker_flail_guard=flail_guard,
    )
    db = Database(db_url)
    orch = Orchestrator(settings, db, FakeChatAdapter(),
                        model_client=FakeModelClient(), harness=FakeHarness())
    await orch.setup()
    return orch, orch.chat, orch.harness, db


async def _drain(orch, rounds: int = 3):
    for _ in range(rounds):
        if orch._bg_tasks:
            await asyncio.gather(*list(orch._bg_tasks))


async def _shutdown(orch, db):
    """Cancel the loops setup() started before disposing the DB (a late tick against a disposed
    DB throws in aiosqlite's worker thread and lands on whatever test runs next)."""
    await _drain(orch)
    for t in (orch._capacity_task, orch._stall_task, orch._reaper_task):
        if t is not None:
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
    await db.dispose()


# -- P9 Phase 0: the guard is a MEASURABLE variable, not a constant ------------
async def test_the_guard_can_be_disarmed_so_the_fork_can_be_measured(db_url):
    """P9's quality thesis says the worker needs a coherent MODEL of the code, and the fork is the
    one mechanism that throws that model away mid-effort. It is a suspect in the P8 regression, so
    it has to be switchable: disarmed, no coding turn carries the guard, the daemon never kills,
    and the effort keeps ONE session end-to-end. This knob exists to measure the fork's effect on
    product quality — not to fix anything."""
    orch, chat, harness, db = await _orch(db_url, flail_guard=False)
    try:
        await orch.projects.add("app", "https://github.com/acme/app.git")
        eid, chan, root = await orch.router.open_effort("spin", project="app")
        await orch.delegate(eid, chan, root, "port the parser to the new API")
        await _drain(orch)
        coding = [w for w in harness.wakes if not w.get("plan_only")]
        assert coding, "the effort must still dispatch coding turns"
        assert all(w["flail_guard"] is False for w in coding), "disarmed = no turn is guarded"
        # no kill => no fork => the model of the code survives the whole effort
        assert {w["session_id"] for w in harness.wakes} == {eid}, "no session rotation without a flail"
    finally:
        await db.dispose()


# -- the full loop: flail -> fork (fresh session) -> forced plan -> execute ------
async def test_flail_forks_a_fresh_session_and_replans_even_with_gate_off(db_url):
    orch, chat, harness, db = await _orch(db_url, plan_gate="off")
    try:
        await orch.projects.add("app", "https://github.com/acme/app.git")
        eid, chan, root = await orch.router.open_effort("spin", project="app")
        harness.output_queue.append(_FLAIL_OUT)     # the first coding turn gets killed flailing
        await orch.delegate(eid, chan, root, "port the parser to the new API")
        await _drain(orch)                          # the queued re-dispatch runs
        # the original coding turn was ARMED with the guard
        assert harness.wakes[0]["flail_guard"] is True
        assert harness.wakes[0]["session_id"] == eid            # generation 0
        # the re-dispatch went through the plan gate DESPITE mode=off (forced one-shot) ...
        assert harness.wakes[1]["plan_only"] is True
        assert "PLAN FIRST" in harness.wakes[1]["prompt"]
        assert "port the parser" in harness.wakes[1]["prompt"]  # the ORIGINAL goal
        assert harness.wakes[1]["flail_guard"] is False         # plan turns are never guarded
        # ... in a FRESH session (the fork — generation bumped by the flail event)
        assert harness.wakes[1]["session_id"] == f"{eid}~r1"
        # then executed (plan approved via the fail-open lens; no model queued)
        assert harness.wakes[2]["plan_only"] is False
        assert "REVIEWED and APPROVED" in harness.wakes[2]["prompt"]
        assert await orch._event_count(eid, "flail_replanned") == 1
        assert any("forking a fresh session" in p["message"] for p in chat.posted)
    finally:
        await _shutdown(orch, db)


# -- ABORTED IS FINAL: no machine loop may dispatch or resurrect an archive ------
async def test_machine_loops_never_dispatch_an_archived_effort(db_url):
    """2026-07-14 live zombie: the operator archived a mis-routed effort, but its queued
    flail-replan re-dispatch ran anyway, the burn-down REOPENED it, and a ghost campaign ground
    rounds on a wrong branch for an hour while the PM narrated nothing useful. Aborted =
    machine-final; only the operator's own re-run path may bring an effort back."""
    orch, chat, harness, db = await _orch(db_url)
    try:
        await orch.projects.add("app", "https://github.com/acme/app.git")
        eid, chan, root = await orch.router.open_effort("ghost", project="app")
        await orch.gate.set_lifecycle(eid, "aborted")
        await orch.delegate(eid, chan, root, "do the thing")      # queued machine re-entry
        assert len(harness.wakes) == 0                            # refused, no worker touched
        assert await orch._event_count(eid, "aborted_dispatch_suppressed") >= 1
        await orch._burndown_loop(eid, "error CS0001: boom")      # zombie burn-down attempt
        assert len(harness.wakes) == 0
        await orch._reopen_if_closed(eid)                         # machine reopen path
        assert await orch._is_aborted(eid) is True                # still archived
    finally:
        await _shutdown(orch, db)


# -- the gym 'ouroboros' quartet (2026-07-15): abort wins, config is not work ----
async def test_machine_done_never_overwrites_an_operator_abort(db_url):
    orch, chat, harness, db = await _orch(db_url)
    try:
        await orch.projects.add("app", "https://github.com/acme/app.git")
        eid, _c, _r = await orch.router.open_effort("racer", project="app")
        await orch.gate.set_lifecycle(eid, "aborted")
        await orch.gate.set_lifecycle(eid, "done")            # the in-flight finish's stamp
        assert await orch._is_aborted(eid) is True            # abort won the race
        assert await orch._event_count(eid, "aborted_finish_suppressed") == 1
        await orch.gate.set_lifecycle(eid, "open")            # operator re-run path still works
        assert await orch._is_aborted(eid) is False
    finally:
        await _shutdown(orch, db)


async def test_abort_without_a_concern_falls_back_to_archive(db_url):
    orch, chat, harness, db = await _orch(db_url)
    try:
        await orch.projects.add("app", "https://github.com/acme/app.git")
        eid, _c, _r = await orch.router.open_effort("plain", project="app")
        mgmt = await orch.mgmt_channel_id()
        await orch.nl_intake(f"abort {eid}", mgmt, user_id="operator-api")
        assert await orch._is_aborted(eid) is True            # archived, not dead-ended
        assert any("archiving it instead" in p["message"] or "Archived" in p["message"]
                   for p in chat.posted)
    finally:
        await _shutdown(orch, db)


async def test_pure_standing_intent_message_is_config_not_work(db_url):
    orch, chat, harness, db = await _orch(db_url)
    try:
        await orch.projects.add("gym", "https://github.com/acme/gym.git")
        mgmt = await orch.mgmt_channel_id()
        await orch.nl_intake(
            "in gym, set the standing intent: tests are never weakened; never use `NuGet` here.",
            mgmt, user_id="operator-api")
        await _drain(orch)
        assert len(harness.wakes) == 0                        # NO effort dispatched
        p = await orch.projects.get("gym")
        assert "never weakened" in (p.get("standing_intent") or "")
        # the setter echoes its blast radius (the `harness` foot-gun class)
        assert any("Forbidden term" in m["message"] and "`NuGet`" in m["message"]
                   for m in chat.posted)
    finally:
        await _shutdown(orch, db)


# -- gym findings ⑤+⑥ (2026-07-15): explicit efforts dispatch; checks are org-run -
async def test_start_effort_idiom_is_deterministic_even_with_hygiene_bait(db_url):
    """Finding ⑤: 'start effort gym-003…' whose goal mentioned branches was captured whole by
    branch hygiene and never dispatched. The explicit idiom now opens + intakes directly."""
    orch, chat, harness, db = await _orch(db_url)
    try:
        await orch.projects.add("gym", "https://github.com/acme/gym.git")
        mgmt = await orch.mgmt_channel_id()
        from app.schemas import ReadinessVerdict
        orch.models._client.queue_structured(
            ReadinessVerdict(clear_and_safe=True, blast_radius="routine"))
        await orch.nl_intake(
            "in gym, start effort gym-007-cleanup: tidy the module layout; do not build on any "
            "other agent branch or leftover branches in the workspace.",
            mgmt, user_id="operator-api")
        await _drain(orch)
        assert await orch._is_aborted("effort-gym-007-cleanup") is False   # row exists, open
        assert len(harness.wakes) >= 1                                     # actually dispatched
        assert not any("Branch inventory" in p["message"] for p in chat.posted)
    finally:
        await _shutdown(orch, db)


async def test_d2_check_is_org_run_when_deterministic_route_works(db_url):
    """Finding ⑥: every gym D2 closed 'worker-reported' — the check's subject was also its
    executor. With a branch+repo, _run_check now execs deterministically on a verifier slot."""
    orch, chat, harness, db = await _orch(db_url)
    try:
        await orch.projects.add("gym", "https://github.com/acme/gym.git")
        eid, _c, _r = await orch.router.open_effort("checked", project="gym")
        harness.check_queue.append((0, "OK 5 tests", False))
        status, tail, prov = await orch._run_check(
            eid, "python3 -m unittest discover", branch="agent/x",
            repo="https://github.com/acme/gym.git")
        assert (status, prov) == ("pass", "org-run")
        harness.check_queue.append((1, "FAILED (failures=1)", False))
        status, tail, prov = await orch._run_check(
            eid, "python3 -m unittest discover", branch="agent/x",
            repo="https://github.com/acme/gym.git")
        assert (status, prov) == ("fail", "org-run") and "FAILED" in tail
        # deterministic route unavailable (empty check queue raises) -> honest fallback
        harness.output_queue.append("CHECK: PASS")
        status, tail, prov = await orch._run_check(
            eid, "python3 -m unittest discover", branch="agent/x",
            repo="https://github.com/acme/gym.git")
        assert (status, prov) == ("pass", "worker-reported")
    finally:
        await _shutdown(orch, db)


# -- bounded: a second flail is a can't-converge signal for the human ------------
async def test_second_flail_escalates_instead_of_looping(db_url):
    orch, chat, harness, db = await _orch(db_url)
    try:
        await orch.projects.add("app", "https://github.com/acme/app.git")
        eid, chan, root = await orch.router.open_effort("stuck", project="app")
        await orch.audit.log("flail_replanned", effort_id=eid, payload={})
        harness.output_queue.append(_FLAIL_OUT)
        await orch.delegate(eid, chan, root, "port the parser")
        await _drain(orch)
        assert len(harness.wakes) == 1                          # no re-dispatch
        assert any("flailed again" in p["message"] for p in chat.posted)
    finally:
        await _shutdown(orch, db)
