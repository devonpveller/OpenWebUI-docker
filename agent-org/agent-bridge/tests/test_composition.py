"""Phase 2 — composition-aware planning + execution (autonomous-project-lifecycle §11d). A task like
"in <engine>, wire <submodule> against <sibling>" is a MULTI-REPO change: edit the submodule's repo,
THEN bump the engine's submodule pointer so the ENGINE reflects it. Deterministic augmentation (the
structure lives in CODE, not the small model) + a coordinated executor (edit → verify → bump via the
App Git Data API). Fakes + a mocked GitHub; no real network."""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from app.adapters.chat import FakeChatAdapter
from app.config import Settings
from app.db import Database
from app.modules.capabilities import RepoState
from app.modules.model_router import FakeModelClient
from app.orchestrator import Orchestrator
from app.schemas import LifecyclePlan, LifecycleStep
from app.worker.harness import FakeHarness

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
    return orch, orch.chat, db


async def test_augment_adds_bump_and_engine_layout(db_url, tmp_path):
    """The model produced only a worker_task on the submodule. The deterministic augmenter must add the
    `submodule_bump` (wiring-back) + inject the engine LAYOUT (relative path to the sibling submodule)."""
    orch, chat, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("monogame-engine", "https://github.com/devonpveller/MonoGame-Engine")
        await orch.projects.add("murder", "https://github.com/devonpveller/murder")
        await orch.projects.add("monogame", "https://github.com/devonpveller/MonoGame")
        states = {"monogame-engine": RepoState(
            readable=True, default_branch="main",
            submodule_paths=["vendor/murder", "vendor/MonoGame"],
            submodule_urls=["https://github.com/devonpveller/murder",
                            "https://github.com/devonpveller/MonoGame"])}
        steps = [LifecycleStep(kind="worker_task", target="murder", task="wire the build", summary="w")]
        steps2, note = await orch._augment_composition(
            "in monogame-engine, wire murder to build against the vendored monogame submodule",
            steps, states)
        bumps = [s for s in steps2 if s.kind == "submodule_bump"]
        assert len(bumps) == 1
        assert (bumps[0].target == "monogame-engine" and bumps[0].path == "vendor/murder"
                and bumps[0].source == "murder")
        wt = [s for s in steps2 if s.kind == "worker_task"][0]
        assert "COMPOSITION CONTEXT" in wt.task and "vendor/murder" in wt.task and "../MonoGame" in wt.task
        assert note
    finally:
        await db.dispose()


async def test_augment_repairs_model_authored_bump_with_blank_source(db_url, tmp_path):
    """LIVE regression: the model emitted its OWN submodule_bump but left `source` blank — the
    augmenter must REPAIR it (fill source/path) so the executor can pair it with the worker task,
    instead of skipping (has_bump) and leaving an unpairable step (the run that reported
    'dispatched worker' instead of 'composition')."""
    orch, chat, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("monogame-engine", "https://github.com/devonpveller/MonoGame-Engine")
        await orch.projects.add("murder", "https://github.com/devonpveller/murder")
        await orch.projects.add("monogame", "https://github.com/devonpveller/MonoGame")
        states = {"monogame-engine": RepoState(
            readable=True, default_branch="main",
            submodule_paths=["vendor/murder", "vendor/MonoGame"],
            submodule_urls=["https://github.com/devonpveller/murder",
                            "https://github.com/devonpveller/MonoGame"])}
        steps = [
            LifecycleStep(kind="worker_task", target="murder", task="wire the build", summary="w"),
            # the model's own bump — right target/path, BLANK source (as happened live)
            LifecycleStep(kind="submodule_bump", target="monogame-engine",
                          path="vendor/murder", source="", summary="b"),
        ]
        steps2, _ = await orch._augment_composition(
            "in monogame-engine, wire murder against the vendored monogame", steps, states)
        bumps = [s for s in steps2 if s.kind == "submodule_bump"]
        assert len(bumps) == 1                              # repaired, not duplicated
        assert bumps[0].source == "murder"                  # source FIXED → executor pairing works
        assert bumps[0].path == "vendor/murder"
    finally:
        await db.dispose()


async def test_executor_pairs_lone_bump_with_lone_worker_task(db_url, tmp_path):
    """Belt-and-braces: even if a bump reaches the executor with a blank source (no augmenter repair),
    one worker task + one wire-back is unambiguous — pair them (composition path, not plain dispatch)."""
    orch, chat, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("murder", "https://github.com/devonpveller/murder")
        await orch.projects.add("monogame-engine", "https://github.com/devonpveller/MonoGame-Engine")
        plan = LifecyclePlan(goal="wire", steps=[
            LifecycleStep(kind="worker_task", target="murder", task="wire the build", summary="w"),
            LifecycleStep(kind="submodule_bump", target="monogame-engine",
                          path="vendor/murder", source="", summary="b"),   # blank source
        ])
        orch._pending_lifecycle["plan-x"] = {
            "plan": plan, "channel_id": "c1", "thread_id": "t", "intent": "wire murder in monogame-engine"}
        # transport: murder verify 404s (branch never lands) → composition HALTS after the worker —
        # but the point is the COMPOSITION path was taken (halt message), not plain dispatch.
        def handler(request: httpx.Request) -> httpx.Response:
            p = request.url.path
            if p == "/repos/devonpveller/murder":
                return httpx.Response(200, json={"default_branch": "main"})
            return httpx.Response(404)
        orch._gh_transport = httpx.MockTransport(handler)
        await orch._execute_lifecycle_plan("plan-x")
        for _ in range(12):
            if not orch._bg_tasks:
                break
            import asyncio
            await asyncio.gather(*list(orch._bg_tasks), return_exceptions=True)
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "composition on `murder`" in msgs             # paired → composition path
        assert "no matching worker task" not in msgs         # not reported as dangling
    finally:
        await db.dispose()


