"""ao-auth: agent-bridge's HTTP control plane needs a bearer token on every route but GET /health.

K8 tester finding (2026-10-07): POST /nl and every other control route took operator verbs from
anything that could reach agent-bridge:8000 - the worker command sandboxes (ao-ot-1/2) included.
These tests drive the real ASGI app (no socket, no Docker) through every classified route:
  - operator routes: no token -> 401, worker token -> 403, operator token -> the handler runs;
  - the worker route (/hook/floor-check): worker or operator token runs it, nothing else does;
  - AO_OPERATOR_TOKEN unset -> 503 (fail closed), equal tokens -> both refused, /health open;
  - an unclassified route stops the app from being built; tokens never reach a response or a log;
  - the floor hook sends the worker token and fails CLOSED when refused or token-less.
"""

from __future__ import annotations

import http.server
import json
import logging
import os
import subprocess
import sys
import threading
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from app import auth
from app.adapters.chat import FakeChatAdapter
from app.config import Settings
from app.db import Database
from app.main import ROUTE_ACCESS, create_app
from app.modules.model_router import FakeModelClient
from app.orchestrator import Orchestrator
from app.schemas import OperatorIntent
from app.worker.harness import FakeHarness
from authtok import OP_HEADERS, OP_TOKEN, WK_HEADERS, WK_TOKEN

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "hooks" / "pretooluse_floor.py"

# A body (and a concrete path) that gets each route past validation into its handler.
CONCERN = {"effort_id": "effort-demo", "trigger": "refusal",
           "concern": {"intent_thread": "i", "what_surfaced": "s", "intent_of_change": "c"}}
CALLS: dict[tuple[str, str], tuple[str, dict | None]] = {
    ("GET", "/health"): ("/health", None),
    ("POST", "/hook/floor-check"): ("/hook/floor-check", {"subject": "w1", "action": "git push origin main"}),
    ("POST", "/effort"): ("/effort", {"name": "demo"}),
    ("POST", "/nl"): ("/nl", {"message": "how's it going?"}),
    ("GET", "/state/{effort_id}"): ("/state/effort-demo", None),
    ("POST", "/concern"): ("/concern", CONCERN),
    ("POST", "/decision"): ("/decision", {"effort_id": "effort-demo", "decision": {"decision": "approve"},
                                          "actor_role": "po"}),
    ("POST", "/kill-switch"): ("/kill-switch", {"on": False}),
    ("GET", "/scheduler"): ("/scheduler", None),
    ("GET", "/audit"): ("/audit", None),
    ("GET", "/profiles"): ("/profiles", None),
    ("POST", "/profiles/lane"): ("/profiles/lane", {"name": "no-such-profile", "lane": "local"}),
    ("POST", "/effort/risk"): ("/effort/risk", {"effort_id": "effort-demo", "risk": "routine"}),
    ("POST", "/effort/dry-run"): ("/effort/dry-run", {"effort_id": "effort-demo", "passed": True}),
    ("POST", "/effort/prepare"): ("/effort/prepare", {"effort_id": "effort-demo", "request": "x"}),
    ("GET", "/execution/{effort_id}"): ("/execution/effort-demo", None),
    ("GET", "/projects"): ("/projects", None),
    ("POST", "/projects"): ("/projects", {"name": "demo", "repo_url": "https://example.invalid/o/demo.git"}),
    ("GET", "/egress"): ("/egress", None),
    ("POST", "/egress"): ("/egress", {"host": "example.invalid"}),
    ("POST", "/lateral-concern"): ("/lateral-concern", {"effort_id": "effort-demo", "from_role": "w",
                                                        "text": "t"}),
    ("POST", "/handoff"): ("/handoff", {"effort_id": "effort-demo", "path": "a.py"}),
    ("POST", "/suggestion"): ("/suggestion", {"worker": "w1", "text": "t"}),
    ("GET", "/suggestions"): ("/suggestions", None),
}
OPERATOR_ROUTES = sorted(k for k, v in ROUTE_ACCESS.items() if v == auth.OPERATOR)
WORKER_ROUTES = sorted(k for k, v in ROUTE_ACCESS.items() if v == auth.WORKER)
ANCHOR_NAMED = {"/nl", "/effort", "/decision", "/kill-switch", "/profiles/lane", "/projects", "/egress",
                "/concern", "/state/{effort_id}", "/scheduler", "/audit", "/execution/{effort_id}",
                "/profiles", "/suggestions"}
