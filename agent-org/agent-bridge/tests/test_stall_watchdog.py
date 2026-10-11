"""Stall watchdog (operator 2026-07-10: "there hasn't been an update in 2 hours"). Root cause: a
clone/focus failure only flipped the effort card to 'error' — no escalation, no audit, no recovery —
so an effort sat SILENT for ~2h. Fix: (1) clone_failed escalates loudly + audits `focus_failed`;
(2) a watchdog sweeps for efforts wedged MID-DISPATCH (silent past a threshold, not delegating/parked,
last event not a resolution) and auto-re-engages them (bounded), escalating past the cap. Fakes only."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import select

from app.adapters.chat import FakeChatAdapter
from app.config import Settings
from app.db import Database
from app.models import Effort, Event, GoalVersion, WorkerInstance
from app.modules.model_router import FakeModelClient
from app.orchestrator import Orchestrator
from app.worker.harness import FakeHarness

ROOT = Path(__file__).resolve().parents[1]


async def _orch(db_url):
    settings = Settings(
        _env_file=None, chat_adapter="fake",
        profiles_dir=str(ROOT / "profiles"), charters_dir=str(ROOT / "charters"),
        floor_dir=str(ROOT / "floor"), worker_instance_urls="http://w1:8090",
        max_concurrent_workers=1, database_url=db_url, project_survey_enabled=False,
        review_mode="off", plan_approval="off",
        stall_threshold_s=900, stall_max_recoveries=2,
    )
    db = Database(db_url)
    orch = Orchestrator(settings, db, FakeChatAdapter(),
                        model_client=FakeModelClient(), harness=FakeHarness())
    await orch.setup()
    return orch, orch.chat, db


async def _seed(orch, eid, *, last_kind, age_min=120, recoveries=0, extra_kinds=()):
    """Insert an effort whose LATEST event is `last_kind` at `age_min` ago (bypasses the fresh
    goal/open events that set_goal/open_effort would stamp NOW)."""
    base = datetime.now(timezone.utc) - timedelta(minutes=age_min)
    # stagger timestamps so `_last_event` deterministically returns `last_kind` (newest), while all
    # events still sit past the stall threshold.
    def _ts(offset_s):  # earlier events get more-negative offsets
        return (base + timedelta(seconds=offset_s)).isoformat()
    chan = await orch.mgmt_channel_id()
    async with orch.db.session_factory() as s:
        s.add(Effort(id=eid, name=eid, channel_id=chan, root_post_id=f"root-{eid}",
                     state="active", lifecycle="open"))
        s.add(GoalVersion(effort_id=eid, version=1, objective="fix the thing", created_by="po"))
        s.add(Event(kind="worker_acquire", effort_id=eid, ts=_ts(0)))
        t = 1
        for k in extra_kinds:
            s.add(Event(kind=k, effort_id=eid, ts=_ts(t))); t += 1
        for _ in range(recoveries):
            s.add(Event(kind="stall_recovered", effort_id=eid, ts=_ts(t))); t += 1
        s.add(Event(kind=last_kind, effort_id=eid, ts=_ts(t)))     # the newest event
        await s.commit()


async def test_watchdog_recovers_a_mid_dispatch_wedge(db_url):
    """An effort last seen at `worker_release` (dispatched, then silent 2h) is auto-re-engaged."""
    orch, chat, db = await _orch(db_url)
    try:
        await _seed(orch, "effort-wedged", last_kind="worker_release", age_min=120)
        await orch._sweep_stalled_efforts()
        assert await orch._event_count("effort-wedged", "stall_recovered") == 1
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "went quiet" in msgs and "effort-wedged" in msgs      # loud, not silent
    finally:
        await db.dispose()


async def test_watchdog_recovers_a_check_exec_terminal_wedge(db_url):
    """P21 F4 (gym-019): an abandon's trailing `check_exec` verify probe — whose verify→publish
    coroutine then died silently — left `check_exec` as the effort's LAST event, outside the old
    kind-gate, so the effort sat idle for 2h until a human `re-run it`. `check_exec` is a
    bridge-issued probe, never a human gate, so a silent-past-threshold `check_exec` must recover."""
    orch, chat, db = await _orch(db_url)
    try:
        await _seed(orch, "effort-checkexec", last_kind="check_exec", age_min=120)
        await orch._sweep_stalled_efforts()
        assert await orch._event_count("effort-checkexec", "stall_recovered") == 1
    finally:
        await db.dispose()


async def test_watchdog_still_skips_the_plan_approval_gate(db_url):
    """SAFETY boundary for F4: adding `check_exec` must not weaken the human-gate exclusion. An
    effort parked at `plan_drafted` (the Stage-3 plan-approval gate) is correctly awaiting the
    operator's `approve` and must NEVER be auto-re-engaged (§4.5 / the paper's dropped-signal)."""
    orch, chat, db = await _orch(db_url)
    try:
        await _seed(orch, "effort-plangate", last_kind="plan_drafted", age_min=200)
        await orch._sweep_stalled_efforts()
        assert await orch._event_count("effort-plangate", "stall_recovered") == 0
    finally:
        await db.dispose()


async def test_abandon_rotates_the_session_generation(db_url):
    """P21 F1 (gym-019): a re-run/auto-recovery after an abandon must NOT resume the rotted session
    that returned EMPTY twice. The `worker_turn_abandoned` event bumps `_session_for`'s generation,
    so the next dispatch starts FRESH (`~r{n}`) instead of reusing the same (bloated) id."""
    orch, _chat, db = await _orch(db_url)
    try:
        eid = "effort-abandoned"
        async with orch.db.session_factory() as s:
            s.add(Effort(id=eid, name=eid, channel_id=await orch.mgmt_channel_id(),
                         root_post_id=f"root-{eid}", state="active", lifecycle="open"))
            await s.commit()
        assert await orch._session_for(eid) == eid            # generation 0: the plain effort id
        async with orch.db.session_factory() as s:
            s.add(Event(kind="worker_turn_abandoned", effort_id=eid,
                        ts=datetime.now(timezone.utc).isoformat()))
            await s.commit()
        assert await orch._session_for(eid) == f"{eid}~r1"    # the abandon rotated it → fresh
    finally:
        await db.dispose()


async def test_stall_recovery_rotates_the_session_generation(db_url):
    """P23 F1-redux (gym-021): the silent-worker recovery re-engaged a hung worker into the SAME
    session, so it hung AGAIN and escalated on the last 2 tasks. `stall_recovered` now rotates the
    generation (like an abandon), so the recovery restarts from a FRESH session instead of the
    rotted one."""
    orch, _chat, db = await _orch(db_url)
    try:
        eid = "effort-silent"
        async with orch.db.session_factory() as s:
            s.add(Effort(id=eid, name=eid, channel_id=await orch.mgmt_channel_id(),
                         root_post_id=f"root-{eid}", state="active", lifecycle="open"))
            await s.commit()
        assert await orch._session_for(eid) == eid            # generation 0
        async with orch.db.session_factory() as s:
            s.add(Event(kind="stall_recovered", effort_id=eid,
                        ts=datetime.now(timezone.utc).isoformat()))
            await s.commit()
        assert await orch._session_for(eid) == f"{eid}~r1"    # the recovery rotated it → fresh
    finally:
        await db.dispose()


async def test_watchdog_skips_an_effort_awaiting_a_human_decision(db_url):
    """P24 — the AUTHORITATIVE gate check. An effort holding a pending operator decision (a drafted
    plan / a held merge) is awaiting `approve`/`merge` and must NEVER be re-engaged, whatever its
    last event kind (§4.5). This is the real check the fragile event-kind allow-list was a proxy for
    (the 2026-07-16 `plan_drafted` incident)."""
    orch, chat, db = await _orch(db_url)
    try:
        await _seed(orch, "effort-plan", last_kind="worker_release", age_min=200)  # normally recoverable
        orch._pending_plan["effort-plan"] = {"plan": None}     # ...but a human decision is pending
        await orch._sweep_stalled_efforts()
        assert await orch._event_count("effort-plan", "stall_recovered") == 0
    finally:
        await db.dispose()


async def test_watchdog_recovers_an_abandon_terminal_wedge(db_url):
    """P24 (gym-022): the effort's terminal event was `worker_turn_abandoned` — the very event P21 F1
    added — which the old allow-list did not cover, so it sat SILENT for 2 HOURS. Keying on silence
    (not the event kind) recovers it. The third mole in a row (wake_done → check_exec → abandon)."""
    orch, chat, db = await _orch(db_url)
    try:
        await _seed(orch, "effort-abandon", last_kind="worker_turn_abandoned", age_min=120)
        await orch._sweep_stalled_efforts()
        assert await orch._event_count("effort-abandon", "stall_recovered") == 1
    finally:
        await db.dispose()


async def test_watchdog_does_not_re_recover_after_it_escalated(db_url):
    """P24 deny-list: `stall_escalated` means the watchdog already hit the cap and asked for a
    re-run — re-recovering it would be the exact loop the escalation exists to stop."""
    orch, chat, db = await _orch(db_url)
    try:
        await _seed(orch, "effort-esc", last_kind="stall_escalated", age_min=120)
        await orch._sweep_stalled_efforts()
        assert await orch._event_count("effort-esc", "stall_recovered") == 0
    finally:
        await db.dispose()


async def test_watchdog_skips_a_fresh_dispatch(db_url):
    """A just-dispatched effort (well within the threshold) is left alone — no premature re-engage."""
    orch, chat, db = await _orch(db_url)
    try:
        await _seed(orch, "effort-fresh", last_kind="worker_project_set", age_min=2)
        await orch._sweep_stalled_efforts()
        assert await orch._event_count("effort-fresh", "stall_recovered") == 0
    finally:
        await db.dispose()


async def test_watchdog_escalates_after_the_recovery_cap(db_url):
    """Past `stall_max_recoveries` auto-re-engages, the watchdog stops looping and escalates loudly."""
    orch, chat, db = await _orch(db_url)
    try:
        await _seed(orch, "effort-stuck", last_kind="worker_release", age_min=120, recoveries=2)
        await orch._sweep_stalled_efforts()
        assert await orch._event_count("effort-stuck", "stall_escalated") == 1
        assert await orch._event_count("effort-stuck", "stall_recovered") == 2   # no new recovery
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "stalled mid-dispatch" in msgs and "stopped auto-retrying" in msgs
    finally:
        await db.dispose()


async def test_clone_failure_escalates_loudly_and_audits(db_url):
    """A clone/focus failure is surfaced to the operator + audited `focus_failed` (never the old
    silent card-flip) so the watchdog can pick it up."""
    orch, chat, db = await _orch(db_url)
    try:
        await _seed(orch, "effort-clone", last_kind="worker_acquire", age_min=1)
        res = SimpleNamespace(status="clone_failed", ok=False,
                              output="fatal: could not read from remote; clone failed", signal=None)
        await orch._handle_clone_failure("effort-clone", res)
        assert await orch._event_count("effort-clone", "focus_failed") == 1
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "couldn't set up its workspace" in msgs and "nothing ran" in msgs.lower()
        assert "not your code" in msgs.lower()                      # honest attribution
    finally:
        await db.dispose()


async def test_sweep_defers_while_a_worker_daemon_is_actually_running(db_url):
    """Restart-safe (live 2026-07-11): a bridge redeploy mid-task wipes the in-memory 'executing'
    marker, so the watchdog must ask the DAEMON — if a worker reports a RUNNING task, work IS
    happening; defer the sweep (re-dispatching would 409 the still-running worker). When workers are
    free again, the genuinely-wedged effort recovers."""
    orch, chat, db = await _orch(db_url)
    try:
        await _seed(orch, "effort-wedged", last_kind="worker_release", age_min=120)
        orch.harness.busy_urls = {"http://w1:8090"}          # a worker is still working
        await orch._sweep_stalled_efforts()
        assert await orch._event_count("effort-wedged", "stall_recovered") == 0   # deferred
        orch.harness.busy_urls = set()                        # workers free now
        await orch._sweep_stalled_efforts()
        assert await orch._event_count("effort-wedged", "stall_recovered") == 1   # recovered
    finally:
        await db.dispose()


async def test_watchdog_recovers_a_silent_running_worker(db_url):
    """Register #25 (arm D, 2026-07-17): a worker that HANGS mid-turn holds task status `running`
    forever, so the legacy `has_running_task` busy-defer sits idle indefinitely — arm D hung 20 min,
    GPU 0%, uncaught. The fix reads the daemon's per-agent-step event offset: a running worker whose
    offset has NOT advanced for `worker_silence_s` is hung → cancel the turn + recover the effort.
    Crucially this fires even though the effort's own DB events are RECENT (8 min < the 900s stall
    threshold) — liveness is judged from the WORKER's silence, not the effort's event age."""
    orch, chat, db = await _orch(db_url)
    try:
        await _seed(orch, "effort-hung", last_kind="worker_project_set", age_min=8)  # recent, not aged
        # bind the worker to the hung effort so base_url -> effort_id resolves
        async with orch.db.session_factory() as s:
            wi = (await s.execute(
                select(WorkerInstance).where(WorkerInstance.base_url == "http://w1:8090"))).scalar_one()
            wi.effort_id = "effort-hung"
            wi.sched_state = "computing"
            await s.commit()
        orch.harness.busy_urls = {"http://w1:8090"}              # daemon still reports RUNNING
        orch.harness.progress_task_ids = {"http://w1:8090": "task-x"}
        orch.harness.progress_offsets = {"http://w1:8090": 42}   # FROZEN — no agent-loop progress
        # we last saw offset 42 well beyond the silence window → the worker is hung
        stale = datetime.now(timezone.utc) - timedelta(seconds=orch.s.worker_silence_s + 60)
        orch._worker_progress = {"http://w1:8090": ("task-x", 42, stale)}
        await orch._sweep_stalled_efforts()
        assert ("http://w1:8090", "task-x") in orch.harness.cancelled   # hung turn cancelled
        assert await orch._event_count("effort-hung", "stall_recovered") == 1   # effort recovered
    finally:
        await db.dispose()


async def test_sweep_defers_while_a_worker_is_progressing(db_url):
    """The other half: a running worker whose offset is ADVANCING is genuinely working — never
    recovered, however long the turn (this is what protects legitimate long work from a wall-clock
    timeout). First sight of a worker is treated as alive (no prior observation to call it silent)."""
    orch, chat, db = await _orch(db_url)
    try:
        await _seed(orch, "effort-working", last_kind="worker_project_set", age_min=30)
        async with orch.db.session_factory() as s:
            wi = (await s.execute(
                select(WorkerInstance).where(WorkerInstance.base_url == "http://w1:8090"))).scalar_one()
            wi.effort_id = "effort-working"
            await s.commit()
        orch.harness.busy_urls = {"http://w1:8090"}
        orch.harness.progress_task_ids = {"http://w1:8090": "task-y"}
        orch.harness.progress_offsets = {"http://w1:8090": 10}
        # seen a while ago, but the offset has since ADVANCED (10 > 3) → alive, defer
        stale = datetime.now(timezone.utc) - timedelta(seconds=orch.s.worker_silence_s + 60)
        orch._worker_progress = {"http://w1:8090": ("task-y", 3, stale)}
        await orch._sweep_stalled_efforts()
        assert orch.harness.cancelled == []                             # nothing cancelled
        assert await orch._event_count("effort-working", "stall_recovered") == 0   # not recovered
    finally:
        await db.dispose()


async def test_watchdog_never_bypasses_the_plan_approval_gate(db_url):
    """`plan_drafted` is the Stage-3 PLAN APPROVAL gate (P3.9): an effort parked there is CORRECTLY
    awaiting the operator's `approve <effort>`, however long that takes. The watchdog must NEVER
    auto-re-engage it — that would bypass a human governance gate (§4.5). A quiet plan gate is the
    system working, not a stall. (2026-07-16: an earlier fix wrongly added `plan_drafted` to the
    mid-dispatch kinds, so the watchdog silently executed unapproved plans after 15 min.)"""
    orch, chat, db = await _orch(db_url)
    try:
        await _seed(orch, "effort-planned", last_kind="plan_drafted", age_min=240)
        await orch._sweep_stalled_efforts()
        assert await orch._event_count("effort-planned", "stall_recovered") == 0
        await _seed(orch, "effort-lifeplan", last_kind="lifecycle_plan_drafted", age_min=240)
        await orch._sweep_stalled_efforts()
        assert await orch._event_count("effort-lifeplan", "stall_recovered") == 0
    finally:
        await db.dispose()


async def test_watchdog_recovers_a_dry_run_strand(db_url):
    """Same coverage gap, one step later: an effort whose dry-run passed but never dispatched is a
    wedge the watchdog must recover (not an operator-hold)."""
    orch, chat, db = await _orch(db_url)
    try:
        await _seed(orch, "effort-dry", last_kind="dry_run_recorded", age_min=40)
        await orch._sweep_stalled_efforts()
        assert await orch._event_count("effort-dry", "stall_recovered") == 1
    finally:
        await db.dispose()


async def test_watchdog_never_recovers_an_effort_waiting_on_a_human(db_url):
    """P8 #2 — WAITING-ON-HUMAN is first-class: an effort parked at ANY human gate is never
    `stall_recovered`, at any age, even when its LAST EVENT is a mid-dispatch kind the watchdog
    would otherwise re-engage. The event-kind exclusion list is fragile (2026-07-16: adding
    `plan_drafted` to it made the watchdog execute unapproved plans); the direct waiting_on state
    check makes that class of mistake impossible — no timeout may ever bypass a human gate (§4.5)."""
    orch, chat, db = await _orch(db_url)
    try:
        # last event is worker_release — a kind the watchdog DOES recover (see the first test) —
        # but the effort is parked at the Stage-3 plan gate: it must be left alone forever.
        await _seed(orch, "effort-heldplan", last_kind="worker_release", age_min=100000)
        orch._pending_plan["effort-heldplan"] = {"proj_channel": "c", "root": "r",
                                                 "request": "req", "plan": None,
                                                 "asked_at": "2026-07-16T00:00:00+00:00"}
        await orch._sweep_stalled_efforts()
        assert await orch._event_count("effort-heldplan", "stall_recovered") == 0
        # same for the readiness/clarification hold…
        await _seed(orch, "effort-clarify", last_kind="worker_release", age_min=100000)
        orch._pending["effort-clarify"] = {"proj_channel": "c", "root": "r", "request": "req",
                                           "questions": ["which port?"], "asked_at": "t"}
        await orch._sweep_stalled_efforts()
        assert await orch._event_count("effort-clarify", "stall_recovered") == 0
        # …and the D4 merge gate.
        await _seed(orch, "effort-merge", last_kind="worker_release", age_min=100000)
        orch._pending_merge["merge-effort-merge"] = {"repo": "x", "pr_number": 7,
                                                     "effort_id": "effort-merge", "asked_at": "t"}
        await orch._sweep_stalled_efforts()
        assert await orch._event_count("effort-merge", "stall_recovered") == 0
        # the decision CLEARS the gate → the watchdog may recover it again
        orch._pending_plan.pop("effort-heldplan")
        await orch._sweep_stalled_efforts()
        assert await orch._event_count("effort-heldplan", "stall_recovered") == 1
    finally:
        await db.dispose()


async def test_waiting_on_is_answerable_in_one_call(db_url):
    """P8 #2 — "why is the GPU idle?" answerable from the org in one look: the status map reports
    `waiting-on-you` (distinct from working and from wedged/idle) and the rendered status carries
    the gate + the exact ask."""
    orch, chat, db = await _orch(db_url)
    try:
        await _seed(orch, "effort-gated", last_kind="plan_drafted", age_min=30)
        orch._pending_plan["effort-gated"] = {"proj_channel": "c", "root": "r",
                                              "request": "req", "plan": None,
                                              "asked_at": "2026-07-16T00:00:00+00:00"}
        w = orch._waiting_on("effort-gated")
        assert w is not None and w["gate"] == "plan_approval"
        assert "approve effort-gated" in w["ask"]
        assert w["asked_at"] == "2026-07-16T00:00:00+00:00"
        efforts = await orch.gate.snapshot(open_only=True)
        smap = await orch._effort_status_map(efforts)
        assert smap["effort-gated"] == "waiting-on-you"           # NOT idle, NOT wedged
        rendered = orch._render_status(efforts, smap)
        assert "waiting-on-you" in rendered and "approve effort-gated" in rendered
        assert "Waiting on you (1)" in rendered
        # an effort NOT at a gate stays honestly separate
        assert orch._waiting_on("effort-nope") is None
    finally:
        await db.dispose()


async def test_watchdog_recovers_a_post_publish_stall(db_url):
    """A delivery that PUBLISHED a branch but whose verify→PR→closure then STALLED (silent, no
    worker running) is a wedge the watchdog must recover — publishing is not the finish line (live
    2026-07-11: an auto-iteration re-published then went silent 20 min, both workers idle)."""
    orch, chat, db = await _orch(db_url)
    try:
        await _seed(orch, "effort-pub", last_kind="effort_published", age_min=30)
        await orch._sweep_stalled_efforts()
        assert await orch._event_count("effort-pub", "stall_recovered") == 1
    finally:
        await db.dispose()


async def test_an_effort_stranded_after_an_abandoned_turn_is_recovered(db_url):
    """LIVE GAP (gym-008, 2026-07-18): a worker turn that ends `abandoned` leaves `wake_done` as the
    effort's last event. `wake_done` was NOT in the mid-dispatch kinds, so the watchdog's kind-gate
    skipped it — the effort sat open+active with an idle worker and idle GPU for 31 min and would
    have stranded forever. An OPEN effort that has been silent past the threshold after a completed
    turn IS a stall: nothing followed the turn. (Safe by construction: the sweep only sees OPEN
    efforts, and human-gated / parked / actively-delegating ones are skipped earlier — so this can
    never bypass a human gate the way adding `plan_drafted` once did.)"""
    orch, chat, db = await _orch(db_url)
    try:
        await _seed(orch, "effort-stranded", last_kind="wake_done", age_min=31)
        await orch._sweep_stalled_efforts()
        assert await orch._event_count("effort-stranded", "stall_recovered") == 1
    finally:
        await db.dispose()


async def _bind_busy(orch, eid, task_id, total):
    async with orch.db.session_factory() as s:
        wi = (await s.execute(
            select(WorkerInstance).where(WorkerInstance.base_url == "http://w1:8090"))).scalar_one()
        wi.effort_id = eid
        await s.commit()
    orch.harness.busy_urls = {"http://w1:8090"}
    orch.harness.progress_task_ids = {"http://w1:8090": task_id}
    orch.harness.progress_totals = {"http://w1:8090": total}   # daemon line-count semantics


async def test_new_task_does_not_inherit_the_previous_tasks_offset(db_url):
    """ao-wd-offset (gym-002 19:00:09): the stored offset 56,200 belonged to the PREVIOUS task; the
    new task (9,000 lines) was probed from 56,200, read as frozen and cancelled as 'silent'. The new
    task must be probed from 0 and judged alive."""
    orch, chat, db = await _orch(db_url)
    try:
        await _seed(orch, "effort-new", last_kind="worker_project_set", age_min=8)
        await _bind_busy(orch, "effort-new", "task-new", 9000)
        stale = datetime.now(timezone.utc) - timedelta(seconds=orch.s.worker_silence_s + 60)
        orch._worker_progress = {"http://w1:8090": ("task-old", 56200, stale)}
        await orch._sweep_stalled_efforts()
        assert orch.harness.cancelled == []
        assert orch.harness.progress_calls[-1][2] == 0               # probed from 0, not 56,200
        assert orch._worker_progress["http://w1:8090"][:2] == ("task-new", 9000)   # state reset
        assert await orch._event_count("effort-new", "stall_recovered") == 0
    finally:
        await db.dispose()


async def test_genuinely_frozen_same_task_is_still_cancelled(db_url):
    """Threat model: same task id, no growth for worker_silence_s -> still hung and cancelled."""
    orch, chat, db = await _orch(db_url)
    try:
        await _seed(orch, "effort-frozen", last_kind="worker_project_set", age_min=8)
        await _bind_busy(orch, "effort-frozen", "task-z", 9000)
        stale = datetime.now(timezone.utc) - timedelta(seconds=orch.s.worker_silence_s + 60)
        orch._worker_progress = {"http://w1:8090": ("task-z", 9000, stale)}
        await orch._sweep_stalled_efforts()
        assert ("http://w1:8090", "task-z") in orch.harness.cancelled
        assert await orch._event_count("effort-frozen", "stall_recovered") == 1
    finally:
        await db.dispose()


def _mock_daemon(state):
    """httpx MockTransport daemon with the REAL /events semantics: events = lines[offset:],
    next_offset = offset + len(events). `state` = {"task": id, "lines": n, "cancelled": [...]}."""
    import httpx

    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path == "/tasks":
            return httpx.Response(200, json={"tasks": [{"task_id": state["task"], "status": "running"}]})
        if path.endswith("/events"):
            off = int(req.url.params.get("offset", 0))
            ev = ["{}"] * max(0, state["lines"] - off)
            return httpx.Response(200, json={"events": ev, "next_offset": off + len(ev)})
        if path.endswith("/cancel"):
            state["cancelled"].append(path.split("/")[2])
            return httpx.Response(200, json={})
        return httpx.Response(200, json={})
    return httpx.MockTransport(handler)


async def test_two_tick_real_client_does_not_cancel_a_healthy_new_task(db_url, monkeypatch):
    """T5 - ao-wd-offset, the full mechanism with the REAL LittleCoderHarness.running_task_progress:
    tick 1 stores the new task's offset, tick 2 (past worker_silence_s, task grew 9,000 -> 9,100)
    must find it alive. At base tick 1 probes from the old 56,200, stores it, and tick 2 reads it
    as frozen -> the healthy turn is cancelled (gym-002 19:00:09)."""
    import httpx
    from app.worker import harness as hmod
    from app.worker.harness import LittleCoderHarness

    state = {"task": "task-new", "lines": 9000, "cancelled": []}
    transport = _mock_daemon(state)
    real = httpx.AsyncClient
    monkeypatch.setattr(hmod.httpx, "AsyncClient",
                        lambda **kw: real(transport=transport, **kw))
    orch, chat, db = await _orch(db_url)
    try:
        orch.harness = LittleCoderHarness()
        await _seed(orch, "effort-t5", last_kind="worker_project_set", age_min=8)
        async with orch.db.session_factory() as s:
            wi = (await s.execute(select(WorkerInstance).where(
                WorkerInstance.base_url == "http://w1:8090"))).scalar_one()
            wi.effort_id = "effort-t5"
            await s.commit()
        url = "http://w1:8090"
        old = datetime.now(timezone.utc) - timedelta(seconds=orch.s.worker_silence_s + 60)
        orch._worker_progress = {url: ("task-old", 56200, old)}
        await orch._sweep_stalled_efforts()                       # tick 1
        t, off, _ = orch._worker_progress[url]
        orch._worker_progress[url] = (t, off, old)                # age past worker_silence_s
        state["lines"] = 9100                                     # the healthy task keeps working
        await orch._sweep_stalled_efforts()                       # tick 2
        assert state["cancelled"] == []
        assert orch._worker_progress[url][:2] == ("task-new", 9100)
        assert await orch._event_count("effort-t5", "stall_recovered") == 0
    finally:
        await db.dispose()


# ── ao-silence: a command running inside its own timeout is alive ─────────────────────────────────

import json as _json  # noqa: E402

_W1 = "http://w1:8090"


def _ev_start(cid, command, tool="bash", **extra):
    return _json.dumps({"type": "tool_execution_start", "toolCallId": cid, "toolName": tool,
                        "args": {"command": command, **extra}})


def _ev_end(cid, tool="bash"):
    return _json.dumps({"type": "tool_execution_end", "toolCallId": cid, "toolName": tool})


async def _bound_busy(orch, eid, lines):
    """A worker bound to `eid`, daemon RUNNING, offset frozen at 42, pi event stream `lines`."""
    await _seed(orch, eid, last_kind="worker_project_set", age_min=8)
    async with orch.db.session_factory() as s:
        wi = (await s.execute(select(WorkerInstance).where(WorkerInstance.base_url == _W1))).scalar_one()
        wi.effort_id = eid
        wi.sched_state = "computing"
        await s.commit()
    orch.harness.busy_urls = {_W1}
    orch.harness.progress_task_ids = {_W1: "task-c"}
    orch.harness.progress_offsets = {_W1: 42}
    orch.harness.event_lines = {_W1: lines}


def _frozen_for(orch, seconds, task="task-c"):
    orch._worker_progress = {_W1: (task, 42, datetime.now(timezone.utc) - timedelta(seconds=seconds))}


def _cmd_started_ago(orch, seconds):
    # (getattr: at the pre-fix tip there is no watch, and the cases must fail on behaviour, not here)
    for oc in getattr(orch, "_command_watch", {}).get(_W1, SimpleNamespace(open={})).open.values():
        oc.seen_at = datetime.now(timezone.utc) - timedelta(seconds=seconds)


async def _last_payload(orch, eid, kind):
    async with orch.db.session_factory() as s:
        ev = (await s.execute(select(Event).where(Event.effort_id == eid, Event.kind == kind)
                              .order_by(Event.id.desc()))).scalars().first()
        return (ev.payload if ev else None)


async def test_nine_minute_command_under_a_fifteen_minute_timeout_is_not_cancelled(db_url):
    """C7: `timeout 900 pytest` ran 9 min with the event offset frozen and was cancelled at 300 s."""
    orch, chat, db = await _orch(db_url)
    try:
        await _bound_busy(orch, "effort-long", [_ev_start("c1", "cd x && timeout 900 python -m pytest -q tests")])
        _frozen_for(orch, 9 * 60)
        await orch._sweep_stalled_efforts()                       # tick 1: sees the command start
        _cmd_started_ago(orch, 9 * 60)
        await orch._sweep_stalled_efforts()                       # tick 2: running 9 min of 15
        assert orch.harness.cancelled == []
        assert await orch._event_count("effort-long", "stall_recovered") == 0
    finally:
        await db.dispose()


async def test_command_with_no_declared_timeout_is_alive_within_the_default_exec_wait(db_url):
    orch, chat, db = await _orch(db_url)
    try:
        await _bound_busy(orch, "effort-nt", [_ev_start("c1", "python -m pytest -q tests")])
        _frozen_for(orch, 20 * 60)
        await orch._sweep_stalled_efforts()
        _cmd_started_ago(orch, 20 * 60)                           # < 1800 s default
        await orch._sweep_stalled_efforts()
        assert orch.harness.cancelled == []
    finally:
        await db.dispose()


async def test_silent_turn_with_no_running_command_is_cancelled_and_audited_as_silent(db_url):
    """Nothing running (the last command ended) and no output for worker_silence_s: hung."""
    orch, chat, db = await _orch(db_url)
    try:
        await _bound_busy(orch, "effort-quiet", [_ev_start("c1", "ls"), _ev_end("c1")])
        _frozen_for(orch, orch.s.worker_silence_s + 60)
        await orch._sweep_stalled_efforts()
        assert (_W1, "task-c") in orch.harness.cancelled
        pl = await _last_payload(orch, "effort-quiet", "stall_recovered")
        assert pl["reason"] == "worker_silent" and pl["rule"] == "silent"
    finally:
        await db.dispose()


async def test_command_past_its_timeout_plus_grace_is_cancelled(db_url):
    orch, chat, db = await _orch(db_url)
    try:
        await _bound_busy(orch, "effort-over", [_ev_start("c1", "timeout 600 python -m pytest -q tests")])
        _frozen_for(orch, 700)
        await orch._sweep_stalled_efforts()                       # registers the command
        assert orch.harness.cancelled == []                       # within 600 + grace
        _cmd_started_ago(orch, 600 + orch.s.worker_command_grace_s + 30)
        _frozen_for(orch, 900)
        await orch._sweep_stalled_efforts()
        assert (_W1, "task-c") in orch.harness.cancelled
        pl = await _last_payload(orch, "effort-over", "stall_recovered")
        assert pl["rule"] == "command_overran" and pl["timeout_s"] == 600
    finally:
        await db.dispose()


async def test_absolute_ceiling_caps_a_command_that_declares_a_huge_timeout(db_url):
    orch, chat, db = await _orch(db_url)
    try:
        await _bound_busy(orch, "effort-ceil", [_ev_start("c1", "timeout 99h python hang.py")])
        _frozen_for(orch, 3600)
        await orch._sweep_stalled_efforts()
        _cmd_started_ago(orch, getattr(orch.s, "worker_command_ceiling_s", 3600.0) + 5)
        await orch._sweep_stalled_efforts()
        assert (_W1, "task-c") in orch.harness.cancelled
        pl = await _last_payload(orch, "effort-ceil", "stall_recovered")
        assert pl["rule"] == "command_ceiling"
    finally:
        await db.dispose()


async def test_a_finished_command_stops_protecting_the_turn(db_url):
    """start then end in the stream: nothing is running, so the silence rule applies again."""
    orch, chat, db = await _orch(db_url)
    try:
        await _bound_busy(orch, "effort-done", [_ev_start("c1", "timeout 900 pytest"), _ev_end("c1")])
        _frozen_for(orch, orch.s.worker_silence_s + 10)
        await orch._sweep_stalled_efforts()
        assert (_W1, "task-c") in orch.harness.cancelled
    finally:
        await db.dispose()


def _budget(cmd, default=1800.0):
    from app.worker.command_watch import budget_s, parse_command
    return budget_s(parse_command(cmd), default)


def test_timeout_parsing_uses_the_timeout_of_what_is_running():
    assert _budget("timeout 900 pytest") == (900, True)
    assert _budget("cd x && timeout 900 python -m pytest -q tests 2>&1 | tail -3")[0] == 900
    assert _budget("timeout -k 10 5m pytest")[0] == 300
    assert _budget("timeout -k5 10m pytest")[0] == 600
    assert _budget("timeout --kill-after=5 600 pytest")[0] == 600
    assert _budget("/usr/bin/timeout 2h pytest")[0] == 7200
    assert _budget("FOO=1 timeout 90s pytest")[0] == 90
    assert _budget("timeout 2m a && timeout 1h b")[0] == 3720          # sequential: both budgets


def test_an_untimed_segment_gets_the_default_not_its_neighbours_timeout():
    # `timeout 120 ruff ...; pytest`: pytest must NOT be given 120 s
    assert _budget("timeout 120 ruff check . 2>&1 | tail -5; python -m pytest -q tests")[0] == 120 + 1800
    assert _budget("python -m pytest -q tests")[0] == 1800


def test_quoted_timeout_text_and_timeout_zero_are_not_budgets():
    assert _budget("grep -rn 'timeout 5' app; python -m pytest -q")[0] == 1800 + 1800
    assert _budget("echo \"timeout 5\" && pytest")[0] == 1800
    assert _budget("timeout 0 pytest")[0] == 1800                       # 0 = no timeout
    assert _budget("timeout $T pytest")[0] == 1800                      # unparseable: default
    assert _budget("pytest --timeout 900")[0] == 1800


async def test_overlapping_commands_every_one_is_judged(db_url):
    """A newer hung `timeout 10` beside an older `timeout 3000` is not protected by the older one."""
    orch, chat, db = await _orch(db_url)
    try:
        await _bound_busy(orch, "effort-overlap", [_ev_start("a", "timeout 3000 long-job"),
                                                   _ev_start("b", "timeout 10 hang")])
        _frozen_for(orch, 900)
        await orch._sweep_stalled_efforts()
        for oc in orch._command_watch[_W1].open.values():
            oc.seen_at = datetime.now(timezone.utc) - timedelta(seconds=60 if oc.call_id == "a" else 400)
        _frozen_for(orch, 900)
        orch._command_watch[_W1].open["b"].seen_at = datetime.now(timezone.utc) - timedelta(seconds=400)
        orch._command_watch[_W1].open["a"].seen_at = datetime.now(timezone.utc) - timedelta(seconds=500)
        await orch._sweep_stalled_efforts()
        assert (_W1, "task-c") in orch.harness.cancelled
        pl = await _last_payload(orch, "effort-overlap", "stall_recovered")
        assert pl["rule"] == "command_overran" and pl["timeout_s"] == 10 and "program" not in pl
    finally:
        await db.dispose()


_SECRETS = ["ghs_AbCdEf0123456789SecretTokenXYZ", "sk-live-9f8e7d6c5b4a39281706", "hunter2-db-password-77"]
_SECRET_CMD = ("timeout 10 git push https://x-access-token:ghs_AbCdEf0123456789SecretTokenXYZ@github.com/o/r.git HEAD"
               " && curl -H 'Authorization: Bearer sk-live-9f8e7d6c5b4a39281706' https://x"
               " && DB_PASSWORD=hunter2-db-password-77 ./run.sh")


async def test_no_secret_from_a_command_reaches_any_audit_payload_or_post(db_url):
    """The daemon's raw pi event stream is NOT redacted: nothing of the command line may be kept."""
    orch, chat, db = await _orch(db_url)
    try:
        await _bound_busy(orch, "effort-sec", [_ev_start("c1", _SECRET_CMD)])
        _frozen_for(orch, 900)
        await orch._sweep_stalled_efforts()
        orch._command_watch[_W1].open["c1"].seen_at = datetime.now(timezone.utc) - timedelta(seconds=4000)
        _frozen_for(orch, 4000)
        await orch._sweep_stalled_efforts()
        assert (_W1, "task-c") in orch.harness.cancelled
        async with orch.db.session_factory() as s:
            blob = " ".join(str(e.payload) for e in (await s.execute(select(Event))).scalars().all())
        blob += " ".join(str(p) for p in chat.posted)
        blob += repr(orch._command_watch)
        for sec in _SECRETS + ["x-access-token", "Authorization", "DB_PASSWORD"]:
            assert sec not in blob, sec
        pl = await _last_payload(orch, "effort-sec", "stall_recovered")
        assert pl["rule"] == "command_ceiling" and "program" not in pl
    finally:
        await db.dispose()


