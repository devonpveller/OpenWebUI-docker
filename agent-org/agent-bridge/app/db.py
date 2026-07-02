"""Async SQLAlchemy engine + session factory.

The state store is the source of truth for the governance gate, so it is persisted
(Postgres in prod, SQLite for dev/tests). `frozen` must survive a restart — hence
never in-memory in production (governance §3.0 invariant i).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase


def utcnow() -> datetime:
    """Timezone-aware UTC now — used where a datetime object is needed."""
    return datetime.now(timezone.utc)


def now_iso() -> str:
    """ISO-8601 UTC string — the canonical stored form for all timestamp columns.

    Stored as text so the same value works identically on SQLite (tests) and Postgres
    (prod), and lexical comparison of these UTC strings is chronologically correct.
    """
    return datetime.now(timezone.utc).isoformat()


class Base(DeclarativeBase):
    pass


class Database:
    """Owns the async engine + session factory and creates the schema on start.

    A single instance is shared across modules; the gate module holds its own
    session per transaction so freeze/clear are atomic.
    """

    def __init__(self, url: str) -> None:
        self._url = url
        # SQLite needs check_same_thread off for async; asyncpg needs nothing special.
        connect_args = {}
        self.engine: AsyncEngine = create_async_engine(
            url, future=True, connect_args=connect_args
        )
        self.session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
            self.engine, expire_on_commit=False, class_=AsyncSession
        )

    # Columns added AFTER a table may already exist in a long-lived prod DB. `create_all` only
    # CREATEs missing tables — it never ALTERs an existing one — so an additive column would be
    # absent and every query 500s. This idempotent, additive-only migration self-heals that (the
    # same posture as the scheduler's reset_stale / lazy team resolution). ADD COLUMN only —
    # never drop/rename (that stays a deliberate, reviewed operator act).
    _ADDITIVE_COLUMNS = [
        # (table, column, ddl_type) — introduced by COMMS-MODEL CM.1 (channel = project,
        # effort = thread): an effort now carries its project + effort-card thread root.
        ("efforts", "project", "VARCHAR(64)"),
        ("efforts", "root_post_id", "VARCHAR(64)"),
    ]

    async def create_all(self) -> None:
        # Import models so metadata is populated before create_all.
        from . import models  # noqa: F401

        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await self._add_missing_columns(conn)

    async def _add_missing_columns(self, conn) -> None:
        dialect = conn.engine.dialect.name  # 'sqlite' (tests) | 'postgresql' (prod)
        for table, col, ddl in self._ADDITIVE_COLUMNS:
            try:
                if dialect == "postgresql":
                    # One idempotent statement; safe if the column is already present.
                    await conn.exec_driver_sql(
                        f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {ddl}"
                    )
                elif dialect == "sqlite":
                    # SQLite has no ADD COLUMN IF NOT EXISTS — probe first. On a fresh test DB the
                    # column already exists via create_all, so this is a no-op there.
                    rows = (
                        await conn.exec_driver_sql(f"PRAGMA table_info({table})")
                    ).fetchall()
                    if col not in {r[1] for r in rows}:
                        await conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
            except Exception:  # noqa: BLE001 - a migration probe must never block startup
                pass

    async def dispose(self) -> None:
        await self.engine.dispose()

    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.session_factory() as s:
            yield s
