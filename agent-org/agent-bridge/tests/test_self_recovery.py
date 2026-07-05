"""Self-recovery: states the org must fix ITSELF instead of handing the operator homework
(operator feedback 2026-07-05: "is this not something the worker can perform? and a task the
orchestration system should be able to recover from?"). Three generic mechanisms, none of them
repo- or task-specific:

1. NL upstream REMOVAL — the registry is bridge-owned state, so correcting a wrong upstream is a
   natural-language operation (D0.f), never operator SQL.
2. Upstream registry SELF-HEAL — when an upstream bake fails, the bridge verifies the repo's
   ACTUAL fork parent via the forge API: not-a-fork ⇒ clear the bogus upstream; different parent
   ⇒ correct it; matching parent ⇒ keep the honest private-or-unreachable warning. An
   unverifiable state never mutates the registry (fail-open).
3. Goal STATE-CHECK on undelivered — "no branch landed" is either unpushed work (real failure,
   escalate) or a goal that ALREADY HOLDS (stale effort — close as a verified no-op with
   evidence). A read-only worker check against the effort's own goal tells them apart.
Run RED against pre-fix code as proof, GREEN after."""

from __future__ import annotations

from pathlib import Path

import httpx

from app.adapters.chat import FakeChatAdapter
from app.config import Settings
from app.db import Database
from app.modules.model_router import FakeModelClient
from app.orchestrator import Orchestrator
from app.schemas import OperatorIntent
from app.worker.harness import FakeHarness

ROOT = Path(__file__).resolve().parents[1]


async def _orch(db_url, tmp_path=None, *, github=False):
    kw = {}
    if github:
        key = tmp_path / "app.pem"
        key.write_text("dummy")
        kw = {"github_app_id": "1", "github_app_owner": "devonpveller",
              "github_app_private_key_path": str(key)}
    settings = Settings(
        _env_file=None, chat_adapter="fake",
        profiles_dir=str(ROOT / "profiles"), charters_dir=str(ROOT / "charters"),
        floor_dir=str(ROOT / "floor"), worker_instance_urls="http://w1:8090",
        max_concurrent_workers=1, database_url=db_url, project_survey_enabled=False,
        review_mode="off", plan_approval="off", **kw,
    )
    db = Database(db_url)
    orch = Orchestrator(settings, db, FakeChatAdapter(),
                        model_client=FakeModelClient(), harness=FakeHarness())
    await orch.setup()
    return orch, orch.chat, orch.harness, db


def _remote(*, parent: str | None = None):
    """MockTransport for `devonpveller/Engine`: repo meta (with an optional fork `parent`),
    branch + compare so the delivery path verifies landed."""

    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        if "/compare/" in p:
            return httpx.Response(200, json={"ahead_by": 1, "behind_by": 0})
        if "/branches/" in p:
            return httpx.Response(200, json={"commit": {"sha": "feedbead12345678"}})
        if p.count("/") == 3:   # /repos/{owner}/{repo}
            meta = {"default_branch": "main"}
            if parent:
                meta["parent"] = {"full_name": parent}
            return httpx.Response(200, json=meta)
        return httpx.Response(404)

    return httpx.MockTransport(handler)


# ── 1. NL upstream removal ─────────────────────────────────────────────────────
async def test_nl_remove_upstream_clears_registry(db_url):
    orch, chat, harness, db = await _orch(db_url)
    try:
        await orch.projects.add("engine", "https://github.com/devonpveller/Engine",
                                upstream_url="https://github.com/isadorasophia/murder.git")
        orch.models._client.queue_structured(OperatorIntent(
            kind="chitchat", reply="Clearing it.", project="engine", remove_upstream=True))
        mgmt = await orch.mgmt_channel_id()
        await orch.nl_intake("engine isn't a fork — remove its upstream", mgmt, thread_id="t")
        assert await orch.projects.upstream_for("engine") is None
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "Cleared the upstream" in msgs and "`engine`" in msgs
    finally:
        await db.dispose()


# ── 2. upstream registry self-heal on a failed bake ────────────────────────────
async def test_upstream_heal_clears_when_repo_is_not_a_fork(db_url, tmp_path):
    """LIVE 2026-07-05: `monogame-engine` carried `isadorasophia/murder.git` as upstream (an NL
    mishap) → every dispatch warned "private or unreachable" and pointed at tokens. The forge
    PROVES the repo is not a fork ⇒ the bridge clears the bogus upstream itself."""
    orch, chat, harness, db = await _orch(db_url, tmp_path, github=True)
    try:
        await orch.projects.add("engine", "https://github.com/devonpveller/Engine",
                                upstream_url="https://github.com/isadorasophia/murder.git")
        orch._gh_transport = _remote(parent=None)               # NOT a fork
        harness.upstream_fails = True                            # the bake fails on focus
        eid, chan, root = await orch.router.open_effort("fix", project="engine")
        await orch.delegate(eid, chan, root, "do the work", plan_steps=["work"])
        assert await orch.projects.upstream_for("engine") is None, "bogus upstream not cleared"
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "not a fork" in msgs and "cleared" in msgs.lower()
        assert "private or unreachable" not in msgs, "still the misleading token warning"
    finally:
        await db.dispose()


