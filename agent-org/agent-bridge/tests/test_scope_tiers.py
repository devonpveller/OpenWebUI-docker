"""Tiered scope tree (ORCHESTRATION-DESIGN §4 — the operator's composition layer). The long-horizon
plan decomposes top-down into bounded scopes; a worker is handed ONE node and is deliberately unaware
of the rest, because that is what keeps a small model inside a scope it can actually hold. The horizon
lives in the TREE, not in any model's context. Escalation routes UP to the tier that owns the adjacent
scope — the only place with the standing to decide a cross-scope issue. Fakes only."""

from __future__ import annotations

from pathlib import Path

from app.adapters.chat import FakeChatAdapter
from app.config import Settings
from app.db import Database
from app.modules.model_router import FakeModelClient
from app.orchestrator import Orchestrator
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
    return orch, db


async def test_scope_tree_nests_and_tracks_depth(db_url):
    """Top-down decomposition: chapter → section → component, each bounding the tier below."""
    orch, db = await _orch(db_url)
    try:
        await orch.projects.add("game", "https://github.com/acme/game.git")
        chapter = await orch.add_scope_node("game", "Persistence", "All data storage and migration.")
        section = await orch.add_scope_node("game", "Save format", "The on-disk save schema.",
                                            parent_id=chapter)
        comp = await orch.add_scope_node("game", "Serializer", "Encode/decode a save record.",
                                         parent_id=section,
                                         contract="python -m pytest tests/test_serializer.py")
        assert chapter and section and comp
        assert (await orch._scope_node(chapter))["depth"] == 0
        assert (await orch._scope_node(section))["depth"] == 1
        assert (await orch._scope_node(comp))["depth"] == 2
        assert await orch.add_scope_node("ghost", "x", "y") is None          # unknown project
        assert await orch.add_scope_node("game", "x", "y", parent_id="sn-nope") is None
    finally:
        await db.dispose()


async def test_a_workers_brief_is_BOUNDED_to_its_scope(db_url):
    """The mechanism: the worker gets its OWN scope + contract, and is told the border exists — but
    NOT what lies beyond it. Withholding the global picture is deliberate (a small model fails at
    whole-project horizon and succeeds inside a bounded scope)."""
    orch, db = await _orch(db_url)
    try:
        await orch.projects.add("game", "https://github.com/acme/game.git")
        chapter = await orch.add_scope_node("game", "Persistence", "SECRET-SIBLING-DETAIL storage.")
        comp = await orch.add_scope_node("game", "Serializer", "Encode/decode a save record.",
                                         parent_id=chapter,
                                         contract="python -m pytest tests/test_serializer.py")
        brief = await orch._scope_context(comp)
        assert "Serializer" in brief and "Encode/decode a save record" in brief
        assert "test_serializer.py" in brief                      # its contract = what DONE means
        assert "SECRET-SIBLING-DETAIL" not in brief               # the rest of the tree is withheld
        assert "ESCALATE:" in brief and "do NOT work around" in brief   # the border is named
    finally:
        await db.dispose()


async def test_escalation_routes_to_the_owner_of_the_adjacent_scope(db_url):
    """A cross-scope issue goes UP to the tier that owns the adjacent scope — a worker inside a
    bounded scope structurally cannot decide it. At the root there is no parent: that is where a
    human governs."""
    orch, db = await _orch(db_url)
    try:
        await orch.projects.add("game", "https://github.com/acme/game.git")
        chapter = await orch.add_scope_node("game", "Persistence", "All storage.")
        comp = await orch.add_scope_node("game", "Serializer", "Encode/decode.", parent_id=chapter)
        target = await orch._escalation_target(comp)
        assert target and target["id"] == chapter and target["title"] == "Persistence"
        assert await orch._escalation_target(chapter) is None      # root → the human governs
    finally:
        await db.dispose()
