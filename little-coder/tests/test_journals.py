"""Append-only task journals (design §4). The cohort math is only as
trustworthy as these — pin the envelope and the rejection behavior."""

import json

import pytest

from littlecoder.journals import (
    Error,
    Journals,
    JournalWriteError,
    TaskEnded,
    TaskStarted,
    ToolCall,
    utc_now,
)


def _envelope(**overrides):
    base = dict(
        ts=utc_now(),
        task_id="01J0000000000000000000000A",
        session_id="sess-1",
        channel="cli",
        user_id="cli",
        repo="https://github.com/acme/widget",
        lang="python",
        seq=0,
    )
    base.update(overrides)
    return base


def test_full_envelope_round_trips(tmp_path):
    j = Journals(tmp_path)
    j.write(ToolCall(**_envelope(seq=1), tool="bash"))
    lines = (tmp_path / "tool_calls.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    # The tier-0 envelope fields are all present from line 1 (design §4.1).
    for fld in ("session_id", "channel", "user_id", "schema_version", "seq"):
        assert fld in rec
    assert rec["event"] == "tool_call"


def test_events_route_to_the_right_file(tmp_path):
    j = Journals(tmp_path)
    j.write(TaskStarted(**_envelope(seq=0), trigger_digest="abc"))
    j.write(Error(**_envelope(seq=1), kind="test_failure", message="boom"))
    j.write(TaskEnded(**_envelope(seq=2), outcome="fail", signal="pytest exit 1"))
    assert (tmp_path / "errors.jsonl").exists()
    # task_started and task_ended both land in outcomes.jsonl.
    outcomes = (tmp_path / "outcomes.jsonl").read_text().splitlines()
    assert len(outcomes) == 2


def test_malformed_record_is_rejected_not_appended(tmp_path):
    j = Journals(tmp_path)
    # Missing the required `kind` field.
    with pytest.raises(JournalWriteError):
        j.write({"event": "error", **_envelope(seq=1), "message": "x"})
    assert j.records_rejected == 1
    assert j.records_written == 0
    assert not (tmp_path / "errors.jsonl").exists()


def test_unknown_event_is_rejected(tmp_path):
    j = Journals(tmp_path)
    with pytest.raises(JournalWriteError, match="unknown"):
        j.write({"event": "not_a_real_event", **_envelope()})
    assert j.records_rejected == 1


def test_extra_field_is_rejected(tmp_path):
    j = Journals(tmp_path)
    with pytest.raises(JournalWriteError):
        j.write({"event": "tool_call", **_envelope(seq=1), "tool": "bash", "sneaky": 1})
    assert j.records_written == 0


def test_tasks_reconstruct_by_task_id_not_adjacency(tmp_path):
    """Interleaved sessions are legal (design §4.2)."""
    j = Journals(tmp_path)
    j.write(TaskStarted(**_envelope(task_id="01J000000000000000000000AA", seq=0), trigger_digest="a"))
    j.write(TaskStarted(**_envelope(task_id="01J000000000000000000000BB", seq=0), trigger_digest="b"))
    j.write(TaskEnded(**_envelope(task_id="01J000000000000000000000AA", seq=1), outcome="pass"))
    j.write(TaskEnded(**_envelope(task_id="01J000000000000000000000BB", seq=1), outcome="fail"))
    by_task: dict[str, list] = {}
    for rec in j.iter_records("outcomes"):
        by_task.setdefault(rec.task_id, []).append(rec)
    assert len(by_task) == 2
    assert {r.event for r in by_task["01J000000000000000000000AA"]} == {
        "task_started",
        "task_ended",
    }


def test_size_triggered_rotation(tmp_path):
    j = Journals(tmp_path, rotation_max_bytes=200)
    for seq in range(6):
        j.write(ToolCall(**_envelope(seq=seq), tool="bash"))
    rotated = list(tmp_path.glob("tool_calls.*.jsonl"))
    assert rotated, "expected at least one rotated segment"
    assert (tmp_path / "tool_calls.jsonl").exists()  # a live file remains
