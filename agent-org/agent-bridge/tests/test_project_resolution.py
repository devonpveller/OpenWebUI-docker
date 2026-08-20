"""Effort project-resolution: a request lands in the RIGHT project (not the sandbox) via an
unambiguous name match, and a mis-assigned effort can be moved with NL. Regression for
`effort-init-monogame-engine` being stuck in the sandbox (no repo → worker 409). Fakes only."""

from __future__ import annotations

from pathlib import Path

from app.adapters.chat import FakeChatAdapter
from app.config import Settings
from app.db import Database
from app.models import Effort
from app.modules.model_router import FakeModelClient
from app.orchestrator import Orchestrator
from app.schemas import OperatorIntent
from app.worker.harness import FakeHarness

ROOT = Path(__file__).resolve().parents[1]


async def _orch(db_url):
    settings = Settings(
        _env_file=None, chat_adapter="fake",
        profiles_dir=str(ROOT / "profiles"), charters_dir=str(ROOT / "charters"),
        floor_dir=str(ROOT / "floor"), worker_instance_urls="http://w1:8090",
        max_concurrent_workers=1, database_url=db_url, project_survey_enabled=False,
        review_mode="off", plan_approval="off",
    )
    db = Database(db_url)
    orch = Orchestrator(settings, db, FakeChatAdapter(),
                        model_client=FakeModelClient(), harness=FakeHarness())
    await orch.setup()
    return orch, orch.chat, db


# ── unambiguous name match resolves to the project (not sandbox) ─────────────
async def test_effort_name_resolves_to_matching_project(db_url):
    orch, chat, db = await _orch(db_url)
    try:
        await orch.projects.add("monogame-engine", "https://github.com/me/mono.git")
        # 'init-monogame-engine' contains the project slug → resolves to it, not the sandbox
        assert await orch._resolve_project_slug(None, None, effort_name="init-monogame-engine") \
            == "monogame-engine"
        # no project substring → falls back to the default (sandbox)
        assert await orch._resolve_project_slug(None, None, effort_name="add-hello-function") \
            == orch.s.default_project
    finally:
        await db.dispose()


async def test_name_match_only_when_unambiguous(db_url):
    orch, chat, db = await _orch(db_url)
    try:
        await orch.projects.add("mono", "https://github.com/me/a.git")
        await orch.projects.add("mono-engine", "https://github.com/me/b.git")
        # both 'mono' and 'mono-engine' appear in the name → ambiguous → sandbox (never guess)
        assert await orch._resolve_project_slug(None, None, effort_name="init-mono-engine") \
            == orch.s.default_project
    finally:
        await db.dispose()


# ── NL re-assignment fixes a mis-assigned effort ────────────────────────────
async def test_nl_reassign_moves_effort_to_project(db_url):
    orch, chat, db = await _orch(db_url)
    try:
        await orch.projects.add("mono", "https://github.com/me/mono.git")
        eid, _c, _r = await orch.router.open_effort("stray")           # lands in sandbox
        async with orch.db.session_factory() as s:
            assert (await s.get(Effort, eid)).project == orch.s.default_project
        orch.models._client.queue_structured(
            OperatorIntent(kind="reassign", effort_id=eid, project="mono", reply="Sure —"))
        mgmt = await orch.mgmt_channel_id()
        await orch.nl_intake(f"move {eid} to the mono project", mgmt, thread_id="t")
        async with orch.db.session_factory() as s:
            assert (await s.get(Effort, eid)).project == "mono"        # actually moved
        assert any("Moved" in p["message"] for p in chat.posted)
    finally:
        await db.dispose()
