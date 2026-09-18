"""Which engines are actually answering — measured, not inferred from errors.

research-trust 2026-09-11. The operational hazard the audit and
``documentation/notes/search-engine-alternatives-2026-09-11.md`` §5 both land
on: **the failing engine is the one that never reports a failure.** Over seven
days of live SearXNG logs, mojeek raised 47 errors, wikipedia 21, google 3 —
and bing, which returned ten irrelevant hits for the first token of every
single query, raised one timeout. Any alert keyed off engine errors shows bing
green forever.

So this records what each response actually CONTAINED. It is a rolling window
over the payloads the provider already parses; no extra requests, no state
outside the process, nothing written anywhere.
"""

from __future__ import annotations

from collections import Counter, deque
from typing import Any

#: How many recent payloads the window keeps. One research job issues 6-20
#: searches, so this covers roughly the last two or three jobs.
DEFAULT_WINDOW = 50


def engines_in_payload(payload: dict[str, Any]) -> set[str]:
    """Engine names that contributed at least one RESULT to this payload.

    SearXNG puts a single ``engine`` on each result and, when several engines
    returned the same URL, the full list under ``engines``. Both are read: an
    engine that only ever appears as a merge partner is still answering.
    """
    out: set[str] = set()
    for item in payload.get("results") or []:
        if not isinstance(item, dict):
            continue
        one = item.get("engine")
        if isinstance(one, str) and one:
            out.add(one)
        many = item.get("engines")
        if isinstance(many, (list, tuple, set)):
            out.update(e for e in many if isinstance(e, str) and e)
    return out


def unresponsive_in_payload(payload: dict[str, Any]) -> dict[str, str]:
    """``{engine: reason}`` for engines SearXNG reported as failing."""
    out: dict[str, str] = {}
    for entry in payload.get("unresponsive_engines") or []:
        if isinstance(entry, (list, tuple)) and entry:
            name = entry[0]
            reason = entry[1] if len(entry) > 1 else ""
            if isinstance(name, str) and name:
                out[name] = str(reason)
        elif isinstance(entry, str) and entry:
            out[entry] = ""
    return out


class EngineHealth:
    """Rolling window over recent SearXNG payloads. Not thread-safe by design:
    the gateway is a single asyncio process and ``deque.append`` is atomic
    under the GIL."""

    def __init__(self, window: int = DEFAULT_WINDOW) -> None:
        self._window = max(1, int(window))
        self._answered: deque[set[str]] = deque(maxlen=self._window)
        self._unresponsive: deque[dict[str, str]] = deque(maxlen=self._window)
        self._empty = 0
        self._total = 0

    def record(self, payload: dict[str, Any]) -> None:
        if not isinstance(payload, dict):
            return
        answered = engines_in_payload(payload)
        self._answered.append(answered)
        self._unresponsive.append(unresponsive_in_payload(payload))
        self._total += 1
        if not answered:
            self._empty += 1

    def snapshot(self) -> dict[str, Any]:
        counts: Counter[str] = Counter()
        for s in self._answered:
            counts.update(s)
        fails: Counter[str] = Counter()
        for d in self._unresponsive:
            fails.update(d.keys())
        samples = len(self._answered)
        per_query = [len(s) for s in self._answered]
        return {
            "samples": samples,
            "searches_total": self._total,
            # Engine -> how many of the recent payloads it put a result into.
            "engines_answering_recent": dict(counts.most_common()),
            "engines_answering_now": len(counts),
            # Fewest engines any recent single payload had. One is the shape of
            # the audited outage; zero means nobody answered at all.
            "min_engines_in_a_search": min(per_query) if per_query else 0,
            "unresponsive_recent": dict(fails.most_common()),
            "empty_payloads": self._empty,
        }


def health_verdict(snap: dict[str, Any], min_engines: int = 2) -> str:
    """``ok`` | ``DEGRADED`` | ``unknown``.

    ``unknown`` before any search has been served — an untouched gateway is not
    healthy and is not degraded, and reporting either would be a guess. This is
    the distinction the old readiness probe could not make: it asked SearXNG for
    a 200, and a 200 full of junk is what the incident was made of.
    """
    if not snap.get("samples"):
        return "unknown"
    if int(snap.get("engines_answering_now", 0)) < min_engines:
        return "DEGRADED"
    if int(snap.get("min_engines_in_a_search", 0)) == 0:
        return "DEGRADED"
    return "ok"