async def test_operator_text_units_ceiling_and_overrun_wording(db_url):
    orch, chat, db = await _orch(db_url)
    try:
        t = orch._silence_reason_text
        s10 = t({"rule": "command_overran", "ran_s": 130, "timeout_s": 10})
        assert "10 s timeout" in s10 and "hang" not in s10 and "0 min" not in s10 and "silent" not in s10
        c = t({"rule": "command_ceiling", "ran_s": 3600, "ceiling_s": 3600})
        assert "ceiling" in c and "60 min" in c and "silent" not in c
        assert "went silent" in t({"rule": "silent"})
    finally:
        await db.dispose()


# ── ao-silence round 3: degenerate durations never stop the watchdog; no `program` anywhere ───────

async def test_overflowing_timeout_digits_do_not_stop_the_watchdog(db_url):
    """T1: `timeout <400 digits>` is float inf; it used to raise OverflowError every tick, so NO stall was ever
    cancelled for ANY effort. Now it is read as undeclared (default), the ceiling still holds, and an
    unrelated wedged effort is still recovered."""
    orch, chat, db = await _orch(db_url)
    try:
        await _bound_busy(orch, "effort-inf", [_ev_start("c1", "timeout " + "9" * 400 + " sleep 1")])
        _frozen_for(orch, 900)
        await orch._sweep_stalled_efforts()                         # registers the command; must not raise
        orch._command_watch[_W1].open["c1"].seen_at = datetime.now(timezone.utc) - timedelta(seconds=36000)
        _frozen_for(orch, 36000)
        await _seed(orch, "effort-wedged", last_kind="worker_release", age_min=120)
        await orch._sweep_stalled_efforts()                         # must not raise
        assert (_W1, "task-c") in orch.harness.cancelled
        pl = await _last_payload(orch, "effort-inf", "stall_recovered")
        assert pl["rule"] == "command_ceiling" and isinstance(pl["timeout_s"], int)
    finally:
        await db.dispose()


