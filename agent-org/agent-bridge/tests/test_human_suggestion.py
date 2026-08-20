"""PM suggests human action for host-only/impossible tasks (operator 2026-07-09: "if I'm building
the shaders, the PM should tell me to do so — when the issue is otherwise impossible for the
workers, make a suggestion with instruction for the human"). When a worker's blocker names a
capability the sandboxed workers structurally lack but the operator's own machine has (a
host-only tool, a GUI step, a licensed binary, credentials), the elevation proposes the HUMAN do
it — rather than a generic "your move". Generic across projects."""

from __future__ import annotations

from pathlib import Path

from app.adapters.chat import FakeChatAdapter
from app.config import Settings
from app.db import Database
from app.modules.model_router import FakeModelClient
from app.worker.harness import FakeHarness
from app.orchestrator import Orchestrator

ROOT = Path(__file__).resolve().parents[1]


async def _orch(db_url, tmp_path):
    key = tmp_path / "app.pem"
    key.write_text("dummy")
    settings = Settings(
        _env_file=None, chat_adapter="fake",
        profiles_dir=str(ROOT / "profiles"), charters_dir=str(ROOT / "charters"),
        floor_dir=str(ROOT / "floor"), worker_instance_urls="http://w1:8090",
        max_concurrent_workers=1, database_url=db_url, project_survey_enabled=False,
        review_mode="off", plan_approval="off",
        github_app_id="1", github_app_owner="devonpveller",
        github_app_private_key_path=str(key),
    )
    db = Database(db_url)
    orch = Orchestrator(settings, db, FakeChatAdapter(),
                        model_client=FakeModelClient(), harness=FakeHarness())
    await orch.setup()
    return orch, orch.chat, orch.harness, db


def test_detects_host_only_capabilities():
    o = Orchestrator
    inst = o.__new__(o)
    assert inst._is_human_capability_blocker(
        {"needs": "the MonoGame shader compiler (mgfxc) which needs Wine on Linux", "blocked": "", "feasible": ""})
    assert inst._is_human_capability_blocker(
        {"needs": "a Windows-only native tool", "blocked": "", "feasible": ""})
    assert inst._is_human_capability_blocker(
        {"needs": "an API key / credential to sign in", "blocked": "", "feasible": ""})
    assert inst._is_human_capability_blocker(
        {"needs": "", "blocked": "this step is interactive / GUI only", "feasible": ""})
    # interaction/display/content signals (the editor UI-testing case)
    assert inst._is_human_capability_blocker(
        {"needs": "", "blocked": "can't reproduce the atlas crash headlessly — needs a display and the game content",
         "feasible": ""})
    assert inst._is_human_capability_blocker(
        {"needs": "someone to click the menu in the running editor", "blocked": "", "feasible": ""})
    # a plain code blocker is NOT a human-capability one
    assert not inst._is_human_capability_blocker(
        {"needs": "the Point.cs conversion operator", "blocked": "ambiguous reference", "feasible": ""})


async def test_shader_blocker_suggests_the_human_builds_it(db_url, tmp_path):
    """THE case: the worker can't compile MonoGame shaders (needs Wine/mgfxc). The elevation must
    PROPOSE the operator do it on their host, not just say 'tell me how to proceed'."""
    orch, chat, harness, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("murder", "https://github.com/devonpveller/murder")
        eid, _c, _r = await orch.router.open_effort("shaders", project="murder")
        await orch.charters.set_goal(eid, "fix the shader crash", created_by="po")
        await orch._elevate_blocker(eid, {
            "blocked": "cannot compile the .fx shaders here",
            "needs": "the MonoGame mgfxc compiler, which needs Wine (unavailable in this worker)",
            "feasible": "no — needs a Windows-capable shader compiler",
            "raw": ""})
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "needs YOU" in msgs                              # proactively points at the human
        assert "on your host" in msgs and "mgfxc" in msgs       # concrete, relays the NEED
        assert "fake pass" in msgs                              # honest: won't force a false green
    finally:
        await db.dispose()


async def test_plain_code_blocker_does_not_suggest_human(db_url, tmp_path):
    orch, chat, harness, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("murder", "https://github.com/devonpveller/murder")
        eid, _c, _r = await orch.router.open_effort("code", project="murder")
        await orch.charters.set_goal(eid, "fix it", created_by="po")
        await orch._elevate_blocker(eid, {
            "blocked": "the API changed and I need guidance on the intended behavior",
            "needs": "clarification on which overload to use", "feasible": "unknown", "raw": ""})
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "needs YOU" not in msgs                          # not a host-capability blocker
        assert "raised a real CONSTRAINT" in msgs               # still elevated honestly
    finally:
        await db.dispose()
