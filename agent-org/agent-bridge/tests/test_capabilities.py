"""Capability plane (autonomous-project-lifecycle P-APL.1a). A repo STRUCTURE action (fork) is a
governed, deterministic operator-plane function: it is PROPOSED, HARD-GATED on the operator, and only
executes on `approve <id>` — never from fuzzy NL. On approval the fork is created via the GitHub App
and auto-registered as a project (with the parent as its read-only upstream). Fakes + a mocked GitHub;
no real network."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from app.adapters.chat import FakeChatAdapter
from app.config import Settings
from app.db import Database
from app.modules.capabilities import fork_repo, parse_owner_repo
from app.modules.github_app import FakeGitHubApp
from app.modules.model_router import FakeModelClient
from app.orchestrator import Orchestrator
from app.schemas import OperatorIntent
from app.worker.harness import FakeHarness

ROOT = Path(__file__).resolve().parents[1]


def test_parse_owner_repo_forms():
    assert parse_owner_repo("https://github.com/isadorasophia/murder.git") == ("isadorasophia", "murder")
    assert parse_owner_repo("https://github.com/MonoGame/MonoGame") == ("MonoGame", "MonoGame")
    assert parse_owner_repo("git@github.com:acme/widget.git") == ("acme", "widget")
    assert parse_owner_repo("isadorasophia/murder") == ("isadorasophia", "murder")
    with pytest.raises(ValueError):
        parse_owner_repo("not a repo")


# ── the executor: forks via the App, surfaces failures cleanly ──────────────
async def test_fork_repo_executor_success():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/isadorasophia/murder/forks"
        assert request.headers["authorization"] == "Bearer ghs_faketoken"
        return httpx.Response(202, json={"full_name": "devonpveller/murder",
                                         "html_url": "https://github.com/devonpveller/murder"})
    res = await fork_repo(FakeGitHubApp(owner="devonpveller"), "isadorasophia/murder",
                          transport=httpx.MockTransport(handler))
    assert res.ok and "devonpveller/murder" in res.summary and res.url.endswith("/murder")


async def test_fork_repo_executor_404_is_clear():
    res = await fork_repo(FakeGitHubApp(), "ghost/missing",
                          transport=httpx.MockTransport(lambda r: httpx.Response(404, json={"message": "Not Found"})))
    assert not res.ok and "404" in res.summary and "token" not in res.detail.lower()


# ── the governed flow via the orchestrator ──────────────────────────────────
async def _orch(db_url, tmp_path):
    # A dummy key file so `github_app_enabled` (id set + key file present) is genuinely True — the
    # FakeGitHubApp never reads it; the property only checks the file exists.
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
    assert settings.github_app_enabled                       # the capability plane is on for the flow
    db = Database(db_url)
    orch = Orchestrator(settings, db, FakeChatAdapter(),
                        model_client=FakeModelClient(), harness=FakeHarness())
    await orch.setup()
    return orch, orch.chat, db


async def test_fork_proposes_hardgate_then_executes_on_approve(db_url, tmp_path):
    orch, chat, db = await _orch(db_url, tmp_path)
    try:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(202, json={"full_name": "devonpveller/murder",
                                             "html_url": "https://github.com/devonpveller/murder"})
        orch._gh_transport = httpx.MockTransport(handler)
        orch.models._client.queue_structured(OperatorIntent(
            kind="capability", capability="fork", repo_url="isadorasophia/murder", reply="Sure —"))
        mgmt = await orch.mgmt_channel_id()
        # 1. NL proposes + HARD-GATES — nothing forked yet, no project registered
        await orch.nl_intake("fork isadorasophia/murder into my account", mgmt, thread_id="t")
        assert "cap-fork-murder" in orch._pending_capability
        assert any("Approval needed" in p["message"] and "approve cap-fork-murder" in p["message"]
                   for p in chat.posted)
        assert await orch.projects.resolve("murder") is None          # NOT executed on proposal

        # 2. operator approves → executes → fork done + project registered with upstream
        await orch.handle_event({"id": "d1", "channel_id": mgmt, "message": "approve cap-fork-murder",
                                 "is_bot": False, "ts": 2})
        assert "cap-fork-murder" not in orch._pending_capability      # consumed
        p = await orch.projects.resolve("murder")
        assert p is not None and p["upstream_url"] == "https://github.com/isadorasophia/murder"
        assert any("Forked" in m["message"] and "murder" in m["message"] for m in chat.posted)
    finally:
        await db.dispose()


async def test_fork_recovers_when_model_misfills_fields(db_url, tmp_path):
    """Regression for the live failure: the small model set kind=capability but NOT capability='fork'
    / repo_url. The handler must still recognise the fork + extract the repo from the raw MESSAGE, so
    'fork isadorasophia/murder into my account' proposes correctly instead of 'I can't do that'."""
    orch, chat, db = await _orch(db_url, tmp_path)
    try:
        # model filled fields badly (capability blank, no repo_url) — only the message carries intent
        orch.models._client.queue_structured(OperatorIntent(kind="capability", reply="Sure —"))
        mgmt = await orch.mgmt_channel_id()
        await orch.nl_intake("fork isadorasophia/murder into my account", mgmt, thread_id="t")
        assert "cap-fork-murder" in orch._pending_capability          # recovered from the message
        assert any("Approval needed" in p["message"] for p in chat.posted)
        assert not any("I can't do" in p["message"] or "isn't wired" in p["message"]
                       for p in chat.posted)
    finally:
        await db.dispose()


async def test_fork_abort_creates_nothing(db_url, tmp_path):
    orch, chat, db = await _orch(db_url, tmp_path)
    try:
        orch.models._client.queue_structured(OperatorIntent(
            kind="capability", capability="fork", repo_url="isadorasophia/murder", reply="Sure —"))
        mgmt = await orch.mgmt_channel_id()
        await orch.nl_intake("fork murder", mgmt, thread_id="t")
        await orch.handle_event({"id": "d1", "channel_id": mgmt, "message": "abort cap-fork-murder",
                                 "is_bot": False, "ts": 2})
        assert "cap-fork-murder" not in orch._pending_capability
        assert await orch.projects.resolve("murder") is None          # nothing created
        assert any("cancelled" in m["message"].lower() for m in chat.posted)
    finally:
        await db.dispose()