AUTH_DETAIL = ("token required", "needs the operator token", "not configured", "not classified")


def _settings(db_url, **tok) -> Settings:
    return Settings(_env_file=None, chat_adapter="fake", database_url=db_url,
                    profiles_dir=str(ROOT / "profiles"), charters_dir=str(ROOT / "charters"),
                    floor_dir=str(ROOT / "floor"), worker_instance_urls="http://w1:8090",
                    egress_allowlist_file="", **tok)


@pytest_asyncio.fixture
async def make_client(db_url):
    stack = []

    async def _make(**tok):
        orch = Orchestrator(_settings(db_url, **tok), Database(db_url), FakeChatAdapter(),
                            model_client=FakeModelClient(), harness=FakeHarness())
        for _ in range(4):
            orch.models._client.queue_structured(OperatorIntent(kind="status", reply="board"))
        app = create_app(orch)
        life = app.router.lifespan_context(app)
        await life.__aenter__()
        c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                              base_url="http://t")
        stack.append((life, c))
        return c

    yield _make
    for life, c in reversed(stack):
        await c.aclose()
        await life.__aexit__(None, None, None)


async def _call(c, key, headers=None):
    path, body = CALLS[key]
    if key[0] == "GET":
        return await c.get(path, headers=headers)
    return await c.post(path, json=body, headers=headers)


def _auth_refusal(r) -> bool:
    if r.status_code not in (401, 403, 503):
        return False
    try:
        detail = str(r.json().get("detail", ""))
    except ValueError:
        return False
    return any(s in detail for s in AUTH_DETAIL)


# ── the table itself ─────────────────────────────────────────────────────────────────────────────
def test_every_route_is_classified_and_the_anchor_routes_are_operator():
    assert set(CALLS) == set(ROUTE_ACCESS), "a route has no test call (or a stale one)"
    assert ROUTE_ACCESS[("GET", "/health")] == auth.PUBLIC
    assert [k for k, v in ROUTE_ACCESS.items() if v == auth.PUBLIC] == [("GET", "/health")]
    assert WORKER_ROUTES == [("POST", "/hook/floor-check")]
    for key, cls in ROUTE_ACCESS.items():
        if key[1] in ANCHOR_NAMED or key[1].startswith("/effort"):
            assert cls == auth.OPERATOR, key


def test_an_unclassified_route_refuses_to_build(db_url):
    from fastapi import FastAPI
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/health")
    async def _h():
        return {}

    @app.post("/new-control")
    async def _n():
        return {}

    with pytest.raises(RuntimeError, match="unclassified"):
        auth.verify_table(app, {("GET", "/health"): auth.PUBLIC})


# ── operator routes ──────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("key", OPERATOR_ROUTES, ids=lambda k: f"{k[0]} {k[1]}")
async def test_operator_route_needs_the_operator_token(make_client, key):
    c = await make_client(operator_token=OP_TOKEN, worker_token=WK_TOKEN)
    r = await _call(c, key)
    assert r.status_code == 401 and _auth_refusal(r), (key, r.status_code, r.text)
    assert r.headers.get("www-authenticate") == "Bearer"
    r = await _call(c, key, {"Authorization": "Bearer " + "wrong-" + OP_TOKEN[::-1]})
    assert r.status_code == 401 and _auth_refusal(r), (key, r.status_code)
    r = await _call(c, key, {"Authorization": OP_TOKEN})          # no scheme
    assert r.status_code == 401 and _auth_refusal(r), (key, r.status_code)
    r = await _call(c, key, WK_HEADERS)
    assert r.status_code == 403 and _auth_refusal(r), (key, r.status_code, r.text)
    assert (await c.post("/effort", json={"name": "demo"}, headers=OP_HEADERS)).status_code == 200
    r = await _call(c, key, OP_HEADERS)
    assert not _auth_refusal(r) and r.status_code < 500, (key, r.status_code, r.text)


