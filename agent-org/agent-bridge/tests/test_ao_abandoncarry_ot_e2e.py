"""ao-abandoncarry - OFFLINE end-to-end replay of the C7f abandon against the REAL worker image
(opt-in; same rig as `test_ao_replancarry_ot_e2e`, whose `pool` fixture and `Worker` it uses).

Skipped unless `AO_E2E_OT_IMAGE` names the worker's open-terminal image and a real Docker engine is
reachable. Two containers `--network none`, labelled `ai-stack.test=$AO_E2E_TEST_LABEL`, removed
at the end; `origin` is a bare repo on a labelled volume. Every bridge /check (the carry, the F33
sync) goes through little-coder's real `OpenTerminalClient.execute` into the image's shell, so every
`git` is the image's git-proxy.

C7f shape: the coding turn on worker-1 leaves its edits uncommitted and comes back `abandoned`
with the daemon's detail `abandoned: exceeded 3600s`; the next turn runs on worker-2 in a fresh
clone and must start from that work, at once.

Run (from agent-org/agent-bridge, Docker reachable, no DOCKER_HOST override):
  PYTHONIOENCODING=utf-8 AO_E2E_TEST_LABEL=ao-abandoncarry \
  AO_E2E_OT_IMAGE=little-coder-open-terminal:dotnet8-gui python -m pytest -v -s \
      tests/test_ao_abandoncarry_ot_e2e.py
"""

from __future__ import annotations

import asyncio
import os
import shlex
import time

import pytest
from sqlalchemy import select

from app.adapters.chat import FakeChatAdapter
from app.config import Settings
from app.db import Database
from app.models import SCHED_COMPUTING, WorkerInstance
from app.modules.model_router import FakeModelClient
from app.orchestrator import Orchestrator
from app.worker.harness import FakeHarness, WorkResult
import test_ao_replancarry_ot_e2e as _rc
from test_ao_replancarry_ot_e2e import (
    CODING_FILE,
    CODING_TEXT,
    IMAGE,
    REPO,
    ROOT,
    W1,
    W2,
    _origin_ref,
)

pool = _rc.pool   # the replancarry e2e's module-scoped fixture: two worker containers + origin

pytestmark = pytest.mark.skipif(not IMAGE, reason="opt-in: set AO_E2E_OT_IMAGE to the worker's "
                                                   "open-terminal image (real Docker needed)")

SRC = "inference/llm-queue/src/queue.py"
SRC_TEXT = "def run():\n    return 2\n"


def _printf(text: str) -> str:
    return shlex.quote(text.replace(chr(10), chr(92) + "n"))


async def _orch(db_url, pool, *, script: list[dict]):
    settings = Settings(
        _env_file=None, chat_adapter="fake",
        profiles_dir=str(ROOT / "profiles"), charters_dir=str(ROOT / "charters"),
        floor_dir=str(ROOT / "floor"), worker_instance_urls=f"{W1},{W2}",
        max_concurrent_workers=2, database_url=db_url, project_survey_enabled=False,
        review_mode="off", plan_approval="off", worker_plan_gate="off",
        worker_flail_guard=True, sync_delivery_branch=True, stall_max_recoveries=2,
    )
    db = Database(db_url)
    harness = FakeHarness()
    orch = Orchestrator(settings, db, FakeChatAdapter(), model_client=FakeModelClient(),
                        harness=harness)
    await orch.setup()
    by_url = {W1: pool[0], W2: pool[1]}
    seen: set[str] = set()
    trace: dict = {"turns": [], "checks": [], "denied_out": []}
    script = list(script)
    orig_set_project, orig_wake = harness.set_project, harness.wake

    async def set_project(base_url, repo, **kw):
        fresh = bool(kw.get("fresh")) or base_url not in seen
        seen.add(base_url)
        by_url[base_url].focus(fresh)
        return await orig_set_project(base_url, repo, **kw)

    async def wake(base_url, session_id, prompt, **kw):
        w = by_url[base_url]
        if "PUBLISH YOUR WORK" in prompt or "NOT PUBLISHED" in prompt or not script:
            return await orig_wake(base_url, session_id, prompt, **kw)
        spec = script.pop(0)
        turn = {"url": base_url, "session": session_id, "t": time.monotonic(),
                "saw": w.files(), "head": w.head(), "carried_note": "CARRIED WORK" in prompt}
        trace["turns"].append(turn)
        for rel, text in (spec.get("write") or {}).items():
            w.turn(f"mkdir -p {os.path.dirname(rel)} && printf {_printf(text)} > {rel}")
        turn["t_end"] = time.monotonic()
        res = WorkResult(spec["status"], task_id=f"t-{len(trace['turns'])}",
                         output=spec.get("output", ""))
        res.detail = spec.get("detail", "")
        return res

    async def run_check(base_url, command, *, cwd=None, timeout=600):
        harness.checks.append({"base_url": base_url, "command": command})
        trace["checks"].append((base_url, command[:60]))
        code, out, timed = by_url[base_url].check(command, timeout=timeout)
        if "git-proxy: DENIED" in out:
            trace["denied_out"].append(out[-300:])
        return code, out, timed

    harness.set_project, harness.wake, harness.run_check = set_project, wake, run_check
    orig = orch.scheduler.acquire

    async def acquire(effort_id, role, session_id, *a, **kw):
        # C7f: keep worker-1 busy for the re-dispatched session so it lands on worker-2
        if "~r1" not in session_id:
            return await orig(effort_id, role, session_id, *a, **kw)
        async with orch.db.session_factory() as s:
            w1 = (await s.execute(select(WorkerInstance).where(
                WorkerInstance.base_url == W1))).scalar_one()
            prev, w1.sched_state = w1.sched_state, SCHED_COMPUTING
            await s.commit()
        try:
            return await orig(effort_id, role, session_id, *a, **kw)
        finally:
            async with orch.db.session_factory() as s:
                w1 = (await s.execute(select(WorkerInstance).where(
                    WorkerInstance.base_url == W1))).scalar_one()
                w1.sched_state = prev
                await s.commit()

    orch.scheduler.acquire = acquire
    return orch, db, trace


