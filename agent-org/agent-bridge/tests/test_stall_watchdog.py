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
