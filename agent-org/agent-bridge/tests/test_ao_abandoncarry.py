"""ao-abandoncarry - a coding turn that ends any way other than success keeps its work.

C7f D10 run (bake-off 2026-10-16, `20261016T150235Z-C7f-ao-queue-r1-e2af`, `c7/audit-related.jsonl`):
the first coding turn ran on worker-1 for 3,600 s and was killed by little-coder's per-task
`abandoned_timeout_seconds.batch` (`wake_done` status `abandoned`, `worker_turn_abandoned`, ev 15-16)
with all of its edits uncommitted. The bridge escalated it as a worker failure and nothing ran
until the stall watchdog re-engaged the effort 1,049 s later (`stall_recovered` idle_min 17,
ev 18). That recovery first ran its "discard uncommitted edits" command on worker-2 - a pool
worker that happened to be free (ev 19, `~clean`) - and posted "Committed work is intact" although
nothing had been committed. The work survived only because the re-engaged turn landed on worker-1
again. ao-replancarry's carry runs only on a flail or loop stop.

The fix: every coding turn that ends without success and without a flail/loop stop is classified
(task timeout, worker shutdown, poll timeout, cancel, rejection, error, backpressure), its
uncommitted work is carried to `agent/<effort>` exactly like a flail stop (pinned to its worker,
wait-quiet, WIP commit, push, WIP-only bookkeeping, audit), and a timed-out turn is re-dispatched
AT ONCE (bounded, once per abandoned task) with a note naming the carried commit. The stall
watchdog carries from the effort's own worker instead of discarding on a random one, and says only
what is true.

Fakes only (one REAL git checkout per fake worker, a local `origin`, every git call of the carry
and the sync through the worker's real `git_proxy.classify`); no daemon, container, GPU or network.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from app.adapters.chat import FakeChatAdapter
from app.config import Settings
from app.db import Database
from app.models import SCHED_COMPUTING, SCHED_IDLE, Event, WorkerInstance
from app.modules.model_router import FakeModelClient
from app.orchestrator import Orchestrator
from app.worker.harness import FakeHarness, WorkResult
from test_ao_plancheck import (
    CODING_FILE,
    CODING_TEXT,
    REPO,
    ROOT,
    W1,
    W2,
    _drain,
    _drop_short_roots,  # noqa: F401 - autouse fixture for _workspaces
    _git,
    _run_python_check,
    _shutdown,
    _workspaces,
)
from test_ao_replancarry import (
    _keep_worker_1_off_the_new_session,
    _no_denials,
    _origin_branches,
    _origin_file,
    _run_sync,
)

TIMEOUT_DETAIL = "abandoned: exceeded 3600s"
SRC = "inference/llm-queue/src/queue.py"
SRC_EDIT = "def run():\n    return 2   # the shielded release, turn 1\n"
_EXEC_OUT = "Done. The carried work was already in the tree; tests pass."
_INTACT = "Committed work is intact"


def _result(spec: dict, n: int) -> WorkResult:
    res = WorkResult(spec.get("status", "done"), task_id=spec.get("task", f"task-{n}"),
                     output=spec.get("output", ""))
    res.detail = spec.get("detail", "")
    return res


async def _orch(db_url, tmp: Path, script: list[dict], *, sync: bool = True,
                max_recoveries: int = 2):
    """An orchestrator over two fake workers with real checkouts. `script` = one spec per CODING
    wake, in order: `write` (files the turn leaves uncommitted), `status`, `detail`, `output`,
    `task`, `during` (an async callable run inside the turn), `raise_` (an exception raised
    after the writes - a worker lost mid-turn). Publish wakes and anything past the script answer
    `_EXEC_OUT`/the fake's default."""
    settings = Settings(
        _env_file=None, chat_adapter="fake",
        profiles_dir=str(ROOT / "profiles"), charters_dir=str(ROOT / "charters"),
        floor_dir=str(ROOT / "floor"), worker_instance_urls=f"{W1},{W2}",
        max_concurrent_workers=2, database_url=db_url, project_survey_enabled=False,
        review_mode="off", plan_approval="off", worker_plan_gate="off",
        worker_flail_guard=True, sync_delivery_branch=sync,
        stall_threshold_s=900, stall_max_recoveries=max_recoveries,
    )
    db = Database(db_url)
    harness = FakeHarness()
    orch = Orchestrator(settings, db, FakeChatAdapter(), model_client=FakeModelClient(),
                        harness=harness)
    await orch.setup()
    orch._carry_slot_pause = 0.05          # the hung-path carry waits for the slot in short steps
    ws = _workspaces(tmp)
    trace: dict = {"turns": [], "checks": [], "publish": 0, "stranded": []}
    script = list(script)

    orig_set_project = harness.set_project

    async def set_project(base_url, repo, **kw):
        ws.focus(base_url, bool(kw.get("fresh")))
        return await orig_set_project(base_url, repo, **kw)

    orig_wake = harness.wake

    async def wake(base_url, session_id, prompt, **kw):
        d = ws.dirs[base_url]
        is_publish = "PUBLISH YOUR WORK" in prompt or "NOT PUBLISHED" in prompt
        if is_publish:
            trace["publish"] += 1
            if not (script and script[0].get("publish")):
                return await orig_wake(base_url, session_id, prompt, **kw)
        if not script:
            harness.output_queue.insert(0, _EXEC_OUT)
            return await orig_wake(base_url, session_id, prompt, **kw)
        spec = script.pop(0)
        turn = {"url": base_url, "session": session_id, "prompt": prompt, "t": time.monotonic(),
                "saw": ws.files(base_url), "head": _git(d, "rev-parse", "HEAD").strip()}
        trace["turns"].append(turn)
        harness.wakes.append({"base_url": base_url, "session_id": session_id, "prompt": prompt,
                              "plan_only": kw.get("plan_only", False)})
        for rel, text in (spec.get("write") or {}).items():
            (d / rel).parent.mkdir(parents=True, exist_ok=True)
            (d / rel).write_text(text)
        if spec.get("during"):
            await spec["during"]()
        turn["t_end"] = time.monotonic()
        if spec.get("raise_"):
            raise spec["raise_"]
        return _result(spec, len(trace["turns"]))

    async def run_check(base_url, command, *, cwd=None, timeout=600):
        harness.checks.append({"base_url": base_url, "command": command})
        trace["checks"].append((base_url, command))
        d = ws.dirs.get(base_url)
        if d is None:
            raise RuntimeError("fake /check: worker not focused")
        if "b64decode" in command:
            return _run_python_check(d, command)
        if command.startswith("cd /workspace && git fetch origin "):
            return _run_sync(d, command)
        if "PM_PUBLISH_DONE" in command:            # the PM's stranded-work salvage (host)
            trace["stranded"].append((base_url, command))
            return 0, "PM_PUBLISH_DONE", False
        if "git checkout -- ." in command and "DISCARD-DONE" in command:   # P16's discard
            st = _git(d, "status", "--porcelain")
            _git(d, "checkout", "--", ".")
            _git(d, "clean", "-fd")
            return 0, st + "DISCARD-DONE", False
        raise RuntimeError(f"fake /check: unexpected command {command[:80]!r}")

    harness.set_project = set_project
    harness.wake = wake
    harness.run_check = run_check
    return orch, harness, db, ws, trace