async def _run(orch, name: str) -> str:
    await orch.projects.add("ai-stack", REPO)
    eid, chan, root = await orch.router.open_effort(name, project="ai-stack")
    try:
        await orch.delegate(eid, chan, root, "fix the llm-queue connection leak")
        for _ in range(3):
            if orch._bg_tasks:
                await asyncio.gather(*list(orch._bg_tasks))
    finally:
        for t in (orch._capacity_task, orch._stall_task, orch._reaper_task):
            if t is not None:
                t.cancel()
    return eid


async def test_e2e_c7f_abandoned_turn_reaches_the_next_turn_on_worker_2_at_once(db_url, pool):
    orch, db, trace = await _orch(db_url, pool, script=[
        {"status": "abandoned", "detail": "abandoned: exceeded 3600s",
         "write": {CODING_FILE: CODING_TEXT, SRC: SRC_TEXT}},
        {"status": "done", "output": "Done. Verified the carried work; tests pass."},
    ])
    try:
        eid = await _run(orch, "ao-queue-c7f")
        carried = await orch._last_event_payload(eid, "stopped_turn_work_carried")
        red = await orch._last_event_payload(eid, "abandoned_turn_redispatched")
        print(f"\n[e2e c7f] effort={eid} image={IMAGE}\ncarried={carried}\nredispatch={red}\n"
              f"turns={[(t['url'], t['session'], round(t['t'], 1)) for t in trace['turns']]}\n"
              f"checks={trace['checks']}\ndenials={[w.denials() for w in pool]}")
        first, second = trace["turns"][:2]
        assert first["url"] == W1 and second["url"] == W2
        assert second["t"] - first["t_end"] < 30, second["t"] - first["t_end"]
        assert second["saw"].get(CODING_FILE) == CODING_TEXT and second["saw"].get(SRC) == SRC_TEXT
        assert carried and carried["pushed"] and carried["cause"] == "task_timeout"
        assert sorted(carried["files"]) == sorted([CODING_FILE, SRC])
        assert _origin_ref(pool, f"agent/{eid}") == carried["commit"] == second["head"]
        assert red and red["task"] == "t-1" and red["commit"] == carried["commit"]
        assert second["carried_note"]
        assert await orch._event_count(eid, "stall_recovered") == 0
        assert not any("Committed work is intact" in p["message"] for p in orch.chat.posted)
        assert not any(w.denials() for w in pool) and not trace["denied_out"]
    finally:
        await db.dispose()


async def test_e2e_the_watchdogs_carry_runs_on_the_hung_worker_not_a_free_one(db_url, pool):
    """The hung-turn recovery carries from the hung worker (worker-1) through its proxy; worker-2
    gets no command at all from the recovery."""
    orch, db, trace = await _orch(db_url, pool, script=[
        {"status": "done", "output": "Done."}])
    try:
        await orch.projects.add("ai-stack", REPO)
        eid, _c, _r = await orch.router.open_effort("ao-queue-hung", project="ai-stack")
        await orch.charters.set_goal(eid, "fix the llm-queue connection leak", created_by="po")
        await orch.harness.set_project(W1, REPO, fresh=True)   # the coding turn's focus
        pool[0].turn(f"mkdir -p {os.path.dirname(CODING_FILE)} && printf {_printf(CODING_TEXT)} "
                     f"> {CODING_FILE}")
        orch.router._ws_focus["worker-1"] = (eid, REPO, "")
        orch.router._coding_ws[eid] = "worker-1"
        n_before = len(trace["checks"])
        await orch._recover_hung_worker(W1, "t-hung", eid, {"rule": "silent"}, set(), None)
        recovery_checks = [u for u, _c in trace["checks"][n_before:n_before + 1]]
        for _ in range(3):
            if orch._bg_tasks:
                await asyncio.gather(*list(orch._bg_tasks))
        carried = await orch._last_event_payload(eid, "stopped_turn_work_carried")
        print(f"\n[e2e hung] effort={eid} carried={carried} checks={trace['checks']}")
        assert recovery_checks == [W1]
        assert carried and carried["worker"] == "worker-1" and carried["cause"] == "hung"
        assert _origin_ref(pool, f"agent/{eid}") == carried["commit"]
        assert trace["turns"] and trace["turns"][0]["saw"].get(CODING_FILE) == CODING_TEXT
        assert not any(w.denials() for w in pool) and not trace["denied_out"]
    finally:
        for t in (orch._capacity_task, orch._stall_task, orch._reaper_task):
            if t is not None:
                t.cancel()
        await db.dispose()
