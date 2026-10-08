"""ao-loopguard round 3 (tester attempt 2 FAIL, plan inadequate).

- A1: a daemon that reports `edits` but omits `workspace_marker` (its scan gave up: > 20000 files,
  or older daemon) left the work guard counting edit-tool calls only, blind to bash edits -> a
  `sed -i` / heredoc coding turn was stopped at command 8. The work guard now watches ONLY while the
  snapshot carries a marker. (Each daemon-side cause of an omitted marker: little-coder
  tests/test_task_view_edits.py; this file covers what the bridge does with the omission.)
- Change-nothing wakes in the plain effort session (the STATE CHECK, the project CHECK run) get the
  readonly guard, not the work guard.
- Readonly turns get a LOOSER window back: <= 2 distinct in 12, or one command >= 7 times in 12. The
  tester's QA shapes run through; the gym-002 A/B loop and a `list`/`add x` alternation stop.
Fakes and an in-process daemon stub only.
"""

from __future__ import annotations

from test_ao_loopguard import (
    AB_START, DOC_LENS, FIX_24, GOAL_221, W1, _first_trip, _h, _orch, _payloads, _ReplayDaemon,
    _route, _stop, _work,
)
from test_ao_loopguard_r2 import HEREDOC_LOOP, PYWRITE_LOOP, QA_CMDS, SED_LOOP, SUITE, _delegate

LIST = "cd /workspace && python3 todo.py list"


# ── A1: no marker -> the work guard is inert, whatever `edits` says ───────────────────────
async def test_a1_edits_without_a_marker_never_stops_a_bash_editing_turn(monkeypatch):
    for cmds in (SED_LOOP, HEREDOC_LOOP, PYWRITE_LOOP):
        d = _ReplayDaemon(cmds, edits_after=[0] * len(cmds))       # edits reported, no marker
        _route(monkeypatch, d)
        r = await _h().wake(W1, "s", "p", loop_guard=_work())
        assert r.status == "done" and d.cancelled == [], cmds[0]


async def test_omitted_marker_any_cause_keeps_the_work_guard_inert(monkeypatch):
    """Whatever the cause (cap, unreadable root, an older daemon), the bridge sees the same thing: a
    snapshot without `workspace_marker` (or with null). Even a true no-edit loop is not stopped then -
    the work guard must never guess."""
    loop = [SUITE] * 12
    for edits, marker in ((None, None), ([0] * 12, None), ([0] * 12, "null"), (None, "null")):
        d = _ReplayDaemon(loop, edits_after=edits)
        if marker == "null":
            d.markers = [None] * 12
        _route(monkeypatch, d)
        r = await _h().wake(W1, "s", "p", loop_guard=_work())
        assert r.status == "done" and d.cancelled == [], (edits, marker)


async def test_a_marker_that_drops_out_mid_turn_pauses_the_guard(monkeypatch):
    """Marker present, then omitted for the rest of the turn (the workspace grew past the cap):
    commands seen without a marker are not fed, so the loop is not stopped on stale evidence."""
    loop = [SUITE] * 12
    d = _ReplayDaemon(loop)
    d.markers = ["m0", "m0"] + [None] * 10
    _route(monkeypatch, d)
    r = await _h().wake(W1, "s", "p", loop_guard=_work())
    assert r.status == "done" and d.cancelled == []


async def test_with_a_marker_the_no_edit_loop_still_stops(monkeypatch):
    ab = DOC_LENS[AB_START:]
    d = _ReplayDaemon(ab, edits_after=[0] * len(ab))
    d.markers = ["m0"] * len(ab)
    _route(monkeypatch, d)
    r = await _h().wake(W1, "s", "p", loop_guard=_work())
    assert r.status == "flail" and r.loop_trip.commands <= 20


