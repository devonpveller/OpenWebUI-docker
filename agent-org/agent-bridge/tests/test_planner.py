"""The PLANNER (P-APL.2) + executor (P-APL.3). The operator describes a multi-step architecture in
plain language; the planner drafts a CONCRETE, reviewable plan of primitives (fork / add_submodule) +
worker tasks; nothing runs until the operator approves the WHOLE plan; then the executor dispatches
each step to its governed primitive or a worker. This is the GENERAL mechanism (any project/
architecture) that replaced the hardcoded `compose` recipe. Fakes + a mocked GitHub."""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

from app.adapters.chat import FakeChatAdapter
from app.config import Settings
from app.db import Database
from app.modules.model_router import FakeModelClient
from app.orchestrator import Orchestrator
from app.schemas import LifecyclePlan, LifecycleStep, OperatorIntent
from app.worker.harness import FakeHarness

ROOT = Path(__file__).resolve().parents[1]


async def _orch(db_url, tmp_path):
    key = tmp_path / "app.pem"
    key.write_text("dummy")  # so github_app_enabled is True (FakeGitHubApp never reads it)
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


async def _drain(orch):
    for _ in range(12):
        if not orch._bg_tasks:
            return
        await asyncio.gather(*list(orch._bg_tasks), return_exceptions=True)


async def test_plan_drafts_presents_then_executes_on_approve(db_url, tmp_path):
    orch, chat, db = await _orch(db_url, tmp_path)
    try:
        # existing state the planner reasons over: the engine repo + one fork already registered
        await orch.projects.add("monogame-engine", "https://github.com/devonpveller/MonoGame-Engine")
        await orch.projects.add("murder", "https://github.com/devonpveller/murder",
                                upstream_url="https://github.com/isadorasophia/murder")
        orch._gh_transport = httpx.MockTransport(lambda r: httpx.Response(
            202, json={"full_name": "devonpveller/MonoGame",
                       "html_url": "https://github.com/devonpveller/MonoGame"}))
        # 1st structured call = intent classification (kind=plan); 2nd = the planner's drafted plan
        orch.models._client.queue_structured(OperatorIntent(kind="plan", reply="Let me draft that."))
        orch.models._client.queue_structured(LifecyclePlan(goal="engine vendoring my forks", steps=[
            LifecycleStep(kind="fork", source="MonoGame/MonoGame", summary="fork monogame"),
            LifecycleStep(kind="add_submodule", source="murder", target="monogame-engine",
                          path="murder", summary="add murder submodule"),
            LifecycleStep(kind="worker_task", target="monogame-engine",
                          task="wire murder to build against the monogame submodule source",
                          summary="wire the build"),
        ]))
        mgmt = await orch.mgmt_channel_id()
        await orch.nl_intake(
            "set up monogame-engine to vendor my forks as submodules and wire the build",
            mgmt, thread_id="t")

        # PRESENTED + held — nothing ran
        assert orch._pending_lifecycle, "a plan should be pending approval"
        pid = next(iter(orch._pending_lifecycle))
        assert any("Plan" in p["message"] and "fork" in p["message"].lower()
                   and "submodule" in p["message"].lower() for p in chat.posted)
        assert not orch.harness.submodules and not orch.harness.wakes   # NOT executed on draft

        # APPROVE → executor runs every step
        await orch.handle_event({"id": "d1", "channel_id": mgmt, "message": f"approve {pid}",
                                 "is_bot": False, "ts": 2})
        await _drain(orch)
        assert pid not in orch._pending_lifecycle
        # the submodule was added (via the operator-plane git executor)
        assert any(path == "murder" for (_b, _u, path) in orch.harness.submodules)
        # the fork ran (monogame fork now registered as a project)
        assert await orch.projects.resolve("monogame") is not None
        # the worker task was dispatched
        assert len(orch.harness.wakes) >= 1
        assert any("Plan run" in m["message"] for m in chat.posted)
    finally:
        await db.dispose()


