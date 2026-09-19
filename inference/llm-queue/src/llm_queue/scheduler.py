"""Admission scheduler — the primitive nothing upstream has.

Per model: a release-on-completion concurrency cap (N in-flight), a priority
wait-heap, a rolling-T metric, and per-key in-flight caps. This is the ordered
depth the design moves *out* of llama.cpp's opaque internal FIFO and into our
control (§3.1, §7.4). All state is in-process and authoritative (§4.4) — one
uvicorn worker on purpose.

Dispatch is priority-ordered AND per-key-capped: when a permit frees we scan the
heap in (rank, seq) order and dispatch the first waiter whose key is under its
max-concurrency, so a batch caller can't own all the slots (§8d) and an
interactive chat jumps ahead of queued batch load (§8c).
"""

from __future__ import annotations

import asyncio
import heapq
import math
import time

from .config import Settings
from .metrics import RollingT
from .models import Cancelled, Rejected, RunningRow, WaitingRow
from .policy import PriorityClass


class Waiter:
    """One request's lifecycle state, from enqueue to release."""

    __slots__ = (
        "id",
        "key",
        "model",
        "cls",
        "seq",
        "rank",
        "enqueued_monotonic",
        "started_monotonic",
        "dispatched_event",
        "dispatched",
        "cancelled",
        "position_at_enqueue",
        "est_at_enqueue",
        "wait_seconds",
    )

    def __init__(self, *, id: str, key: str, model: str, cls: PriorityClass, seq: int) -> None:
        self.id = id
        self.key = key
        self.model = model
        self.cls = cls
        self.seq = seq
        self.rank = cls.rank
        self.enqueued_monotonic = time.monotonic()
        self.started_monotonic: float | None = None
        self.dispatched_event = asyncio.Event()
        self.dispatched = False
        self.cancelled = False
        self.position_at_enqueue = 0
        self.est_at_enqueue = 0.0
        self.wait_seconds = 0.0


