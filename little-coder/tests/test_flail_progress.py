"""ao-progress (2026-10-10): the flail guard trips on lack of PROGRESS, not on a count of reads.

Every behavioural case drives the real `_FlailWatcher` over a pi `--mode json` events file - the
seam that exists both before and after the change - so the RED/GREEN cases fail on the old
fixed-25 guard and pass on the progress guard.

Fixtures (tests/fixtures/flail_replay/): RECORDED turns from the agent bake-off, re-encoded so the
novelty structure is exact and no recorded text survives - every result line normalized the way
progress.py normalizes it and replaced by a 12-letter hash, every argument set by one hash, tool
names / order / isError kept, call ids renumbered. Source: gym results/runs/*-C7-*/c7/capture/
worker-*/sessions (coding turns: first user segment of the non-plan sessions) and *-C1-*/
transcript.jsonl (passing runs, cut after their first successful edit). The generator and the
full 32-turn / 12-run replay table live in the plan store (journal/evidence/ao-progress/).
"""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from littlecoder import agent
from littlecoder.config import FlailConfig

FIXTURES = Path(__file__).parent / "fixtures" / "flail_replay"
C7_TURNS = sorted(FIXTURES.glob("c7_*.jsonl"))
C1_PASSES = sorted(FIXTURES.glob("c1pass_*.jsonl"))

NUMBERS_ONLY = re.compile(r"^rule=(stale|cap|time): [a-z0-9 ,/()]+$")


# -- helpers ---------------------------------------------------------------------------------

class _Proc:
    pid = 0

    def poll(self):
        return None


def _start(cid, tool, args):
    return {"type": "tool_execution_start", "toolCallId": cid, "toolName": tool, "args": args}


def _end(cid, tool, text, is_error=False):
    return {"type": "tool_execution_end", "toolCallId": cid, "toolName": tool,
            "result": {"content": [{"type": "text", "text": text}]}, "isError": is_error}


class Script:
    """Builds an events file call by call."""

    def __init__(self):
        self.events = []
        self.n = 0

    def call(self, tool, args, text, is_error=False):
        self.n += 1
        cid = f"t{self.n}"
        self.events += [_start(cid, tool, args), _end(cid, tool, text, is_error)]
        return self

    def write(self, path: Path) -> str:
        path.write_text("\n".join(json.dumps(e) for e in self.events) + "\n", encoding="utf-8")
        return str(path)


def _file_body(name: str, lines: int = 40) -> str:
    return "\n".join(f"def {name}_part_{chr(97 + i % 26)}{'q' * (i // 26)}(): pass" for i in range(lines))


def _novel_reads(s: Script, k: int, tag: str = "f") -> Script:
    for i in range(k):
        name = f"{tag}{chr(97 + i % 26)}{'z' * (i // 26)}"
        s.call("read", {"path": f"/workspace/{name}.py"}, _file_body(name))
    return s


def run_watcher(monkeypatch, path: str, cfg: FlailConfig | None = None, settle: float = 0.4):
    """Run the real watcher over a fully written events file. Returns the trip detail or None."""
    killed = []
    monkeypatch.setattr(agent, "kill_process_group", lambda proc: killed.append(proc))
    cfg = cfg or FlailConfig(poll_seconds=0.01)
    ctx = SimpleNamespace(state=SimpleNamespace(signal=None, detail=None))
    w = agent._FlailWatcher(_Proc(), path, cfg, ctx)
    w.start()
    deadline = time.monotonic() + settle
    while time.monotonic() < deadline and ctx.state.signal is None:
        time.sleep(0.01)
    w.stop()
    w.join(timeout=2)
    if ctx.state.signal is None:
        assert not killed
        return None
    assert ctx.state.signal == "flail_guard" and len(killed) == 1
    return ctx.state.detail


# -- 1. recorded turns: still discovering at call 25 -> not tripped (RED at base) ---------------

def test_fixtures_present():
    assert len(C7_TURNS) == 4 and len(C1_PASSES) == 3


@pytest.mark.parametrize("fixture", C7_TURNS, ids=lambda p: p.stem)
def test_c7_flail_killed_turn_still_reading_new_code_is_not_tripped(monkeypatch, fixture):
    """Each of these C7 coding turns was killed by the old guard at 25 read-only calls (or, for
    wd_offset_r2_r2, ran 46 calls and edited at call 21). Every one was still bringing back new
    lines at call 25; the progress guard lets each run its whole recorded span."""
    calls = sum(1 for ln in fixture.read_text(encoding="utf-8").splitlines()
                if '"tool_execution_start"' in ln)
    assert calls >= 25
    assert run_watcher(monkeypatch, str(fixture)) is None


