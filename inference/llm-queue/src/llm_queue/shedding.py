"""Shedding episodes (ao-queue, 2026-10-07) - so the stack LEARNS that inference is being refused.

gym-002: llm-queue held 126 of its 128 connections and refused 46 requests in ten minutes with
503 `queue_connections_exhausted`. Each refusal was one structured log line; nothing turned that
into "inference is shedding", so callers (agent-org workers among them) read it as their own
failure.

This module folds the raw signals into EPISODES with hysteresis:

  start  - a capacity refusal (503 queue_connections_exhausted), or held connections reaching
           `alert_at` (ceil(cap * LLM_QUEUE_SHED_ALERT_RATIO), 96 of 128 by default) - the leak or
           a storm is eating the cap before the first refusal;
  clear  - held at or below `clear_at` (cap * LLM_QUEUE_SHED_CLEAR_RATIO, 64) AND no capacity
           refusal for LLM_QUEUE_SHED_CLEAR_QUIET_S (300 s), so a value swinging on the line is one
           episode, not a flap.

Each transition is logged ONCE (WARNING `inference_shedding_started` / INFO
`inference_shedding_cleared`) and recorded as a `shed_start` / `shed_clear` event. The current state
and the last closed episode ride on `GET /healthz` under `shedding`; the host watchdog
(`scripts/checks/stack-watchdog.ps1`, Test-InferenceShedding) reads that and pages ONCE per episode
with an all-clear. Read-only bookkeeping: it never changes an admission decision.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass
class Episode:
    id: int
    started_at: float
    trigger: str  # "refused" | "held_threshold"
    refusals: int = 0
    peak_held: int = 0
    cleared_at: float | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "started_at": round(self.started_at, 3),
            "cleared_at": round(self.cleared_at, 3) if self.cleared_at is not None else None,
            "trigger": self.trigger,
            "refusals": self.refusals,
            "peak_held": self.peak_held,
        }


@dataclass
class ShedMonitor:
    cap: int
    alert_ratio: float = 0.75
    clear_ratio: float = 0.5
    clear_quiet_s: float = 300.0
    wall: Callable[[], float] = time.time
    mono: Callable[[], float] = time.monotonic
    process_started_at: float = field(default=0.0)
    refusals_total: int = 0
    current: Episode | None = None
    last: Episode | None = None
    _episodes: int = 0
    _last_refusal_mono: float | None = None

    def __post_init__(self) -> None:
        if not self.process_started_at:
            self.process_started_at = self.wall()

    @property
    def alert_at(self) -> int:
        return max(1, math.ceil(self.cap * self.alert_ratio))

    @property
    def clear_at(self) -> int:
        return min(self.alert_at - 1, int(self.cap * self.clear_ratio))

    def observe(self, held: int, *, refused: bool = False) -> str | None:
        """Fold one observation in. Returns "started", "cleared" or None (no transition)."""
        if refused:
            self.refusals_total += 1
            self._last_refusal_mono = self.mono()
        if self.current is None:
            if refused or held >= self.alert_at:
                self._episodes += 1
                self.current = Episode(
                    id=self._episodes,
                    started_at=self.wall(),
                    trigger="refused" if refused else "held_threshold",
                    refusals=1 if refused else 0,
                    peak_held=held,
                )
                return "started"
            return None
        ep = self.current
        if refused:
            ep.refusals += 1
        ep.peak_held = max(ep.peak_held, held)
        quiet = (
            self._last_refusal_mono is None
            or self.mono() - self._last_refusal_mono >= self.clear_quiet_s
        )
        if held <= self.clear_at and quiet:
            ep.cleared_at = self.wall()
            self.last, self.current = ep, None
            return "cleared"
        return None

    def snapshot(self, held: int) -> dict[str, object]:
        return {
            "state": "shedding" if self.current is not None else "ok",
            "held": held,
            "cap": self.cap,
            "alert_at": self.alert_at,
            "clear_at": self.clear_at,
            "clear_quiet_s": self.clear_quiet_s,
            "refusals_total": self.refusals_total,
            "episodes_total": self._episodes,
            "process_started_at": round(self.process_started_at, 3),
            "current": self.current.as_dict() if self.current else None,
            "last": self.last.as_dict() if self.last else None,
        }