async def _run(orch, name: str = "ao-queue"):
    await orch.projects.add("ai-stack", REPO)
    eid, chan, root = await orch.router.open_effort(name, project="ai-stack")
    await orch.delegate(eid, chan, root, "fix the llm-queue connection leak")
    await _drain(orch)
    return eid


def _posts(orch) -> list[str]:
    return [p["message"] for p in orch.chat.posted]


def _no_false_intact(orch) -> None:
    bad = [m for m in _posts(orch) if _INTACT in m]
    assert not bad, f"claimed committed work that nobody committed: {bad}"


async def _kinds(orch, eid: str) -> list[str]:
    async with orch.db.session_factory() as s:
        return [r[0] for r in (await s.execute(select(Event.kind).where(
            Event.effort_id == eid).order_by(Event.id))).all()]


async def _payloads(orch, eid: str, kind: str) -> list[dict]:
    async with orch.db.session_factory() as s:
        rows = (await s.execute(select(Event).where(
            Event.effort_id == eid, Event.kind == kind).order_by(Event.id))).scalars().all()
    return [r.payload or {} for r in rows]


def _checks_on(trace, url: str) -> list[str]:
    return [c for u, c in trace["checks"] if u == url]


# == 1. RED/GREEN: the C7f sequence ===============================================================
async def test_c7f_an_abandoned_turns_work_reaches_the_next_turn_on_another_worker_at_once(
        db_url, tmp_path):
    """Turn 1 on worker-1 writes the test file and the src edit, then the daemon's 3,600-s task
    timeout abandons it. The next turn - on worker-2, a fresh clone - must start from that work
    within seconds, with no stall-watchdog wait, and every message must be true."""
    orch, harness, db, ws, trace = await _orch(db_url, tmp_path, [
        {"status": "abandoned", "detail": TIMEOUT_DETAIL,
         "write": {CODING_FILE: CODING_TEXT, SRC: SRC_EDIT}},
        {"status": "done", "output": _EXEC_OUT},
    ])
    try:
        await _keep_worker_1_off_the_new_session(orch)
        eid = await _run(orch)
        turns = trace["turns"]
        assert len(turns) >= 2, ("nothing ran after the abandon - the effort waits for the stall "
                                 "watchdog (C7f: 1,049 s)")
        first, second = turns[0], turns[1]
        assert first["url"] == W1 and second["url"] == W2, (first["url"], second["url"])
        # F6: ordered by events, not only by wall time - the re-dispatch is recorded before the
        # next turn's acquire, with nothing from the watchdog in between; the wall bound is
        # generous (a loaded host), the 1,049-s C7f wait is what it rules out.
        assert second["t"] - first["t_end"] < 60, "the next turn did not start at once"
        order = await _kinds(orch, eid)
        i_red = order.index("abandoned_turn_redispatched")
        assert "worker_acquire" in order[i_red:], order
        assert "stall_recovered" not in order, order
        # THE BAR: the next turn's workspace holds the abandoned turn's work
        assert second["saw"].get(CODING_FILE) == CODING_TEXT, sorted(second["saw"])
        assert second["saw"].get(SRC) == SRC_EDIT
        carried = await orch._last_event_payload(eid, "stopped_turn_work_carried")
        assert carried is not None, "the abandoned turn's work was not carried"
        assert carried["worker"] == "worker-1" and carried["cause"] == "task_timeout"
        assert sorted(carried["files"]) == sorted([CODING_FILE, SRC]) and carried["pushed"]
        assert second["head"] == carried["commit"]
        branch = f"agent/{eid}"
        assert _origin_branches(ws)[branch] == carried["commit"]
        assert _origin_branches(ws)["main"] == ws.base
        assert _origin_file(ws, branch, SRC) == SRC_EDIT
        # recorded why, once
        red = await _payloads(orch, eid, "abandoned_turn_redispatched")
        assert len(red) == 1, red
        assert red[0]["task"] == "task-1" and red[0]["cause"] == "task_timeout"
        assert red[0]["worker"] == "worker-1" and red[0]["commit"] == carried["commit"]
        assert await orch._event_count(eid, "stall_recovered") == 0
        # the WIP head is bookkept as carried WIP (never a delivery on its own)
        assert carried["commit"] in orch._carried_heads.get(eid, set())
        # the next turn is told what it starts from
        assert "CARRIED WORK" in second["prompt"] and carried["commit"][:12] in second["prompt"]
        # truthful messages
        _no_false_intact(orch)
        said = [m for m in _posts(orch) if "time limit" in m]
        assert said, _posts(orch)
        assert carried["commit"][:12] in said[0] and branch in said[0]
        assert not any("worker ended **abandoned**" in m for m in _posts(orch))
        # one abandon, one re-dispatch: the watchdog finds nothing to recover afterwards
        await orch._sweep_stalled_efforts()
        await _drain(orch)
        assert await orch._event_count(eid, "stall_recovered") == 0
        assert len(await _payloads(orch, eid, "abandoned_turn_redispatched")) == 1
        _no_denials(ws)
    finally:
        await _shutdown(orch, db)


async def test_same_worker_next_turn_starts_on_the_carried_commit(db_url, tmp_path):
    orch, harness, db, ws, trace = await _orch(db_url, tmp_path, [
        {"status": "abandoned", "detail": TIMEOUT_DETAIL, "write": {CODING_FILE: CODING_TEXT}},
        {"status": "done", "output": _EXEC_OUT},
    ])
    try:
        eid = await _run(orch)
        first, second = trace["turns"][:2]
        assert first["url"] == second["url"] == W1
        carried = await orch._last_event_payload(eid, "stopped_turn_work_carried")
        assert second["head"] == carried["commit"]
        assert second["saw"].get(CODING_FILE) == CODING_TEXT
        _no_false_intact(orch)
        _no_denials(ws)
    finally:
        await _shutdown(orch, db)


async def test_a_clean_abandoned_turn_is_re_dispatched_and_says_nothing_was_left(db_url, tmp_path):
    orch, harness, db, ws, trace = await _orch(db_url, tmp_path, [
        {"status": "abandoned", "detail": TIMEOUT_DETAIL},
        {"status": "done", "output": _EXEC_OUT},
    ])
    try:
        eid = await _run(orch)
        assert len(trace["turns"]) >= 2
        assert await orch._event_count(eid, "stopped_turn_work_carried") == 0
        assert await orch._event_count(eid, "stopped_turn_carry_clean") == 1
        assert f"agent/{eid}" not in _origin_branches(ws)
        said = [m for m in _posts(orch) if "time limit" in m]
        assert said and "nothing uncommitted" in said[0].lower()
        assert "CARRIED WORK" not in trace["turns"][1]["prompt"]
        _no_false_intact(orch)
    finally:
        await _shutdown(orch, db)


