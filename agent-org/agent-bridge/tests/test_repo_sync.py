"""RS.2 — the bridge's THIN repo-sync triggers (REPO-SOURCES-WIRING §5). The ENGINE owns
enumeration/fetch/screening/staging; the bridge only says WHEN: project onboarding, a D4 merge
(main moved), or the operator asking in plain language. Syncs the repo AND its registered upstream
(the docs usually live in the parent). Transparent: results are announced. Mocked engine."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from app.adapters.chat import FakeChatAdapter
from app.config import Settings
from app.db import Database
from app.modules.model_router import FakeModelClient
from app.orchestrator import Orchestrator
from app.worker.harness import FakeHarness

ROOT = Path(__file__).resolve().parents[1]


async def _orch(db_url, tmp_path=None, **over):
    kw = dict(
        _env_file=None, chat_adapter="fake",
        profiles_dir=str(ROOT / "profiles"), charters_dir=str(ROOT / "charters"),
        floor_dir=str(ROOT / "floor"), worker_instance_urls="http://w1:8090",
        max_concurrent_workers=1, database_url=db_url, project_survey_enabled=False,
        review_mode="off", plan_approval="off",
    )
    if tmp_path is not None:   # enable the GitHub App plane (for merge-trigger tests)
        key = tmp_path / "app.pem"
        key.write_text("dummy")
        kw.update(github_app_id="1", github_app_owner="devonpveller",
                  github_app_private_key_path=str(key))
    kw.update(over)
    settings = Settings(**kw)
    db = Database(db_url)
    orch = Orchestrator(settings, db, FakeChatAdapter(),
                        model_client=FakeModelClient(), harness=FakeHarness())
    await orch.setup()
    return orch, orch.chat, db


async def _drain(orch):
    for _ in range(12):
        if not orch._bg_tasks:
            return
        await asyncio.gather(*list(orch._bg_tasks), return_exceptions=True)


def _engine(calls: list):
    """Mock openbrain-research: records every repo-sync POST, returns a happy sync result."""
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/sources/repo-sync"
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={
            "ok": True, "sha": "08b80bca708e6e35f78ea8a8bb718478285642be",
            "synced": 10, "synced_urls": [], "quarantined": [], "skipped": []})
    return httpx.MockTransport(handler)


async def test_repo_sync_hits_engine_for_repo_and_upstream(db_url):
    orch, chat, db = await _orch(db_url)
    try:
        calls: list = []
        orch._research_transport = _engine(calls)
        await orch.projects.add("murder", "https://github.com/devonpveller/murder",
                                upstream_url="https://github.com/isadorasophia/murder")
        mgmt = await orch.mgmt_channel_id()
        await orch._repo_sync("murder", announce_channel=mgmt, announce_thread="t")
        urls = [c["repo_url"] for c in calls]
        assert "https://github.com/devonpveller/murder" in urls          # the fork
        assert "https://github.com/isadorasophia/murder" in urls         # AND the upstream (§4)
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "Knowledge sync" in msgs and "10" in msgs                 # transparent result
    finally:
        await db.dispose()


async def test_project_add_triggers_sync(db_url):
    orch, chat, db = await _orch(db_url)
    try:
        calls: list = []
        orch._research_transport = _engine(calls)
        mgmt = await orch.mgmt_channel_id()
        await orch.handle_event({"id": "a1", "channel_id": mgmt, "is_bot": False, "ts": 1,
                                 "message": "/project add game https://github.com/devonpveller/Docker-Game"})
        await _drain(orch)
        assert any(c["repo_url"].endswith("/Docker-Game") for c in calls)   # onboard → sync fired
    finally:
        await db.dispose()


async def test_nl_sync_docs_triggers_sync(db_url):
    orch, chat, db = await _orch(db_url)
    try:
        calls: list = []
        orch._research_transport = _engine(calls)
        await orch.projects.add("murder", "https://github.com/devonpveller/murder")
        mgmt = await orch.mgmt_channel_id()
        await orch.nl_intake("sync murder docs", mgmt, thread_id="t")    # plain language, no model
        await _drain(orch)
        assert calls and calls[0]["repo_url"].endswith("/murder")
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "syncing" in msgs.lower()
        assert not orch.models._client.calls                             # deterministic — no model
    finally:
        await db.dispose()


async def test_engine_down_is_reported_not_fatal(db_url):
    orch, chat, db = await _orch(db_url)
    try:
        def down(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("down", request=request)
        orch._research_transport = httpx.MockTransport(down)
        await orch.projects.add("murder", "https://github.com/devonpveller/murder")
        mgmt = await orch.mgmt_channel_id()
        await orch._repo_sync("murder", announce_channel=mgmt)           # must not raise
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "unreachable" in msgs                                     # honest, non-fatal
    finally:
        await db.dispose()


async def test_sync_disabled_is_a_noop(db_url):
    orch, chat, db = await _orch(db_url, repo_sync_enabled=False)
    try:
        calls: list = []
        orch._research_transport = _engine(calls)
        await orch.projects.add("murder", "https://github.com/devonpveller/murder")
        await orch._repo_sync("murder", announce_channel=await orch.mgmt_channel_id())
        assert not calls                                                 # kill-switch honored
    finally:
        await db.dispose()
