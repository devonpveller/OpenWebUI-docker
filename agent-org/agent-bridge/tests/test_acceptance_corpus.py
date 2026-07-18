"""Durable acceptance corpus (ORCHESTRATION-DESIGN §10 — the finding→durable-check pipeline). An
operator review finding becomes a PERMANENT executable check on the PROJECT; every future delivery
must satisfy it, so the org cannot repeat a defect a human already found (measured: PR#11's findings
recurred in PR#14 because they were never made durable). The corpus outlives efforts, is enforced as
a hard red-gate on the merge (route back once → still red → withdraw + burn-down), and is
content-addressed so re-capturing the same finding is idempotent. Fakes + mocked GitHub."""

from __future__ import annotations

from pathlib import Path

import httpx

from app.adapters.chat import FakeChatAdapter
from app.config import Settings
from app.db import Database
from app.modules.model_router import FakeModelClient
from app.orchestrator import Orchestrator
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
    return orch, orch.chat, orch.harness, db


def _remote(state: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        if "/branches/" in p:
            handler.reads = getattr(handler, "reads", 0) + 1
            sha = "prehead000000" if handler.reads == 1 else "cafe1234beef"
            return httpx.Response(200, json={"commit": {"sha": sha}})
        if "/compare/" in p:
            return httpx.Response(200, json={"ahead_by": 1, "commits": [],
                "files": [{"filename": "todo.py", "additions": 1, "deletions": 0}]})
        if p.endswith("/pulls") and request.method == "POST":
            return httpx.Response(201, json={"number": 7,
                "html_url": "https://github.com/devonpveller/Docker-Game/pull/7"})
        if "/merge" in p and request.method == "PUT":
            state["merged"] = True
            return httpx.Response(200, json={"merged": True})
        if p.count("/") == 3:
            return httpx.Response(200, json={"default_branch": "main"})
        return httpx.Response(404)
    return handler


async def _game(orch):
    await orch.projects.add("game", "https://github.com/devonpveller/Docker-Game")
    return await orch.router.open_effort("wire", project="game")


async def test_corpus_is_durable_idempotent_and_retirable(db_url, tmp_path):
    """Storage: content-addressed (re-capturing the same finding is a no-op, not a duplicate); lives
    on the durable project so it OUTLIVES efforts; retirable without deleting the audit trail; unknown
    project rejected."""
    orch, _chat, _h, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("game", "https://github.com/devonpveller/Docker-Game")
        id1 = await orch.projects.add_acceptance_check(
            "game", "operator PR#11: no reopen command", "python -m pytest tests/test_reopen.py")
        id2 = await orch.projects.add_acceptance_check(
            "game", "operator PR#14: still no reopen", "python -m pytest tests/test_reopen.py")
        assert id1 == id2                                          # same body → same id (idempotent)
        assert len(await orch.projects.list_acceptance_checks("game")) == 1   # no duplicate
        assert await orch.projects.add_acceptance_check("ghost", "x", "y") is None   # unknown project
        # outlives a round
        await orch.router.open_effort("round-1", project="game")
        assert len(await orch.projects.list_acceptance_checks("game")) == 1
        # retire without erasing
        assert await orch.projects.set_acceptance_check_active(id1, False)
        assert len(await orch.projects.list_acceptance_checks("game")) == 0
        assert len(await orch.projects.list_acceptance_checks("game", active_only=False)) == 1
    finally:
        await db.dispose()


async def test_corpus_pass_keeps_the_merge_gate(db_url, tmp_path):
    """A delivery that SATISFIES the corpus proceeds — merge gate presented, honest pass note."""
    orch, chat, harness, db = await _orch(db_url, tmp_path)
    try:
        eid, chan, root = await _game(orch)
        await orch.projects.add_acceptance_check("game", "PR#11: reopen", "pytest tests/test_reopen.py")
        orch._gh_transport = httpx.MockTransport(_remote({}))
        harness.output_queue = ["did the work", "pushed"]
        harness.check_queue = [(0, "1 passed", False)]            # corpus check GREEN
        await orch.delegate(eid, chan, root, "wire", plan_steps=["work"])
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "Acceptance corpus passed" in msgs
        assert f"merge-{eid}" in orch._pending_merge
    finally:
        await db.dispose()


async def test_operator_captures_a_check_via_nl(db_url, tmp_path):
    """The operator-facing capture (governor-issued, deterministic — not LLM-classified). A message
    `accept check for <project>: <command> :: <note>` records a durable check and dispatches NO work."""
    orch, chat, harness, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("game", "https://github.com/devonpveller/Docker-Game")
        await orch.nl_intake(
            "accept check for game: python -m pytest tests/test_reopen.py :: PR#11 review: reopen must exist",
            channel_id="c1", user_id="operator-api")
        checks = await orch.projects.list_acceptance_checks("game")
        assert len(checks) == 1
        assert checks[0]["body"] == "python -m pytest tests/test_reopen.py"
        assert "reopen must exist" in checks[0]["origin_note"]
        assert harness.wakes == []                                # captured config, dispatched no work
        assert any("Acceptance check captured" in p["message"] for p in chat.posted)
    finally:
        await db.dispose()


async def test_corpus_is_visible_upstream_at_plan_and_build_time(db_url, tmp_path):
    """ALTERATION 1 (2026-07-17): the corpus must reach the worker's goal/plan context, not only the
    delivery gate. Live evidence it matters — gym-007's plan omitted `reopen`, the gate caught it, and
    a SECOND worker turn was burned adding it. Seeing the durable checks up front turns
    build-wrong-then-fix into build-right-first-time."""
    orch, _chat, harness, db = await _orch(db_url, tmp_path)
    try:
        eid, chan, root = await _game(orch)
        await orch.projects.add_acceptance_check(
            "game", "operator PR#11+#14: reopen was missing", "python3 todo.py reopen --help")
        orch._gh_transport = httpx.MockTransport(_remote({}))
        harness.output_queue = ["did the work", "pushed"]
        harness.check_queue = [(0, "ok", False)]
        await orch.delegate(eid, chan, root, "wire", plan_steps=["work"])
        prompts = " ".join(w["prompt"] for w in harness.wakes)
        assert "ACCEPTANCE CORPUS" in prompts                      # the worker SEES it while working
        assert "todo.py reopen --help" in prompts                  # …including the exact check
        assert "reopen was missing" in prompts                     # …and its human origin
    finally:
        await db.dispose()


async def test_corpus_red_withdraws_the_merge_gate(db_url, tmp_path):
    """THE POINT: a delivery that BREAKS a durable check the org already committed to is hard-gated —
    route back once, still red → the merge gate is WITHDRAWN and burn-down engaged. Without the corpus
    gate this same delivery would present the merge (the recurrence the pipeline exists to stop)."""
    orch, chat, harness, db = await _orch(db_url, tmp_path)
    try:
        eid, chan, root = await _game(orch)
        await orch.projects.add_acceptance_check(
            "game", "operator PR#11 review: a `reopen` command must exist",
            "python -m pytest tests/test_reopen.py")
        orch._gh_transport = httpx.MockTransport(_remote({}))
        harness.output_queue = ["did the work", "pushed", "tried to add reopen"]
        harness.check_queue = [(1, "AssertionError: no reopen command", False),  # fails …
                               (1, "AssertionError: still missing", False)]        # … still fails after fix
        await orch.delegate(eid, chan, root, "wire", plan_steps=["work"])
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "Acceptance corpus" in msgs and "withdrawn" in msgs.lower()   # a broken promise never ships
        assert f"merge-{eid}" not in orch._pending_merge                     # merge gate withdrawn
        # routed back to the effort naming the exact broken standard
        assert any("DURABLE ACCEPTANCE CHECKS FAILED" in w["prompt"] for w in harness.wakes)
        ev = [e for e in await orch.audit.replay(eid) if e["kind"] == "acceptance_corpus_failed"]
        assert ev
    finally:
        await db.dispose()


# NOTE — §11 producer deliberately NOT wired here. Raising a verifiable concern on the corpus-red
# path FREEZES the effort, which (a) contradicts the burn-down queued alongside it (a frozen effort
# cannot dispatch, governance §3.0) and (b) deadlocks the clear-gate: the check can only go green if
# work runs, and work cannot run while frozen. §11's enforcement half is live; its producer needs a
# home where freezing is already correct (a genuine give-up point), which is a separate increment.
