"""ao-flailtime (2026-10-13): the flail guard's time rule counts MODEL time, not the worker's own
tool time.

C7c evidence: the 900-s rule (wall time since the turn started) ended 5 of 12 coding turns before
any edit, each with 658-727 s of the 900 s spent inside the worker's own test runs. The rule now
reads the turn's time with NO tool call open (progress.ModelClock): waiting on / thinking in /
streaming model responses, retries and aborted calls included; tool execution excluded.

Every behavioural case drives the real `_FlailWatcher` over a pi `--mode json` events file through
its `check()` - the seam that exists before and after the change - with `time.monotonic` replaced
by a scripted clock, so the RED cases fail on the wall-time rule. The real-pi end-to-end evidence
(stub model, real `sleep` commands) is tests/node/e2e_flailtime/, run by the test plan.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from littlecoder import agent
from littlecoder.config import FlailConfig

TIME_REASON = re.compile(
    r"^rule=time: (\d+) min of model time with zero file edits "
    r"\(model (\d+) s, tools (\d+) s, wall (\d+) s, (\d+) tool calls, (\d+) novel\)$")
NUMBERS_ONLY = re.compile(r"^rule=(stale|cap|time): [a-z0-9 ,/()]+$")


class FakeTime:
    def __init__(self):
        self.t = 1000.0

    def monotonic(self):
        return self.t


class Turn:
    """A pi events file and a watcher over it, driven on a scripted clock. `model(s)` lets s seconds
    pass with no tool open (the model thinking); `tool(s)` writes a tool start, lets s seconds pass,
    writes its end. The watcher is checked once per `tick` seconds of the scripted clock, as the real
    one is sampled, so the attribution error per start/end is one tick."""

    def __init__(self, monkeypatch, tmp_path: Path, cfg: FlailConfig | None = None, tick: float = 1.0):
        self.ft = FakeTime()
        monkeypatch.setattr(agent, "time", SimpleNamespace(monotonic=self.ft.monotonic, time=self.ft.monotonic))
        self.killed = []
        monkeypatch.setattr(agent, "kill_process_group", lambda proc: self.killed.append(proc))
        self.path = tmp_path / "ev.jsonl"
        self.path.write_text("", encoding="utf-8")
        self.cfg = cfg or FlailConfig()
        self.ctx = SimpleNamespace(state=SimpleNamespace(signal=None, detail=None))
        self.w = agent._FlailWatcher(SimpleNamespace(pid=0, poll=lambda: None), str(self.path), self.cfg, self.ctx)
        self.tick = tick
        self.n = 0
        self.reason = None
        self.reason_at = None

    def _emit(self, ev):
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(ev) + "\n")

    def _pass(self, secs: float):
        end = self.ft.t + secs
        while self.ft.t < end - 1e-9:
            self.ft.t = min(end, self.ft.t + self.tick)
            r = self.w.check()
            if r is not None and self.reason is None:
                self.reason, self.reason_at = r, self.ft.t

    def model(self, secs: float, *, events=()):
        for ev in events:
            self._emit(ev)
        self._pass(secs)
        return self

    def start(self, tool="bash", args=None):
        self.n += 1
        cid = f"c{self.n}"
        self._emit({"type": "tool_execution_start", "toolCallId": cid, "toolName": tool,
                    "args": args if args is not None else {"command": f"step {self.n}"}})
        return cid

    def end(self, cid, tool="bash", text=None, is_error=False):
        self._emit({"type": "tool_execution_end", "toolCallId": cid, "toolName": tool,
                    "result": {"content": [{"type": "text", "text": text or f"output of {cid}"}]},
                    "isError": is_error})

    def tool(self, secs: float, tool="bash", text=None):
        cid = self.start(tool)
        self._pass(secs)
        self.end(cid, tool, text)
        return self


# -- RED/GREEN: the anchor's two cases ---------------------------------------------------------

def test_800s_of_tool_time_and_100s_of_model_time_does_not_trip(monkeypatch, tmp_path):
    """The C7c shape: the worker's own suite runs take most of the turn. Wall 900 s, model 100 s.
    RED at base: the wall-time rule trips at 900 s."""
    t = Turn(monkeypatch, tmp_path)
    for _ in range(10):
        t.model(10).tool(80)
    t.model(5)
    assert t.reason is None, t.reason
    assert t.w.meter.calls == 10 and t.w.meter.edits == 0
    # passes for the right reason: the clock really split the turn (one tick per start/end)
    assert abs(t.w.clock.tool_s - 800) <= 10 and abs(t.w.clock.model_s - 105) <= 10


def test_over_900s_of_model_time_with_no_progress_trips_naming_model_seconds(monkeypatch, tmp_path):
    """A turn that only thinks between quick calls: 10 calls, 95 s of model time each."""
    t = Turn(monkeypatch, tmp_path)
    for _ in range(10):
        t.model(95).tool(2)
    assert t.reason is not None
    m = TIME_REASON.match(t.reason)
    assert m, t.reason
    minutes, model_s, tool_s, wall_s, calls, _novel = map(int, m.groups())
    assert model_s >= 900 and minutes == model_s // 60 == 15
    assert tool_s <= 30 and wall_s == model_s + tool_s and calls >= 8
    # tripped within one judge interval of crossing 900 s of model time
    # reported at the first check after crossing 900 s of model time (check() judges every tick)
    assert model_s <= 902


def test_long_tools_do_not_hide_model_time(monkeypatch, tmp_path):
    """Mixed: 100 s model + 200 s tool per call. Model time crosses 900 s at call 9-10 (wall ~2,700
    s); it trips then - on model time, not before."""
    t = Turn(monkeypatch, tmp_path)
    for _ in range(12):
        t.model(100).tool(200)
        if t.reason:
            break
    m = TIME_REASON.match(t.reason or "")
    assert m, t.reason
    model_s, tool_s = int(m.group(2)), int(m.group(3))
    assert 900 <= model_s <= 925 and tool_s >= 1600


# -- C4: where model time is measured ---------------------------------------------------------

def test_parallel_batch_counts_tool_time_once(monkeypatch, tmp_path):
    """Two calls open together (a parallel batch): the union, not the sum."""
    t = Turn(monkeypatch, tmp_path)
    for _ in range(8):
        t.model(5)
        a, b = t.start(), t.start()
        t._pass(30)
        t.end(a)
        t._pass(50)
        t.end(b)
    t.model(1)
    assert abs(t.w.clock.tool_s - 8 * 80) <= 16, t.w.clock.tool_s
    assert abs(t.w.clock.model_s - 41) <= 16


def test_sequential_batch_counts_each_call(monkeypatch, tmp_path):
    """ao-asyncexec's sequential bash: start, end, start, end inside one batch."""
    t = Turn(monkeypatch, tmp_path)
    for _ in range(4):
        t.model(5).tool(40).tool(40)
    t.model(1)
    assert abs(t.w.clock.tool_s - 320) <= 16 and abs(t.w.clock.model_s - 21) <= 16


