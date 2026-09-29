"""The mnemory cloud gateway forwards identity headers exactly once: its own.

mnemory resolves identity and authentication from these request headers
(mnemory/server.py, APIKeyMiddleware): authorization, x-api-key, cookie,
x-user-id, x-openwebui-user-email, x-agent-id. A client may send any of them
in any letter case; the upstream must receive only the gateway's value, or
none where the gateway binds none.

The upstream here is a stub (httpx.MockTransport) that records the raw header
list it received, and reads it back through Starlette's Headers - the same
class mnemory's middleware reads - so the test asks what the upstream would
actually resolve, not only what the gateway meant to send.

Run: python -m pytest memory/mnemory-gateway -q
"""
import importlib.util
import json
import os
import pathlib

import httpx
import pytest
from starlette.datastructures import Headers
from starlette.testclient import TestClient

GATEWAY_KEY = "gw-test-key"
MNEMORY_KEY = "upstream-test-key"
BOUND_USER = "bound-user@example.test"

os.environ.setdefault("MNEMORY_URL", "http://stub-mnemory:8050")
os.environ["MNEMORY_KEY"] = MNEMORY_KEY
os.environ["GATEWAY_KEY"] = GATEWAY_KEY
os.environ["BOUND_USER_ID"] = BOUND_USER

_spec = importlib.util.spec_from_file_location(
    "mnemory_gateway_app", pathlib.Path(__file__).with_name("app.py"))
gw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gw)

# header -> the single value the upstream must see (None = must be absent)
EXPECTED = {
    "authorization": f"Bearer {MNEMORY_KEY}",
    "x-user-id": BOUND_USER,
    "x-api-key": None,
    "cookie": None,
    "x-openwebui-user-email": None,
    "x-agent-id": None,
}

SPOOF = {
    "authorization": "Bearer spoofed-token",
    "x-user-id": "spoofed-user",
    "x-api-key": "spoofed-key",
    "cookie": "mnemory_exchange_session=spoofed; cognis_session=spoofed",
    "x-openwebui-user-email": "spoofed@example.test",
    "x-agent-id": "spoofed-agent",
}


def _variants(name):
    """lower, Title-Case and UPPER spellings of one header name."""
    return sorted({name.lower(), "-".join(p.capitalize() for p in name.split("-")),
                   name.upper()})


@pytest.fixture
def upstream(monkeypatch):
    """Patch the gateway's httpx.AsyncClient onto a stub that echoes headers."""
    seen = []

    def handler(request: httpx.Request):
        # an ASGI server (uvicorn, which mnemory runs under) hands the app
        # header names lowercased and in wire order; do the same
        seen.append([(k.lower(), v) for k, v in request.headers.raw])
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {}})

    real = httpx.AsyncClient

    def factory(*a, **kw):
        kw["transport"] = httpx.MockTransport(handler)
        return real(*a, **kw)

    monkeypatch.setattr(gw.httpx, "AsyncClient", factory)
    return seen


def _send(extra_headers):
    """POST a harmless JSON-RPC call through the gateway with raw headers."""
    client = TestClient(gw.app)
    raw = [(b"authorization", f"Bearer {GATEWAY_KEY}".encode()),
           (b"content-type", b"application/json")]
    raw += [(k.encode(), v.encode()) for k, v in extra_headers]
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"})
    # a list of tuples keeps duplicate names and their spelling
    r = client.post("/mcp", content=body,
                    headers=[(k.decode(), v.decode()) for k, v in raw])
    assert r.status_code == 200, r.text
    return r


def _values(raw, name):
    return [v.decode() for k, v in raw if k.decode().lower() == name]


@pytest.mark.parametrize("header,spelled", [
    (h, s) for h in sorted(SPOOF) for s in _variants(h)])
def test_spoofed_header_in_any_case_reaches_upstream_once_as_gateway_value(
        upstream, header, spelled):
    # for authorization this is a SECOND value after the client's gateway key
    _send([(spelled, SPOOF[header])])
    raw = upstream[-1]
    got = _values(raw, header)
    want = EXPECTED[header]
    if want is None:
        assert got == [], f"{header} ({spelled}) forwarded: {got}"
    else:
        assert got == [want], f"{header} ({spelled}) upstream saw {got}"
        assert Headers(raw=raw).get(header) == want


def test_all_spoofs_in_all_cases_at_once(upstream):
    extra = []
    for name, val in SPOOF.items():
        for spelled in _variants(name):
            extra.append((spelled, val))
    _send(extra)
    raw = upstream[-1]
    h = Headers(raw=raw)
    for name, want in EXPECTED.items():
        got = _values(raw, name)
        assert got == ([] if want is None else [want]), (name, got)
        assert h.get(name) == want


def test_starlette_get_resolves_gateway_user_not_client(upstream):
    """The shape of the defect: lowercase client header ahead of the gateway's."""
    _send([("x-user-id", "spoofed-user")])
    assert Headers(raw=upstream[-1]).get("x-user-id") == BOUND_USER


def test_non_identity_headers_still_pass(upstream):
    _send([("mcp-session-id", "abc123"), ("x-timezone", "Europe/Prague"),
           ("accept", "application/json, text/event-stream")])
    h = Headers(raw=upstream[-1])
    assert h.get("mcp-session-id") == "abc123"
    assert h.get("x-timezone") == "Europe/Prague"


def test_wrong_gateway_key_still_401(upstream):
    client = TestClient(gw.app)
    r = client.post("/mcp", content=b"{}",
                    headers={"authorization": "Bearer nope"})
    assert r.status_code == 401
    assert upstream == []