async def test_augment_noop_when_engine_not_named(db_url, tmp_path):
    """No engine named in the intent → no composition inferred (don't over-augment a plain sub-repo edit)."""
    orch, chat, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("monogame-engine", "https://github.com/devonpveller/MonoGame-Engine")
        await orch.projects.add("murder", "https://github.com/devonpveller/murder")
        states = {"monogame-engine": RepoState(
            readable=True, submodule_paths=["vendor/murder"],
            submodule_urls=["https://github.com/devonpveller/murder"])}
        steps = [LifecycleStep(kind="worker_task", target="murder", task="fix a bug", summary="w")]
        steps2, note = await orch._augment_composition("fix a bug in murder", steps, states)
        assert not [s for s in steps2 if s.kind == "submodule_bump"] and not note
    finally:
        await db.dispose()


async def test_run_composition_edits_then_bumps_engine(db_url, tmp_path):
    """End-to-end: the worker edits `murder` (branch lands, verified), then the engine's `vendor/murder`
    submodule is bumped to that exact commit on the paired branch — so `monogame-engine` reflects it."""
    orch, chat, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("murder", "https://github.com/devonpveller/murder")
        await orch.projects.add("monogame-engine", "https://github.com/devonpveller/MonoGame-Engine")
        bumped: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            p = request.url.path
            # --- murder branch verification (the worker's code edit) ---
            if p == "/repos/devonpveller/murder":
                return httpx.Response(200, json={"default_branch": "main"})
            if "/repos/devonpveller/murder/branches/" in p:
                return httpx.Response(200, json={"commit": {"sha": "murder_sha_0123456789abcdef0000"}})
            if "/repos/devonpveller/murder/compare/" in p:
                return httpx.Response(200, json={"ahead_by": 1})
            # --- engine submodule bump (Git Data API) ---
            if p == "/repos/devonpveller/MonoGame-Engine":
                return httpx.Response(200, json={"default_branch": "main"})
            if p.endswith("/git/ref/heads/main"):
                return httpx.Response(200, json={"object": {"sha": "eng_base"}})
            if p.endswith("/git/commits/eng_base"):
                return httpx.Response(200, json={"tree": {"sha": "eng_tree"}})
            if p.endswith("/git/trees") and request.method == "POST":
                bumped["tree"] = json.loads(request.content)
                return httpx.Response(201, json={"sha": "eng_newtree"})
            if p.endswith("/git/commits") and request.method == "POST":
                return httpx.Response(201, json={"sha": "eng_newcommit"})
            if p.endswith("/git/refs") and request.method == "POST":
                bumped["ref"] = json.loads(request.content)
                return httpx.Response(201, json={})
            return httpx.Response(404)

        orch._gh_transport = httpx.MockTransport(handler)
        eid, chan, root = await orch.router.open_effort("wire", project="murder")
        worker_step = LifecycleStep(kind="worker_task", target="murder", task="wire the build", summary="w")
        bump_step = LifecycleStep(kind="submodule_bump", target="monogame-engine",
                                  path="vendor/murder", source="murder", summary="b")
        plan = LifecyclePlan(goal="wire", steps=[worker_step, bump_step])
        await orch._run_composition(eid, chan, root, "wire the build", worker_step, bump_step, plan, "t")
        # the engine's submodule gitlink was bumped to the worker's exact commit, on the paired branch
        assert bumped["tree"]["tree"][0]["path"] == "vendor/murder"
        assert bumped["tree"]["tree"][0]["sha"] == "murder_sha_0123456789abcdef0000"
        assert bumped["ref"]["ref"] == f"refs/heads/agent/{eid}"
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "Composition wired" in msgs and "monogame-engine" in msgs
    finally:
        await db.dispose()


async def test_run_composition_halts_if_edit_didnt_land(db_url, tmp_path):
    """If the submodule edit didn't land a verified commit, the engine is NOT bumped (no false wiring)."""
    orch, chat, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("murder", "https://github.com/devonpveller/murder")
        await orch.projects.add("monogame-engine", "https://github.com/devonpveller/MonoGame-Engine")
        bumped: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            p = request.url.path
            if p == "/repos/devonpveller/murder":
                return httpx.Response(200, json={"default_branch": "main"})
            if "/repos/devonpveller/murder/branches/" in p:
                return httpx.Response(404, json={"message": "Not Found"})   # branch never landed
            if "/git/trees" in p and request.method == "POST":
                bumped["hit"] = True
            return httpx.Response(404)

        orch._gh_transport = httpx.MockTransport(handler)
        eid, chan, root = await orch.router.open_effort("wire", project="murder")
        worker_step = LifecycleStep(kind="worker_task", target="murder", task="wire", summary="w")
        bump_step = LifecycleStep(kind="submodule_bump", target="monogame-engine",
                                  path="vendor/murder", source="murder", summary="b")
        plan = LifecyclePlan(goal="wire", steps=[worker_step, bump_step])
        await orch._run_composition(eid, chan, root, "wire", worker_step, bump_step, plan, "t")
        assert "hit" not in bumped                        # engine NOT bumped
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "composition halted" in msgs.lower() and "not** bumped" in msgs.lower()
    finally:
        await db.dispose()