def test_retries_errors_and_aborted_calls_count_as_model_time(monkeypatch, tmp_path):
    """A failed model call (stopReason error), pi's auto-retry wait and an aborted stream emit no
    tool events: all of it is model time."""
    t = Turn(monkeypatch, tmp_path)
    err = {"type": "message_end", "message": {"role": "assistant", "stopReason": "error", "content": []}}
    retry = {"type": "auto_retry_start", "attempt": 1, "maxAttempts": 3, "delayMs": 2000}
    aborted = {"type": "message_end", "message": {"role": "assistant", "stopReason": "aborted", "content": []}}
    for i in range(9):
        t.model(40, events=[{"type": "message_start", "message": {"role": "assistant"}}, err, retry])
        t.model(40, events=[aborted] if i % 2 else [])
        t.model(20).tool(1)
    m = TIME_REASON.match(t.reason or "")
    assert m, t.reason
    assert int(m.group(2)) >= 900


def test_interval_is_attributed_to_the_state_at_its_start(monkeypatch, tmp_path):
    """The attribution rule, tick by tick: a tool that starts and ends between two samples is not
    seen open (counted as model time, at most one tick), and a tool seen open keeps the clock
    paused until its end is seen. The per-tick placement is asserted, not only the totals (the
    totals are the same whichever of advance/feed runs first - each errs by one tick)."""
    t = Turn(monkeypatch, tmp_path, tick=1.0)
    cid = t.start()
    t.end(cid)
    t._pass(1)                     # both events land inside one tick: counted as model time
    assert t.w.clock.tool_s == 0 and t.w.clock.model_s == pytest.approx(1)
    cid = t.start()
    t._pass(1)                     # start seen at this sample; the tick before it was model
    assert t.w.clock.tool_s == 0 and t.w.clock.model_s == pytest.approx(2)
    t._pass(10)                    # open across ten samples
    assert t.w.clock.tool_s == pytest.approx(10)
    t.end(cid)
    t._pass(1)                     # end seen at this sample; that tick was still open
    assert t.w.clock.tool_s == pytest.approx(11) and t.w.clock.model_s == pytest.approx(2)
    t._pass(3)
    assert t.w.clock.tool_s == pytest.approx(11) and t.w.clock.model_s == pytest.approx(5)


def test_the_clock_is_never_negative_and_ignores_a_backward_clock(monkeypatch, tmp_path):
    t = Turn(monkeypatch, tmp_path)
    t.model(5)
    t.ft.t -= 100
    t.w.check()
    t.ft.t += 100
    t.w.check()
    assert t.w.clock.model_s == pytest.approx(5) and t.w.clock.tool_s == 0


# -- boundaries kept --------------------------------------------------------------------------

def test_a_hung_tool_is_not_this_rules_job(monkeypatch, tmp_path):
    """A tool that never ends keeps the clock paused: command timeouts and the bridge's
    running-command watch bound it, not the time rule (documented boundary)."""
    t = Turn(monkeypatch, tmp_path)
    for _ in range(8):
        t.model(5).tool(1)
    t.start()
    t._pass(5000)
    assert t.reason is None and t.w.clock.model_s < 60