class ModelQueue:
    """Admission + observability for ONE backend model (design §10.2: keyed by
    model now, even with one model, so P4 is configuration not a rewrite)."""

    def __init__(
        self,
        model_key: str,
        *,
        slots: int,
        max_in_flight: int,
        settings: Settings,
        backstop_depth: int | None = None,
        enforce_budget: bool | None = None,
    ) -> None:
        self.model_key = model_key
        self._slots = slots
        self._permits = max_in_flight
        self._settings = settings
        # Per-model overrides (design §10.2: key config by model). Embeddings get a
        # generous backstop + no budget gate; chat uses the global settings.
        self._backstop = backstop_depth if backstop_depth is not None else settings.backstop_depth
        self._enforce_budget = (
            enforce_budget if enforce_budget is not None else settings.enforce_budget
        )
        self._t = RollingT(
            window=settings.t_window,
            initial=settings.t_initial_s,
            trim_outlier=settings.t_trim_outlier,
        )
        self._lock = asyncio.Lock()
        self._heap: list[tuple[int, int, str]] = []  # (rank, seq, id) — waiting, ordered
        self._waiters: dict[str, Waiter] = {}  # waiting only
        self._running: dict[str, Waiter] = {}
        self._inflight_by_key: dict[str, int] = {}

    # ---- metrics / introspection -----------------------------------------

    @property
    def avg_t(self) -> float:
        return self._t.value

    @property
    def slots(self) -> int:
        return self._slots

    def held(self) -> int:
        """Total requests currently held (waiting + running)."""
        return len(self._heap) + len(self._running)

    def _estimate_wait_locked(self, rank: int) -> float:
        """Projected wait for a NEW request of ``rank`` (design §8b):
        ceil(position_ahead / P) * T, where position_ahead = everything running
        plus every waiter with higher-or-equal priority."""
        ahead = len(self._running) + sum(1 for (r, _s, _i) in self._heap if r <= rank)
        return math.ceil(ahead / self._slots) * self._t.value

    def estimate_wait(self, rank: int) -> float:
        # Read-only estimate for the pre-flight /queue/estimate endpoint. The
        # GIL + single event loop make this snapshot consistent enough without
        # the lock (no await between reads).
        return self._estimate_wait_locked(rank)

    # ---- admission --------------------------------------------------------

    async def enqueue(self, waiter: Waiter) -> None:
        """Admission DECISION + enqueue. Raises Rejected (over depth/budget)
        synchronously so the caller can still send an honest 429 before any
        response bytes go out. On success the waiter is in the heap and a
        dispatch attempt has been made. Await dispatch separately so a streaming
        caller can heartbeat while it waits (design §10.4)."""
        async with self._lock:
            waiting = len(self._heap)
            # Time-based budget gate (design §8b) — only when enforced (P2+).
            if self._enforce_budget:
                est = self._estimate_wait_locked(waiter.rank)
                if est > waiter.cls.acceptable_wait_s:
                    raise Rejected(
                        type="queue_over_budget",
                        message=(
                            f"{waiter.model} saturated: projected wait ~{est:.0f}s exceeds "
                            f"this service's {waiter.cls.acceptable_wait_s:.0f}s budget."
                        ),
                        model=waiter.model,
                        status_code=429,
                        projected_wait_s=est,
                        acceptable_wait_s=waiter.cls.acceptable_wait_s,
                        queue_depth=waiting,
                        avg_completion_s=self._t.value,
                        slots=self._slots,
                        retry_after_s=max(1, int(est)),
                    )
            # Coarse depth backstop (design §8b) — also bounds held FDs (§10.3.3).
            if waiting >= self._backstop:
                est = self._estimate_wait_locked(waiter.rank)
                raise Rejected(
                    type="queue_over_depth",
                    message=(
                        f"{waiter.model} saturated: {waiting} requests already waiting "
                        f"(backstop {self._backstop})."
                    ),
                    model=waiter.model,
                    status_code=429,
                    projected_wait_s=est,
                    queue_depth=waiting,
                    avg_completion_s=self._t.value,
                    slots=self._slots,
                    retry_after_s=max(1, int(est)),
                )

            heapq.heappush(self._heap, (waiter.rank, waiter.seq, waiter.id))
            self._waiters[waiter.id] = waiter
            waiter.position_at_enqueue = len(self._running) + len(self._heap)
            waiter.est_at_enqueue = self._estimate_wait_locked(waiter.rank)
            self._try_dispatch_locked()

    async def await_dispatch(self, waiter: Waiter) -> None:
        """Block until the waiter is dispatched (permit held, running registered,
        started timestamp set) or evicted (raises Cancelled)."""
        await waiter.dispatched_event.wait()
        if waiter.cancelled:
            raise Cancelled()

    def _try_dispatch_locked(self) -> None:
        """Wake the highest-priority eligible waiter(s) while permits remain.

        Eligible = key under its per-class max-concurrency. Ineligible waiters
        stay in the heap (they become eligible when one of their key's in-flight
        finishes), so a capped batch caller yields its turn to the next class
        instead of blocking the whole queue."""
        while self._permits > 0 and self._heap:
            picked: tuple[int, int, str] | None = None
            skipped: list[tuple[int, int, str]] = []
            while self._heap:
                entry = heapq.heappop(self._heap)
                _rank, _seq, wid = entry
                w = self._waiters.get(wid)
                if w is None or w.cancelled:
                    self._waiters.pop(wid, None)
                    continue  # drop stale/cancelled entry entirely
                cap = w.cls.max_concurrency
                if cap is not None and self._inflight_by_key.get(w.key, 0) >= cap:
                    skipped.append(entry)  # at cap — leave queued, try next
                    continue
                picked = entry
                break
            for entry in skipped:
                heapq.heappush(self._heap, entry)
            if picked is None:
                break  # nothing eligible right now
            _rank, _seq, wid = picked
            w = self._waiters.pop(wid)
            self._permits -= 1
            w.dispatched = True
            w.started_monotonic = time.monotonic()
            w.wait_seconds = w.started_monotonic - w.enqueued_monotonic
            self._running[wid] = w
            self._inflight_by_key[w.key] = self._inflight_by_key.get(w.key, 0) + 1
            w.dispatched_event.set()

    async def release(self, waiter: Waiter, *, record_duration: bool = True) -> None:
        """Return the permit and (optionally) feed T. Idempotent for waiters that
        were cancelled before dispatch."""
        async with self._lock:
            if waiter.id in self._running:
                self._running.pop(waiter.id, None)
                n = self._inflight_by_key.get(waiter.key, 0) - 1
                if n > 0:
                    self._inflight_by_key[waiter.key] = n
                else:
                    self._inflight_by_key.pop(waiter.key, None)
                self._permits += 1
                if record_duration and waiter.started_monotonic is not None:
                    self._t.record(time.monotonic() - waiter.started_monotonic)
                self._try_dispatch_locked()

    async def cancel_waiting(self, waiter: Waiter) -> bool:
        """Evict a still-waiting request (client disconnect / operator cancel,
        design §4.2/§10.3.4). No permit was held, so none is returned. Returns
        True if it was waiting and is now evicted."""
        async with self._lock:
            if waiter.id in self._waiters and not waiter.dispatched:
                waiter.cancelled = True
                self._waiters.pop(waiter.id, None)
                # Heap entry is dropped lazily by _try_dispatch_locked.
                waiter.dispatched_event.set()  # unblock acquire() -> raises Cancelled
                return True
            return False

    async def set_priority(self, waiter_id: str, new_rank: int) -> bool:
        """Dynamic re-prioritise a waiting request (design §4.2). Re-heapify."""
        async with self._lock:
            w = self._waiters.get(waiter_id)
            if w is None or w.dispatched:
                return False
            w.rank = new_rank
            # Rebuild the heap from the live waiter ranks.
            self._heap = [
                (self._waiters[i].rank, s, i)
                for (_r, s, i) in self._heap
                if i in self._waiters
            ]
            heapq.heapify(self._heap)
            self._try_dispatch_locked()
            return True

    # ---- snapshot ---------------------------------------------------------

    def snapshot(self) -> dict[str, object]:
        now = time.monotonic()
        running = [
            RunningRow(
                id=w.id,
                key=w.key,
                model=w.model,
                started=time.time() - (now - (w.started_monotonic or now)),
                elapsed_s=round(now - (w.started_monotonic or now), 2),
            ).__dict__
            for w in self._running.values()
        ]
        waiting = [
            WaitingRow(
                id=self._waiters[i].id,
                key=self._waiters[i].key,
                prio=self._waiters[i].rank,
                waited_s=round(now - self._waiters[i].enqueued_monotonic, 2),
                est_wait_s=round(self._estimate_wait_locked(self._waiters[i].rank), 1),
            ).__dict__
            for (_r, _s, i) in sorted(self._heap)
            if i in self._waiters
        ]
        return {
            "model": self.model_key,
            "running": running,
            "waiting": waiting,
            "avg_T_s": round(self._t.value, 2),
            "P": self._slots,
            "permits_free": self._permits,
            "inflight_by_key": dict(self._inflight_by_key),
        }
