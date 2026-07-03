"""Operator audit log — `audit.jsonl` (design §4.4).

A separate journal for operator actions, kept apart from the three task
journals: different reader, longer retention, different access controls.
Mixing them works but bleeds responsibilities.

Events emitted in Tool: `project_switched`, `task_outcome_amended`,
`shutdown`. Later chapters add `upstream_pulled`, `approve_decision`,
`artifact_retired`, deploys, preflight exit.

Every audit record is fsync'd — operator actions are low-volume and their
durability matters more than throughput.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from . import SCHEMA_VERSION
from .journals import utc_now

# Known audit events. Unknown events are rejected — the audit log is a
# disciplined record, not a free-for-all.
TOOL_EVENTS = {"project_switched", "project_upstream_set", "task_outcome_amended", "shutdown"}
OBSERVER_EVENTS = {
    # Chapter 3 transitions + iteration lifecycle. `chapter_advanced` is
    # how the operator records moving between chapters (already used at
    # the Ch2→Ch3 transition); `observer_iteration_*` are the meta
    # outer-loop autotrigger trail (design §3.2).
    "chapter_advanced",
    "observer_iteration_completed",
    "observer_iteration_failed",
}
LATER_EVENTS = {
    "upstream_pulled",
    "approve_decision",
    "artifact_retired",
    "deploy",
    "preflight_exit",
    "invalidated_by_upstream",
    # Chapter 5 §5c / §5f — tier-3 justification + PR drafting trail.
    "tier3_justification_drafted",
    "tier3_justification_refused",
    "tier3_pr_drafted",
    # Operator-triggered AGENTS.md bootstrap (the explicit-trigger path
    # for the §3.7 layer-3 cycle). Records the requested mode + the
    # task_id the daemon spawned, so the trigger trail is auditable
    # even when the task itself fails / is cancelled.
    "bootstrap_agents_triggered",
}
KNOWN_EVENTS = TOOL_EVENTS | OBSERVER_EVENTS | LATER_EVENTS


class AuditRecord(BaseModel):
    model_config = {"extra": "forbid"}

    ts: str
    event: str
    actor: str  # operator id, channel, or "system"
    detail: dict[str, Any] = {}
    schema_version: int = SCHEMA_VERSION


class AuditWriteError(RuntimeError):
    """An audit record was malformed or could not be written."""


class AuditLog:
    """Writer for `audit.jsonl`. Thread-safe; one instance per process."""

    def __init__(self, directory: str | Path, filename: str = "audit.jsonl") -> None:
        self.path = Path(directory) / filename
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.records_written = 0

    def write(self, event: str, actor: str, **detail: Any) -> AuditRecord:
        """Append one audit record. Raises on an unknown event."""
        if event not in KNOWN_EVENTS:
            raise AuditWriteError(f"unknown audit event: {event!r}")
        record = AuditRecord(ts=utc_now(), event=event, actor=actor, detail=detail)
        line = record.model_dump_json()
        with self._lock:
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
                fh.flush()
                os.fsync(fh.fileno())  # every operator action is durable
            self.records_written += 1
        return record