def test_degenerate_durations_are_the_default():
    for bad in ("9" * 400, "9" * 12, "0", "1e999", "nan", "inf", "-5", "1.5.2", "$T", "9" * 400 + "h"):
        assert _budget(f"timeout {bad} sleep 1") == (1800, False), bad
    from app.worker.command_watch import CommandWatch
    cw = CommandWatch("t")
    for t in (float("inf"), float("nan"), -3, 10 ** 12, True):
        cw.feed([_json.dumps({"type": "tool_execution_start", "toolCallId": "x", "toolName": "bash",
                              "args": {"command": "ls", "timeout": t}}).replace("Infinity", "1e999")
                 .replace("NaN", "1e999")], datetime.now(timezone.utc))
        assert budget_s_of(cw) == 1800


def budget_s_of(cw):
    from app.worker.command_watch import budget_s
    return budget_s(next(iter(cw.open.values())).plan, 1800.0)[0]


async def test_an_error_for_one_worker_does_not_stop_the_sweep_for_another(db_url, monkeypatch):
    """An exception while judging/recovering one worker is logged and isolated: the other worker's hung
    turn is still cancelled and its effort recovered."""
    orch, chat, db = await _orch(db_url)
    try:
        await _seed(orch, "effort-ok", last_kind="worker_project_set", age_min=8)
        async with orch.db.session_factory() as s:
            wi = (await s.execute(select(WorkerInstance).where(WorkerInstance.base_url == _W1))).scalar_one()
            wi.effort_id = "effort-ok"
            await s.commit()
        orch.harness.busy_urls = {_W1}
        orch.harness.progress_task_ids = {_W1: "task-c"}
        orch.harness.progress_offsets = {_W1: 42}
        orch.harness.event_lines = {_W1: []}
        _frozen_for(orch, orch.s.worker_silence_s + 60)
        import app.orchestrator as om
        real = om.judge_frozen
        calls = {"n": 0}

        def boom(watch, *a, **k):
            calls["n"] += 1
            if watch is not None:
                raise RuntimeError("injected")
            return real(watch, *a, **k)
        monkeypatch.setattr(om, "judge_frozen", boom)
        await orch._sweep_stalled_efforts()                          # must not raise; falls back to silence rule
        assert (_W1, "task-c") in orch.harness.cancelled
        # and a failure while RECOVERING one worker must not stop the sweep either
        orch.harness.cancelled.clear()
        _frozen_for(orch, orch.s.worker_silence_s + 60)
        async def bad_cancel(url, tid):
            raise RuntimeError("cancel failed")
        monkeypatch.setattr(orch.harness, "cancel_task", bad_cancel)
        await orch._sweep_stalled_efforts()                          # must not raise
    finally:
        await db.dispose()


