"""ao-loopguard round 2 (tester attempt 1 FAIL: the work guard stopped productive turns).

The tester's cases (scratchpad lgt/test_zz_tester_replay.py), RED at 042778b5 and GREEN here:
  - a QA evaluation turn re-runs `python3 todo.py list` between inputs (4 times in 12 commands) -
    change-nothing turns (QA, verify, plan) now get the identical-run rule only;
  - a stopped QA turn says "QA could not complete (loop guard: ...)" and keeps the defects it printed;
  - a coding step that edits through bash (`sed -i`, heredoc) and re-runs the same suite is progress:
    the daemon's `workspace_marker` (a stat fingerprint of the workspace) resets the window;
  - a stop is ONE line in the effort thread; #management gets at most the existing notice.
Fakes and an in-process daemon stub only.
"""

from __future__ import annotations

import asyncio

from test_ao_loopguard import (
    AB_START, DOC_LENS, FINDINGS, REPO, W1, _delivery, _effort, _first_trip, _h, _orch,
    _payloads, _ReplayDaemon, _review, _route, _stop, _work,
)

from app.worker.harness import LoopWatch

QA_CMDS = [
    "cd /workspace && git fetch origin agent/feat && git checkout -f agent/feat",
    "cd /workspace && python3 todo.py",
    "cd /workspace && python3 todo.py --help",
    "cd /workspace && python3 todo.py add --help",
    "cd /workspace && python3 todo.py add \"buy milk\" --due 2026-08-01",
    "cd /workspace && python3 todo.py list",
    "cd /workspace && python3 todo.py add \"\"",
    "cd /workspace && python3 todo.py list",
    "cd /workspace && python3 todo.py done 1",
    "cd /workspace && python3 todo.py list",
    "cd /workspace && python3 todo.py done 99",
    "cd /workspace && python3 todo.py delete 1",
    "cd /workspace && python3 todo.py list",
    "cd /workspace && python3 todo.py add x --due not-a-date",
    "cd /workspace && python3 todo.py list --due-before 2026-09-01",
]
QA_ANSWER = ("WORKS: add/list/done work on valid input.\nDEFECTS:\n1. `add \"\"` stores an empty todo\n"
             "2. `done 99` raises a raw IndexError traceback\nFOLLOWUPS: none\n"
             "VERDICT: crashes on an out-of-range id")
SUITE = "cd /workspace && python3 -m unittest discover -s tests 2>&1 | tail -5"
SED_LOOP = [x for fix in ["s/due=None/due=args.due/", "13a import datetime",
                          "s/item\\['due'\\]/item.get('due')/", "s/< cutoff/<= cutoff/",
                          "s/return 0/return 1/"]
            for x in (f"cd /workspace && sed -i '{fix}' todo.py", SUITE)]
HEREDOC_LOOP = [x for i in range(5)
                for x in ("cd /workspace && cat > todo_filter.py <<'EOF'\ndef keep(item, cutoff):\n"
                          f"    return item.get('due') and item['due'] <= cutoff  # v{i}\nEOF", SUITE)]
PYWRITE_LOOP = [x for i in range(5)
                for x in ("cd /workspace && python3 -c \"import pathlib; p=pathlib.Path('todo.py'); "
                          f"p.write_text(p.read_text().replace('v{i}', 'v{i + 1}'))\"", SUITE)]


async def _delegate(orch, goal="add a --due-before filter to list"):
    await orch.projects.add("app", "https://github.com/acme/app.git")
    eid, chan, root = await orch.router.open_effort("spin", project="app")
    await orch.delegate(eid, chan, root, goal)
    for _ in range(3):
        if orch._bg_tasks:
            await asyncio.gather(*list(orch._bg_tasks), return_exceptions=True)
    return eid, chan, root


