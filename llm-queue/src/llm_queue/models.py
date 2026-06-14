"""Shared types: the structured rejection (design §4.5) and queue-view rows."""

from __future__ import annotations

from dataclasses import dataclass


class Rejected(Exception):
    """An admission rejection carrying the honest, actionable body of design §4.5.

    Replaces llama-swap's opaque ``Too many requests`` (18 bytes) with a real
    429/503 + a structured reason + a live ``Retry-After``. LiteLLM relays the
    upstream error body, so the caller sees *why* it was turned away.
    """

    def __init__(
        self,
        *,
        type: str,
        message: str,
        model: str,
        status_code: int = 429,
        projected_wait_s: float | None = None,
        acceptable_wait_s: float | None = None,
        queue_depth: int | None = None,
        avg_completion_s: float | None = None,
        slots: int | None = None,
        retry_after_s: int = 5,
    ) -> None:
        super().__init__(message)
        self.type = type
        self.message = message
        self.model = model
        self.status_code = status_code
        self.projected_wait_s = projected_wait_s
        self.acceptable_wait_s = acceptable_wait_s
        self.queue_depth = queue_depth
        self.avg_completion_s = avg_completion_s
        self.slots = slots
        self.retry_after_s = retry_after_s

    def body(self) -> dict[str, object]:
        err: dict[str, object] = {"type": self.type, "message": self.message, "model": self.model}
        if self.projected_wait_s is not None:
            err["projected_wait_s"] = round(self.projected_wait_s, 1)
        if self.acceptable_wait_s is not None:
            err["acceptable_wait_s"] = self.acceptable_wait_s
        if self.queue_depth is not None:
            err["queue_depth"] = self.queue_depth
        if self.avg_completion_s is not None:
            err["avg_completion_s"] = round(self.avg_completion_s, 1)
        if self.slots is not None:
            err["slots"] = self.slots
        err["retry_after_s"] = self.retry_after_s
        return {"error": err}


class Cancelled(Exception):
    """The waiting request was evicted (client disconnect or operator cancel)
    before it was ever dispatched upstream — no slot was burned."""


@dataclass
class RunningRow:
    id: str
    key: str
    model: str
    started: float  # unix epoch (informational)
    elapsed_s: float


@dataclass
class WaitingRow:
    id: str
    key: str
    prio: int
    waited_s: float
    est_wait_s: float