_FAKE_SK = "sk-" + "Zq8" * 7
_FAKE_GHP = "ghp_" + "A1b2C3d4E5" * 3 + "xyz012"
_FAKE_AKIA = "AKIA" + "QWERTY0123456789"[:16]
_FAKE_HOOK = "https://hooks.example.com/services/T000/B000/" + "Xx9Yy8Zz7Ww6Vv5Uu4"
_PROBES = [
    f"{_FAKE_SK} --serve",                                   # secret as the first word
    f"OPENAI_API_KEY= {_FAKE_SK} python app.py",             # empty-assignment typo: the key becomes the command
    f"cd x && {_FAKE_GHP}",                                  # after cd x &&
    f"timeout 10 {_FAKE_AKIA}",                              # after timeout 10
    _FAKE_HOOK,                                              # bare webhook URL
    f"'{_FAKE_SK}' go",                                      # quoted token
    f"./{_FAKE_GHP}.sh",                                     # token-named file
]


async def test_no_command_word_reaches_payloads_posts_or_state_whatever_its_shape(db_url):
    """T2: not even a first word is kept - `program` is gone from payloads, posts and the watch state."""
    orch, chat, db = await _orch(db_url)
    try:
        for n, probe in enumerate(_PROBES):
            eid = f"effort-p{n}"
            await _bound_busy(orch, eid, [_ev_start("c1", probe)])
            _frozen_for(orch, 900)
            await orch._sweep_stalled_efforts()
            orch._command_watch[_W1].open["c1"].seen_at = datetime.now(timezone.utc) - timedelta(seconds=4000)
            _frozen_for(orch, 4000)
            await orch._sweep_stalled_efforts()
            assert (_W1, "task-c") in orch.harness.cancelled, probe
            orch.harness.cancelled.clear()
            blob = repr(orch._command_watch) + " ".join(str(p) for p in chat.posted)
            async with orch.db.session_factory() as s:
                blob += " ".join(str(e.payload) for e in (await s.execute(select(Event))).scalars().all())
            for secret in (_FAKE_SK, _FAKE_GHP, _FAKE_AKIA, _FAKE_HOOK, "hooks.example.com", "Zq8Zq8", "A1b2C3"):
                assert secret not in blob, (probe, secret)
            pl = await _last_payload(orch, eid, "stall_recovered")
            assert set(pl) <= {"reason", "n", "rule", "ran_s", "timeout_s", "declared", "ceiling_s"}, pl
    finally:
        await db.dispose()