async def test_repeated_abandons_are_re_dispatched_only_up_to_the_cap(db_url, tmp_path):
    """Bounded like the watchdog's recoveries (`stall_max_recoveries`): past the cap the abandon
    is escalated as before, and its work is still carried."""
    orch, harness, db, ws, trace = await _orch(db_url, tmp_path, [
        {"status": "abandoned", "detail": TIMEOUT_DETAIL, "write": {CODING_FILE: CODING_TEXT}},
        {"status": "abandoned", "detail": TIMEOUT_DETAIL, "write": {SRC: SRC_EDIT}},
        {"status": "abandoned", "detail": TIMEOUT_DETAIL, "write": {"third.py": "x = 3\n"}},
        {"status": "done", "output": _EXEC_OUT},
    ], max_recoveries=2)
    try:
        eid = await _run(orch)
        assert len(trace["turns"]) == 3, [t["session"] for t in trace["turns"]]
        assert len(await _payloads(orch, eid, "abandoned_turn_redispatched")) == 2
        carried = await _payloads(orch, eid, "stopped_turn_work_carried")
        assert [c["files"] for c in carried] == [[CODING_FILE], [SRC], ["third.py"]]
        assert any("worker ended **abandoned**" in m for m in _posts(orch))
        assert _origin_file(ws, f"agent/{eid}", "third.py") == "x = 3\n"
        _no_false_intact(orch)
    finally:
        await _shutdown(orch, db)


async def test_one_abandoned_task_is_re_dispatched_once(db_url, tmp_path):
    """The same abandoned task reported twice (a retried poll, a restart re-reading it) queues one
    re-dispatch, not two - also across a bridge restart (the event is the record)."""
    orch, harness, db, ws, trace = await _orch(db_url, tmp_path, [])
    try:
        await orch.projects.add("ai-stack", REPO)
        eid, _c, _r = await orch.router.open_effort("once", project="ai-stack")
        await orch.charters.set_goal(eid, "fix it", created_by="po")
        res = _result({"status": "abandoned", "detail": TIMEOUT_DETAIL, "task": "t-9"}, 1)
        res.instance_id = "worker-1"
        assert await orch._redispatch_abandoned(eid, res, "task_timeout", None) is True
        assert await orch._redispatch_abandoned(eid, res, "task_timeout", None) is True
        orch._abandon_redispatched.clear()                       # a restart forgets memory ...
        assert await orch._redispatch_abandoned(eid, res, "task_timeout", None) is True
        assert len(await _payloads(orch, eid, "abandoned_turn_redispatched")) == 1  # ... not events
        assert orch._iterate_after.get(eid)
    finally:
        await _shutdown(orch, db)


# == 2. every other way a coding turn ends ========================================================
@pytest.mark.parametrize("spec,cause", [
    ({"status": "error", "output": "poll timeout"}, "poll_timeout"),
    ({"status": "abandoned", "detail": "abandoned: drain deadline exceeded"}, "worker_shutdown"),
    ({"status": "abandoned", "detail": "abandoned: shutting down"}, "worker_shutdown"),
], ids=["poll-timeout", "worker-drain", "worker-shutdown"])
async def test_timeouts_and_worker_shutdown_carry_and_re_dispatch(db_url, tmp_path, spec, cause):
    orch, harness, db, ws, trace = await _orch(db_url, tmp_path, [
        dict(spec, write={CODING_FILE: CODING_TEXT}),
        {"status": "done", "output": _EXEC_OUT},
    ])
    try:
        await _keep_worker_1_off_the_new_session(orch)
        eid = await _run(orch)
        carried = await orch._last_event_payload(eid, "stopped_turn_work_carried")
        assert carried["cause"] == cause
        assert len(trace["turns"]) >= 2 and trace["turns"][1]["url"] == W2
        assert trace["turns"][1]["saw"].get(CODING_FILE) == CODING_TEXT
        assert (await orch._last_event_payload(eid, "abandoned_turn_redispatched"))["cause"] == cause
        _no_false_intact(orch)
    finally:
        await _shutdown(orch, db)


@pytest.mark.parametrize("spec,cause", [
    ({"status": "rejected", "output": "I will not do this."}, "rejected"),
    ({"status": "error", "output": "daemon: tool crashed"}, "error"),
    ({"status": "abandoned", "detail": "cancelled by the operator"}, "cancelled"),
], ids=["rejected", "error", "external-cancel"])
async def test_other_stops_carry_but_do_not_re_dispatch(db_url, tmp_path, spec, cause):
    orch, harness, db, ws, trace = await _orch(db_url, tmp_path, [
        dict(spec, write={CODING_FILE: CODING_TEXT}),
    ])
    try:
        eid = await _run(orch)
        assert len(trace["turns"]) == 1
        carried = await orch._last_event_payload(eid, "stopped_turn_work_carried")
        assert carried is not None and carried["cause"] == cause
        assert _origin_file(ws, f"agent/{eid}", CODING_FILE) == CODING_TEXT
        assert await orch._event_count(eid, "abandoned_turn_redispatched") == 0
        if cause == "rejected":
            assert await orch.gate.state_of(eid) == "frozen"     # still the human's refusal gate
        _no_false_intact(orch)
    finally:
        await _shutdown(orch, db)


async def test_backpressure_carries_then_parks(db_url, tmp_path):
    orch, harness, db, ws, trace = await _orch(db_url, tmp_path, [
        {"status": "error", "output": "llm error: Error code: 503 shedding load",
         "write": {CODING_FILE: CODING_TEXT}},
    ])
    try:
        eid = await _run(orch)
        carried = await orch._last_event_payload(eid, "stopped_turn_work_carried")
        assert carried is not None and carried["cause"] == "backpressure"
        assert eid in {t["effort_id"] for t in await orch.parks.all()}
        assert await orch._event_count(eid, "abandoned_turn_redispatched") == 0
    finally:
        await _shutdown(orch, db)


async def test_an_operator_abort_mid_turn_carries_and_never_re_dispatches(db_url, tmp_path):
    holder: dict = {}

    async def abort():
        await holder["orch"].gate.set_lifecycle(holder["eid"], "aborted")

    orch, harness, db, ws, trace = await _orch(db_url, tmp_path, [
        {"status": "abandoned", "detail": "cancelled by the operator",
         "write": {CODING_FILE: CODING_TEXT}, "during": abort},
    ])
    holder["orch"] = orch
    try:
        await orch.projects.add("ai-stack", REPO)
        eid, chan, root = await orch.router.open_effort("abort", project="ai-stack")
        holder["eid"] = eid
        await orch.delegate(eid, chan, root, "fix the llm-queue connection leak")
        await _drain(orch)
        assert len(trace["turns"]) == 1
        carried = await orch._last_event_payload(eid, "stopped_turn_work_carried")
        assert carried is not None and carried["cause"] == "cancelled"
        assert await orch._event_count(eid, "abandoned_turn_redispatched") == 0
    finally:
        await _shutdown(orch, db)


