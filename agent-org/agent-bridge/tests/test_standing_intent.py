"""Standing intent + convergence (live 2026-07-07: the org DRIFTED — it reverted to the
`Murder.FNA` NuGet package to manufacture a green build, the exact thing the operator forbade —
and it sprayed 9+ branches/PRs, one per repeated prompt). Two GENERIC mechanisms (any project,
any architecture, no repo-specific logic):

1. STANDING INTENT — a durable per-project invariant, set in plain language, injected into every
   effort goal AND enforced at delivery: a diff that reintroduces a `backticked` forbidden term is
   rejected (auto-iterate, then escalate — never merged). A green build is necessary but NOT
   sufficient; the intent gate catches building-but-wrong deliveries.
2. CONVERGENCE — a re-report of the same problem reuses the existing open effort's branch + PR
   instead of minting a new slug/branch/PR each time."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import httpx

from app.adapters.chat import FakeChatAdapter
from app.config import Settings
from app.db import Database
from app.modules.model_router import FakeModelClient
from app.orchestrator import Orchestrator
from app.schemas import OperatorIntent, ReadinessVerdict
from app.worker.harness import FakeHarness

ROOT = Path(__file__).resolve().parents[1]

INTENT = "murder builds from the vendored MonoGame source; never use the `Murder.FNA` NuGet package"


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


def _remote(added_lines: list[str], *, moving=True):
    """Branch lands (moving head so the stale-head gate passes), files non-empty, and the diff's
    added lines are `added_lines` (what the standing-intent gate scans)."""
    state = {"reads": 0}
    patch = "".join(f"+{ln}\n" for ln in added_lines)

    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        if "/compare/" in p:
            return httpx.Response(200, json={
                "ahead_by": 1, "behind_by": 0, "commits": [],
                "files": [{"filename": "src/Murder/Murder.csproj", "additions": len(added_lines),
                           "deletions": 0, "patch": patch}]})
        if "/contents/src/Murder/Murder.csproj" in p:
            return httpx.Response(200, json={"type": "file", "sha": "aa"})
        if "/branches/" in p:
            state["reads"] += 1
            sha = "prehead000000" if (moving and state["reads"] == 1) else "newhead1234567890"
            return httpx.Response(200, json={"commit": {"sha": sha}})
        if p.endswith("/pulls") and request.method == "POST":
            return httpx.Response(201, json={"number": 9, "html_url": "https://x/pull/9"})
        if p.endswith("/pulls") and request.method == "GET":
            return httpx.Response(200, json=[])
        if p.count("/") == 3:
            return httpx.Response(200, json={"default_branch": "main"})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


async def _drain(orch):
    import asyncio
    for _ in range(20):
        if not orch._bg_tasks:
            return
        await asyncio.gather(*list(orch._bg_tasks), return_exceptions=True)


def test_forbidden_terms_extraction():
    from app.orchestrator import Orchestrator as O
    assert O._forbidden_terms(INTENT) == ["Murder.FNA"]
    # required (non-negated) backticks are NOT forbidden
    assert O._forbidden_terms("build from the `vendored MonoGame` source") == []
    multi = "never a `PackageReference`, and don't use the `Murder.FNA` package"
    assert set(O._forbidden_terms(multi)) == {"PackageReference", "Murder.FNA"}


async def test_nl_set_standing_intent_and_inject_into_goal(db_url, tmp_path):
    orch, chat, harness, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("murder", "https://github.com/devonpveller/murder")
        orch.models._client.queue_structured(OperatorIntent(
            kind="chitchat", reply="Rule set.", project="murder", standing_intent=INTENT))
        mgmt = await orch.mgmt_channel_id()
        await orch.nl_intake("murder must always build from the vendored MonoGame source, never "
                             "the `Murder.FNA` NuGet package", mgmt, thread_id="t")
        p = await orch.projects.get("murder")
        assert INTENT in (p["standing_intent"] or "")
        msgs = " ".join(m["message"] for m in chat.posted)
        assert "Standing intent set" in msgs and "`Murder.FNA`" in msgs
        # it now rides on an effort goal
        orch._gh_transport = _remote([])
        eid, chan, root = await orch.router.open_effort("some-fix", project="murder")
        await orch._intake_or_dispatch(eid, chan, root, "fix a thing",
                                       reply_prefix="", mgmt_channel=chan)
        _, goal, _ = await orch.charters.current_goal(eid)
        assert "STANDING INTENT" in goal and "Murder.FNA" in goal
    finally:
        await db.dispose()


async def test_delivery_reintroducing_forbidden_term_is_rejected(db_url, tmp_path):
    """THE anti-drift test: a delivery whose diff adds the forbidden NuGet reference must NOT
    merge — it auto-iterates with the violation, never opens a PR for the drift."""
    orch, chat, harness, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("murder", "https://github.com/devonpveller/murder")
        await orch.projects.set_standing_intent("murder", INTENT)
        # the diff RE-INTRODUCES the forbidden NuGet package (the exact live drift)
        orch._gh_transport = _remote(
            ['    <PackageReference Include="Murder.FNA" Version="26.4.1" />'])
        eid, chan, root = await orch.router.open_effort("drift", project="murder")
        await orch.charters.set_goal(eid, "fix the build", created_by="po")
        await orch.delegate(eid, chan, root, "fix the build", plan_steps=["work"])
        await _drain(orch)
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "Standing-intent violation" in msgs and "Murder.FNA" in msgs
        assert "PR opened for review" not in msgs, "a drifting delivery still opened a PR"
        assert "Auto-iteration" in msgs                 # it self-corrects, not asks
    finally:
        await db.dispose()


async def test_clean_delivery_passes_the_intent_gate(db_url, tmp_path):
    orch, chat, harness, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("murder", "https://github.com/devonpveller/murder")
        await orch.projects.set_standing_intent("murder", INTENT)
        # a diff that does NOT reintroduce the forbidden term → sails through
        orch._gh_transport = _remote(
            ['    <ProjectReference Include="..\\..\\MonoGame\\MonoGame.Framework.csproj" />'])
        eid, chan, root = await orch.router.open_effort("clean", project="murder")
        await orch.charters.set_goal(eid, "wire the source", created_by="po")
        await orch.delegate(eid, chan, root, "wire the source", plan_steps=["work"])
        await _drain(orch)
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "Standing-intent violation" not in msgs
        assert "PR opened for review" in msgs
    finally:
        await db.dispose()


async def test_rereport_converges_on_existing_effort(db_url, tmp_path):
    """A second build-error prompt on the same project reuses the FIRST effort's thread — one
    branch, one PR — instead of a new slug."""
    orch, chat, harness, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("murder", "https://github.com/devonpveller/murder")
        orch._gh_transport = _remote([])
        first, chan, root = await orch.router.open_effort("fix-murder-build-errors",
                                                          project="murder")
        await orch.charters.set_goal(
            first, "errors:\n'Point' is an ambiguous reference between 'Murder.Core.Geometry.Point'"
            " and 'Microsoft.Xna.Framework.Point'\nfix it", created_by="po")
        # the re-report: same signature line, model proposes a NEW slug
        orch.models._client.queue_structured(OperatorIntent(
            kind="request", reply="On it.", project="murder",
            effort_name="fix-ambiguous-point-again"))
        orch.models._client.queue_structured(
            ReadinessVerdict(clear_and_safe=True, blast_radius="routine"))
        mgmt = await orch.mgmt_channel_id()
        await orch.nl_intake(
            "still failing:\n'Point' is an ambiguous reference between "
            "'Murder.Core.Geometry.Point' and 'Microsoft.Xna.Framework.Point'", mgmt, thread_id="t")
        await _drain(orch)
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "Continuing the existing effort" in msgs and first in msgs
        from app.models import Effort
        import sqlalchemy
        async with orch.db.session_factory() as s:
            rows = (await s.execute(sqlalchemy.select(Effort))).scalars().all()
        assert not any(e.id == "effort-fix-ambiguous-point-again" for e in rows), \
            "a new sprawl effort was created instead of converging"
    finally:
        await db.dispose()


async def test_new_effort_phrase_bypasses_convergence(db_url, tmp_path):
    orch, chat, harness, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("murder", "https://github.com/devonpveller/murder")
        orch._gh_transport = _remote([])
        first, _c, _r = await orch.router.open_effort("existing", project="murder")
        await orch.charters.set_goal(first, "errors:\n'Point' ambiguous reference here\nfix",
                                     created_by="po")
        conv = await orch._find_convergent_effort(
            "murder", "as a NEW effort:\n'Point' ambiguous reference here")
        assert conv is None      # explicit "new effort" opts out of convergence
    finally:
        await db.dispose()


async def test_work_request_that_restates_the_rule_still_dispatches(db_url, tmp_path):
    """LIVE 2026-07-07 bug: 'in murder, ... (never NuGet Murder.FNA); fix ...' populated
    standing_intent and the handler ATE the request — no work ran. A work request that restates
    the rule must set the rule AND dispatch the work."""
    orch, chat, harness, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("murder", "https://github.com/devonpveller/murder")
        orch._gh_transport = _remote([])
        orch.models._client.queue_structured(OperatorIntent(
            kind="request", reply="On it.", project="murder",
            effort_name="vendored-build-fix",
            standing_intent="build from the vendored MonoGame source; never `Murder.FNA`"))
        orch.models._client.queue_structured(
            ReadinessVerdict(clear_and_safe=True, blast_radius="routine"))
        mgmt = await orch.mgmt_channel_id()
        await orch.nl_intake(
            "in murder, make it build from the vendored MonoGame source (never NuGet Murder.FNA); "
            "fix the OnExiting signature", mgmt, thread_id="t")
        await _drain(orch)
        # the rule was recorded
        p = await orch.projects.get("murder")
        assert "Murder.FNA" in (p["standing_intent"] or ""), "the rule was not set"
        # AND the work actually dispatched (an effort exists + a worker woke)
        assert len(orch.harness.wakes) >= 1, "the work request was eaten by the rule handler"
        from app.models import Effort
        import sqlalchemy
        async with orch.db.session_factory() as s:
            rows = (await s.execute(sqlalchemy.select(Effort))).scalars().all()
        assert any(e.id == "effort-vendored-build-fix" for e in rows), "no work effort opened"
    finally:
        await db.dispose()