# ── ao-asyncexec: GOLDEN pi event streams captured from a real run ─────────────────────────────────
# The hand-built `_ev_start` lines above assumed the start of a running command is in the daemon's
# events file while it runs. It was not (findings ao-silence-ineffective-2026-10-11): the
# open-terminal-exec extension ran bash with spawnSync, which froze pi's event loop, and pi's
# `--mode json` writer flushes a line per write callback. These fixtures are pi's REAL stdout,
# captured 2026-10-11 from the little-coder 1.20.0 image (bundled pi 0.83.0, launcher + entrypoint-
# installed extension, LC_ROUTE_EXEC=1), a scripted stub model issuing one `bash` call `sleep 20` and
# a stub ot-exec; the "running" files are snapshots of the events file taken 10 s into the sleep:
#   pi_events_bash_running_async.jsonl     - the fixed (async spawn, sequential) extension
#   pi_events_bash_running_spawnsync.jsonl - the pre-fix extension: the stream stops mid-message
#   pi_events_bash_finished_async.jsonl    - the whole run, after the command and the turn ended

_FXD = ROOT / "tests" / "fixtures"


def _golden(name):
    return [ln for ln in (_FXD / name).read_text("utf-8").splitlines() if ln]   # CRLF checkouts too


def _judge(lines):
    from app.worker.command_watch import CommandWatch, judge_frozen
    now = datetime.now(timezone.utc)
    cw = CommandWatch("t")
    cw.feed(lines, now)
    return cw, judge_frozen(cw, 600, now + timedelta(seconds=30), silence_s=300, default_timeout_s=1800,
                            grace_s=120, ceiling_s=3600)