async def test_a_turn_the_watchdog_cancelled_is_carried_by_the_watchdog_not_twice(db_url, tmp_path):
    """The delegate that sees its turn come back `cancelled` because the stall watchdog cancelled
    it (hung) leaves the carry to the watchdog, which runs it before re-engaging."""
    orch, harness, db, ws, trace = await _orch(db_url, tmp_path, [
        {"status": "abandoned", "detail": "cancelled by the operator", "task": "t-hung",
         "write": {CODING_FILE: CODING_TEXT}},
    ])
    try:
        orch._stop_claims["t-hung"] = "watchdog"     # the watchdog claimed it first
        eid = await _run(orch)
        assert await orch._event_count(eid, "stopped_turn_work_carried") == 0
        assert not [c for c in _checks_on(trace, W1) if "b64decode" in c]
        assert await orch._event_count(eid, "abandoned_turn_redispatched") == 0
    finally:
        await _shutdown(orch, db)


async def _set_worker(orch, url: str, **fields) -> None:
    async with orch.db.session_factory() as s:
        wi = (await s.execute(select(WorkerInstance).where(
            WorkerInstance.base_url == url))).scalar_one()
        for k, v in fields.items():
            setattr(wi, k, v)
        await s.commit()


async def _effort_with_dirty_worker_1(orch, ws, name: str) -> str:
    """An effort whose last coding turn ran on worker-1 and left CODING_FILE uncommitted."""
    await orch.projects.add("ai-stack", REPO)
    eid, _c, _r = await orch.router.open_effort(name, project="ai-stack")
    await orch.charters.set_goal(eid, "fix the llm-queue connection leak", created_by="po")
    ws.focus(W1, True)
    ws.focus(W2, True)
    (ws.dirs[W1] / CODING_FILE).parent.mkdir(parents=True, exist_ok=True)
    (ws.dirs[W1] / CODING_FILE).write_text(CODING_TEXT)
    orch.router._ws_focus["worker-1"] = (eid, REPO, ws.base)
    orch.router._coding_ws[eid] = "worker-1"
    return eid


async def test_hung_turn_is_carried_from_its_worker_after_the_slot_frees_never_discarded(
        db_url, tmp_path):
    orch, harness, db, ws, trace = await _orch(db_url, tmp_path, [
        {"status": "done", "output": _EXEC_OUT}])
    try:
        eid = await _effort_with_dirty_worker_1(orch, ws, "hung")
        # the dead turn's delegate still holds worker-1's slot for a moment after the cancel
        await _set_worker(orch, W1, sched_state=SCHED_COMPUTING, effort_id=eid)

        async def free_later():
            import asyncio
            await asyncio.sleep(0.3)
            await _set_worker(orch, W1, sched_state=SCHED_IDLE, effort_id=None)
        orch._spawn(free_later())
        why = {"rule": "silent", "silent_s": 1200}
        await orch._recover_hung_worker(W1, "t-hung", eid, why, set(), None)
        await _drain(orch)
        assert (W1, "t-hung") in harness.cancelled
        assert orch._stop_claims.get("t-hung") == "watchdog"
        carried = await orch._last_event_payload(eid, "stopped_turn_work_carried")
        assert carried is not None and carried["worker"] == "worker-1" and carried["cause"] == "hung"
        assert not [c for _u, c in trace["checks"] if "DISCARD-DONE" in c]
        assert await orch._event_count(eid, "stall_tree_discarded") == 0
        # the re-engaged turn starts from it
        assert trace["turns"] and trace["turns"][0]["saw"].get(CODING_FILE) == CODING_TEXT
        _no_false_intact(orch)
        said = [m for m in _posts(orch) if "re-engaged" in m]
        assert said and carried["commit"][:12] in said[0]
    finally:
        await _shutdown(orch, db)


async def _age_effort(orch, eid: str, minutes: int = 20) -> None:
    old = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
    async with orch.db.session_factory() as s:
        for ev in (await s.execute(select(Event).where(Event.effort_id == eid))).scalars():
            ev.ts = old
        await s.commit()


async def test_idle_stall_carries_from_the_efforts_own_worker_not_a_free_one(db_url, tmp_path):
    """C7f ev 19: the idle recovery ran its discard on worker-2 (free) while the work sat on
    worker-1. Now the carry runs pinned to the worker of the effort's last coding turn, and no
    command at all runs on the other worker."""
    orch, harness, db, ws, trace = await _orch(db_url, tmp_path, [
        {"status": "done", "output": _EXEC_OUT}])
    try:
        eid = await _effort_with_dirty_worker_1(orch, ws, "idle")
        await _age_effort(orch, eid)
        await orch._sweep_stalled_efforts()
        await _drain(orch)
        assert await orch._event_count(eid, "stall_recovered") == 1
        carried = await orch._last_event_payload(eid, "stopped_turn_work_carried")
        assert carried is not None and carried["worker"] == "worker-1" and carried["cause"] == "stall"
        assert not [c for _u, c in trace["checks"] if "DISCARD-DONE" in c]
        assert trace["turns"][0]["saw"].get(CODING_FILE) == CODING_TEXT
        _no_false_intact(orch)
    finally:
        await _shutdown(orch, db)


async def test_idle_stall_with_no_known_worker_touches_nothing_and_says_so(db_url, tmp_path):
    """After a bridge restart the bridge does not know which worker held the turn: nothing is
    carried or discarded anywhere, and the notice says exactly that."""
    orch, harness, db, ws, trace = await _orch(db_url, tmp_path, [
        {"status": "done", "output": _EXEC_OUT}])
    try:
        eid = await _effort_with_dirty_worker_1(orch, ws, "restart")
        orch.router._coding_ws.clear()
        orch.router._ws_focus.clear()
        await _age_effort(orch, eid)
        before = len(trace["checks"])
        await orch._sweep_stalled_efforts()
        await _drain(orch)
        # neither a carry nor P16's discard ran anywhere. (The re-engaged turn's own focus is the
        # P8 #3 fresh clone that a lost focus record always meant; a turn the restart orphaned
        # is saved by the boot carry - next test.)
        assert not any("DISCARD-DONE" in c or "b64decode" in c
                       for _u, c in trace["checks"][before:])
        assert await orch._event_count(eid, "stall_carry_skipped") == 1
        said = [m for m in _posts(orch) if "went quiet" in m]
        assert said and "nothing was saved or discarded" in said[0]
        _no_false_intact(orch)
    finally:
        await _shutdown(orch, db)