# ── change-nothing turns: QA ─────────────────────────────────────────────────────────────
async def test_qa_turn_is_not_stopped_and_keeps_its_defects(db_url):
    orch, db = await _orch(db_url, qa_gate="on", qa_code_review=False)
    try:
        eid, chan, root = await _effort(orch)
        orch.harness.stream_commands = list(QA_CMDS)
        orch.harness.output = QA_ANSWER
        note, defects = await orch._qa_evaluation(eid, chan, root, REPO, _delivery())
        assert [p["status"] for p in await _payloads(db, "wake_done")] == ["done"]
        assert await _payloads(db, "loop_guard_stopped") == [] and len(defects) == 2
        (w,) = orch.harness.wakes
        assert w["loop_guard"].kind == "readonly" and w["loop_guard"].window == 0
    finally:
        await _stop(orch, db)


async def test_a_stopped_qa_turn_says_it_could_not_complete(db_url):
    orch, db = await _orch(db_url, qa_gate="on", qa_code_review=False)
    try:
        eid, chan, root = await _effort(orch)
        orch.harness.stream_commands = QA_CMDS[:5] + ["cd /workspace && python3 todo.py list"] * 8
        orch.harness.flail_answer = "DEFECTS:\n1. `add \"\"` stores an empty todo\n"
        note, defects = await orch._qa_evaluation(eid, chan, root, REPO, _delivery())
        (ev,) = await _payloads(db, "loop_guard_stopped")
        assert ev["guard"] == "identical_run" and ev["turn"] == "QA review"
        assert "QA could not complete (loop guard:" in note
        assert "exercised cleanly" not in note
        assert defects == ["`add \"\"` stores an empty todo"]        # what it printed is kept
        lines = [m["message"] for m in orch.chat.posted if "Loop guard" in m["message"]]
        assert len(lines) == 1 and "kept" not in lines[0] and "not used" in lines[0]
    finally:
        await _stop(orch, db)


async def test_change_nothing_turns_get_the_readonly_guard(db_url):
    orch, db = await _orch(db_url)
    try:
        r = orch.router
        for sid, plan in (("e~qa3", False), ("e~vfy2", False), ("e~r1~plan", False), ("e", True)):
            g = r.default_loop_guard(sid, plan_only=plan)
            assert g.kind == "readonly" and g.window == 0
            assert g.identical_run == orch.s.lens_flail_repeats
        assert r.default_loop_guard("e~r1").kind == "work"
        assert _first_trip(QA_CMDS, r.readonly_loop_guard()) is None
        assert _first_trip([SUITE] * 6, r.readonly_loop_guard()).commands == 6
    finally:
        await _stop(orch, db)


# ── coding steps that edit through bash ──────────────────────────────────────────────────
async def test_coding_step_editing_with_sed_heredoc_or_script_is_not_stopped(tmp_path):
    for i, cmds in enumerate((SED_LOOP, HEREDOC_LOOP, PYWRITE_LOOP)):
        orch, db = await _orch(f"sqlite+aiosqlite:///{tmp_path / f'r2_{i}.db'}")
        try:
            orch.harness.stream_commands = list(cmds)
            orch.harness.output = "Implemented --due-before; all tests pass."
            await _delegate(orch)
            assert await _payloads(db, "loop_guard_stopped") == [], cmds[0]
            assert await _payloads(db, "flail_replanned") == []
        finally:
            await _stop(orch, db)