async def test_planner_is_anchored_to_actual_repo_state(db_url, tmp_path):
    """UX-FLOW Stage 1 anchor: the planner must be given each repo's ACTUAL current state (submodules
    + tree) so it reconciles instead of blindly duplicating. Verify the state reaches the model call."""
    import base64
    orch, chat, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("monogame-engine", "https://github.com/devonpveller/MonoGame-Engine")

        def handler(request: httpx.Request) -> httpx.Response:
            p = request.url.path
            if p == "/repos/devonpveller/MonoGame-Engine":
                return httpx.Response(200, json={"default_branch": "main"})
            if p.endswith("/contents/.gitmodules"):
                gm = base64.b64encode(
                    b'[submodule "vendor/murder"]\n\tpath = vendor/murder\n\turl = https://github.com/devonpveller/murder\n').decode()
                return httpx.Response(200, json={"content": gm})
            if p.endswith("/contents"):
                return httpx.Response(200, json=[{"name": "vendor", "type": "dir"}])
            return httpx.Response(404)
        orch._gh_transport = httpx.MockTransport(handler)
        orch.models._client.queue_structured(OperatorIntent(kind="plan", reply="Drafting."))
        orch.models._client.queue_structured(LifecyclePlan(goal="x", steps=[
            LifecycleStep(kind="worker_task", target="monogame-engine", task="wire", summary="w")]))
        mgmt = await orch.mgmt_channel_id()
        await orch.nl_intake("finish setting up monogame-engine", mgmt, thread_id="t")
        # the planner's model call carried the ACTUAL state (submodule vendor/murder already present)
        planner_call = next(c for c in orch.models._client.calls
                            if c.get("kind") == "structured" and "CURRENT STATE" in str(c.get("user", "")))
        assert "vendor/murder" in planner_call["user"]           # anchored to reality
        assert "ANCHOR" in planner_call["user"] or "do NOT re-add" in planner_call["user"]
    finally:
        await db.dispose()


async def test_planner_deterministically_drops_already_present_submodules(db_url, tmp_path):
    """The model doesn't reliably subtract against the anchor (it still proposed adds that exist). The
    CODE must reconcile: drop add_submodule steps whose path already exists in the target — so the
    plan presented has NO duplicate. When ALL steps are already satisfied → 'nothing to do'."""
    import base64
    orch, chat, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("monogame-engine", "https://github.com/devonpveller/MonoGame-Engine")

        def handler(request: httpx.Request) -> httpx.Response:
            p = request.url.path
            if p == "/repos/devonpveller/MonoGame-Engine":
                return httpx.Response(200, json={"default_branch": "main"})
            if p.endswith("/contents/.gitmodules"):
                gm = base64.b64encode(
                    b'[submodule "vendor/MonoGame"]\n\tpath = vendor/MonoGame\n\turl = x\n'
                    b'[submodule "vendor/murder"]\n\tpath = vendor/murder\n\turl = y\n').decode()
                return httpx.Response(200, json={"content": gm})
            if p.endswith("/contents"):
                return httpx.Response(200, json=[{"name": "vendor", "type": "dir"}])
            return httpx.Response(404)
        orch._gh_transport = httpx.MockTransport(handler)
        orch.models._client.queue_structured(OperatorIntent(kind="plan", reply="Drafting."))
        # the model STILL proposes both adds (as qwen did live) — the code must drop them
        orch.models._client.queue_structured(LifecyclePlan(goal="vendor forks", steps=[
            LifecycleStep(kind="add_submodule", source="monogame", target="monogame-engine",
                          path="vendor/MonoGame", summary="add monogame"),
            LifecycleStep(kind="add_submodule", source="murder", target="monogame-engine",
                          path="vendor/murder", summary="add murder"),
        ]))
        mgmt = await orch.mgmt_channel_id()
        await orch.nl_intake("vendor my forks into monogame-engine", mgmt, thread_id="t")
        # both already present → reconciled to a no-op plan; NOTHING pending, clear "already holds" msg
        assert not orch._pending_lifecycle
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "already holds" in msgs or "nothing to do" in msgs
        assert "vendor/MonoGame" in msgs and "vendor/murder" in msgs   # names what's already in place
    finally:
        await db.dispose()


async def test_plan_abort_runs_nothing(db_url, tmp_path):
    orch, chat, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("monogame-engine", "https://github.com/devonpveller/MonoGame-Engine")
        orch.models._client.queue_structured(OperatorIntent(kind="plan", reply="Drafting."))
        orch.models._client.queue_structured(LifecyclePlan(goal="x", steps=[
            LifecycleStep(kind="worker_task", target="monogame-engine", task="do a thing", summary="t")]))
        mgmt = await orch.mgmt_channel_id()
        await orch.nl_intake("build something multi-step in monogame-engine", mgmt, thread_id="t")
        pid = next(iter(orch._pending_lifecycle))
        await orch.handle_event({"id": "d1", "channel_id": mgmt, "message": f"abort {pid}",
                                 "is_bot": False, "ts": 2})
        assert pid not in orch._pending_lifecycle
        assert not orch.harness.wakes                                   # nothing dispatched
        assert any("dropped" in m["message"].lower() for m in chat.posted)
    finally:
        await db.dispose()