async def test_a_turn_orphaned_by_a_bridge_restart_is_carried_at_boot(db_url, tmp_path):
    """Boot cancels every running daemon turn (F31.3). The turn's worker row is still bound to its
    effort: its uncommitted work is carried from that worker, although the restart lost every
    focus record."""
    orch, harness, db, ws, trace = await _orch(db_url, tmp_path, [])
    orch2 = None
    try:
        eid = await _effort_with_dirty_worker_1(orch, ws, "boot")
        await orch.audit.log("worker_project_set", effort_id=eid, actor="worker-1",
                             payload={"repo": REPO, "ok": True})
        await _set_worker(orch, W1, sched_state=SCHED_COMPUTING, effort_id=eid)
        harness.busy_urls = {W1}
        harness.progress_task_ids = {W1: "t-orphan"}
        orig_cancel = harness.cancel_task

        async def cancel(base_url, task_id):
            harness.busy_urls.discard(base_url)          # the daemon stops the cancelled task
            return await orig_cancel(base_url, task_id)
        harness.cancel_task = cancel
        # the restarted bridge: same DB, same workers, no in-memory focus records
        orch2 = Orchestrator(orch.s, db, FakeChatAdapter(), model_client=FakeModelClient(),
                             harness=harness)
        await orch2.setup()
        await _drain(orch2)
        assert (W1, "t-orphan") in harness.cancelled
        carried = await orch2._last_event_payload(eid, "stopped_turn_work_carried")
        assert carried is not None and carried["worker"] == "worker-1"
        assert carried["cause"] == "bridge_restart" and carried["files"] == [CODING_FILE]
        assert _origin_file(ws, f"agent/{eid}", CODING_FILE) == CODING_TEXT
        _no_denials(ws)
    finally:
        if orch2 is not None:
            await _drain(orch2)
        await _shutdown(orch, db)


async def test_a_turn_cancelled_by_a_freeze_is_not_carried_while_frozen_and_not_discarded(
        db_url, tmp_path):
    """Governance §3.0: a frozen effort computes nothing - the carry too. The refusal is recorded
    and said, and the tree is left exactly as it was (never discarded)."""
    holder: dict = {}

    async def freeze():
        from app.schemas import Concern, Trigger

        o, e = holder["orch"], holder["eid"]
        await o.gate.freeze(e, Trigger.refusal, Concern(
            intent_thread=f"effort {e}", what_surfaced="a concern raised mid-turn",
            intent_of_change="test", pm_recommendation="review", blocked_efforts=[e]))
        await o.scheduler.enforce_freeze(e)

    orch, harness, db, ws, trace = await _orch(db_url, tmp_path, [
        {"status": "abandoned", "detail": "cancelled by the operator",
         "write": {CODING_FILE: CODING_TEXT}, "during": freeze},
    ])
    holder["orch"] = orch
    try:
        await orch.projects.add("ai-stack", REPO)
        eid, chan, root = await orch.router.open_effort("freeze", project="ai-stack")
        holder["eid"] = eid
        await orch.delegate(eid, chan, root, "fix the llm-queue connection leak")
        await _drain(orch)
        assert await orch.gate.state_of(eid) == "frozen"
        assert await orch._event_count(eid, "stopped_turn_work_carried") == 0
        failed = await orch._last_event_payload(eid, "stopped_turn_carry_failed")
        assert failed is not None and "frozen" in failed["reason"]
        assert (ws.dirs[W1] / CODING_FILE).read_text() == CODING_TEXT
        assert await orch._event_count(eid, "abandoned_turn_redispatched") == 0
    finally:
        await _shutdown(orch, db)


async def test_without_the_delivery_branch_sync_the_legacy_discard_stays_but_is_truthful(
        db_url, tmp_path):
    orch, harness, db, ws, trace = await _orch(db_url, tmp_path, [
        {"status": "done", "output": _EXEC_OUT}], sync=False)
    try:
        eid = await _effort_with_dirty_worker_1(orch, ws, "legacy")
        await _age_effort(orch, eid)
        await orch._sweep_stalled_efforts()
        await _drain(orch)
        assert await orch._event_count(eid, "stopped_turn_work_carried") == 0
        _no_false_intact(orch)
    finally:
        await _shutdown(orch, db)


async def test_a_worker_lost_mid_turn_is_retried_elsewhere_and_its_tree_is_not_discarded(
        db_url, tmp_path):
    """Worker loss (transport error mid-turn): the router quarantines it and hands the turn to the
    other worker (unchanged). Nothing can reach the lost worker, so nothing is carried from it -
    and nothing is discarded there either."""
    lost = httpx.ConnectError("connection refused", request=httpx.Request("GET", W1))
    orch, harness, db, ws, trace = await _orch(db_url, tmp_path, [
        {"write": {CODING_FILE: CODING_TEXT}, "raise_": lost},
        {"status": "done", "output": _EXEC_OUT},
    ])
    try:
        eid = await _run(orch)
        assert [t["url"] for t in trace["turns"][:2]] == [W1, W2]
        assert await orch._event_count(eid, "worker_dispatch_failed") == 1
        assert not any("DISCARD-DONE" in c or "b64decode" in c for c in _checks_on(trace, W1))
        assert (ws.dirs[W1] / CODING_FILE).read_text() == CODING_TEXT
    finally:
        await _shutdown(orch, db)


# == units ========================================================================================
@pytest.mark.parametrize("status,detail,output,cause", [
    ("abandoned", "abandoned: exceeded 3600s", "", "task_timeout"),
    ("abandoned", "", "", "task_timeout"),
    ("abandoned", "cancelled by the operator", "", "cancelled"),
    ("abandoned", "abandoned: drain deadline exceeded", "", "worker_shutdown"),
    ("abandoned", "abandoned: shutting down", "", "worker_shutdown"),
    ("error", "", "poll timeout", "poll_timeout"),
    ("error", "", "Error code: 503", "backpressure"),
    ("error", "", "boom", "error"),
    ("rejected", "", "no", "rejected"),
    ("done", "", "ok", None),
])
def test_stop_causes(status, detail, output, cause):
    res = WorkResult(status, task_id="t", output=output)
    res.detail = detail
    assert Orchestrator._stopped_turn_cause(res) == cause


def test_the_harness_keeps_the_daemons_detail():
    res = WorkResult("abandoned", task_id="t")
    assert res.detail == ""


def test_the_carry_commit_body_names_the_cause_never_model_text():
    from app.orchestrator import _carry_body

    assert "flail guard stopped this coding turn" in _carry_body("flail")
    assert "task time limit" in _carry_body("task_timeout")
    for c in ("loop", "task_timeout", "worker_shutdown", "poll_timeout", "cancelled", "rejected",
              "error", "backpressure", "hung", "stall"):
        assert "ghp_" not in _carry_body(c) and _carry_body(c)