@pytest.mark.parametrize("fixture", C1_PASSES, ids=lambda p: p.stem)
def test_c1_passing_run_is_not_tripped_before_its_first_edit(monkeypatch, tmp_path, fixture):
    """Bare little-coder's passing runs whose first edit came after call 25 (calls 28, 27, 35):
    the old guard would have killed all three before the edit that started the fix."""
    lines = fixture.read_text(encoding="utf-8").splitlines()
    starts = 0
    for cut, ln in enumerate(lines):
        if '"tool_execution_start"' in ln:
            starts += 1
            if '"toolName": "edit"' in ln or '"toolName": "write"' in ln:
                break
    assert starts > 25                               # the first edit is call 27, 28 or 35
    before = tmp_path / "before_first_edit.jsonl"   # everything the guard saw before that edit
    before.write_text("\n".join(lines[:cut]) + "\n", encoding="utf-8")
    assert run_watcher(monkeypatch, str(before)) is None


# -- 2. genuine loops are still stopped within N (RED at base: 13 < 25) ------------------------

def test_same_three_files_reread_trips_within_window(monkeypatch, tmp_path):
    cfg = FlailConfig(poll_seconds=0.01)
    s = _novel_reads(Script(), 3)
    for i in range(cfg.stale_calls):
        name = f"f{chr(97 + i % 3)}"
        s.call("read", {"path": f"/workspace/{name}.py"}, _file_body(name))
    detail = run_watcher(monkeypatch, s.write(tmp_path / "ev.jsonl"), cfg)
    assert detail is not None and detail.startswith("rule=stale: 10 calls in a row")
    assert "novel 3/13" in detail


def test_near_identical_rereads_trip(monkeypatch, tmp_path):
    """The same content fetched with different arguments (read, cat, a wider sed range over the
    same lines) is not new information."""
    s = _novel_reads(Script(), 3)
    for i in range(10):
        name = f"f{chr(97 + i % 3)}"
        cmd = ["cat /workspace/{n}.py", "sed -n 1,{k}p /workspace/{n}.py", "head -n {k} /workspace/{n}.py"][i % 3]
        s.call("bash", {"command": cmd.format(n=name, k=100 + i)}, _file_body(name))
    detail = run_watcher(monkeypatch, s.write(tmp_path / "ev.jsonl"))
    assert detail is not None and detail.startswith("rule=stale")


def test_same_command_repeated_with_only_numbers_changing_trips(monkeypatch, tmp_path):
    s = _novel_reads(Script(), 3)
    for i in range(11):      # the first run is news; the next 10 are the same answer again
        s.call("bash", {"command": "pytest -q tests/test_x.py 2>&1 | tail -3"},
               f"F.. [100%]\n1 failed, {i} passed in {i}.{i * 7}s")
    detail = run_watcher(monkeypatch, s.write(tmp_path / "ev.jsonl"))
    assert detail is not None and detail.startswith("rule=stale: 10 calls in a row")


def test_cycle_of_two_commands_trips(monkeypatch, tmp_path):
    s = _novel_reads(Script(), 2)
    for i in range(12):
        if i % 2:
            s.call("bash", {"command": "git status -s"}, " M app/x.py")
        else:
            s.call("bash", {"command": "grep -n foo app/x.py"}, "12:    foo()\n40:    foo(1)")
    detail = run_watcher(monkeypatch, s.write(tmp_path / "ev.jsonl"))
    assert detail is not None and detail.startswith("rule=stale")


def test_a_short_answer_to_a_new_question_is_progress(monkeypatch, tmp_path):
    """A regression sweep: one test file per call, each printing the same-shaped summary line.
    Those are new questions with short answers - progress, not a loop."""
    s = _novel_reads(Script(), 3)
    for i in range(30):
        s.call("bash", {"command": f"pytest -q tests/test_mod_{chr(97 + i % 26)}{i // 26}.py | tail -1"},
               f"{i + 3} passed in 0.{i}s")
    assert run_watcher(monkeypatch, s.write(tmp_path / "ev.jsonl")) is None


# -- 3. the hard cap (RED at base: base trips at 25, not at the cap) ---------------------------

CAP = 120   # the default, pinned: novel reads are never killed before it


