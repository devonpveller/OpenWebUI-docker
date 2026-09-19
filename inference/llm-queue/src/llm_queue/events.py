"""Analytics event sink (design §4.1.7 / §4.4 / P3).

Emits admit/start/finish/reject/wait events with the measured duration and
estimate-vs-actual error, for evals over time. Writes to llm-queue's OWN SQLite
store — NEVER LiteLLM's schema-managed ``LiteLLM_SpendLogs`` (a Liskov/
encapsulation violation that breaks on a LiteLLM upgrade, §10). The live board
joins this against the ledger at query time (§5).

Events are always emitted as structured logs; the SQLite store is optional
(enabled by LLM_QUEUE_EVENTS_DB_PATH) and best-effort — a store write failure
must never break the inference path.
"""

from __future__ import annotations

import asyncio

import aiosqlite

from .logging import get_logger

log = get_logger("llm_queue.events")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS queue_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           REAL    NOT NULL,
    event        TEXT    NOT NULL,   -- admit|finish|reject
    request_id   TEXT,
    key          TEXT,
    model        TEXT,
    prio         INTEGER,
    wait_s       REAL,               -- measured queue wait
    duration_s   REAL,               -- measured upstream processing
    est_wait_s   REAL,               -- estimate at enqueue (for est-vs-actual)
    depth        INTEGER,
    status       INTEGER,
    reason       TEXT
);
CREATE INDEX IF NOT EXISTS idx_queue_events_ts ON queue_events(ts);
CREATE INDEX IF NOT EXISTS idx_queue_events_model ON queue_events(model);
"""


class EventSink:
    def __init__(self, db_path: str = "") -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        if not self._db_path:
            log.info("events_log_only", reason="no LLM_QUEUE_EVENTS_DB_PATH set")
            return
        try:
            self._db = await aiosqlite.connect(self._db_path)
            await self._db.executescript(_SCHEMA)
            await self._db.commit()
            log.info("events_store_ready", path=self._db_path)
        except Exception as exc:  # noqa: BLE001 — never fatal
            log.warning("events_store_init_failed", error=str(exc))
            self._db = None

    async def stop(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def emit(self, event: str, **fields: object) -> None:
        log.info(f"queue_{event}", **fields)
        if self._db is None:
            return
        cols = (
            "ts", "event", "request_id", "key", "model", "prio",
            "wait_s", "duration_s", "est_wait_s", "depth", "status", "reason",
        )
        values = [fields.get("ts"), event] + [fields.get(c) for c in cols[2:]]
        try:
            async with self._lock:
                await self._db.execute(
                    f"INSERT INTO queue_events ({','.join(cols)}) "
                    f"VALUES ({','.join('?' * len(cols))})",
                    values,
                )
                await self._db.commit()
        except Exception as exc:  # noqa: BLE001 — best-effort, never break inference
            log.warning("events_store_write_failed", error=str(exc))