# == round 2 (tester attempt 1) ===================================================================
async def _race_run(db_url, tmp_path, pause: float):
    """Tester P1: the watchdog judges turn 1 hung (and cancels it) while the daemon's own task
    timeout ends the same task; `pause` = how far the watchdog gets before the result arrives."""
    import asyncio

    holder: dict = {}

    async def race():
        o = holder["orch"]
        eid = next(iter(o._delegating))
        o._spawn(o._recover_hung_worker(W1, "task-1", eid, {"rule": "silent", "silent_s": 1200},
                                        set(), None))
        await asyncio.sleep(pause)

    orch, harness, db, ws, trace = await _orch(db_url, tmp_path, [
        {"status": "abandoned", "detail": TIMEOUT_DETAIL, "task": "task-1",
         "write": {CODING_FILE: CODING_TEXT, SRC: SRC_EDIT}, "during": race},
        {"status": "done", "output": _EXEC_OUT},
        {"status": "done", "output": _EXEC_OUT},
        {"status": "done", "output": _EXEC_OUT},
    ])
    holder["orch"] = orch
    return orch, db, ws, trace


@pytest.mark.parametrize("pause", [0.0, 0.05, 0.2, 0.2, 0.2, 1.0])
async def test_r2_watchdog_hung_verdict_racing_the_task_timeout_re_engages_once(
        db_url, tmp_path, pause):
    """FAILURE 1 / P1: one owner per stopped task. Exactly one follow-up turn, it starts from the
    work, it is told about the commit that was actually made, and no post contradicts it."""
    orch, db, ws, trace = await _race_run(db_url, tmp_path, pause)
    try:
        eid = await _run(orch)
        await _drain(orch, rounds=6)
        turns = trace["turns"]
        assert len(turns) == 2, [(t["url"], t["session"]) for t in turns]
        follow = turns[1]
        assert follow["saw"].get(CODING_FILE) == CODING_TEXT
        carried = await _payloads(orch, eid, "stopped_turn_work_carried")
        assert len(carried) == 1, carried                  # one carry, by its owner
        assert await orch._event_count(eid, "stopped_turn_carry_failed") == 0
        owners = (await orch._event_count(eid, "abandoned_turn_redispatched")
                  + await orch._event_count(eid, "stall_recovered"))
        assert owners == 1
        assert follow["head"] == carried[0]["commit"]
        assert "CARRIED WORK" in follow["prompt"] and carried[0]["commit"][:12] in follow["prompt"]
        assert not any("could not save" in m.lower() for m in _posts(orch)), _posts(orch)
        _no_false_intact(orch)
        _no_denials(ws)
    finally:
        await _shutdown(orch, db)


async def test_r2_restart_during_the_race_re_engages_once(db_url, tmp_path):
    """The delegate recorded the re-dispatch, the bridge died before launching it; after the
    restart the watchdog judges the turn hung: ONE re-engage, and the dead run's record does not
    queue a second one."""
    orch, harness, db, ws, trace = await _orch(db_url, tmp_path, [
        {"status": "done", "output": _EXEC_OUT}, {"status": "done", "output": _EXEC_OUT}])
    orch2 = None
    try:
        eid = await _effort_with_dirty_worker_1(orch, ws, "race-restart")
        res = _result({"status": "abandoned", "detail": TIMEOUT_DETAIL, "task": "task-1"}, 1)
        res.instance_id = "worker-1"
        assert orch._claim_stop("task-1", "delegate")
        assert await orch._redispatch_abandoned(eid, res, "task_timeout", None) is True
        orch2 = Orchestrator(orch.s, db, FakeChatAdapter(), model_client=FakeModelClient(),
                             harness=harness)
        await orch2.setup()
        orch2._carry_slot_pause = 0.05
        orch2.router._ws_focus["worker-1"] = (eid, REPO, ws.base)
        await orch2._recover_hung_worker(W1, "task-1", eid, {"rule": "silent"}, set(), None)
        assert await orch2._redispatch_abandoned(eid, res, "task_timeout", None) is True
        assert not orch2._iterate_after.get(eid)
        await _drain(orch2, rounds=6)
        assert len(trace["turns"]) == 1, [(t["url"], t["session"]) for t in trace["turns"]]
        assert trace["turns"][0]["saw"].get(CODING_FILE) == CODING_TEXT
        assert await orch2._event_count(eid, "stall_recovered") == 1
    finally:
        if orch2 is not None:
            await _drain(orch2)
        await _shutdown(orch, db)


def test_r2_a_finishing_holder_never_clears_a_newer_holders_marker():
    o = Orchestrator.__new__(Orchestrator)
    o._delegating, o._delegating_tok = set(), {}
    old = o._take_delegating("e")
    assert o._take_delegating("e") is None            # single flight
    o._drop_delegating("e")                           # the watchdog: the old run is dead
    new = o._take_delegating("e")
    o._release_delegating("e", old)                   # the old run's finally
    assert "e" in o._delegating                       # ... leaves the new run's marker
    o._release_delegating("e", new)
    assert "e" not in o._delegating


async def test_r2_f2_abandon_re_dispatches_and_watchdog_recoveries_share_one_cap(db_url, tmp_path):
    orch, harness, db, ws, trace = await _orch(db_url, tmp_path, [
        {"status": "done", "output": _EXEC_OUT}], max_recoveries=2)
    try:
        eid = await _effort_with_dirty_worker_1(orch, ws, "cap")
        for n in (1, 2, 3):
            res = _result({"status": "abandoned", "detail": TIMEOUT_DETAIL, "task": f"t-{n}"}, n)
            res.instance_id = "worker-1"
            ok = await orch._redispatch_abandoned(eid, res, "task_timeout", None)
            assert ok is (n <= 2), n
        orch._iterate_after.clear()
        await _age_effort(orch, eid)
        await orch._sweep_stalled_efforts()
        assert await orch._event_count(eid, "stall_recovered") == 0
        assert await orch._event_count(eid, "stall_escalated") == 1
    finally:
        await _shutdown(orch, db)


@pytest.mark.parametrize("stop", ["aborted", "frozen"])
async def test_r2_f3_a_task_timeout_on_an_aborted_or_frozen_effort_is_never_re_dispatched(
        db_url, tmp_path, stop):
    from app.schemas import Concern, Trigger

    holder: dict = {}

    async def stop_it():
        o, e = holder["orch"], holder["eid"]
        if stop == "aborted":
            await o.gate.set_lifecycle(e, "aborted")
        else:
            await o.gate.freeze(e, Trigger.refusal, Concern(
                intent_thread=f"effort {e}", what_surfaced="concern mid-turn",
                intent_of_change="test", pm_recommendation="review", blocked_efforts=[e]))

    orch, harness, db, ws, trace = await _orch(db_url, tmp_path, [
        {"status": "abandoned", "detail": TIMEOUT_DETAIL, "write": {CODING_FILE: CODING_TEXT},
         "during": stop_it},
        {"status": "done", "output": _EXEC_OUT},
    ])
    holder["orch"] = orch
    try:
        await orch.projects.add("ai-stack", REPO)
        eid, chan, root = await orch.router.open_effort(f"t-{stop}", project="ai-stack")
        holder["eid"] = eid
        await orch.delegate(eid, chan, root, "fix the llm-queue connection leak")
        await _drain(orch)
        assert len(trace["turns"]) == 1
        assert await orch._event_count(eid, "abandoned_turn_redispatched") == 0
        assert (ws.dirs[W1] / CODING_FILE).read_text() == CODING_TEXT   # never discarded
    finally:
        await _shutdown(orch, db)