def test_hard_cap_trips_at_tool_calls(monkeypatch, tmp_path):
    detail = run_watcher(monkeypatch, _novel_reads(Script(), CAP).write(tmp_path / "a.jsonl"))
    assert detail is not None and detail.startswith(f"rule=cap: {CAP} calls since the last edit")


def test_one_below_the_cap_is_not_tripped(monkeypatch, tmp_path):
    assert run_watcher(monkeypatch, _novel_reads(Script(), CAP - 1).write(tmp_path / "a.jsonl")) is None


# -- 4. an edit resets the window (RED at base: base never trips after any edit) ---------------

def _stale(s: Script, k: int) -> Script:
    for _ in range(k):
        s.call("read", {"path": "/workspace/fa.py"}, _file_body("fa"))
    return s


def test_an_edit_resets_the_stale_window(monkeypatch, tmp_path):
    s = _stale(_novel_reads(Script(), 3), 9)
    s.call("edit", {"path": "/workspace/fa.py", "edits": []}, "Successfully replaced text")
    _stale(s, 9)
    assert run_watcher(monkeypatch, s.write(tmp_path / "ev.jsonl")) is None


def test_a_loop_after_an_edit_is_still_stopped(monkeypatch, tmp_path):
    s = _novel_reads(Script(), 3)
    s.call("edit", {"path": "/workspace/fa.py", "edits": []}, "Successfully replaced text")
    _stale(s, 10)
    detail = run_watcher(monkeypatch, s.write(tmp_path / "ev.jsonl"))
    assert detail is not None and detail.startswith("rule=stale") and "edits 1" in detail


def test_an_edit_resets_the_cap(monkeypatch, tmp_path):
    s = _novel_reads(Script(), CAP - 5, tag="g")
    s.call("write", {"path": "/workspace/new.py", "content": "x"}, "Wrote 1 bytes")
    _novel_reads(s, CAP - 5, tag="h")
    assert run_watcher(monkeypatch, s.write(tmp_path / "ev.jsonl")) is None


def test_a_failing_edit_repeated_is_not_progress(monkeypatch, tmp_path):
    s = _novel_reads(Script(), 3)
    for _ in range(11):      # the first failure is news; the next 10 are the same failure
        s.call("edit", {"path": "/workspace/fa.py", "edits": [{"oldText": "nope", "newText": "x"}]},
               "Could not find the exact text in /workspace/fa.py", is_error=True)
    detail = run_watcher(monkeypatch, s.write(tmp_path / "ev.jsonl"))
    assert detail is not None and detail.startswith("rule=stale") and "edits 0" in detail


# -- 5. numbers only, bounded input, isolated errors (ao-silence lessons) ----------------------

PLANTED = ("ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8", "sk-" + "proj-ZZtopsecretZZ0123456789",
           "AKIA" + "IOSFODNN7EXAMPLE", "hunter2-password")


def test_trip_detail_is_numbers_only_with_planted_secrets(monkeypatch, tmp_path):
    s = Script()
    body = "\n".join(f"token={t}" for t in PLANTED) + "\nmore\nlines\nhere\nand\nmore"
    s.call("bash", {"command": f"git push https://x:{PLANTED[0]}@github.com/o/r"}, body)
    for _ in range(10):
        s.call("bash", {"command": f"curl -H 'Authorization: Bearer {PLANTED[1]}' http://h/{PLANTED[2]}"},
               body)
    detail = run_watcher(monkeypatch, s.write(tmp_path / "ev.jsonl"))
    assert detail is not None
    assert NUMBERS_ONLY.match(detail), detail
    for t in PLANTED:
        assert t not in detail and t[:6] not in detail
    assert "git" not in detail and "curl" not in detail and "workspace" not in detail


def test_cap_and_time_details_are_numbers_only(monkeypatch, tmp_path):
    cfg = FlailConfig(poll_seconds=0.01)
    detail = run_watcher(monkeypatch, _novel_reads(Script(), CAP).write(tmp_path / "a.jsonl"), cfg)
    assert NUMBERS_ONLY.match(detail), detail
    m = agent.new_progress_meter(cfg)
    for i in range(8):
        m.start(f"c{i}", "bash", {"command": f"echo {PLANTED[0]} {i}"})
        m.end(f"c{i}", "bash", f"{PLANTED[1]} {chr(97 + i)}", False)
    t = agent.flail_tripped(m, 10**6, cfg)
    assert t is not None and NUMBERS_ONLY.match(t), t


