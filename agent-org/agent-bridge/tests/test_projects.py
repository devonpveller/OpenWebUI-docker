"""Multi-project registry (COMMS-MODEL §4: channel = project = repo) + the remotely-managed
worker git-egress allowlist. The org works on ANY onboarded repo; AO_DEFAULT_REPO is only a
fallback. Fakes only."""

from __future__ import annotations

import asyncio
from pathlib import Path

from app.adapters.chat import FakeChatAdapter
from app.config import Settings
from app.db import Database
from app.modules.audit_sink import AuditSink
from app.modules.egress import EgressAllowlist
from app.modules.model_router import FakeModelClient
from app.modules.projects import ProjectRegistry, host_of
from app.orchestrator import Orchestrator
from app.schemas import OperatorIntent, ReadinessVerdict
from app.worker.harness import FakeHarness

ROOT = Path(__file__).resolve().parents[1]


# ── git-URL host parsing ─────────────────────────────────────────────────────
def test_host_of_parses_all_git_url_forms():
    assert host_of("https://github.com/acme/app.git") == "github.com"
    assert host_of("http://gitlab.example.com/x/y") == "gitlab.example.com"
    assert host_of("git@github.com:acme/app.git") == "github.com"            # scp-like
    assert host_of("ssh://git@code.internal:2222/x/y.git") == "code.internal"
    assert host_of("git://bitbucket.org/x/y.git") == "bitbucket.org"
    assert host_of("") == ""


# ── ProjectRegistry ──────────────────────────────────────────────────────────
async def _registry(db, settings):
    return ProjectRegistry(db, AuditSink(db, settings))


async def test_registry_add_resolve_repo_and_hosts(db, settings):
    reg = await _registry(db, settings)
    await reg.add("Acme App", "https://github.com/acme/app.git")
    await reg.add("internal", "git@code.internal:team/svc.git")
    # resolve by slug OR display name
    assert (await reg.resolve("acme-app"))["repo_url"].endswith("app.git")
    assert (await reg.resolve("internal"))["git_host"] == "code.internal"
    assert await reg.repo_for("acme-app") == "https://github.com/acme/app.git"
    assert await reg.hosts() == {"github.com", "code.internal"}
    # remove deactivates
    assert await reg.remove("internal") is True
    assert await reg.repo_for("internal") is None
    assert await reg.hosts() == {"github.com"}


# ── EgressAllowlist ──────────────────────────────────────────────────────────
async def test_egress_allowlist_render_and_file(db, settings, tmp_path):
    reg = await _registry(db, settings)
    await reg.add("acme", "https://github.com/acme/app.git")   # host already seeded
    await reg.add("internal", "https://code.internal/x/y.git")  # a new host
    path = tmp_path / "egress-allowlist.txt"
    eg = EgressAllowlist(db, AuditSink(db, settings), reg, str(path))
    hosts = await eg.hosts()
    assert "github.com" in hosts and "code.internal" in hosts  # seed ∪ project hosts
    # a role widens scope manually; suppression overrides even a seed host
    await eg.allow("extra.example.com")
    await eg.remove("githubusercontent.com")
    hosts2 = await eg.hosts()
    assert "extra.example.com" in hosts2 and "githubusercontent.com" not in hosts2
    # sync writes the tinyproxy filter file
    content = await eg.sync()
    assert path.exists()
    written = path.read_text(encoding="utf-8")
    assert r"^(.*\.)?code\.internal$" in written           # host → regex pattern
    assert written == content


# ── end-to-end: a request names a project → its repo is focused ──────────────
async def _orch(db_url, *, survey=False):
    settings = Settings(
        _env_file=None, chat_adapter="fake",
        profiles_dir=str(ROOT / "profiles"), charters_dir=str(ROOT / "charters"),
        floor_dir=str(ROOT / "floor"), worker_instance_urls="http://w1:8090",
        max_concurrent_workers=1, database_url=db_url, project_survey_enabled=survey,
    )
    db = Database(db_url)
    orch = Orchestrator(settings, db, FakeChatAdapter(),
                        model_client=FakeModelClient(), harness=FakeHarness())
    await orch.setup()
    return orch, orch.chat, orch.harness, db