@pytest.mark.parametrize("detail,cause", [("abandoned: exceeded 3600s", "task_timeout"),
                                          ("cancelled by the operator", "cancelled"),
                                          ("abandoned: drain deadline exceeded", "worker_shutdown")])
async def test_r2_f4_the_real_harness_copies_the_daemons_detail(detail, cause):
    from app.worker.harness import LittleCoderHarness

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "POST" and req.url.path == "/tasks":
            return httpx.Response(200, json={"task_id": "t-1", "status": "queued"})
        if req.url.path == "/tasks/t-1":     # little-coder TaskState.public() of a terminal task
            return httpx.Response(200, json={"task_id": "t-1", "status": "abandoned",
                                             "detail": detail, "answer": "", "activity": []})
        return httpx.Response(404)
    h = LittleCoderHarness(poll_interval_s=0.01, poll_timeout_s=5,
                           transport=httpx.MockTransport(handler))
    res = await h.wake("http://w1:8090", "s", "p")
    assert res.status == "abandoned" and res.detail == detail
    assert Orchestrator._stopped_turn_cause(res) == cause


async def test_r2_f7_the_boot_carry_refuses_a_worker_last_focused_for_another_effort(
        db_url, tmp_path):
    orch, harness, db, ws, trace = await _orch(db_url, tmp_path, [])
    orch2 = None
    try:
        eid = await _effort_with_dirty_worker_1(orch, ws, "boot-mismatch")
        await orch.audit.log("worker_project_set", effort_id=eid, actor="worker-1",
                             payload={"repo": REPO, "ok": True})
        # ... then worker-1 was re-focused for another effort (its tree is that effort's now)
        await orch.audit.log("worker_project_set", effort_id="effort-other", actor="worker-1",
                             payload={"repo": REPO, "ok": True})
        await _set_worker(orch, W1, sched_state=SCHED_COMPUTING, effort_id=eid)
        harness.busy_urls = {W1}
        harness.progress_task_ids = {W1: "t-orphan"}
        orig_cancel = harness.cancel_task

        async def cancel(base_url, task_id):
            harness.busy_urls.discard(base_url)
            return await orig_cancel(base_url, task_id)
        harness.cancel_task = cancel
        orch2 = Orchestrator(orch.s, db, FakeChatAdapter(), model_client=FakeModelClient(),
                             harness=harness)
        await orch2.setup()
        await _drain(orch2)
        assert await orch2._event_count(eid, "stopped_turn_work_carried") == 0
        failed = await orch2._last_event_payload(eid, "stopped_turn_carry_failed")
        assert failed and "effort-other" in failed["reason"]
        assert not [c for _u, c in trace["checks"] if "b64decode" in c]
        assert f"agent/{eid}" not in _origin_branches(ws)
    finally:
        if orch2 is not None:
            await _drain(orch2)
        await _shutdown(orch, db)


# -- FAILURE 2: every coding wake site outside _run_step -------------------------------------------
async def _route_round_2_to_worker_2(orch, marker: str) -> None:
    orig = orch.scheduler.acquire

    async def acquire(effort_id, role, session_id, *a, **kw):
        if marker not in session_id:
            return await orig(effort_id, role, session_id, *a, **kw)
        await _set_worker(orch, W1, sched_state=SCHED_COMPUTING)
        try:
            return await orig(effort_id, role, session_id, *a, **kw)
        finally:
            await _set_worker(orch, W1, sched_state=SCHED_IDLE)
    orch.scheduler.acquire = acquire


async def test_r2_burndown_round_abandoned_work_reaches_the_next_round_on_another_worker(
        db_url, tmp_path):
    """Tester P2: round 1 on worker-1 is abandoned at the task timeout with an uncommitted file;
    round 2 runs on worker-2 and must start from it (carry + the F33 sync on burn-down rounds)."""
    from types import SimpleNamespace

    orch, harness, db, ws, trace = await _orch(db_url, tmp_path, [
        {"status": "abandoned", "detail": TIMEOUT_DETAIL, "task": "bd-1",
         "write": {CODING_FILE: CODING_TEXT}},
        {"status": "done", "output": "fixed"},
    ])
    try:
        await orch.projects.add("ai-stack", REPO)
        eid, _c, _r = await orch.router.open_effort("burn", project="ai-stack")
        await orch.charters.set_goal(eid, "fix the build", created_by="po")
        checks = iter([("fail", "error CS0001: a.cs(1): x\nerror CS0002: b.cs(2): y", 2),
                       ("pass", "ok", 0)])

        async def build_check(effort_id, on_branch=False):
            return next(checks)

        async def verify(effort_id, repo):
            return SimpleNamespace(landed=True, head_sha="h", summary="")

        async def finish(effort_id, res, delivery=None):
            return None

        async def standing(*a, **kw):
            return True
        orch._org_build_check = build_check
        orch._verify_delivery = verify
        orch._finish_effort = finish
        orch._gate_standing_intent = standing
        await _route_round_2_to_worker_2(orch, "~bd2")
        await orch._burndown_loop(eid, "error CS0001: a.cs(1): x\nerror CS0002: b.cs(2): y\n"
                                       "error CS0003: c.cs(3): z")
        await _drain(orch)
        turns = trace["turns"]
        assert [t["url"] for t in turns] == [W1, W2]
        assert turns[1]["saw"].get(CODING_FILE) == CODING_TEXT
        carried = await orch._last_event_payload(eid, "stopped_turn_work_carried")
        assert carried["cause"] == "task_timeout" and carried["branch"] == f"agent/{eid}"
        assert turns[1]["head"] == carried["commit"]
        _no_denials(ws)
    finally:
        await _shutdown(orch, db)


async def test_r2_a_burndown_part_round_carries_to_its_part_branch(db_url, tmp_path):
    orch, harness, db, ws, trace = await _orch(db_url, tmp_path, [])
    try:
        eid = await _effort_with_dirty_worker_1(orch, ws, "part")
        res = _result({"status": "abandoned", "detail": TIMEOUT_DETAIL, "task": "pt"}, 1)
        res.instance_id = "worker-1"
        out = await orch._carry_if_stopped(eid, res, REPO, site="burndown r2",
                                           branch=f"agent/{eid}-pt1r2")
        assert out["status"] == "carried" and out["pushed"]
        assert _origin_file(ws, f"agent/{eid}-pt1r2", CODING_FILE) == CODING_TEXT
    finally:
        await _shutdown(orch, db)