def test_time_rule_still_needs_zero_edits_and_minimum_calls(monkeypatch, tmp_path):
    t = Turn(monkeypatch, tmp_path)
    for _ in range(3):
        t.model(400).tool(1)
    assert t.reason is None                       # 1,200 s of model time but only 3 calls
    (tmp_path / "second").mkdir()
    t2 = Turn(monkeypatch, tmp_path / "second")
    for _ in range(8):
        t2.model(10).tool(1)
    cid = t2.start("edit", {"path": "a.py"})
    t2.end(cid, "edit", "ok")
    t2.model(2000)
    assert t2.reason is None                      # an edit happened: never on time


def test_stale_and_cap_rules_are_unchanged_and_win_over_time(monkeypatch, tmp_path):
    t = Turn(monkeypatch, tmp_path, FlailConfig(stale_calls=10))
    t.model(1).tool(1, text="\n".join(f"same line {chr(97 + i)}" for i in range(20)))
    for _ in range(10):
        t.model(1).tool(1, text="\n".join(f"same line {chr(97 + i)}" for i in range(20)))
    t.model(1)                     # the sample that feeds the last end
    assert t.reason is not None and t.reason.startswith("rule=stale: 10")


def test_time_reason_is_numbers_only(monkeypatch, tmp_path):
    t = Turn(monkeypatch, tmp_path)
    for i in range(10):
        t.model(95)
        cid = t.start("bash", {"command": f"echo ghp_{'A' * 36} /workspace/secret {i}"})
        t._pass(1)
        t.end(cid, "bash", f"token sk_live_{'b' * 24} {chr(97 + i)}")
    assert t.reason and NUMBERS_ONLY.match(t.reason) and TIME_REASON.match(t.reason), t.reason
    assert "ghp_" not in t.reason and "sk_live" not in t.reason and "workspace" not in t.reason


def test_flail_tripped_without_a_clock_reports_model_seconds_only():
    cfg = FlailConfig()
    m = agent.new_progress_meter(cfg)
    for i in range(8):
        m.start(f"c{i}", "read", {"path": f"f{i}"})
        m.end(f"c{i}", "read", f"line {chr(97 + i)}", False)
    assert agent.flail_tripped(m, 899.9, cfg) is None
    r = agent.flail_tripped(m, 900, cfg)
    assert r == "rule=time: 15 min of model time with zero file edits (model 900 s, 8 tool calls, 8 novel)"
    huge = agent.flail_tripped(m, float("inf"), cfg)
    assert huge is not None and NUMBERS_ONLY.match(huge)


# -- C6: bounded --------------------------------------------------------------------------------

def test_watcher_tick_is_bounded_by_the_judge_cadence():
    w = agent._FlailWatcher(SimpleNamespace(pid=0, poll=lambda: None), "/nonexistent",
                            FlailConfig(poll_seconds=5, clock_seconds=60), SimpleNamespace())
    assert w._tick == 5
    w = agent._FlailWatcher(SimpleNamespace(pid=0, poll=lambda: None), "/nonexistent",
                            FlailConfig(poll_seconds=20, clock_seconds=0.5), SimpleNamespace())
    assert w._tick == 0.5


@pytest.mark.parametrize("bad", [0, -1, 61])
def test_clock_seconds_is_validated(bad):
    from pydantic import ValidationError
    # the field exists and takes a sane value (else the refusal below would be "unknown field")
    assert FlailConfig(clock_seconds=0.5).clock_seconds == 0.5 and FlailConfig().clock_seconds == 1.0
    with pytest.raises(ValidationError):
        FlailConfig(clock_seconds=bad)


def test_judge_cadence_is_unchanged_by_the_short_tick(monkeypatch, tmp_path):
    """The live loop samples every clock tick but judges (and kills) on `poll_seconds` only."""
    import threading
    import time as real_time
    judged = []
    monkeypatch.setattr(agent, "kill_process_group", lambda proc: None)
    p = tmp_path / "ev.jsonl"
    p.write_text("", encoding="utf-8")
    w = agent._FlailWatcher(SimpleNamespace(pid=0, poll=lambda: None), str(p),
                            FlailConfig(poll_seconds=0.3, clock_seconds=0.02),
                            SimpleNamespace(state=SimpleNamespace(signal=None, detail=None)))
    samples = []
    real_sample, real_judge = w.sample, w.judge
    w.sample = lambda now=None: (samples.append(1), real_sample(now))[1]
    w.judge = lambda: (judged.append(1), real_judge())[1]
    w.start()
    real_time.sleep(1.0)
    w.stop()
    w.join(timeout=2)
    assert not w.is_alive() and isinstance(w, threading.Thread)
    assert len(samples) >= 15 and 2 <= len(judged) <= 4, (len(samples), len(judged))
