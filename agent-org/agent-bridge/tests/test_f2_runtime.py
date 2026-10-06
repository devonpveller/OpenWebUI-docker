"""f2-runtime (followups-2, tracker G38 part b): the agent-bridge leftovers of ef-worker-model / ef-lane-500.

- G1: a re-run the operator was told is "Dispatching workers now" must not leave a model refusal
  pinned when the bridge dies before `scheduler.acquire` (repro: the ef-worker-model attempt-3
  tester's `test_T3_gap_bridge_dies_between_rerun_and_acquire`, %TEMP%\\t3-efwm-x9, made asserting).
  Threat model: a still-refused effort is never re-run in a loop.
- O1: a refused effort-thread reply is handled (posted once, event processed), not raised - so a
  restart's catch-up does not replay it. Threat model: a non-model failure still retries.
- The per-effort filter of `_model_refusal_unresolved` is pinned.
- C4: set_model's lost race against ANOTHER registry (process) refreshes this registry's cache.

Real Orchestrator/Router/LittleCoderHarness against the in-process stub daemon of
test_worker_model (no socket, no model, no GPU); sqlite via conftest's db_url. A "restart" is a
new Orchestrator on the same DB file."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from sqlalchemy import select

from app.models import Event, Profile
from app.modules.profiles import ConcurrentProfileChange, ProfileRegistry
from tests.test_profile_lane_errors import _HookDb, _app
from tests.test_worker_model import (
    REPO, StubDaemon, _Down, _Switch, _backdate, _orch, _route_harness_to, _wake,
)


async def _kinds(db, eid):
    async with db.session_factory() as s:
        return [k for (k,) in (await s.execute(
            select(Event.kind).where(Event.effort_id == eid).order_by(Event.id))).all()]


async def _drain(orch):
    for _ in range(50):
        if not orch._bg_tasks:
            return
        await asyncio.gather(*list(orch._bg_tasks), return_exceptions=True)


async def _sweep(orch, db, eid):
    """One stall-watchdog pass over a backdated effort: (stall_recovered, stall_escalated) totals."""
    await _backdate(db, eid)
    orch._delegating.discard(eid)
    await orch._sweep_stalled_efforts()
    await _drain(orch)
    return (await orch._event_count(eid, "stall_recovered"),
            await orch._event_count(eid, "stall_escalated"))


async def _rerun_then_die(orch, monkeypatch, eid):
    """The operator's NL re-run (`_reengage` posts "Dispatching workers now" and spawns delegate),
    then the bridge dies before scheduler.acquire: acquire never returns and the task is cancelled."""
    gate = asyncio.Event()

    async def _never(*a, **k):
        await gate.wait()
    monkeypatch.setattr(orch.scheduler, "acquire", _never)
    await orch._reengage([eid], mgmt_channel="mgmt-chan", mgmt_thread="mt1")
    await asyncio.sleep(0.2)
    for t in list(orch._bg_tasks):
        t.cancel()
    await asyncio.gather(*list(orch._bg_tasks), return_exceptions=True)
    return [p["message"] for p in orch.chat.posted if p.get("thread_id") == "mt1"]


# ── G1 ─────────────────────────────────────────────────────────────────────────────────────────

async def test_T3_gap_bridge_dies_between_rerun_and_acquire(db_url, monkeypatch):
    """The attempt-3 tester's repro, asserting. Refuse -> fix -> re-run ("Dispatching workers now")
    -> the bridge dies before acquire. After a restart the effort is NOT read as refused, and the
    watchdog recovers it once (base: unresolved=True, three sweeps (0, 0), zero posts)."""
    strict = StubDaemon(allowed=("local-large",))
    _route_harness_to(monkeypatch, strict)
    orch, chat, db = await _orch(db_url)
    eid, chan, root = await orch.router.open_effort("t3-gap")
    await orch.charters.set_goal(eid, "edit one file", created_by="po")
    with pytest.raises(httpx.HTTPStatusError):
        await _wake(orch, eid, chan, root)
    await orch.profiles.set_model("worker-default", "local-large",
                                  registered={"local-large", "local-small"})
    mgmt = await _rerun_then_die(orch, monkeypatch, eid)
    assert mgmt and "Dispatching workers now" in mgmt[-1]       # the operator's last word
    await db.dispose()
    orch2, chat2, db2 = await _orch(db_url)
    try:
        assert not await orch2._model_refusal_unresolved(eid)
        rs = [await _sweep(orch2, db2, eid) for _ in range(3)]
        rec, esc = rs[-1]
        assert rec + esc >= 1, rs                                # the watchdog owns it again
        assert len(chat2.posted) > 0                             # and says so
    finally:
        await db2.dispose()


async def test_g1_a_still_refused_effort_is_not_re_run_in_a_loop_after_a_lost_dispatch(
        db_url, monkeypatch):
    """Threat model: refuse, NO fix, re-run, bridge dies before acquire. After the restart the lost
    dispatch is recovered once; that re-run is refused again (its intent and acquire precede its
    refusal), so the effort re-pins and later sweeps re-run nothing."""
    strict = StubDaemon(allowed=("local-large",))
    _route_harness_to(monkeypatch, strict)
    orch, chat, db = await _orch(db_url)
    eid, chan, root = await orch.router.open_effort("g1-loop")
    await orch.charters.set_goal(eid, "edit one file", created_by="po")
    with pytest.raises(httpx.HTTPStatusError):
        await _wake(orch, eid, chan, root)
    assert len(strict.task_bodies) == 1
    await _rerun_then_die(orch, monkeypatch, eid)
    await db.dispose()
    orch2, chat2, db2 = await _orch(db_url)
    try:
        assert not await orch2._model_refusal_unresolved(eid)
        await _sweep(orch2, db2, eid)                            # the one recovery of the lost dispatch
        assert len(strict.task_bodies) == 2                      # it really re-ran ...
        assert await orch2._model_refusal_unresolved(eid)        # ... was refused, and re-pinned
        for _ in range(3):
            await _sweep(orch2, db2, eid)
        assert len(strict.task_bodies) == 2                      # no loop
        ks = await _kinds(db2, eid)
        assert ks.count("worker_model_refused") == 2
    finally:
        await db2.dispose()


async def test_g1_an_operator_re_run_refused_again_stays_pinned(db_url, monkeypatch):
    """Threat model, no crash: the intent `_reengage`/`delegate` log does not unpin a re-run that is
    itself refused - the refusal is newer than both its intent and its acquire."""
    strict = StubDaemon(allowed=("local-large",))
    _route_harness_to(monkeypatch, strict)
    orch, chat, db = await _orch(db_url)
    try:
        eid, chan, root = await orch.router.open_effort("g1-again")
        await orch.charters.set_goal(eid, "edit one file", created_by="po")
        with pytest.raises(httpx.HTTPStatusError):
            await _wake(orch, eid, chan, root)
        await orch._reengage([eid], mgmt_channel="mgmt-chan", mgmt_thread="mt1")
        await _drain(orch)
        ks = await _kinds(db, eid)
        assert ks.count("dispatch_intent") >= 1 and ks.count("worker_model_refused") == 2
        assert await orch._model_refusal_unresolved(eid)
        n = len(strict.task_bodies)
        for _ in range(3):
            assert await _sweep(orch, db, eid) == (0, 0)
        assert len(strict.task_bodies) == n
    finally:
        await db.dispose()


# ── the per-effort filter (test gap from attempt 3) ───────────────────────────────────────────

async def test_refusal_is_resolved_only_by_the_same_efforts_later_dispatch(db_url, monkeypatch):
    """Another effort's acquire/intent and the project survey's never resolve a refusal; removing
    `Event.effort_id == eid` from `_model_refusal_unresolved` turns this red."""
    sw = _Switch(StubDaemon(allowed=("local-large",)))
    _route_harness_to(monkeypatch, sw)
    orch, chat, db = await _orch(db_url)
    try:
        a, chan, root = await orch.router.open_effort("pin-a")
        b, chan_b, root_b = await orch.router.open_effort("pin-b")
        with pytest.raises(httpx.HTTPStatusError):
            await _wake(orch, a, chan, root)
        sw.cur = StubDaemon(allowed=("local-large", "local-small"))
        assert (await _wake(orch, b, chan_b, root_b)).ok
        await orch.audit.log("dispatch_intent", effort_id=b, payload={"source": "test"})
        await orch.router.survey_project(REPO)
        assert await orch._model_refusal_unresolved(a)
        assert not await orch._model_refusal_unresolved(b)
    finally:
        await db.dispose()


# ── O1 ─────────────────────────────────────────────────────────────────────────────────────────

def _reply(chan, root, pid="p-reply", ts=10):
    return {"channel_id": chan, "thread_id": root, "id": pid, "message": "please continue",
            "user_id": "u1", "ts": ts}


async def _restart_and_catch_up(db_url, monkeypatch, chan, event):
    """A bridge restart whose boot catch-up sees `event` again in the effort channel."""
    orch2, chat2, db2 = await _orch(db_url)

    async def posts_since(channel_id, since_ms):
        return [event] if channel_id == chan else []
    monkeypatch.setattr(chat2, "posts_since", posts_since, raising=False)
    orch2.events.track_channel(chan)
    replayed = await orch2.events.catch_up()
    return orch2, chat2, db2, replayed


async def test_o1_a_refused_effort_thread_reply_is_not_replayed_on_restart(db_url, monkeypatch):
    """The reply is refused for its model: posted once, the event is processed, and a restart's
    catch-up does not dispatch it again (base: dispatch raises, the event stays unprocessed, the
    restart replays it - a second refusal post and a second /tasks)."""
    strict = StubDaemon(allowed=("local-large",))
    _route_harness_to(monkeypatch, strict)
    orch, chat, db = await _orch(db_url)
    eid, chan, root = await orch.router.open_effort("o1-reply")
    ev = _reply(chan, root)
    assert await orch.events.dispatch(ev) is True                # handled, not raised
    refusals = [p for p in chat.posted if "refused model" in p["message"]]
    assert len(refusals) == 1 and refusals[0].get("thread_id") == root
    assert len(strict.task_bodies) == 1
    await db.dispose()
    orch2, chat2, db2, replayed = await _restart_and_catch_up(db_url, monkeypatch, chan, ev)
    try:
        assert replayed == 0
        assert len(strict.task_bodies) == 1                      # the stale reply never ran again
        assert not any("refused model" in p["message"] for p in chat2.posted)
        assert (await _kinds(db2, eid)).count("worker_model_refused") == 1
    finally:
        await db2.dispose()


async def test_o1_a_non_model_failure_of_the_reply_still_retries(db_url, monkeypatch):
    """Threat model: a non-model failure (a daemon 500) still raises out of handle_event, the event
    stays unprocessed, and the restart's catch-up retries it."""
    down = _Down(500)
    _route_harness_to(monkeypatch, down)
    orch, chat, db = await _orch(db_url)
    eid, chan, root = await orch.router.open_effort("o1-500")
    ev = _reply(chan, root, pid="p-500")
    with pytest.raises(httpx.HTTPStatusError):
        await orch.events.dispatch(ev)
    assert down.posts == 1
    await db.dispose()
    orch2, chat2, db2, replayed = await _restart_and_catch_up(db_url, monkeypatch, chan, ev)
    try:
        assert down.posts == 2                                   # retried after the restart
        assert replayed == 0                                     # (failed again: not counted as handled)
        assert not await orch2.events._already_processed("p-500")
    finally:
        await db2.dispose()


# ── C4 ─────────────────────────────────────────────────────────────────────────────────────────

async def test_c4_set_model_losing_to_another_registry_refreshes_its_cache(db_url):
    """A lane flip by ANOTHER registry (process) commits while set_model is between its read and its
    commit: set_model loses (ConcurrentProfileChange) and its own cache must show the winner's row
    without an outside refresh (base: the cache still says lane=local)."""
    orch, app = await _app(db_url)
    async with app.router.lifespan_context(app):
        real_db = orch.profiles.db
        other = ProfileRegistry(real_db, str(orch.profiles.dir))
        await other.refresh()
        orch.profiles.db = _HookDb(real_db, lambda: other.set_lane("pm", "cloud"))
        with pytest.raises(ConcurrentProfileChange):
            await orch.profiles.set_model("pm", "local-small", registered={"local-small"})
        orch.profiles.db = real_db
        async with real_db.session_factory() as s:
            active = (await s.execute(select(Profile).where(
                Profile.name == "pm", Profile.active.is_(True)))).scalars().all()
        assert len(active) == 1 and active[0].lane == "cloud"
        assert orch.profiles.get("pm").lane == "cloud"            # no refresh() by the test
        assert orch.profiles.get("pm").model == active[0].model
    await orch.db.dispose()