def test_golden_running_stream_opens_the_command_and_judges_it_running():
    lines = _golden("pi_events_bash_running_async.jsonl")
    assert '"tool_execution_start"' in lines[-1] and not any('"tool_execution_end"' in x for x in lines)
    cw, (hung, info) = _judge(lines)
    assert sorted(cw.open) == ["call_sleep20"]
    assert (hung, info["rule"]) == (False, "command_running")


def test_golden_spawnsync_stream_is_what_the_bridge_saw_before_the_fix():
    """Documents the failure: 10 s into `sleep 20` the file ends inside the assistant message - no
    start, not even its message_end - so the watch is empty and the turn reads as silent."""
    lines = _golden("pi_events_bash_running_spawnsync.jsonl")
    assert not any('"tool_execution_' in x for x in lines)
    assert not any('"type":"message_end","message":{"role":"assistant"' in x for x in lines)
    cw, (hung, info) = _judge(lines)
    assert cw.open == {}
    assert (hung, info["rule"]) == (True, "silent")


def test_golden_finished_stream_closes_the_command():
    lines = _golden("pi_events_bash_finished_async.jsonl")
    cw, (hung, info) = _judge(lines)
    assert cw.open == {}
    assert info["rule"] == "silent"


def test_hand_built_start_lines_have_the_golden_shape():
    """The hand-built `_ev_start` lines above must stay shaped like pi's real event (same keys)."""
    real = next(_json.loads(x) for x in _golden("pi_events_bash_running_async.jsonl")
                if '"tool_execution_start"' in x)
    built = _json.loads(_ev_start("c1", "sleep 20"))
    assert set(built) == set(real) and set(built["args"]) == set(real["args"])
    assert (real["toolName"], real["args"]) == ("bash", {"command": "sleep 20"})