async def test_approve_stale_plan_id_gives_clear_message(db_url, tmp_path):
    """Regression: `approve <plan-id>` for a plan that already ran / expired must say so plainly, NOT
    fall through to concern-resolution and emit the confusing 'no open concern for effort <plan-id>'."""
    orch, chat, db = await _orch(db_url, tmp_path)
    try:
        mgmt = await orch.mgmt_channel_id()
        await orch.handle_event({"id": "s1", "channel_id": mgmt,
                                 "message": "approve plan-wire-murder-to-build-aga",
                                 "is_bot": False, "ts": 1})
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "isn't awaiting approval" in msgs and "no open concern" not in msgs
    finally:
        await db.dispose()


async def test_planner_empty_plan_asks_for_more(db_url, tmp_path):
    orch, chat, db = await _orch(db_url, tmp_path)
    try:
        orch.models._client.queue_structured(OperatorIntent(kind="plan", reply="Hmm."))
        orch.models._client.queue_structured(LifecyclePlan(goal="", steps=[]))   # model gave nothing
        mgmt = await orch.mgmt_channel_id()
        await orch.nl_intake("do the architecture thing", mgmt, thread_id="t")
        assert not orch._pending_lifecycle
        assert any("concrete steps" in m["message"] for m in chat.posted)
    finally:
        await db.dispose()


# ── bare `approve` ergonomics (NL-first: no id needed when exactly one thing is pending) ──
async def test_bare_approve_resolves_the_single_pending_item(db_url, tmp_path):
    """A bare `approve` (no id) resolves THE one pending decision and echoes which — instead of the
    old rigid `usage: approve <effort_id>` error. Governance stays crisp: it names the target it ran."""
    orch, chat, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("monogame-engine", "https://github.com/devonpveller/MonoGame-Engine")
        orch.models._client.queue_structured(OperatorIntent(kind="plan", reply="Drafting."))
        orch.models._client.queue_structured(LifecyclePlan(goal="x", steps=[
            LifecycleStep(kind="worker_task", target="monogame-engine", task="wire", summary="w")]))
        mgmt = await orch.mgmt_channel_id()
        await orch.nl_intake("finish setting up monogame-engine", mgmt, thread_id="t")
        pid = next(iter(orch._pending_lifecycle))

        await orch._handle_command("approve", mgmt, "t")        # ← bare, no id
        await _drain(orch)

        assert pid not in orch._pending_lifecycle               # the one pending item was resolved
        msgs = " ".join(p["message"] for p in chat.posted)
        assert pid in msgs and "resolving the only item" in msgs  # echoed WHICH (crisp + auditable)
        assert not any("usage:" in p["message"] for p in chat.posted)  # no rigid usage error
    finally:
        await db.dispose()


async def test_bare_approve_disambiguates_when_several_pending(db_url, tmp_path):
    """Two decisions pending → a bare `approve` must NOT guess; it lists them and asks which. Never
    auto-fire a decision when the target is ambiguous (§3)."""
    orch, chat, db = await _orch(db_url, tmp_path)
    try:
        orch._pending_lifecycle["plan-alpha"] = {"proj_channel": "c", "root": "r", "plan": None}
        orch._pending_lifecycle["plan-beta"] = {"proj_channel": "c", "root": "r", "plan": None}
        mgmt = await orch.mgmt_channel_id()
        await orch._handle_command("approve", mgmt, "t")
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "plan-alpha" in msgs and "plan-beta" in msgs and "which" in msgs.lower()
        # nothing ran — both still pending
        assert "plan-alpha" in orch._pending_lifecycle and "plan-beta" in orch._pending_lifecycle
    finally:
        await db.dispose()


async def test_bare_approve_with_nothing_pending_is_friendly(db_url, tmp_path):
    orch, chat, db = await _orch(db_url, tmp_path)
    try:
        mgmt = await orch.mgmt_channel_id()
        await orch._handle_command("approve", mgmt, "t")
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "nothing" in msgs.lower()
        assert not any("usage:" in p["message"] for p in chat.posted)
    finally:
        await db.dispose()


async def test_bare_modify_still_needs_explicit_target(db_url, tmp_path):
    """`modify` conveys a change, so it keeps requiring an id + note even when one item is pending."""
    orch, chat, db = await _orch(db_url, tmp_path)
    try:
        orch._pending_lifecycle["plan-alpha"] = {"proj_channel": "c", "root": "r", "plan": None}
        mgmt = await orch.mgmt_channel_id()
        await orch._handle_command("modify", mgmt, "t")
        assert any("usage:" in p["message"] and "modify" in p["message"] for p in chat.posted)
        assert "plan-alpha" in orch._pending_lifecycle           # untouched
    finally:
        await db.dispose()


