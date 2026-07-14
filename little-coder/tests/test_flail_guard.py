"""Flail guard (agent-org bridge, 2026-07-14) — "too many thinking turns or time iterating on
read without editing anything" kills the turn with a FLAIL-GUARD marker so the bridge can fork a
fresh session from the original goal and re-ask in plan mode. Pins: the pi-event counter (total
vs edit/write tool executions, partial-line-safe), the trip matrix (edits present -> never; count
trip; time trip needs a minimum of activity), and the trigger-route plumbing."""

from __future__ import annotations

import json

from littlecoder.agent import count_tool_executions, flail_tripped
from littlecoder.config import FlailConfig
from littlecoder.daemon import TriggerRequest


def _events_file(tmp_path, events, partial_tail: bool = False) -> str:
    path = tmp_path / "pi-events.jsonl"
    body = "\n".join(json.dumps(e) for e in events)
    if partial_tail:
        body += '\n{"type":"tool_execution_start","toolName":"re'   # mid-write line
    path.write_text(body, encoding="utf-8")
    return str(path)


def _exec(tool: str) -> dict:
    return {"type": "tool_execution_start", "toolCallId": "x", "toolName": tool, "args": {}}


def test_counts_only_tool_execution_start_and_classifies_edits(tmp_path):
    path = _events_file(tmp_path, [
        _exec("bash"), _exec("read"), _exec("edit"), _exec("bash"), _exec("write"),
        {"type": "toolCall", "toolName": "bash"},          # a snapshot, NOT an execution
        {"type": "toolcall_delta", "toolName": "edit"},    # a delta, NOT an execution
        {"type": "turn_start"},
    ], partial_tail=True)                                  # mid-write tail is skipped, not fatal
    assert count_tool_executions(path) == (5, 2)


def test_missing_file_counts_zero(tmp_path):
    assert count_tool_executions(str(tmp_path / "nope.jsonl")) == (0, 0)


def test_any_edit_means_never_tripped():
    cfg = FlailConfig()
    assert flail_tripped(500, 1, 99999, cfg) is None


def test_count_trip_fires_on_read_only_tool_calls():
    cfg = FlailConfig(tool_calls=25)
    assert flail_tripped(24, 0, 10, cfg) is None
    reason = flail_tripped(25, 0, 10, cfg)
    assert reason is not None and "zero file edits" in reason


def test_time_trip_requires_minimum_activity():
    cfg = FlailConfig(seconds=900, min_tool_calls=8)
    # long-elapsed but nearly idle: a slow thoughtful turn, NOT flailing
    assert flail_tripped(3, 0, 5000, cfg) is None
    reason = flail_tripped(8, 0, 901, cfg)
    assert reason is not None and "min iterating on reads" in reason


def test_trigger_request_carries_flail_guard_default_false():
    assert TriggerRequest(prompt="x").flail_guard is False
    assert TriggerRequest(prompt="x", flail_guard=True).flail_guard is True
