"""Rolling completion-time metric (T) — the single most load-adaptive signal.

T = mean duration of the last ~window completed requests for a model. Feeds the
wait estimate ``ceil(position_ahead / P) * T`` (design §4.1.3/§8b). Optionally
trims the single worst outlier so one 9-minute deep-research request doesn't skew
the next arrival's estimate and over-reject it (§7.2).
"""

from __future__ import annotations

from collections import deque


class RollingT:
    """Per-model rolling mean of completion durations (seconds)."""

    def __init__(self, window: int = 5, initial: float = 30.0, trim_outlier: bool = True) -> None:
        self._samples: deque[float] = deque(maxlen=window)
        self._initial = initial
        self._trim_outlier = trim_outlier

    def record(self, duration_s: float) -> None:
        if duration_s > 0:
            self._samples.append(duration_s)

    @property
    def value(self) -> float:
        """Current T. Returns the configured initial estimate until samples land."""
        if not self._samples:
            return self._initial
        samples = list(self._samples)
        # Trim the single worst outlier only when there's enough signal that
        # dropping one sample still leaves a meaningful mean.
        if self._trim_outlier and len(samples) >= 4:
            samples.remove(max(samples))
        return sum(samples) / len(samples)

    @property
    def count(self) -> int:
        return len(self._samples)
