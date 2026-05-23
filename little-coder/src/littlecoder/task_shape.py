"""Task-shape inference (design §5.5).

`task_shape` is one half of the cohort scoping key (`lang` + `task_shape`,
aggregated across repos). A craft gap — say "Rust async lifetimes during a
multi-file refactor" — only ever reaches the quarantine window M if
occurrences carry a stable shape tag; per-repo or per-task scoping never
escalates.

Shape is a derived attribute, not a journal envelope field — the envelope
ships before the agent runs (`task_started`), so the shape is computed by
looking at what the agent actually DID. The judge (Chapter 3 §3e) refines
the label later; this module ships the cheap heuristic for ingest-time
assignment so the cohort projection (`cohorts.py`) has a key from the
moment a task ends.

The heuristic is conservative on purpose: when in doubt, return
`"unknown"`. A cluster keyed on `unknown` is still a valid cluster — better
than a wrong shape that splits a real cluster in half.
"""

from __future__ import annotations

import dataclasses
from typing import Iterable, Literal

from .journals import Envelope

TaskShape = Literal[
    "bugfix",
    "refactor",
    "greenfield",
    "investigation",
    "test_authoring",
    "unknown",
]


# Tool-call digests carry no semantics, so the heuristic works from the
# coarse shape of the per-task record sequence — what kinds of records
# arrived, how many of each, and whether errors clustered into a
# fix-then-test rhythm. Keep this readable: the rules are the test cases.


@dataclasses.dataclass(frozen=True)
class TaskTrace:
    """The per-task slice the heuristic looks at. One field per signal that
    the rules consult; building it once per task keeps the rule body cheap."""

    tool_calls: int
    bash_calls: int
    file_write_calls: int  # write_file / file edits
    file_read_calls: int  # read_file
    test_runs: int  # commands whose digest matched a test-runner signature
    test_failures: int  # errors with kind=test_failure
    other_errors: int  # errors with kind != test_failure
    git_log_calls: int  # signals "is there history here?"
    repo_was_empty: bool  # git log returned nothing → likely greenfield


def _signature(rec: Envelope) -> str:
    """Pick the discriminating attribute of a journal record. Errors expose
    `kind`; tool calls expose `tool`. Returns "" for envelopes that carry
    neither (task_started / task_ended)."""
    return str(getattr(rec, "kind", None) or getattr(rec, "tool", None) or "")


def trace_from_records(records: Iterable[Envelope]) -> TaskTrace:
    """Roll a task's records into a `TaskTrace`. Cheap (single pass)."""
    tools = bash = writes = reads = test_runs = 0
    test_failures = other_errors = git_log_calls = 0
    repo_was_empty = False

    for rec in records:
        event = getattr(rec, "event", None)
        sig = _signature(rec)
        if event == "tool_call":
            tools += 1
            if sig == "bash":
                bash += 1
            elif sig in ("write_file", "edit_file"):
                writes += 1
            elif sig in ("read_file", "cat_file"):
                reads += 1
            args_digest = getattr(rec, "args_digest", None) or ""
            # The args_digest is opaque, but stable per-command. A real
            # test-runner-detector belongs in the judge (it sees the
            # plaintext); for now, the heuristic counts bash calls that the
            # task explicitly tagged as test runs in its `tool` field.
            if sig in ("pytest", "go_test", "npm_test", "cargo_test"):
                test_runs += 1
            # git log is the "is there history?" probe in the founding
            # knowledge orientation pattern — record so we can detect
            # greenfield with high precision.
            if sig == "bash":
                # crude: any bash call counts; if EVERY bash call was a
                # git-log probe and the repo had no commits, that's a
                # strong greenfield signal — but we cannot read the
                # command text here. Leave the detector to the judge.
                pass
        elif event == "error":
            if sig == "test_failure":
                test_failures += 1
            else:
                other_errors += 1
        # task_started / task_ended carry the lifecycle but not the shape.

    return TaskTrace(
        tool_calls=tools,
        bash_calls=bash,
        file_write_calls=writes,
        file_read_calls=reads,
        test_runs=test_runs,
        test_failures=test_failures,
        other_errors=other_errors,
        git_log_calls=git_log_calls,
        repo_was_empty=repo_was_empty,
    )


def classify(trace: TaskTrace) -> TaskShape:
    """Infer `task_shape` from the trace. Conservative — returns `unknown`
    for any task that doesn't match a sharp signature, so cohort scoping
    stays trustworthy (design §5.5)."""
    # No tool calls AT ALL ⇒ the task barely ran. Don't shape-classify it.
    if trace.tool_calls == 0:
        return "unknown"

    # Fix-then-test rhythm: test failures present, writes present, more
    # writes than test runs. The "bugfix" cluster is what tier-0/1
    # interventions most often address (design §5).
    if trace.test_failures >= 1 and trace.file_write_calls >= 1:
        return "bugfix"

    # Many writes, no failing tests ⇒ refactor / authoring.
    if trace.file_write_calls >= 3 and trace.test_failures == 0:
        # If tests were actually authored (not just run), prefer
        # test_authoring over refactor — the failure modes differ.
        if trace.test_runs >= 2:
            return "test_authoring"
        return "refactor"

    # Read-heavy, write-light ⇒ investigation. "exploring the code" should
    # not get clustered with bugfix work.
    if trace.file_read_calls >= 3 and trace.file_write_calls == 0:
        return "investigation"

    # Empty repo + writes ⇒ greenfield. The empty-repo signal is set by
    # the judge / a future detector; today it stays False.
    if trace.repo_was_empty and trace.file_write_calls >= 1:
        return "greenfield"

    return "unknown"


def classify_records(records: Iterable[Envelope]) -> TaskShape:
    """Convenience: trace + classify in one call."""
    return classify(trace_from_records(records))