async def test_pending_approvals_survive_a_restart(db_url, tmp_path):
    """The bug behind 'I rebuilt but there are no ids in the chat': a proposed plan/fork lived ONLY in
    memory, so a bridge rebuild dropped the hard gate the operator hadn't decided. Proposals must now
    persist + rehydrate on boot (mirrors test_frozen_persists_across_restart for the gate)."""
    # ── run 1: propose a lifecycle plan AND a capability, then 'bounce' the bridge ──
    orch, chat, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("monogame-engine", "https://github.com/devonpveller/MonoGame-Engine")
        orch.models._client.queue_structured(OperatorIntent(kind="plan", reply="Drafting."))
        orch.models._client.queue_structured(LifecyclePlan(goal="vendor forks", steps=[
            LifecycleStep(kind="worker_task", target="monogame-engine", task="wire", summary="w")]))
        mgmt = await orch.mgmt_channel_id()
        await orch.nl_intake("finish setting up monogame-engine", mgmt, thread_id="t")
        pid = next(iter(orch._pending_lifecycle))

        orch.models._client.queue_structured(OperatorIntent(
            kind="capability", capability="fork", repo_url="isadorasophia/murder", reply="Sure —"))
        await orch.nl_intake("fork isadorasophia/murder into my account", mgmt, thread_id="t")
        assert "cap-fork-murder" in orch._pending_capability
    finally:
        await db.dispose()          # ← simulate the container rebuild (fresh process, same DB file)

    # ── run 2: a fresh orchestrator on the SAME db file rehydrates BOTH proposals ──
    orch2, chat2, db2 = await _orch(db_url, tmp_path)
    try:
        assert pid in orch2._pending_lifecycle                    # the plan survived the bounce
        rehydrated = orch2._pending_lifecycle[pid]["plan"]
        assert isinstance(rehydrated, LifecyclePlan)              # reconstructed as the real schema
        assert rehydrated.steps[0].kind == "worker_task"          # nested steps intact
        assert "cap-fork-murder" in orch2._pending_capability     # the fork proposal survived too

        # and both are live decisions again — a bare `approve` sees two and disambiguates (proves it)
        mgmt2 = await orch2.mgmt_channel_id()
        await orch2._handle_command("approve", mgmt2, "t")
        msgs = " ".join(p["message"] for p in chat2.posted)
        assert pid in msgs and "cap-fork-murder" in msgs and "which" in msgs.lower()
    finally:
        await db2.dispose()


async def test_status_surfaces_the_pending_approval_queue(db_url, tmp_path):
    """After a restart there may be NO running efforts but a proposal still pending — `/status` must
    show the awaiting-approval queue with each id (+ a summary) so the operator acts without re-asking."""
    orch, chat, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("monogame-engine", "https://github.com/devonpveller/MonoGame-Engine")
        orch.models._client.queue_structured(OperatorIntent(kind="plan", reply="Drafting."))
        orch.models._client.queue_structured(LifecyclePlan(goal="vendor forks", steps=[
            LifecycleStep(kind="worker_task", target="monogame-engine", task="wire", summary="w")]))
        mgmt = await orch.mgmt_channel_id()
        await orch.nl_intake("finish setting up monogame-engine", mgmt, thread_id="t")
        pid = next(iter(orch._pending_lifecycle))
        # also a fork proposal, so the queue shows both kinds
        orch._pending_capability["cap-fork-murder"] = {"kind": "fork", "parent": "isadorasophia/murder"}
        chat.posted.clear()

        await orch._handle_command("/status", mgmt, "t")
        msg = " ".join(p["message"] for p in chat.posted)
        assert "Awaiting your approval" in msg
        assert pid in msg and "vendor forks" in msg              # the plan, with its goal
        assert "cap-fork-murder" in msg and "isadorasophia/murder" in msg  # the fork
    finally:
        await db.dispose()


async def test_resolved_approval_is_removed_from_the_store(db_url, tmp_path):
    """A decided proposal must NOT resurrect on the next restart — approve/abort deletes the mirror."""
    orch, chat, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("monogame-engine", "https://github.com/devonpveller/MonoGame-Engine")
        orch.models._client.queue_structured(OperatorIntent(kind="plan", reply="Drafting."))
        orch.models._client.queue_structured(LifecyclePlan(goal="x", steps=[
            LifecycleStep(kind="worker_task", target="monogame-engine", task="wire", summary="w")]))
        mgmt = await orch.mgmt_channel_id()
        await orch.nl_intake("finish setting up monogame-engine", mgmt, thread_id="t")
        pid = next(iter(orch._pending_lifecycle))
        assert any(r["id"] == pid for r in await orch.pending.all())   # persisted

        await orch._handle_command(f"abort {pid}", mgmt, "t")
        await _drain(orch)
        assert not any(r["id"] == pid for r in await orch.pending.all())  # gone after the decision
    finally:
        await db.dispose()
