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

    async def create_all(self) -> None:
        # Import models so metadata is populated before create_all.
        from . import models  # noqa: F401

        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def dispose(self) -> None:
        await self.engine.dispose()

    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.session_factory() as s:
            yield s
