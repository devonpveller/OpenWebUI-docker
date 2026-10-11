"""Flail guard (agent-org bridge, 2026-07-14; progress-based since ao-progress 2026-10-10) - a
coding turn that stops making progress is killed with a FLAIL-GUARD marker so the bridge can fork a
fresh session from the original goal and re-ask in plan mode. Pins: the trip matrix through
`flail_tripped` (stale / cap / time; the time trip needs a minimum of activity and zero edits),
and the trigger-route plumbing. The progress rules themselves: test_flail_progress.py."""

from __future__ import annotations

from littlecoder.agent import flail_tripped, new_progress_meter
from littlecoder.config import FlailConfig
from littlecoder.daemon import TriggerRequest


def _novel(meter, n: int, start: int = 0) -> None:
    for i in range(start, start + n):
        meter.start(f"c{i}", "read", {"path": f"f{i}"})
        meter.end(f"c{i}", "read", f"line of file {chr(97 + i % 26)}{'x' * (i // 26)}", False)


def test_time_trip_requires_minimum_activity():
    cfg = FlailConfig(seconds=900, min_tool_calls=8)
    m = new_progress_meter(cfg)
    _novel(m, 3)
    # long-elapsed but nearly idle: a slow thoughtful turn, NOT flailing
    assert flail_tripped(m, 5000, cfg) is None
    _novel(m, 5, start=3)
    reason = flail_tripped(m, 901, cfg)
    assert reason is not None and reason.startswith("rule=time: 15 min")


def test_time_trip_never_after_an_edit():
    cfg = FlailConfig(seconds=900, min_tool_calls=8)
    m = new_progress_meter(cfg)
    _novel(m, 9)
    m.start("e", "edit", {"path": "f0"})
    m.end("e", "edit", "ok", False)
    assert flail_tripped(m, 99999, cfg) is None


def test_novel_reads_below_the_cap_never_trip():
    cfg = FlailConfig()
    m = new_progress_meter(cfg)
    _novel(m, cfg.tool_calls - 1)
    assert flail_tripped(m, 10, cfg) is None


def test_defaults():
    cfg = FlailConfig()
    assert (cfg.tool_calls, cfg.stale_calls, cfg.min_new_lines) == (120, 10, 1)
    assert (cfg.seconds, cfg.min_tool_calls) == (900, 8)


def test_trigger_request_carries_flail_guard_default_false():
    assert TriggerRequest(prompt="x").flail_guard is False
    assert TriggerRequest(prompt="x", flail_guard=True).flail_guard is True
