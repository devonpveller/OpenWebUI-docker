"""AppState — the single composition root holding the long-lived collaborators
(design §10.2: transport / scheduler / policy / metrics / control wired together
but each varying independently)."""

from __future__ import annotations

import asyncio
import itertools
import time

from .config import Settings, get_settings
from .events import EventSink
from .logging import get_logger
from .policy import PriorityPolicy, build_policy
from .registry import Registry
from .transport import Upstream

log = get_logger("llm_queue.app_state")


class AppState:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.registry = Registry(settings)
        self.upstream = Upstream(settings.upstream_timeout_s)
        self.policy: PriorityPolicy = build_policy(
            settings.policy_json, settings.default_acceptable_wait_s
        )
        self.events = EventSink(settings.events_db_path)
        self._seq = itertools.count()
        self._reaper_task: asyncio.Task | None = None

    def next_seq(self) -> int:
        return next(self._seq)

    async def start(self) -> None:
        await self.events.start()
        # Connection-leak reaper: reclaims held slots that Starlette abandoned on client disconnect
        # (their release generator never ran). Without this, leaked slots accumulate and permanently
        # wedge the hard connection cap — shedding all load while the GPU is idle (observed failure).
        self._reaper_task = asyncio.create_task(self._reap_loop())

    async def stop(self) -> None:
        if self._reaper_task is not None:
            self._reaper_task.cancel()
            try:
                await self._reaper_task
            except asyncio.CancelledError:
                pass
            self._reaper_task = None
        await self.events.stop()
        await self.upstream.aclose()

    async def _reap_loop(self) -> None:
        """Periodically reclaim leaked held connections. Any reap is LOGGED (WARNING) + emitted as an
        event so a persistent leak stays visible rather than being silently self-healed forever."""
        ttl = self.settings.conn_ttl_s
        interval = self.settings.reap_interval_s
        while True:
            try:
                await asyncio.sleep(interval)
                reclaimed = await self.registry.reap_stale_connections(ttl)
                if reclaimed:
                    log.warning(
                        "conn_reaper reclaimed leaked held connection(s)",
                        count=len(reclaimed),
                        request_ids=reclaimed[:20],
                        held_after=self.registry.held_total,
                        ttl_s=ttl,
                    )
                    await self.events.emit(
                        "conn_reaped", ts=time.time(), count=len(reclaimed),
                        held_after=self.registry.held_total,
                    )
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the reaper must never die on a transient error
                log.exception("conn_reaper sweep failed (will retry)")


def build_state() -> AppState:
    return AppState(get_settings())