async def _host_effort(orch, ws, name: str):
    eid = await _effort_with_dirty_worker_1(orch, ws, name)

    async def vendored_host(slug):
        return ("engine", "vendor/proj", REPO, "")
    orch._vendored_host = vendored_host
    # the turn runs on worker-2 (worker-1 busy); worker-1 is free again by the time the salvage
    # runs, so only a salvage PINNED to the turn's worker reaches worker-2
    await _set_worker(orch, W1, sched_state=SCHED_COMPUTING)
    return eid


async def _free_worker_1(orch):
    await _set_worker(orch, W1, sched_state=SCHED_IDLE)


def _record_exec_pins(orch) -> list:
    """The pin of every exec_check (session affinity alone may pick the right worker by luck)."""
    pins: list = []
    orig = orch.router.exec_check

    async def exec_check(effort_id, **kw):
        pins.append((kw.get("session_id"), kw.get("instance_id")))
        return await orig(effort_id, **kw)
    orch.router.exec_check = exec_check
    return pins


async def test_r2_a_host_burndown_round_that_stops_is_salvaged_on_its_own_worker(db_url, tmp_path):
    spec = {"status": "abandoned", "detail": TIMEOUT_DETAIL, "task": "hb-1"}
    orch, harness, db, ws, trace = await _orch(db_url, tmp_path, [spec])
    spec["during"] = lambda: _free_worker_1(orch)
    try:
        eid = await _host_effort(orch, ws, "host-bd")
        pins = _record_exec_pins(orch)
        loc = await orch.router.effort_thread(eid)
        await orch._burndown_wake(eid, loc[0], loc[1], 3, ["error CS1: a.cs(1): x"], part=None,
                                  host=("engine", "vendor/proj", REPO, ""),
                                  branch=f"agent/{eid}", repo=REPO, branch_exists=True)
        assert len(trace["stranded"]) == 1
        url, cmd = trace["stranded"][0]
        assert url == trace["turns"][0]["url"] == W2                 # pinned to the round's worker
        assert [p for _s, p in pins] == ["worker-2"], pins
        assert cmd.startswith("cd vendor/proj ") and f"git push origin agent/{eid}" in cmd
        pub = await orch._last_event_payload(eid, "pm_published_stranded_work")
        assert pub["cause"] == "task_timeout" and pub["worker"] == "worker-2"
    finally:
        await _shutdown(orch, db)


async def test_r2_a_host_context_turn_that_stops_is_salvaged_even_when_a_branch_landed(
        db_url, tmp_path):
    from app.modules.capabilities import BranchDelivery

    spec = {"status": "abandoned", "detail": TIMEOUT_DETAIL, "task": "hc-1"}
    orch, harness, db, ws, trace = await _orch(db_url, tmp_path, [spec])
    spec["during"] = lambda: _free_worker_1(orch)
    try:
        eid = await _host_effort(orch, ws, "host-ctx")
        pins = _record_exec_pins(orch)

        async def verify(effort_id, repo):      # an earlier round's branch already landed
            return BranchDelivery(verifiable=True, exists=True, ahead=1, head_sha="h" * 40,
                                  branch=f"agent/{effort_id}")

        async def standing(*a, **kw):
            return False                        # stop after the salvage (nothing else under test)
        orch._verify_delivery = verify
        orch._gate_standing_intent = standing
        await orch._run_in_host_context(eid)
        assert len(trace["stranded"]) == 1
        assert trace["stranded"][0][0] == trace["turns"][0]["url"] == W2
        assert [p for _s, p in pins] == ["worker-2"], pins
        assert (await orch._last_event_payload(eid, "pm_published_stranded_work"))["cause"] \
            == "task_timeout"
    finally:
        await _shutdown(orch, db)


@pytest.mark.parametrize("site", ["publish", "stale_delivery", "empty_delivery"])
async def test_r2_publish_and_re_engage_turns_that_stop_are_carried(db_url, tmp_path, site):
    from app.modules.capabilities import BranchDelivery

    orch, harness, db, ws, trace = await _orch(db_url, tmp_path, [
        {"status": "abandoned", "detail": TIMEOUT_DETAIL, "task": f"{site}-1", "publish": True,
         "write": {CODING_FILE: CODING_TEXT}},
        {"status": "abandoned", "detail": TIMEOUT_DETAIL, "task": f"{site}-2"}])
    try:
        eid = await _effort_with_dirty_worker_1(orch, ws, site.replace("_", "-"))
        await _set_worker(orch, W2, sched_state=SCHED_COMPUTING)   # the turn lands on worker-1
        chan, root = await orch.router.effort_thread(eid)
        stale = BranchDelivery(verifiable=True, exists=True, ahead=1, files_changed=0,
                               head_sha="a" * 40, branch=f"agent/{eid}")
        if site == "publish":
            await orch._publish_effort(eid, chan, root, REPO)
        elif site == "stale_delivery":
            await orch._recover_stale_delivery(eid, chan, root, REPO, stale)
        else:
            await orch._recover_empty_delivery(eid, chan, root, REPO, stale)
        carried = await orch._last_event_payload(eid, "stopped_turn_work_carried")
        assert carried is not None and carried["cause"] == "task_timeout"
        assert carried["worker"] == "worker-1"
        assert _origin_file(ws, f"agent/{eid}", CODING_FILE) == CODING_TEXT
    finally:
        await _shutdown(orch, db)


async def test_r2_a_flail_stop_the_watchdog_owns_is_left_to_it(db_url, tmp_path):
    """The flail/loop path takes the same one-owner rule: a task the watchdog claimed first is
    neither carried nor re-planned here."""
    from test_ao_plancheck import _FLAIL_OUT

    orch, harness, db, ws, trace = await _orch(db_url, tmp_path, [
        {"status": "done", "output": _FLAIL_OUT, "task": "t-fl",
         "write": {CODING_FILE: CODING_TEXT}}])
    try:
        orch._stop_claims["t-fl"] = "watchdog"
        eid = await _run(orch)
        assert await orch._event_count(eid, "flail_replanned") == 0
        assert await orch._event_count(eid, "stopped_turn_work_carried") == 0
        assert await orch._event_count(eid, "stopped_turn_left_to_watchdog") == 1
    finally:
        await _shutdown(orch, db)


async def test_r2_a_publish_turn_the_watchdog_owns_is_left_to_it(db_url, tmp_path):
    orch, harness, db, ws, trace = await _orch(db_url, tmp_path, [
        {"status": "abandoned", "detail": TIMEOUT_DETAIL, "task": "pub-w", "publish": True,
         "write": {CODING_FILE: CODING_TEXT}}])
    try:
        eid = await _effort_with_dirty_worker_1(orch, ws, "pub-watchdog")
        await _set_worker(orch, W2, sched_state=SCHED_COMPUTING)
        orch._stop_claims["pub-w"] = "watchdog"
        chan, root = await orch.router.effort_thread(eid)
        await orch._publish_effort(eid, chan, root, REPO)
        assert await orch._event_count(eid, "stopped_turn_work_carried") == 0
        assert await orch._event_count(eid, "stopped_turn_left_to_watchdog") == 1
    finally:
        await _shutdown(orch, db)
