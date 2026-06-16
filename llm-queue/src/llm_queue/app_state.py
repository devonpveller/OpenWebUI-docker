"""AppState — the single composition root holding the long-lived collaborators
(design §10.2: transport / scheduler / policy / metrics / control wired together
but each varying independently)."""

from __future__ import annotations

import itertools

from .config import Settings, get_settings
from .events import EventSink
from .policy import PriorityPolicy, build_policy
from .registry import Registry
from .transport import Upstream


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

    def next_seq(self) -> int:
        return next(self._seq)

    async def start(self) -> None:
        await self.events.start()

    async def stop(self) -> None:
        await self.events.stop()
        await self.upstream.aclose()


def build_state() -> AppState:
    return AppState(get_settings())