async def test_request_named_project_focuses_its_repo(db_url):
    orch, chat, harness, db = await _orch(db_url)
    try:
        await orch.projects.add("acme", "https://github.com/acme/app.git")
        mgmt = await orch.mgmt_channel_id()
        orch.models._client.queue_structured(
            OperatorIntent(kind="request", effort_name="add-feature", project="acme", reply="ok"))
        orch.models._client.queue_structured(ReadinessVerdict(clear_and_safe=True, blast_radius="routine"))
        await orch.handle_event(
            {"id": "r1", "channel_id": mgmt, "message": "in acme, add a feature",
             "is_bot": False, "ts": 1})
        # the effort landed in acme's project channel, not the sandbox
        assert await orch.gate.state_of("effort-add-feature") == "active"
        eff_repo = await orch._effort_repo("effort-add-feature")
        assert eff_repo == "https://github.com/acme/app.git"
        if orch._bg_tasks:
            await asyncio.gather(*orch._bg_tasks)
        # the worker was focused on acme's repo (not AO_DEFAULT_REPO / sandbox)
        assert "https://github.com/acme/app.git" in harness.projects.values()
    finally:
        await db.dispose()


async def test_project_add_command_onboards_and_allows_host(db_url):
    orch, chat, harness, db = await _orch(db_url)
    try:
        mgmt = await orch.mgmt_channel_id()
        await orch.handle_event(
            {"id": "c1", "channel_id": mgmt,
             "message": "/project add widgets https://gitlab.com/acme/widgets.git",
             "is_bot": False, "ts": 1})
        assert (await orch.projects.resolve("widgets"))["repo_url"].endswith("widgets.git")
        assert "gitlab.com" in await orch.egress.hosts()             # egress widened
        assert any("widgets" in p["message"] for p in chat.posted)
        assert "proj-widgets" in chat.channels                        # channel created
    finally:
        await db.dispose()


async def test_repo_effort_commits_and_pushes_a_feature_branch(db_url):
    """An effort against a real repo publishes its work to a feature branch on completion (commit +
    push are additive/routine) — so the work is durable, visible, and hand-off-able."""
    settings = Settings(
        _env_file=None, chat_adapter="fake",
        profiles_dir=str(ROOT / "profiles"), charters_dir=str(ROOT / "charters"),
        floor_dir=str(ROOT / "floor"), worker_instance_urls="http://w1:8090",
        max_concurrent_workers=1, database_url=db_url, project_survey_enabled=False,
        review_mode="off", plan_approval="off", default_repo="https://github.com/acme/app.git",
    )
    db = Database(db_url)
    orch = Orchestrator(settings, db, FakeChatAdapter(),
                        model_client=FakeModelClient(), harness=FakeHarness())
    await orch.setup()
    try:
        eid, chan, root = await orch.router.open_effort("feature")
        await orch.delegate(eid, chan, root, "do the thing")
        publish = next(w for w in orch.harness.wakes if "git push -u origin agent/effort-feature" in w["prompt"])
        # commits carry the AGENT's identity (not the baked "little-coder") for blame/provenance
        assert 'GIT_AUTHOR_NAME="worker-default"' in publish["prompt"]
        assert "worker-default@agent-org.local" in publish["prompt"]
        # the completion summary reports the branch to fetch — never main
        assert any("agent/effort-feature" in p["message"] for p in orch.chat.posted)
    finally:
        await db.dispose()


async def test_per_project_deploy_token_threaded_to_clone(db_url, monkeypatch):
    """A project can carry its OWN deploy token (env-var name) so different repos use different PATs;
    the resolved token is threaded to the worker's /project clone."""
    monkeypatch.setenv("AO_TOKEN_ACME", "ghp_secret_acme_123")
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
    try:
        await orch.projects.add("acme", "https://github.com/acme/app.git", token_env="AO_TOKEN_ACME")
        eid, chan, root = await orch.router.open_effort("feat", project="acme")
        assert await orch._project_token(eid) == "ghp_secret_acme_123"   # resolved from env
        await orch.delegate(eid, chan, root, "do it")
        # the per-project token was passed to the worker's clone (not the global one)
        assert "ghp_secret_acme_123" in orch.harness.tokens.values()
    finally:
        await db.dispose()


async def test_default_repo_auto_registered_as_project(db_url):
    settings = Settings(
        _env_file=None, chat_adapter="fake",
        profiles_dir=str(ROOT / "profiles"), charters_dir=str(ROOT / "charters"),
        floor_dir=str(ROOT / "floor"), database_url=db_url,
        default_repo="https://github.com/me/fallback.git", project_survey_enabled=False,
    )
    db = Database(db_url)
    orch = Orchestrator(settings, db, FakeChatAdapter(),
                        model_client=FakeModelClient(), harness=FakeHarness())
    try:
        await orch.setup()
        ps = await orch.projects.list()
        assert any(p["repo_url"] == "https://github.com/me/fallback.git" for p in ps)
    finally:
        await db.dispose()
