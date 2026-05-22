"""Append-only task journals (design §4).

Three task journals — `tool_calls.jsonl`, `errors.jsonl`, `outcomes.jsonl`.
Every line carries the full envelope (design §4.1). The envelope fields are
unrecoverable retroactively, so they ship complete from line 1 — `session_id`,
`channel`, `user_id`, `schema_version` included (tier-0 build requirement).

Durability (design §4.3):
  - append + fsync on every terminal record (task_ended / task_abandoned /
    task_outcome_amended) and every error record; line-buffered for the rest.
  - records are schema-validated at write time; malformed records are
    REJECTED, never appended.
  - size-triggered rotation; rotated segments are kept (the longitudinal
    miner consumes them from Learner onward).

Task lifecycle records (`task_started` ... `task_ended`) live in
`outcomes.jsonl`: tasks are reconstructed by `task_id`, never by adjacency,
so interleaved sessions are legal (design §4.2).
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Union

from pydantic import BaseModel, ValidationError

from . import SCHEMA_VERSION

Channel = Literal["owui", "cli", "validation", "batch"]
Outcome = Literal["pass", "fail", "unverified"]


def utc_now() -> str:
    """UTC timestamp, millisecond precision, ISO-8601 with trailing Z."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


# --------------------------------------------------------------------------
# Records. Each is the envelope (design §4.1) plus an `event` discriminator
# and event-specific payload. `extra="forbid"` makes a stray field a
# validation error — i.e. a rejected record, not a silently-accepted one.
# --------------------------------------------------------------------------


class Envelope(BaseModel):
    model_config = {"extra": "forbid"}

    ts: str  # UTC timestamp (utc_now())
    task_id: str  # ULID, minted at trigger
    session_id: str  # trigger session
    channel: Channel
    user_id: str  # OWUI authn id, or "cli"
    repo: str  # full canonical URL ("" before first /project)
    lang: str  # detected primary language ("" if unknown)
    seq: int  # per-task counter
    schema_version: int = SCHEMA_VERSION


class ToolCall(Envelope):
    event: Literal["tool_call"] = "tool_call"
    tool: str
    args_digest: str | None = None  # short digest, never raw args
    ok: bool = True
    duration_ms: int | None = None


class Error(Envelope):
    event: Literal["error"] = "error"
    kind: str  # tool_error | test_failure | parse_error | loop | ...
    message: str
    tool: str | None = None


class TaskStarted(Envelope):
    event: Literal["task_started"] = "task_started"
    trigger_digest: str  # digest of the task prompt, not the raw prompt


class TaskEnded(Envelope):
    event: Literal["task_ended"] = "task_ended"
    outcome: Outcome
    # What produced the verdict: test-suite exit, the task's acceptance
    # command, or explicit caller confirmation. None ⇒ outcome must be
    # `unverified` (design §4.2).
    signal: str | None = None


class TaskAbandoned(Envelope):
    event: Literal["task_abandoned"] = "task_abandoned"
    reason: str  # timeout | shutdown


class TaskOutcomeAmended(Envelope):
    event: Literal["task_outcome_amended"] = "task_outcome_amended"
    outcome: Outcome
    prior_outcome: Outcome
    amended_by: str  # operator id / channel that issued the amendment


Record = Union[
    ToolCall, Error, TaskStarted, TaskEnded, TaskAbandoned, TaskOutcomeAmended
]

_MODEL_FOR_EVENT: dict[str, type[Envelope]] = {
    "tool_call": ToolCall,
    "error": Error,
    "task_started": TaskStarted,
    "task_ended": TaskEnded,
    "task_abandoned": TaskAbandoned,
    "task_outcome_amended": TaskOutcomeAmended,
}

# Which journal file each event lands in.
_FILE_FOR_EVENT: dict[str, str] = {
    "tool_call": "tool_calls.jsonl",
    "error": "errors.jsonl",
    "task_started": "outcomes.jsonl",
    "task_ended": "outcomes.jsonl",
    "task_abandoned": "outcomes.jsonl",
    "task_outcome_amended": "outcomes.jsonl",
}

# Events that force an fsync: every terminal record + every error (design §4.3).
_FSYNC_EVENTS = {"error", "task_ended", "task_abandoned", "task_outcome_amended"}


class JournalWriteError(RuntimeError):
    """A record was malformed and rejected, or could not be written."""


class Journals:
    """Writer for the three task journals. Thread-safe; one instance per
    process. Validation happens at write time — a malformed record raises
    `JournalWriteError` and is never appended."""

    def __init__(
        self,
        directory: str | Path,
        rotation_max_bytes: int = 128 * 1024 * 1024,
        fsync_on_terminal: bool = True,
    ) -> None:
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.rotation_max_bytes = rotation_max_bytes
        self.fsync_on_terminal = fsync_on_terminal
        self._lock = threading.Lock()
        # Counters for the metrics endpoint (design §9.3).
        self.records_written = 0
        self.records_rejected = 0

    def write(self, record: Record | dict) -> Envelope:
        """Validate and append a single record. Returns the validated record.

        Accepts a typed Record (already valid by construction) or a dict
        (validated here). Either way, an invalid record is rejected."""
        validated = self._coerce(record)
        event = validated.event  # type: ignore[attr-defined]
        fname = _FILE_FOR_EVENT.get(event)
        if fname is None:  # unreachable for validated records, defensive
            self.records_rejected += 1
            raise JournalWriteError(f"no journal for event {event!r}")

        line = validated.model_dump_json()
        with self._lock:
            path = self.dir / fname
            self._rotate_if_needed(path)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
                fh.flush()
                if self.fsync_on_terminal and event in _FSYNC_EVENTS:
                    os.fsync(fh.fileno())
            self.records_written += 1
        return validated

    def _coerce(self, record: Record | dict) -> Envelope:
        if isinstance(record, Envelope):
            return record
        if not isinstance(record, dict):
            self.records_rejected += 1
            raise JournalWriteError(f"record must be a dict or Record, got {type(record)}")
        model = _MODEL_FOR_EVENT.get(record.get("event"))
        if model is None:
            self.records_rejected += 1
            raise JournalWriteError(f"unknown or missing event: {record.get('event')!r}")
        try:
            return model.model_validate(record)
        except ValidationError as exc:
            self.records_rejected += 1
            raise JournalWriteError(f"malformed record rejected: {exc}") from exc

    def _rotate_if_needed(self, path: Path) -> None:
        """Rename the live file aside once it crosses the size threshold
        (design §4.3). Rotated segments are kept on disk."""
        if path.exists() and path.stat().st_size >= self.rotation_max_bytes:
            stamp = utc_now().replace(":", "").replace("-", "").replace(".", "")
            rotated = path.parent / f"{path.stem}.{stamp}.jsonl"
            path.rename(rotated)

    def iter_records(self, journal: str):
        """Yield validated records from a journal file (live segment only).
        `journal` is one of tool_calls / errors / outcomes."""
        path = self.dir / f"{journal}.jsonl"
        if not path.exists():
            return
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                data = json.loads(line)
                model = _MODEL_FOR_EVENT.get(data.get("event"))
                if model is not None:
                    yield model.model_validate(data)