@pytest.mark.parametrize("key", OPERATOR_ROUTES, ids=lambda k: f"{k[0]} {k[1]}")
async def test_unset_operator_token_fails_closed(make_client, key):
    c = await make_client(worker_token=WK_TOKEN)                  # AO_OPERATOR_TOKEN unset
    for h in (None, WK_HEADERS, OP_HEADERS):
        r = await _call(c, key, h)
        assert r.status_code == 503 and "not configured" in r.json()["detail"], (key, h, r.status_code)


async def test_blank_operator_token_is_unset(make_client):
    c = await make_client(operator_token="   ")
    r = await c.post("/nl", json={"message": "approve effort-x"}, headers={"Authorization": "Bearer    "})
    assert r.status_code == 503


async def test_equal_tokens_are_both_refused(make_client):
    c = await make_client(operator_token=OP_TOKEN, worker_token=OP_TOKEN)
    assert (await c.post("/nl", json={"message": "x"}, headers=OP_HEADERS)).status_code == 503
    r = await c.post("/hook/floor-check", json={"subject": "w1", "action": "ls"}, headers=OP_HEADERS)
    assert r.status_code == 503


# ── the worker route ─────────────────────────────────────────────────────────────────────────────
async def test_floor_check_takes_the_worker_or_operator_token_only(make_client):
    c = await make_client(operator_token=OP_TOKEN, worker_token=WK_TOKEN)
    key = ("POST", "/hook/floor-check")
    assert (await _call(c, key)).status_code == 401
    assert (await _call(c, key, {"Authorization": "Bearer nope"})).status_code == 401
    for h in (WK_HEADERS, OP_HEADERS):
        r = await _call(c, key, h)
        assert r.status_code == 200 and r.json()["allowed"] is False   # git push main: blocked by floor
    r = await c.post("/hook/floor-check", json={"subject": "w1", "action": "ls -la"}, headers=WK_HEADERS)
    assert r.status_code == 200 and r.json()["allowed"] is True


async def test_floor_check_without_operator_token_still_takes_the_worker_token(make_client):
    c = await make_client(worker_token=WK_TOKEN)
    r = await c.post("/hook/floor-check", json={"subject": "w1", "action": "ls"}, headers=WK_HEADERS)
    assert r.status_code == 200
    assert (await c.post("/hook/floor-check", json={"subject": "w1", "action": "ls"})).status_code == 401


async def test_no_tokens_at_all_refuses_everything_but_health(make_client):
    c = await make_client()
    for key in ROUTE_ACCESS:
        r = await _call(c, key, OP_HEADERS)
        if key == ("GET", "/health"):
            assert r.status_code == 200 and r.json() == {"status": "ok"}
        else:
            assert r.status_code == 503, key


async def test_health_is_open_and_docs_are_gone(make_client):
    c = await make_client(operator_token=OP_TOKEN, worker_token=WK_TOKEN)
    assert (await c.get("/health")).json() == {"status": "ok"}
    for p in ("/openapi.json", "/docs", "/redoc"):
        assert (await c.get(p)).status_code == 404
        assert (await c.get(p, headers=OP_HEADERS)).status_code == 404


async def test_refusal_happens_before_the_body_is_read(make_client):
    c = await make_client(operator_token=OP_TOKEN, worker_token=WK_TOKEN)
    r = await c.post("/nl", content=b"{not json", headers={"Content-Type": "application/json"})
    assert r.status_code == 401                                    # not 422: schema never consulted


