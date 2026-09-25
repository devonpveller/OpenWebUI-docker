"""The Open Brain gateway forwards its access key exactly once: its own.

openbrain-mcp reads exactly one request header for authorisation, and none
for identity, share scope or exposure: `x-brain-key` (integrations/
kubernetes-deployment/index.ts, the `app.all("*")` catch-all and
researchAuthed). Share and exposure are set by the gateway in the JSON-RPC
body (metadata_filter / metadata_extra) and, for agent-memory, by the
server's own door constant - never read from a header.

A client may send x-brain-key (and authorization) in any letter case; the
upstream must receive x-brain-key once, the gateway's, and no authorization.
The upstream here is a stub (httpx.MockTransport) that records the header
list it received; the check reads it back through Starlette's Headers and
through a WHATWG-style join (Deno/Hono's Headers.get concatenates duplicate
values with ", ", so a duplicate would fail auth rather than pick one).

Run: python -m pytest openbrain-gateway -q
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
OPENBRAIN_KEY = "upstream-test-key"

os.environ.setdefault("OPENBRAIN_URL", "http://stub-openbrain:8000")
os.environ["OPENBRAIN_KEY"] = OPENBRAIN_KEY
os.environ["GATEWAY_KEY"] = GATEWAY_KEY

_spec = importlib.util.spec_from_file_location(
    "openbrain_gateway_app", pathlib.Path(__file__).with_name("app.py"))
gw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gw)

EXPECTED = {
    "x-brain-key": OPENBRAIN_KEY,
    "authorization": None,
}

SPOOF = {
    "x-brain-key": "spoofed-brain-key",
    "authorization": "Bearer spoofed-token",
}


def _variants(name):
    return sorted({name.lower(), "-".join(p.capitalize() for p in name.split("-")),
                   name.upper()})


@pytest.fixture
def upstream(monkeypatch):
    seen = []

    def handler(request: httpx.Request):
        seen.append([(k.lower(), v) for k, v in request.headers.raw])
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {}})

    real = httpx.AsyncClient

    def factory(*a, **kw):
        kw["transport"] = httpx.MockTransport(handler)
        return real(*a, **kw)

    monkeypatch.setattr(gw.httpx, "AsyncClient", factory)
    return seen


def _send(extra_headers):
    client = TestClient(gw.app)
    headers = [("authorization", f"Bearer {GATEWAY_KEY}"),
               ("content-type", "application/json")] + list(extra_headers)
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"})
    r = client.post("/mcp", content=body, headers=headers)
    assert r.status_code == 200, r.text
    return r


def _values(raw, name):
    return [v.decode() for k, v in raw if k.decode() == name]


@pytest.mark.parametrize("header,spelled", [
    (h, s) for h in sorted(SPOOF) for s in _variants(h)])
def test_spoofed_header_in_any_case_reaches_upstream_once_as_gateway_value(
        upstream, header, spelled):
    _send([(spelled, SPOOF[header])])
    raw = upstream[-1]
    got = _values(raw, header)
    want = EXPECTED[header]
    assert got == ([] if want is None else [want]), (header, spelled, got)
    assert Headers(raw=raw).get(header) == want
    # WHATWG join, as Hono's c.req.header() would see it
    assert (", ".join(got) or None) == want


def test_all_spoofs_in_all_cases_at_once(upstream):
    extra = [(s, v) for n, v in SPOOF.items() for s in _variants(n)]
    _send(extra)
    raw = upstream[-1]
    for name, want in EXPECTED.items():
        assert _values(raw, name) == ([] if want is None else [want]), name


def test_query_key_cannot_displace_header_key(upstream):
    """openbrain-mcp falls back to ?key= only when x-brain-key is absent; the
    gateway always sets the header, so a client ?key= decides nothing."""
    client = TestClient(gw.app)
    r = client.post("/mcp?key=spoofed", content=b'{"jsonrpc":"2.0","id":1,"method":"ping"}',
                    headers={"authorization": f"Bearer {GATEWAY_KEY}"})
    assert r.status_code == 200
    assert _values(upstream[-1], "x-brain-key") == [OPENBRAIN_KEY]


def test_non_identity_headers_still_pass(upstream):
    _send([("mcp-session-id", "abc123"),
           ("accept", "application/json, text/event-stream")])
    assert Headers(raw=upstream[-1]).get("mcp-session-id") == "abc123"


def test_wrong_gateway_key_still_401(upstream):
    client = TestClient(gw.app)
    r = client.post("/mcp", content=b"{}", headers={"authorization": "Bearer nope"})
    assert r.status_code == 401
    assert upstream == []