async def test_real_harness_sees_bash_edits_through_the_workspace_marker(monkeypatch):
    # the marker moves with every sed (the real daemon stats the workspace): progress, never stopped
    d = _ReplayDaemon(SED_LOOP)
    d.markers = [f"m{(i + 2) // 2}" for i in range(len(SED_LOOP))]
    _route(monkeypatch, d)
    r = await _h().wake(W1, "s", "p", loop_guard=_work())
    assert r.status == "done" and d.cancelled == []
    # the same suite re-run on an unchanged workspace: stopped at the 4th
    d2 = _ReplayDaemon([SUITE] * 12)
    d2.markers = ["m0"] * 12
    _route(monkeypatch, d2)
    r2 = await _h().wake(W1, "s", "p", loop_guard=_work())
    assert r2.status == "flail" and r2.loop_trip.commands == 4
    # the recorded gym-002 no-edit loop on an unchanged workspace is still stopped
    ab = DOC_LENS[AB_START:]
    d3 = _ReplayDaemon(ab)
    d3.markers = ["m0"] * len(ab)
    _route(monkeypatch, d3)
    r3 = await _h().wake(W1, "s", "p", loop_guard=_work())
    assert r3.status == "flail" and r3.loop_trip.commands <= 20


# ── messages: one thread line per stop ───────────────────────────────────────────────────
async def test_coding_step_stop_posts_one_thread_line(db_url):
    orch, db = await _orch(db_url)
    try:
        orch.harness.stream_commands = list(DOC_LENS[AB_START:])
        eid, chan, root = await _delegate(orch, "port the parser to the new API")
        stops = await _payloads(db, "loop_guard_stopped")
        assert stops and stops[0]["turn"] == "work"
        assert await _payloads(db, "flail_replanned")
        thread = [m["message"] for m in orch.chat.posted if m.get("thread_id") == root]
        about = [t for t in thread if any(k in t for k in (
            "Loop guard", "spinning", "flailed again", "ended **flail**", "LOOP-GUARD"))]
        # the fork re-runs the same loop once (bounded): exactly one guard line per stop
        assert len(about) == len(stops) and all(t.startswith("🛑 Loop guard") for t in about)
        mgmt = [m["message"] for m in orch.chat.posted if m.get("thread_id") != root
                and "looping on the same commands" in m["message"]]
        assert mgmt, "the replan notice still reaches #management"
    finally:
        await _stop(orch, db)


async def test_plan_turn_stop_is_one_line_plus_the_existing_escalation(db_url):
    orch, db = await _orch(db_url, worker_plan_gate="all")
    try:
        orch.harness.stream_commands = ["cd /workspace && grep -rn parse ."] * 8
        eid, chan, root = await _delegate(orch, "port the parser to the new API")
        (ev,) = await _payloads(db, "loop_guard_stopped")
        assert ev["turn"] == "planning" and ev["guard"] == "identical_run"
        about = [m for m in orch.chat.posted
                 if any(k in m["message"] for k in ("Loop guard", "LOOP-GUARD", "flail"))]
        assert len(about) <= 2, [m["message"] for m in about]
        assert sum(1 for m in about if m.get("thread_id") == root) == 1
    finally:
        await _stop(orch, db)


# ── review turns: unchanged, plus the miscount fix ───────────────────────────────────────
def test_productive_lens_paging_and_greps_is_not_stopped():
    cmds = ["cd /workspace && git log -n 20 --format='%h %s%n%b'", "cd /workspace && cat README.md"]
    for i in range(10):
        cmds.append(f"cd /workspace && git show HEAD~{i} | sed -n '{i*80+1},{i*80+80}p'")
        cmds.append(f"cd /workspace && grep -rn 'def cmd_{i}' .")
        if i % 3 == 2:
            cmds.append(f"echo 'FINDING: commit HEAD~{i} says nothing about why' >> /tmp/lens-findings.txt")
    assert _first_trip(cmds, _review()) is None


def test_reading_the_findings_file_back_is_not_a_finding():
    w = LoopWatch(_review())
    w.feed(f"echo '{FINDINGS[0]}' >> /tmp/lens-findings.txt")
    w.feed("grep -c 'FINDING:' /tmp/lens-findings.txt")
    w.feed("grep 'FINDING:' /tmp/lens-findings.txt 2>/dev/null | head")
    w.feed("cat >> /tmp/lens-findings.txt <<'EOF'\nFINDING: a second one\nEOF")
    assert w.findings == 2