def test_hostile_and_malformed_events_do_not_break_the_guard(monkeypatch, tmp_path):
    huge_num = "9" * 400
    lines = [
        "not json at all",
        '{"type": "tool_execution_start", "toolCallId": ' + huge_num + ', "toolName": 7, "args": ' + huge_num + '}',
        '{"type": "tool_execution_end", "toolCallId": ' + huge_num + ', "toolName": null, "result": ' + huge_num + '}',
        json.dumps({"type": "tool_execution_start", "toolCallId": ["x"], "toolName": {"a": 1}, "args": [1, 2]}),
        json.dumps({"type": "tool_execution_end", "toolCallId": ["x"], "toolName": "bash",
                    "result": {"content": [{"type": "text", "text": 5}, "str", None]}, "isError": "yes"}),
        json.dumps({"type": "tool_execution_end", "toolCallId": "never-started", "toolName": "bash",
                    "result": "plain string result " + huge_num}),
        json.dumps(["tool_execution_start"]),
        json.dumps(_start("big", "bash", {"command": "cat big"})),
        json.dumps(_end("big", "bash", "\n".join(f"row {chr(97 + i % 26)} {i}" for i in range(50_000)))),
    ]
    p = tmp_path / "ev.jsonl"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert run_watcher(monkeypatch, str(p)) is None
    # the guard still works after the garbage: append a loop
    s = _stale(_novel_reads(Script(), 1), 10)
    with p.open("a", encoding="utf-8") as fh:
        fh.write("\n".join(json.dumps(e) for e in s.events) + "\n")
    detail = run_watcher(monkeypatch, str(p))
    assert detail is not None and NUMBERS_ONLY.match(detail), detail


def test_a_scan_that_raises_does_not_disarm_the_guard(monkeypatch, tmp_path):
    from littlecoder import progress
    real = progress.EventFeed.poll
    calls = {"n": 0}

    def flaky(self):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise RuntimeError("boom")
        return real(self)

    monkeypatch.setattr(progress.EventFeed, "poll", flaky)
    path = _stale(_novel_reads(Script(), 3), 10).write(tmp_path / "ev.jsonl")
    detail = run_watcher(monkeypatch, path)
    assert calls["n"] > 2 and detail is not None and detail.startswith("rule=stale")


def test_feed_reads_incrementally_and_waits_for_a_partial_line(tmp_path):
    from littlecoder.progress import EventFeed, ProgressMeter
    p = tmp_path / "ev.jsonl"
    first = json.dumps(_start("a", "read", {"path": "x"})) + "\n"
    second = json.dumps(_end("a", "read", "alpha\nbeta"))
    p.write_text(first + second[:20], encoding="utf-8")       # agent mid-write
    m = ProgressMeter(10, 120)
    feed = EventFeed(str(p), m)
    feed.poll()
    assert (m.calls, m.ended) == (1, 0)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(second[20:] + "\n")
    feed.poll()
    feed.poll()
    assert (m.calls, m.ended, m.novel) == (1, 1, 1)


def test_watcher_trips_live_while_the_file_grows(monkeypatch, tmp_path):
    """The real cadence: the file grows while the watcher polls; the first trip is latched even
    if later calls would have reset the run inside one scan."""
    killed = []
    monkeypatch.setattr(agent, "kill_process_group", lambda proc: killed.append(proc))
    p = tmp_path / "ev.jsonl"
    p.write_text("", encoding="utf-8")
    ctx = SimpleNamespace(state=SimpleNamespace(signal=None, detail=None))
    w = agent._FlailWatcher(_Proc(), str(p), FlailConfig(poll_seconds=0.05), ctx)
    s = _novel_reads(_stale(_novel_reads(Script(), 3), 10), 5, tag="late")   # loop, then news

    def writer():
        with p.open("a", encoding="utf-8") as fh:
            for e in s.events:
                fh.write(json.dumps(e) + "\n")
                fh.flush()
                time.sleep(0.002)

    w.start()
    t = threading.Thread(target=writer)
    t.start()
    t.join()
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and ctx.state.signal is None:
        time.sleep(0.01)
    w.stop()
    w.join(timeout=2)
    assert ctx.state.signal == "flail_guard" and ctx.state.detail.startswith("rule=stale: 10")


# -- 6. config ---------------------------------------------------------------------------------

@pytest.mark.parametrize("field", ["tool_calls", "stale_calls", "min_new_lines"])
def test_config_refuses_non_positive(field):
    with pytest.raises(ValidationError):
        FlailConfig(**{field: 0})
