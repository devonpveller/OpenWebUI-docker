"""HTTP control-surface tests through the real ASGI app (regression guard for the
`from __future__ import annotations` + FastAPI body-model resolution bug — request-body
models MUST be module-scope, else FastAPI mis-reads them as query params -> 422)."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest_asyncio

from app.adapters.chat import FakeChatAdapter
from app.config import Settings
from app.db import Database
from app.main import create_app
from app.modules.model_router import FakeModelClient
from app.orchestrator import Orchestrator
from app.worker.harness import FakeHarness
from authtok import OP_HEADERS, OP_TOKEN

ROOT = Path(__file__).resolve().parents[1]


@pytest_asyncio.fixture
async def client(db_url):
    settings = Settings(
        _env_file=None, chat_adapter="fake", database_url=db_url,
        profiles_dir=str(ROOT / "profiles"), charters_dir=str(ROOT / "charters"),
        floor_dir=str(ROOT / "floor"), worker_instance_urls="http://w1:8090",
        operator_token=OP_TOKEN,
    )
    orch = Orchestrator(settings, Database(db_url), FakeChatAdapter(),
                        model_client=FakeModelClient(), harness=FakeHarness())
    app = create_app(orch)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t", headers=OP_HEADERS) as c:
            yield c


async def test_health(client):
    assert (await client.get("/health")).json() == {"status": "ok"}


async def test_body_endpoints_accept_json(client):
    # If body models were mis-resolved as query params this would 422.
    r = await client.post("/effort", json={"name": "demo"})
    assert r.status_code == 200 and r.json()["effort_id"] == "effort-demo"
    r = await client.post("/hook/floor-check", json={"subject": "w1", "action": "git push origin main"})
    assert r.status_code == 200 and r.json()["allowed"] is False


async def test_concern_freeze_and_authority_over_http(client):
    await client.post("/effort", json={"name": "demo"})
    r = await client.post("/concern", json={
        "effort_id": "effort-demo", "trigger": "refusal",
        "concern": {"intent_thread": "i", "what_surfaced": "refusal", "intent_of_change": "blocks"},
    })
    assert r.json()["state"] == "frozen"
    # PO cannot clear a hard-gate over HTTP -> 400
    r = await client.post("/decision", json={
        "effort_id": "effort-demo", "decision": {"decision": "approve"}, "actor_role": "po"})
    assert r.status_code == 400
    # human can
    r = await client.post("/decision", json={
        "effort_id": "effort-demo", "decision": {"decision": "approve"}, "actor_role": "human"})
    assert r.status_code == 200 and r.json()["state"] == "active"


async def test_profiles_seeded_over_http(client):
    profiles = (await client.get("/profiles")).json()["profiles"]
    assert "worker-default" in profiles and profiles["worker-default"]["lane"] == "local"
    assert {"pm", "po", "planner", "reviewer-ethics"} <= set(profiles)


async def test_kill_switch_over_http(client):
    await client.post("/effort", json={"name": "demo"})
    await client.post("/kill-switch", json={"on": True})
    st = (await client.get("/state/effort-demo")).json()
    assert st["can_dispatch"] is False


async def test_nl_inlet_drives_the_org_like_an_operator_message(db_url):
    """The internal /nl inlet injects an operator NL message → nl_intake (classify → govern →
    dispatch), so tooling/automation can drive the org exactly like a chat turn (operator
    2026-07-11: "you lead the orchestration as me")."""
    from app.schemas import OperatorIntent
    settings = Settings(
        _env_file=None, chat_adapter="fake", database_url=db_url,
        profiles_dir=str(ROOT / "profiles"), charters_dir=str(ROOT / "charters"),
        floor_dir=str(ROOT / "floor"), worker_instance_urls="http://w1:8090",
        operator_token=OP_TOKEN)
    orch = Orchestrator(settings, Database(db_url), FakeChatAdapter(),
                        model_client=FakeModelClient(), harness=FakeHarness())
    orch.models._client.queue_structured(OperatorIntent(kind="status", reply="Here's the board."))
    app = create_app(orch)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t", headers=OP_HEADERS) as c:
            r = await c.post("/nl", json={"message": "how's it going?"})
            assert r.status_code == 200 and r.json()["ok"] is True
    assert orch.chat.posted, "the /nl inlet did not reach nl_intake (no org reply posted)"


async def test_control_routes_refuse_a_caller_without_the_operator_token(client):
    """ao-auth RED/GREEN in this file's own terms: the same app, a caller with no bearer. Before
    ao-auth every one of these answered (the K8 finding); now each is 401 and nothing ran."""
    anon = {"Authorization": ""}
    for path, body in (("/nl", {"message": "kill"}), ("/kill-switch", {"on": True}),
                       ("/effort", {"name": "x"}), ("/egress", {"host": "evil.example"})):
        r = await client.post(path, json=body, headers=anon)
        assert r.status_code == 401, (path, r.status_code)
    for path in ("/audit", "/scheduler", "/projects", "/profiles", "/suggestions"):
        assert (await client.get(path, headers=anon)).status_code == 401, path
    assert (await client.get("/health", headers=anon)).status_code == 200
    r = await client.get("/egress")                       # the fixture's operator bearer
    assert r.status_code == 200 and "evil.example" not in r.text
