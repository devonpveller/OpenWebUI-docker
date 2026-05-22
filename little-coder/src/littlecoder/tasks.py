"""Task lifecycle — state, the FIFO-queue unit, and the per-task journal
context (design §4.2). Tasks are reconstructed by `task_id`, never adjacency,
so interleaved sessions are legal."""

from __future__ import annotations

import dataclasses
import hashlib
from enum import Enum

from .journals import (
    Error,
    Outcome,
    TaskAbandoned,
    TaskEnded,
    TaskOutcomeAmended,
    TaskStarted,
    ToolCall,
    utc_now,
)


def digest(text: str, length: int = 16) -> str:
    """Short, stable digest. Journals record digests of prompts and args,
    never the raw text (design §4.1 envelope carries no raw payloads)."""
    return hashlib.sha256((text or "").encode("utf-8", "replace")).hexdigest()[:length]


class TaskStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    ABANDONED = "abandoned"
    REJECTED = "rejected"


@dataclasses.dataclass
class TaskState:
    """Everything the daemon tracks for one task. The journal is the durable
    record; this is the in-memory view that backs `GET /tasks/{id}`."""

    task_id: str
    session_id: str
    channel: str
    user_id: str
    prompt: str
    repo: str = ""
    lang: str = ""
    acceptance_command: str | None = None
    status: TaskStatus = TaskStatus.QUEUED
    outcome: Outcome | None = None
    signal: str | None = None
    detail: str = ""
    created_ts: str = dataclasses.field(default_factory=utc_now)
    started_ts: str | None = None
    ended_ts: str | None = None

    def public(self) -> dict:
        """API view — a prompt preview, never the full prompt in listings."""
        return {
            "task_id": self.task_id,
            "status": self.status.value,
            "channel": self.channel,
            "user_id": self.user_id,
            "outcome": self.outcome,
            "signal": self.signal,
            "prompt_preview": (self.prompt or "")[:120],
            "repo": self.repo,
            "lang": self.lang,
            "created_ts": self.created_ts,
            "started_ts": self.started_ts,
            "ended_ts": self.ended_ts,
            "detail": self.detail,
        }


class TaskContext:
    """Mints journal records for one task, carrying the per-task `seq`
    counter (design §4.1). Kept alive past `task_ended` so a later outcome
    amendment continues the same sequence."""

    def __init__(self, state: TaskState) -> None:
        self.state = state
        self._seq = 0

    @property
    def seq(self) -> int:
        return self._seq

    def _envelope(self) -> dict:
        env = dict(
            ts=utc_now(),
            task_id=self.state.task_id,
            session_id=self.state.session_id,
            channel=self.state.channel,
            user_id=self.state.user_id,
            repo=self.state.repo,
            lang=self.state.lang,
            seq=self._seq,
        )
        self._seq += 1
        return env

    def started(self) -> TaskStarted:
        return TaskStarted(
            **self._envelope(), trigger_digest=digest(self.state.prompt)
        )

    def tool_call(
        self,
        tool: str,
        ok: bool = True,
        args_digest: str | None = None,
        duration_ms: int | None = None,
    ) -> ToolCall:
        return ToolCall(
            **self._envelope(),
            tool=tool,
            ok=ok,
            args_digest=args_digest,
            duration_ms=duration_ms,
        )

    def error(self, kind: str, message: str, tool: str | None = None) -> Error:
        return Error(**self._envelope(), kind=kind, message=message, tool=tool)

    def ended(self, outcome: Outcome, signal: str | None = None) -> TaskEnded:
        return TaskEnded(**self._envelope(), outcome=outcome, signal=signal)

    def abandoned(self, reason: str) -> TaskAbandoned:
        return TaskAbandoned(**self._envelope(), reason=reason)

    def amended(
        self, outcome: Outcome, prior_outcome: Outcome, amended_by: str
    ) -> TaskOutcomeAmended:
        return TaskOutcomeAmended(
            **self._envelope(),
            outcome=outcome,
            prior_outcome=prior_outcome,
            amended_by=amended_by,
        )
