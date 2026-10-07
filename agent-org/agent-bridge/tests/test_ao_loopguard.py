"""ao-loopguard (agent-org gym-002, 2026-10-07): the org notices a looping or unproductive worker
turn and stops it, instead of grinding.

gym-002's evidence, replayed here from the RECORDED command lists (tests/fixtures/
gym002_loop_replays.json, the little-coder `activity` of each task):
  - the project_documentation lens alternated two commands 38 times each (117 commands, 0 findings);
    F31.4's "6 CONSECUTIVE identical commands" rule never tripped (max consecutive run = 1);
  - the goal_alignment lens ran 382 commands / 48 min with 0 findings, and another ran 221 with its
    first finding at command 212.
The guard: a sliding-window repeat/alternation rule (all worker turns; a coding turn is edit-aware),
a no-finding stop on review turns, a budget in the review prompts, and one event + one honest line
when it trips. The threat model is a FALSE STOP of a productive turn: a turn writing findings or
edits at a steady rate must never be stopped. Fakes and an in-process daemon stub only.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
from fastapi import FastAPI
from sqlalchemy import select

from app.adapters.chat import FakeChatAdapter
from app.config import Settings
from app.db import Database
from app.models import Event
from app.modules.capabilities import BranchDelivery
from app.modules.model_router import FakeModelClient
from app.orchestrator import _LENS_PROJECT_DOCUMENTATION, _REVIEW_BUDGET, Orchestrator
from app.worker import harness as harness_mod
from app.worker.harness import (
    FAKE_EDIT, FakeHarness, LittleCoderHarness, LoopGuard, LoopWatch,
)

ROOT = Path(__file__).resolve().parents[1]
REPO = "https://github.com/acme/gym.git"
GOAL = "a todo CLI that adds, lists, completes and deletes todos"
W1 = "http://w1:8090"

_FX = json.loads((ROOT / "tests" / "fixtures" / "gym002_loop_replays.json").read_text("utf-8"))
DOC_LENS = _FX["replays"]["doc_lens_ab_loop"]["commands"]        # 117, A/B from ~41
GOAL_382 = _FX["replays"]["goal_lens_382"]["commands"]           # 382, 0 findings
GOAL_221 = _FX["replays"]["goal_lens_221"]["commands"]           # 221, first finding at 212
FIX_24 = _FX["replays"]["fix_turn_24"]["commands"]               # a work turn that ended done
FINDINGS = _FX["finding_lines_goal_lens_221"]                    # 20 real FINDING lines
AB_START = 40                                                    # 0-based: the alternation begins


def _review() -> LoopGuard:
    s = Settings(_env_file=None)
    return LoopGuard("review", identical_run=s.lens_flail_repeats, window=s.loop_guard_window,
                     window_distinct=s.loop_guard_window_distinct,
                     window_repeats=s.loop_guard_window_repeats,
                     first_finding_by=s.review_first_finding_by, finding_gap=s.review_finding_gap)


def _work() -> LoopGuard:
    s = Settings(_env_file=None)
    return LoopGuard("work", identical_run=s.lens_flail_repeats, window=s.loop_guard_window,
                     window_distinct=s.loop_guard_window_distinct,
                     window_repeats=s.loop_guard_window_repeats)


def _first_trip(cmds, guard):
    w = LoopWatch(guard)
    for c in cmds:
        if c == FAKE_EDIT:
            w.progress()
            continue
        t = w.feed(c)
        if t is not None:
            return t
    return None


def _productive_lens() -> list[str]:
    """A productive review turn at a steady rate: the recorded goal lens's own exploration
    (commands 1-200 of 01M4BRVP), with one of its own real FINDING lines echoed every 8 commands.
    The interleaving is constructed (no gym-002 lens wrote findings at a steady rate); every
    command and every finding text is recorded."""
    out: list[str] = []
    fi = 0
    for i, c in enumerate(GOAL_221[:200], 1):
        out.append(c)
        if i % 8 == 0:
            text = FINDINGS[fi % len(FINDINGS)].replace("'", "")
            out.append(f"echo '{text} [{fi}]' >> /tmp/lens-findings.txt")
            fi += 1
    return out


# ── (1) the window rule on the recorded loops ────────────────────────────────────────────
def test_base_rule_never_trips_on_the_recorded_ab_loop():
    """RED evidence for the old rule: F31.4 (6 consecutive identical) never sees the A/B loop."""
    assert _first_trip(DOC_LENS, LoopGuard("review", identical_run=6, window=0)) is None


def test_window_rule_trips_the_recorded_ab_loop_early():
    ab = DOC_LENS[AB_START:]
    t = _first_trip(ab, _work())
    assert t is not None and t.guard in ("window_repeats", "window_distinct")
    assert t.commands <= 20, t.as_dict()                       # anchor: by ~command 20 of the loop
    # replayed as the whole recorded turn, with only the window rules (no-finding off)
    whole = _first_trip(DOC_LENS, _work())
    assert whole is not None and whole.commands <= AB_START + 20, whole.as_dict()


def test_review_guard_stops_the_382_command_zero_finding_review_at_the_threshold():
    s = Settings(_env_file=None)
    assert _first_trip(GOAL_382, LoopGuard("review", identical_run=6, window=0)).commands == 382
    t = _first_trip(GOAL_382, _review())
    assert t.guard == "no_finding" and t.commands == s.review_first_finding_by
    t2 = _first_trip(GOAL_221, _review())                     # first finding came at 212
    assert t2.guard == "no_finding" and t2.commands == s.review_first_finding_by


def test_a_productive_review_is_never_stopped():
    assert _first_trip(_productive_lens(), _review()) is None


def test_a_finding_gap_stops_a_review_that_stopped_producing():
    s = Settings(_env_file=None)
    cmds = [f"echo '{FINDINGS[0]}' >> /tmp/lens-findings.txt"] + GOAL_221[:60]
    t = _first_trip(cmds, _review())
    assert t.guard == "finding_gap" and t.commands == 1 + s.review_finding_gap


def test_repeating_one_finding_is_not_progress():
    line = f"echo '{FINDINGS[0]}' >> /tmp/lens-findings.txt"
    t = _first_trip([line] * 10, _review())
    assert t is not None and t.guard == "window_repeats"


def test_edit_then_test_is_progress_but_the_same_loop_without_edits_is_not():
    test_cmd = FIX_24[7]                       # a recorded suite run of the fix turn
    with_edits = [x for _ in range(12) for x in (FAKE_EDIT, test_cmd)]
    assert _first_trip(with_edits, _work()) is None
    t = _first_trip([test_cmd] * 12, _work())
    assert t is not None and t.commands == 4


def test_the_recorded_fix_turn_is_not_stopped():
    """A real work turn (24 commands, many suite re-runs, ended done) is untouched."""
    assert _first_trip(FIX_24, _work()) is None
    assert _first_trip(GOAL_221, _work()) is None             # nor 221 varied exploration commands


# ── (1)+(2) end to end through the lens sweep (RED at base: every lens turn ran to "done") ──
async def _orch(db_url, harness=None, **over):
    settings = Settings(
        _env_file=None, chat_adapter="fake",
        profiles_dir=str(ROOT / "profiles"), charters_dir=str(ROOT / "charters"),
        floor_dir=str(ROOT / "floor"), worker_instance_urls=W1,
        max_concurrent_workers=1, database_url=db_url, project_survey_enabled=False,
        review_mode="off", plan_approval="off", **over,
    )
    db = Database(db_url)
    orch = Orchestrator(settings, db, FakeChatAdapter(),
                        model_client=FakeModelClient(), harness=harness or FakeHarness())
    await orch.setup()
    return orch, db


async def _stop(orch, db):
    for _ in range(3):
        if orch._bg_tasks:
            await asyncio.gather(*list(orch._bg_tasks), return_exceptions=True)
    for t in (orch._capacity_task, orch._stall_task, orch._reaper_task):
        if t is not None:
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
    await db.dispose()


async def _effort(orch):
    await orch.projects.add("gym", REPO)
    eid, chan, root = await orch.router.open_effort("feat", project="gym")
    await orch.charters.set_goal(eid, GOAL, created_by="po")
    return eid, chan, root


def _delivery():
    return BranchDelivery(branch="agent/feat", exists=True, ahead=1, head_sha="abc1234567")


async def _payloads(db, kind):
    async with db.session_factory() as s:
        rows = (await s.execute(select(Event).where(Event.kind == kind))).scalars().all()
    return [r.payload for r in rows]


async def test_lens_sweep_stops_the_recorded_doc_lens_loop(db_url):
    orch, db = await _orch(db_url)
    try:
        eid, chan, root = await _effort(orch)
        orch.harness.stream_commands = list(DOC_LENS)
        await orch._lens_sweep(eid, chan, root, REPO, _delivery(), round_no=1)
        statuses = [p["status"] for p in await _payloads(db, "wake_done")]
        assert statuses and all(s == "flail" for s in statuses), statuses
        stops = await _payloads(db, "loop_guard_stopped")
        assert stops and all(p["commands"] <= AB_START + 20 for p in stops), stops
    finally:
        await _stop(orch, db)


async def test_lens_sweep_stops_the_382_command_review_at_the_threshold(db_url):
    orch, db = await _orch(db_url)
    try:
        eid, chan, root = await _effort(orch)
        orch.harness.stream_commands = list(GOAL_382)
        await orch._lens_sweep(eid, chan, root, REPO, _delivery(), round_no=1)
        stops = await _payloads(db, "loop_guard_stopped")
        assert stops and all(p["guard"] == "no_finding"
                             and p["commands"] == orch.s.review_first_finding_by for p in stops)
    finally:
        await _stop(orch, db)


async def test_lens_sweep_leaves_a_productive_review_alone(db_url):
    orch, db = await _orch(db_url)
    try:
        eid, chan, root = await _effort(orch)
        orch.harness.stream_commands = _productive_lens()
        await orch._lens_sweep(eid, chan, root, REPO, _delivery(), round_no=1)
        assert await _payloads(db, "loop_guard_stopped") == []
        assert all(p["status"] == "done" for p in await _payloads(db, "wake_done"))
    finally:
        await _stop(orch, db)


# ── (4) visibility: one event, one honest line ────────────────────────────────────────────
async def test_a_stop_is_one_event_and_one_honest_line(db_url):
    orch, db = await _orch(db_url)
    try:
        eid, chan, root = await _effort(orch)
        orch.harness.stream_commands = list(DOC_LENS)
        r = await orch.router.wake(eid, role="worker-default", thread_id=root, channel_id=chan,
                                   session_id=f"{eid}~lens9", instruction="x", repo=REPO,
                                   withhold_goal=True, loop_guard=orch.router.review_loop_guard(),
                                   turn_label="project_documentation lens review")
        assert r.status == "flail" and r.loop_trip is not None
        (ev,) = await _payloads(db, "loop_guard_stopped")
        assert ev["turn"] == "project_documentation lens review" and ev["guard"] == "no_finding"
        lines = [m for m in orch.chat.posted if "Loop guard" in m["message"]]
        assert len(lines) == 1, lines
        text = lines[0]["message"]
        assert "\n" not in text and "project_documentation lens review" in text
        assert "40 commands" in text and "FINDING" in text
        assert lines[0].get("thread_id") == root
        assert not any("went silent" in m["message"] for m in orch.chat.posted)
    finally:
        await _stop(orch, db)


# ── (3) the prompts carry the budget; the documentation lens is checkable ─────────────────
async def test_review_prompts_carry_the_budget_and_the_doc_lens_is_scoped(db_url):
    assert "at most ~40 commands" in _REVIEW_BUDGET and "after 15 commands" in _REVIEW_BUDGET
    assert "git log -n 20" in _LENS_PROJECT_DOCUMENTATION
    assert "Do not inspect old file contents or count tests" in _LENS_PROJECT_DOCUMENTATION
    orch, db = await _orch(db_url)
    try:
        eid, chan, root = await _effort(orch)
        await orch._lens_sweep(eid, chan, root, REPO, _delivery(), round_no=1)
        lens_wakes = [w for w in orch.harness.wakes if "~lens" in w["session_id"]]
        assert lens_wakes and all(_REVIEW_BUDGET in w["prompt"] for w in lens_wakes)
        assert all(w["loop_guard"] is not None and w["loop_guard"].kind == "review"
                   and w["loop_guard"].first_finding_by == orch.s.review_first_finding_by
                   for w in lens_wakes)
        await orch._mode_b_phase(eid, chan, root, REPO, _delivery())
        modeb = [w for w in orch.harness.wakes if "~modeb" in w["session_id"]]
        assert modeb and _REVIEW_BUDGET in modeb[0]["prompt"]
        assert modeb[0]["loop_guard"].kind == "review"
    finally:
        await _stop(orch, db)


# ── coding turns: armed (edit-aware); a stop routes into the fork + plan-first re-ask ───────
async def test_a_work_turn_gets_the_work_guard_and_a_loop_stop_replans_honestly(db_url):
    orch, db = await _orch(db_url)
    try:
        await orch.projects.add("app", "https://github.com/acme/app.git")
        eid, chan, root = await orch.router.open_effort("spin", project="app")
        orch.harness.stream_commands = list(DOC_LENS[AB_START:])
        await orch.delegate(eid, chan, root, "port the parser to the new API")
        for _ in range(3):
            if orch._bg_tasks:
                await asyncio.gather(*list(orch._bg_tasks), return_exceptions=True)
        coding = [w for w in orch.harness.wakes if not w.get("plan_only")]
        assert coding and coding[0]["loop_guard"] is not None
        assert coding[0]["loop_guard"].kind == "work"
        stops = await _payloads(db, "loop_guard_stopped")
        assert stops and stops[0]["turn"] == "work"
        assert await _payloads(db, "flail_replanned")
        texts = " ".join(m["message"] for m in orch.chat.posted)
        assert "looping on the same commands" in texts
        assert "without a single edit" not in texts
    finally:
        await _stop(orch, db)


async def test_work_guard_can_be_switched_off(db_url):
    orch, db = await _orch(db_url, loop_guard_work=False)
    try:
        assert orch.router.work_loop_guard() is None
    finally:
        await _stop(orch, db)


# ── the REAL harness poll loop against a little-coder stub (growing activity + edits) ──────
class _ReplayDaemon:
    """A little-coder daemon stub: each GET /tasks/<id> reveals one more recorded command; the task
    is `done` once all are shown. `edits_after[i]` = the edit count once command i is shown; None =
    a daemon older than ao-loopguard (no `edits` field)."""

    def __init__(self, cmds, edits_after=None) -> None:
        self.cmds, self.edits_after = cmds, edits_after
        self.markers: list[str] | None = None   # round 2: workspace_marker per shown command
        self.shown = 0
        self.cancelled: list[str] = []
        app = FastAPI()

        @app.post("/tasks")
        async def post_task() -> dict:
            return {"task_id": "r1", "status": "queued"}

        @app.get("/tasks/{tid}")
        async def get_task(tid: str) -> dict:
            if tid in self.cancelled:
                return {"task_id": tid, "status": "abandoned", "activity": []}
            self.shown = min(len(self.cmds), self.shown + 1)
            v = {"task_id": tid, "status": "running" if self.shown < len(self.cmds) else "done",
                 "answer": "" if self.shown < len(self.cmds) else "finished",
                 "activity": [{"command": c, "ok": True} for c in self.cmds[:self.shown]]}
            if self.edits_after is not None:
                v["edits"] = self.edits_after[self.shown - 1]
            if self.markers is not None:
                v["workspace_marker"] = self.markers[self.shown - 1]
            return v

        @app.post("/tasks/{tid}/cancel")
        async def cancel(tid: str) -> dict:
            self.cancelled.append(tid)
            return {"ok": True}

        self.app = app


_REAL_CLIENT = httpx.AsyncClient


def _route(monkeypatch, daemon) -> None:
    real = _REAL_CLIENT
    transport = httpx.ASGITransport(app=daemon.app)

    def client(*a, **kw):
        kw["transport"] = transport
        return real(*a, **kw)

    monkeypatch.setattr(harness_mod.httpx, "AsyncClient", client)


def _h() -> LittleCoderHarness:
    return LittleCoderHarness(poll_interval_s=0.0, poll_timeout_s=60.0)


async def test_real_harness_stops_the_ab_loop_and_cancels_the_task(monkeypatch):
    d = _ReplayDaemon(DOC_LENS)
    _route(monkeypatch, d)
    r = await _h().wake(W1, "s", "p", loop_guard=_review())
    assert r.status == "flail" and d.cancelled == ["r1"]
    assert r.loop_trip.commands == 40 and d.shown == 40
    d2 = _ReplayDaemon(DOC_LENS)
    _route(monkeypatch, d2)
    r2 = await _h().wake(W1, "s", "p", max_repeat=6)          # F31.4 alone: runs to the end
    assert r2.status == "done" and d2.cancelled == []


async def test_real_harness_work_guard_counts_edits_and_is_inert_without_them(monkeypatch):
    test_cmd = FIX_24[7]
    cmds = [test_cmd] * 12
    # an edit lands before every suite run: progress, never stopped
    d = _ReplayDaemon(cmds, edits_after=list(range(1, 13)))
    _route(monkeypatch, d)
    r = await _h().wake(W1, "s", "p", loop_guard=_work())
    assert r.status == "done" and d.cancelled == []
    # the same suite run 12 times and NO edit: a loop, stopped at the 4th
    d2 = _ReplayDaemon(cmds, edits_after=[0] * 12)
    _route(monkeypatch, d2)
    r2 = await _h().wake(W1, "s", "p", loop_guard=_work())
    assert r2.status == "flail" and r2.loop_trip.commands == 4
    assert r2.output.startswith("LOOP-GUARD:")
    # a daemon that does not report edits: the work guard cannot tell, so it never stops
    d3 = _ReplayDaemon(cmds, edits_after=None)
    _route(monkeypatch, d3)
    r3 = await _h().wake(W1, "s", "p", loop_guard=_work())
    assert r3.status == "done" and d3.cancelled == []