async def test_fake_daemon_without_a_marker_is_inert_on_a_coding_step(db_url):
    orch, db = await _orch(db_url)
    try:
        orch.harness.reports_edits = False      # the fake reports no workspace_marker
        orch.harness.stream_commands = list(DOC_LENS[AB_START:])
        await _delegate(orch, "port the parser to the new API")
        assert await _payloads(db, "loop_guard_stopped") == []
    finally:
        await _stop(orch, db)


# ── readonly: the looser window ──────────────────────────────────────────────────────────
async def test_readonly_window_lets_qa_shapes_run_and_stops_cycles(db_url):
    orch, db = await _orch(db_url)
    try:
        g = orch.router.readonly_loop_guard()
        # must NOT stop: the tester's QA turn; input-then-check (a check after every add); a long
        # varied review; the recorded fix turn
        add_list = [x for i in range(20) for x in (f"cd /workspace && python3 todo.py add 'item {i}'",
                                                   LIST)]
        for cmds in (QA_CMDS, add_list, GOAL_221, FIX_24):
            assert _first_trip(cmds, g) is None, cmds[0]
        # must stop: gym-002's A/B loop (replayed from its start, and as the whole turn) ...
        t = _first_trip(DOC_LENS[AB_START:], g)
        assert t is not None and t.guard == "window_distinct" and t.commands <= 20
        assert _first_trip(DOC_LENS, g).commands <= AB_START + 20
        # ... a `list` / `add x` alternation (the same two commands, round and round) ...
        alt = [LIST, "cd /workspace && python3 todo.py add x"] * 40
        assert _first_trip(alt, g).commands == 12
        # ... a check run back-to-back more than half the window, and 6 identical in a row
        burst = ["cd /workspace && python3 todo.py add a", LIST, LIST, "cd /workspace && ls", LIST,
                 LIST, "cd /workspace && cat todo.py", LIST, LIST, LIST]
        assert _first_trip(burst, g).guard == "window_repeats"
        assert _first_trip([LIST] * 6, g).commands == 6
    finally:
        await _stop(orch, db)


async def test_a_qa_turn_looping_list_add_is_stopped(db_url):
    orch, db = await _orch(db_url, qa_gate="on", qa_code_review=False)
    try:
        from test_ao_loopguard import _delivery, _effort, REPO
        eid, chan, root = await _effort(orch)
        orch.harness.stream_commands = QA_CMDS[:3] + [LIST, "cd /workspace && python3 todo.py add x"] * 40
        note, _defects = await orch._qa_evaluation(eid, chan, root, REPO, _delivery())
        (ev,) = await _payloads(db, "loop_guard_stopped")
        assert ev["guard"] == "window_distinct" and ev["turn"] == "QA review"
        assert "QA could not complete" in note
    finally:
        await _stop(orch, db)


# ── state check / project check: change-nothing wakes in the plain session ──────────────────
async def test_state_check_and_project_check_get_the_readonly_guard(db_url):
    orch, db = await _orch(db_url)
    try:
        await orch.projects.add("app", "https://github.com/acme/app.git")
        eid, chan, root = await orch.router.open_effort("chk", project="app")
        await orch.charters.set_goal(eid, "a todo CLI", created_by="po")
        orch.harness.stream_commands = QA_CMDS          # list 4x in 12: a work window would trip
        orch.harness.output = "STATE MISSING: nothing there"
        await orch._verify_goal_state(eid, chan, root, "https://github.com/acme/app.git")
        orch.harness.output = "CHECK: PASS"
        verdict = await orch._run_check(eid, "python -m unittest")   # no branch: the wake route
        wakes = orch.harness.wakes
        assert wakes and all(w["loop_guard"] is not None and w["loop_guard"].kind == "readonly"
                             for w in wakes), [(w["session_id"], w["loop_guard"]) for w in wakes]
        assert await _payloads(db, "loop_guard_stopped") == []
        assert verdict[0] == "pass" and len(wakes) == 2
    finally:
        await _stop(orch, db)
