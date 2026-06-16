"""Model registry + global connection cap.

Routes a request's model name to its ModelQueue and upstream base URL. Keyed by
model from the start (design §10.2) so P4 (embed upstream, swap-aware admission)
is configuration, not a rewrite. Also owns the hard absolute cap on total
concurrent held requests — a FD/socket safety valve independent of any
per-service budget (§10.3.3).
"""

from __future__ import annotations

import asyncio

from .config import Settings
from .models import Rejected
from .scheduler import ModelQueue


def normalize_model(model: str | None) -> str:
    """Collapse variant suffixes to the queue key. ``qwen36-27b`` and
    ``qwen36-27b:nothink`` share ONE upstream slot (design §3.1), so they must
    share ONE ModelQueue — else the :nothink variant gets its own permit pool and
    the global cap is a lie."""
    if not model:
        return "qwen36-27b"  # default chat model
    return model.split(":", 1)[0]


class Registry:
    """Holds per-model queues, the upstream map, and the global connection cap."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._lock = asyncio.Lock()
        self._held_total = 0
        # Build one queue per known model family. One entry today; P4 adds embed.
        self._queues: dict[str, ModelQueue] = {
            "qwen36-27b": ModelQueue(
                "qwen36-27b",
                slots=settings.slots,
                max_in_flight=settings.max_in_flight,
                settings=settings,
            ),
        }
        # Embed queue (P4). The embed upstream is plain llama.cpp with an unbounded
        # internal FIFO — generous backstop + NO budget gate so high-volume
        # embedding bursts (OB1 backfill) behave as before, never rejected.
        self._queues["bge-m3"] = ModelQueue(
            "bge-m3",
            slots=settings.embed_slots,
            max_in_flight=settings.embed_max_in_flight,
            settings=settings,
            backstop_depth=settings.embed_backstop_depth,
            enforce_budget=False,
        )
        self._embed_models = {"bge-m3", "bge-m3-f16.gguf", "qllama/bge-m3"}

    def upstream_for(self, model: str | None) -> str:
        key = normalize_model(model)
        if key in self._embed_models or key.startswith("bge"):
            return self._settings.embed_upstream_base_url
        return self._settings.upstream_base_url

    def queue_for(self, model: str | None) -> ModelQueue:
        key = normalize_model(model)
        if key in self._embed_models or key.startswith("bge"):
            return self._queues["bge-m3"]
        # Unknown chat-ish model → the chat queue (single shared backend slot).
        return self._queues.get(key, self._queues["qwen36-27b"])

    def queues(self) -> dict[str, ModelQueue]:
        return dict(self._queues)

    async def reserve_connection(self, model: str) -> None:
        """Admit one held connection against the global cap, or reject (§10.3.3)."""
        async with self._lock:
            if self._held_total >= self._settings.max_total_connections:
                raise Rejected(
                    type="queue_connections_exhausted",
                    message=(
                        f"llm-queue at hard connection cap "
                        f"({self._settings.max_total_connections}); shedding load."
                    ),
                    model=model,
                    status_code=503,
                    retry_after_s=10,
                )
            self._held_total += 1

    async def release_connection(self) -> None:
        async with self._lock:
            if self._held_total > 0:
                self._held_total -= 1

    @property
    def held_total(self) -> int:
        return self._held_total