async def test_golden_running_stream_through_the_sweep_is_not_cancelled(db_url):
    """The whole sweep (FakeHarness serving the golden lines as /tasks/{id}/events would) spares the
    turn: the real stream's start opens the command, which runs inside the default exec wait."""
    orch, chat, db = await _orch(db_url)
    try:
        await _bound_busy(orch, "effort-golden", _golden("pi_events_bash_running_async.jsonl"))
        _frozen_for(orch, orch.s.worker_silence_s + 60)
        await orch._sweep_stalled_efforts()
        _cmd_started_ago(orch, orch.s.worker_silence_s + 60)
        await orch._sweep_stalled_efforts()
        assert orch.harness.cancelled == []
        assert await orch._event_count("effort-golden", "stall_recovered") == 0
    finally:
        await db.dispose()


async def test_golden_spawnsync_stream_through_the_sweep_is_cancelled_as_silent(db_url):
    orch, chat, db = await _orch(db_url)
    try:
        await _bound_busy(orch, "effort-golden-old", _golden("pi_events_bash_running_spawnsync.jsonl"))
        _frozen_for(orch, orch.s.worker_silence_s + 60)
        await orch._sweep_stalled_efforts()
        assert (_W1, "task-c") in orch.harness.cancelled
        pl = await _last_payload(orch, "effort-golden-old", "stall_recovered")
        assert pl["rule"] == "silent"
    finally:
        await db.dispose()
