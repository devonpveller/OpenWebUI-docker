"""Task-shape inference (design §5.5).

These drive the pure heuristic. The judge will refine these labels later;
the heuristic only needs to be conservative — `unknown` is always a safe
fallback. A wrong shape that splits a real cluster in half is the failure
mode to avoid.
"""

import pytest

from littlecoder.journals import Error, TaskEnded, TaskStarted, ToolCall, utc_now
from littlecoder.task_shape import (
    TaskTrace,
    classify,
    classify_records,
    trace_from_records,
)


def _env(**overrides):
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


# --- classify() over hand-built traces ----------------------------------


def test_no_tool_calls_returns_unknown():
    assert classify(TaskTrace(0, 0, 0, 0, 0, 0, 0, 0, False)) == "unknown"


def test_bugfix_pattern_test_failure_plus_writes():
    t = TaskTrace(
        tool_calls=10,
        bash_calls=4,
        file_write_calls=3,
        file_read_calls=2,
        test_runs=2,
        test_failures=2,  # ← the bugfix signal
        other_errors=0,
        git_log_calls=1,
        repo_was_empty=False,
    )
    assert classify(t) == "bugfix"


def test_refactor_many_writes_no_failures():
    t = TaskTrace(
        tool_calls=12,
        bash_calls=3,
        file_write_calls=6,  # ≥ 3 writes
        file_read_calls=2,
        test_runs=1,
        test_failures=0,
        other_errors=0,
        git_log_calls=1,
        repo_was_empty=False,
    )
    assert classify(t) == "refactor"


def test_test_authoring_writes_and_multiple_runs_no_failures():
    """Many writes + ≥2 test runs without failures = the agent was writing
    tests, not bugfixing or refactoring. Different cluster than refactor."""
    t = TaskTrace(
        tool_calls=15,
        bash_calls=4,
        file_write_calls=5,
        file_read_calls=2,
        test_runs=3,  # ← actively running tests
        test_failures=0,
        other_errors=0,
        git_log_calls=1,
        repo_was_empty=False,
    )
    assert classify(t) == "test_authoring"


def test_investigation_read_heavy_write_zero():
    t = TaskTrace(
        tool_calls=8,
        bash_calls=2,
        file_write_calls=0,  # ← no writes
        file_read_calls=5,  # ← many reads
        test_runs=0,
        test_failures=0,
        other_errors=0,
        git_log_calls=2,
        repo_was_empty=False,
    )
    assert classify(t) == "investigation"


def test_greenfield_empty_repo_plus_writes():
    t = TaskTrace(
        tool_calls=6,
        bash_calls=2,
        file_write_calls=2,
        file_read_calls=0,
        test_runs=0,
        test_failures=0,
        other_errors=0,
        git_log_calls=1,
        repo_was_empty=True,  # ← empty repo
    )
    assert classify(t) == "greenfield"


def test_unknown_when_no_signature_matches():
    """One bash call, no writes, no errors — too thin to label confidently.
    Returning `unknown` is the right answer (design §5.5 conservatism)."""
    t = TaskTrace(
        tool_calls=1,
        bash_calls=1,
        file_write_calls=0,
        file_read_calls=0,
        test_runs=0,
        test_failures=0,
        other_errors=0,
        git_log_calls=1,
        repo_was_empty=False,
    )
    assert classify(t) == "unknown"


# --- trace_from_records() over real Record objects ----------------------


def test_trace_counts_tool_calls_by_kind():
    records = [
        TaskStarted(**_env(seq=0), trigger_digest="abc"),
        ToolCall(**_env(seq=1), tool="bash"),
        ToolCall(**_env(seq=2), tool="bash"),
        ToolCall(**_env(seq=3), tool="write_file"),
        ToolCall(**_env(seq=4), tool="read_file"),
        TaskEnded(**_env(seq=5), outcome="unverified"),
    ]
    t = trace_from_records(records)
    assert t.tool_calls == 4
    assert t.bash_calls == 2
    assert t.file_write_calls == 1
    assert t.file_read_calls == 1
    assert t.test_failures == 0


def test_trace_splits_errors_by_kind():
    records = [
        ToolCall(**_env(seq=1), tool="bash"),
        Error(**_env(seq=2), kind="test_failure", message="pytest exit 1"),
        Error(**_env(seq=3), kind="parse_error", message="bad json"),
        Error(**_env(seq=4), kind="test_failure", message="pytest exit 1"),
    ]
    t = trace_from_records(records)
    assert t.test_failures == 2
    assert t.other_errors == 1


def test_classify_records_end_to_end_bugfix():
    """The full integration: feed it a realistic bugfix-shaped sequence and
    expect `bugfix` back. This is what `cohorts.project` will call."""
    records = [
        TaskStarted(**_env(seq=0), trigger_digest="abc"),
        ToolCall(**_env(seq=1), tool="bash"),
        ToolCall(**_env(seq=2), tool="pytest"),
        Error(**_env(seq=3), kind="test_failure", message="assertion"),
        ToolCall(**_env(seq=4), tool="write_file"),
        ToolCall(**_env(seq=5), tool="pytest"),
        Error(**_env(seq=6), kind="test_failure", message="assertion"),
        ToolCall(**_env(seq=7), tool="write_file"),
        ToolCall(**_env(seq=8), tool="pytest"),
        TaskEnded(**_env(seq=9), outcome="pass", signal="pytest exit 0"),
    ]
    assert classify_records(records) == "bugfix"