async def test_upstream_heal_corrects_to_actual_parent(db_url, tmp_path):
    orch, chat, harness, db = await _orch(db_url, tmp_path, github=True)
    try:
        await orch.projects.add("engine", "https://github.com/devonpveller/Engine",
                                upstream_url="https://github.com/wrong/parent")
        orch._gh_transport = _remote(parent="realowner/RealParent")   # a fork of someone ELSE
        harness.upstream_fails = True
        eid, chan, root = await orch.router.open_effort("fix", project="engine")
        await orch.delegate(eid, chan, root, "do the work", plan_steps=["work"])
        assert await orch.projects.upstream_for("engine") == "https://github.com/realowner/RealParent"
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "corrected the registry" in msgs
    finally:
        await db.dispose()


async def test_upstream_warning_kept_when_config_is_right(db_url, tmp_path):
    """Parent matches the registry ⇒ the config is CORRECT and the parent is genuinely
    private/unreachable — the honest warning must survive, the registry must not change."""
    orch, chat, harness, db = await _orch(db_url, tmp_path, github=True)
    try:
        await orch.projects.add("engine", "https://github.com/devonpveller/Engine",
                                upstream_url="https://github.com/realowner/RealParent")
        orch._gh_transport = _remote(parent="realowner/RealParent")
        harness.upstream_fails = True
        eid, chan, root = await orch.router.open_effort("fix", project="engine")
        await orch.delegate(eid, chan, root, "do the work", plan_steps=["work"])
        assert await orch.projects.upstream_for("engine") == "https://github.com/realowner/RealParent"
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "private or unreachable" in msgs
    finally:
        await db.dispose()


# ── 3. goal state-check on verified non-delivery ───────────────────────────────
def _never_lands():
    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        if "/branches/" in p:
            return httpx.Response(404, json={"message": "Not Found"})
        if p.count("/") == 3:
            return httpx.Response(200, json={"default_branch": "main"})
        return httpx.Response(404)
    return httpx.MockTransport(handler)


async def test_state_holds_closes_undelivered_effort_as_noop_done(db_url, tmp_path):
    """LIVE 2026-07-05: three stale efforts ran, pushed nothing, and each became operator
    homework ("did not land — re-run it or confirm"). When a read-only check PROVES the goal
    already holds, the org closes the effort itself, with evidence."""
    orch, chat, harness, db = await _orch(db_url, tmp_path, github=True)
    try:
        await orch.projects.add("engine", "https://github.com/devonpveller/Engine")
        eid, chan, root = await orch.router.open_effort("noop", project="engine")
        await orch.charters.set_goal(eid, "ensure build props reference the vendored engine",
                                     created_by="po")
        orch._gh_transport = _never_lands()
        harness.output_queue = [
            "did the work",                                     # step
            "published (self-report)",                          # publish
            "published again (firm)",                           # firm re-engage
            "STATE HOLDS: Directory.Build.props already references ../MonoGame (verified)",
        ]
        await orch.delegate(eid, chan, root, "ensure props", plan_steps=["work"])
        from app.models import Effort
        async with orch.db.session_factory() as s:
            e = await s.get(Effort, eid)
        assert e.lifecycle == "done", "verified no-op should close done"
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "already holds" in msgs and "Directory.Build.props" in msgs   # evidence surfaced
        assert "not verify the change landed" not in msgs, "still escalated operator homework"
    finally:
        await db.dispose()


async def test_state_missing_still_escalates(db_url, tmp_path):
    """The state check is a recovery, not a rubber stamp: MISSING (or garbage) still climbs the
    ladder — never a false done."""
    orch, chat, harness, db = await _orch(db_url, tmp_path, github=True)
    try:
        await orch.projects.add("engine", "https://github.com/devonpveller/Engine")
        eid, chan, root = await orch.router.open_effort("broken", project="engine")
        await orch.charters.set_goal(eid, "wire the vendored engine", created_by="po")
        orch._gh_transport = _never_lands()
        harness.output_queue = ["did work", "published", "published firm",
                                "STATE MISSING: no props file exists"]
        await orch.delegate(eid, chan, root, "wire it", plan_steps=["work"])
        from app.models import Effort
        async with orch.db.session_factory() as s:
            e = await s.get(Effort, eid)
        assert e.lifecycle != "done"
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "not verify the change landed" in msgs or "did not land" in msgs
    finally:
        await db.dispose()