async def test_tokens_never_reach_a_response_or_a_log(make_client, caplog):
    caplog.set_level(logging.DEBUG)
    c = await make_client(operator_token=OP_TOKEN, worker_token=WK_TOKEN)
    texts = []
    for key in ROUTE_ACCESS:
        for h in (None, WK_HEADERS, OP_HEADERS):
            r = await _call(c, key, h)
            texts.append(r.text + json.dumps(dict(r.headers)))
    blob = "\n".join(texts) + "\n" + caplog.text
    assert OP_TOKEN not in blob and WK_TOKEN not in blob
    assert "auth: refused POST /nl -> 401" in caplog.text
    assert "auth: operator token configured" in caplog.text


def test_unset_operator_token_logs_at_startup(db_url, caplog):
    caplog.set_level(logging.INFO)
    auth.load_tokens(_settings(db_url))
    assert "AO_OPERATOR_TOKEN is not set" in caplog.text and "AO_WORKER_TOKEN is not set" in caplog.text


def test_decide_is_pure_and_fails_closed_on_unknown_class():
    t = auth.Tokens(OP_TOKEN.encode(), WK_TOKEN.encode())
    assert auth.decide(None, OP_TOKEN.encode(), t) == (403, "route is not classified for access; refused")
    assert auth.decide("bogus", OP_TOKEN.encode(), t)[0] == 403
    assert auth.decide(auth.PUBLIC, None, t) is None


# ── the floor hook (the worker-side caller) ──────────────────────────────────────────────────────
class _FakeBridge(http.server.BaseHTTPRequestHandler):
    seen: list = []
    mode = "auth"

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        got = self.headers.get("Authorization")
        _FakeBridge.seen.append((self.path, got, body))
        if got != "Bearer " + WK_TOKEN:
            code, out = 401, {"detail": "worker token required"}
        else:
            code, out = 200, {"allowed": _FakeBridge.mode == "allow", "reason": "fake"}
        data = json.dumps(out).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


@pytest.fixture
def fake_bridge():
    srv = http.server.HTTPServer(("127.0.0.1", 0), _FakeBridge)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    _FakeBridge.seen = []
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def _hook(command, **env):
    e = {k: v for k, v in os.environ.items() if not k.startswith("AO_")}
    e.update(env)
    p = subprocess.run([sys.executable, str(HOOK)], input=json.dumps({"tool_input": {"command": command}}),
                       capture_output=True, text=True, env=e, timeout=30)
    return p.returncode, p.stderr


def test_hook_sends_the_worker_token_and_follows_the_floor(fake_bridge):
    _FakeBridge.mode = "allow"
    rc, err = _hook("git push origin main", AO_BRIDGE_URL=fake_bridge, AO_WORKER_TOKEN=WK_TOKEN)
    assert rc == 0, err
    assert _FakeBridge.seen[-1][:2] == ("/hook/floor-check", "Bearer " + WK_TOKEN)
    _FakeBridge.mode = "deny"
    rc, err = _hook("git push origin main", AO_BRIDGE_URL=fake_bridge, AO_WORKER_TOKEN=WK_TOKEN)
    assert rc == 2


def test_hook_fails_closed_without_or_with_a_refused_token(fake_bridge):
    _FakeBridge.mode = "allow"
    rc, err = _hook("git push origin main", AO_BRIDGE_URL=fake_bridge)
    assert rc == 2 and "no AO_WORKER_TOKEN" in err and not _FakeBridge.seen
    rc, err = _hook("git push origin main", AO_BRIDGE_URL=fake_bridge, AO_WORKER_TOKEN="wrong-" + WK_TOKEN)
    assert rc == 2 and "HTTP 401" in err and WK_TOKEN not in err
    # a reversible action never needs the bridge
    assert _hook("ls -la", AO_BRIDGE_URL=fake_bridge)[0] == 0
