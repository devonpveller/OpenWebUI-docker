"""Shared fixtures. Tests run on a file-backed SQLite DB (each Database session opens the
same file, so state persists across sessions — needed for the restart-persistence test and
faithful to the fail-safe design). No network / no GPU / no openai-instructor needed."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import pytest_asyncio

# Make `app` importable when running pytest from the agent-bridge dir.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import Database  # noqa: E402
from app.modules.audit_sink import AuditSink  # noqa: E402
from app.config import Settings  # noqa: E402


@pytest.fixture
def db_url(tmp_path: Path) -> str:
    return f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"


@pytest_asyncio.fixture
async def db(db_url: str):
    d = Database(db_url)
    await d.create_all()
    yield d
    await d.dispose()


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None)  # ignore any real .env during tests


@pytest.fixture
def audit(db, settings) -> AuditSink:
    return AuditSink(db, settings)
