"""The Open Brain gateway fails CLOSED on request bodies it cannot parse.

The gateway's policy (blocked tools, the forced share filter on reads, the
origin/share stamp on writes) runs on the body it parsed. A body it cannot
parse must therefore be refused - a 4xx carrying a JSON-RPC error - and never
reach the upstream, whose own parser accepts more shapes than the gateway's.
Only bytes the gateway re-serialised after policy may go upstream.

The upstream is a stub (httpx.MockTransport) that records every body it gets.

Run: python -m pytest openbrain-gateway -q
"""
import importlib.util
import json
import os
import pathlib

import httpx
import pytest
from starlette.testclient import TestClient

GATEWAY_KEY = "gw-test-key"
os.environ.setdefault("OPENBRAIN_URL", "http://stub-openbrain:8000")
os.environ["OPENBRAIN_KEY"] = "upstream-test-key"
os.environ["GATEWAY_KEY"] = GATEWAY_KEY

_spec = importlib.util.spec_from_file_location(
    "openbrain_gateway_app_body", pathlib.Path(__file__).with_name("app.py"))
gw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gw)

AUTH = {"authorization": f"Bearer {GATEWAY_KEY}", "content-type": "application/json"}

# A call the gateway rewrites when it can parse it (force the share filter),
# and one it blocks outright (in the default cloud profile).
_READ = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
    "name": "search_thoughts",
    "arguments": {"query": "q", "metadata_filter": {"share": "local"}}}})
_BLOCKED = json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                       "params": {"name": "thought_stats", "arguments": {}}})

UNPARSEABLE = {
    "bom-read": b"\xef\xbb\xbf" + _READ.encode(),
    "bom-blocked": b"\xef\xbb\xbf" + _BLOCKED.encode(),
    "utf16-bom": _READ.encode("utf-16"),
    "utf16-le": _READ.encode("utf-16-le"),
    "utf16-be": _READ.encode("utf-16-be"),
    "utf32": _READ.encode("utf-32"),
    "trailing-garbage": _READ.encode() + b" garbage",
    "two-documents": (_READ + _READ).encode(),
    "not-json": b"not json at all",
    "invalid-utf8": b'{"jsonrpc":"2.0","id":1,"method":"ping","x":"\xff"}',
    "scalar-number": b"42",
    "scalar-string": b'"tools/call"',
    "scalar-null": b"null",
    "scalar-true": b"true",
    "empty-batch": b"[]",
    "batch-non-object": b"[1]",
    "batch-mixed": ("[" + _READ + ", 2]").encode(),
    "nan-constant": b'{"jsonrpc":"2.0","id":NaN,"method":"ping"}',
    "empty-body": b"",
}

# Parseable JSON whose shape the policy cannot apply to: answered with a
# JSON-RPC error like any blocked call (HTTP 200), and never forwarded.
MALFORMED_CALL = {
    "params-not-object": b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":[1]}',
    "arguments-not-object": (b'{"jsonrpc":"2.0","id":1,"method":"tools/call",'
                             b'"params":{"name":"search_thoughts","arguments":[1]}}'),
}


@pytest.fixture
def upstream(monkeypatch):
    seen = []

    def handler(request: httpx.Request):
        seen.append((request.method, request.content))
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {}})

    real = httpx.AsyncClient

    def factory(*a, **kw):
        kw["transport"] = httpx.MockTransport(handler)
        return real(*a, **kw)

    monkeypatch.setattr(gw.httpx, "AsyncClient", factory)
    return seen


def _refused(r):
    assert 400 <= r.status_code < 500, (r.status_code, r.text[:200])
    err = r.json().get("error")
    assert isinstance(err, dict) and isinstance(err.get("code"), int), r.text[:200]


@pytest.mark.parametrize("shape", sorted(UNPARSEABLE))
def test_unparseable_post_is_refused_and_never_forwarded(upstream, shape):
    r = TestClient(gw.app).post("/mcp", content=UNPARSEABLE[shape], headers=AUTH)
    _refused(r)
    assert upstream == [], f"{shape}: upstream received {upstream}"


@pytest.mark.parametrize("shape", sorted(MALFORMED_CALL))
def test_malformed_tools_call_is_answered_locally_never_forwarded(upstream, shape):
    r = TestClient(gw.app).post("/mcp", content=MALFORMED_CALL[shape], headers=AUTH)
    assert r.json()["error"]["code"] == -32602, r.text[:200]
    assert upstream == []


@pytest.mark.parametrize("method", ["GET", "DELETE"])
def test_other_method_with_body_is_refused(upstream, method):
    r = TestClient(gw.app).request(method, "/mcp", content=_READ.encode(), headers=AUTH)
    _refused(r)
    assert upstream == []


def test_ordinary_call_is_forwarded_with_policy_applied(upstream):
    r = TestClient(gw.app).post("/mcp", content=_READ.encode(), headers=AUTH)
    assert r.status_code == 200
    (_, sent), = upstream
    assert json.loads(sent)["params"]["arguments"]["metadata_filter"]["share"] == "cloud"


def test_ordinary_blocked_tool_is_answered_locally(upstream):
    r = TestClient(gw.app).post("/mcp", content=_BLOCKED.encode(), headers=AUTH)
    assert r.json()["error"]["code"] == -32601
    assert upstream == []


def test_ordinary_batch_with_whitespace_is_forwarded_reserialised(upstream):
    body = ("  [" + _READ + "]\n").encode()
    r = TestClient(gw.app).post("/mcp", content=body, headers=AUTH)
    assert r.status_code == 200
    (_, sent), = upstream
    assert sent != body
    assert json.loads(sent)[0]["params"]["arguments"]["metadata_filter"]["share"] == "cloud"


@pytest.mark.parametrize("method", ["GET", "DELETE"])
def test_bodyless_get_and_delete_still_pass(upstream, method):
    r = TestClient(gw.app).request(method, "/mcp", headers=AUTH)
    assert r.status_code == 200
    assert upstream == [(method, b"")]
