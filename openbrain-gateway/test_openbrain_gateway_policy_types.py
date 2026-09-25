"""The Open Brain gateway refuses policed arguments of the wrong JSON type,
refuses tools/list inside a batch, and forwards no hop-by-hop header.

The policy forces metadata_filter.share on every read tool and stamps
metadata_extra on every write tool. Each of those arguments must arrive as a
JSON object (or be absent/null); a string, list or other type is refused with
-32602 and never forwarded.

Run: python -m pytest openbrain-gateway -q
"""
import importlib.util
import json
import os
import pathlib

import httpx
import pytest
from starlette.requests import Request
from starlette.testclient import TestClient

GATEWAY_KEY = "gw-test-key"
os.environ.setdefault("OPENBRAIN_URL", "http://stub-openbrain:8000")
os.environ["OPENBRAIN_KEY"] = "upstream-test-key"
os.environ["GATEWAY_KEY"] = GATEWAY_KEY

_spec = importlib.util.spec_from_file_location(
    "openbrain_gateway_app_types", pathlib.Path(__file__).with_name("app.py"))
gw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gw)

AUTH = {"authorization": f"Bearer {GATEWAY_KEY}", "content-type": "application/json"}

_LOCAL = {"share": "local"}
_BAD_VALUES = {"str": json.dumps(_LOCAL), "list": [["share", "local"]], "number": 1}

# Default (cloud) profile: every read tool x metadata_filter, every write tool
# x metadata_extra, each with every wrong type.
WRONG_TYPE = {}
for _t in sorted(gw.READ_TOOLS):
    for _k, _v in _BAD_VALUES.items():
        WRONG_TYPE[f"{_t}-metadata_filter-{_k}"] = (_t, {"metadata_filter": _v})
for _t in sorted(gw.WRITE_TOOLS):
    for _k, _v in _BAD_VALUES.items():
        WRONG_TYPE[f"{_t}-metadata_extra-{_k}"] = (_t, {"metadata_extra": _v})


def _call(tool, args, rpc_id=1):
    return {"jsonrpc": "2.0", "id": rpc_id, "method": "tools/call",
            "params": {"name": tool, "arguments": args}}


@pytest.fixture
def upstream(monkeypatch):
    seen = []

    def handler(request: httpx.Request):
        seen.append(request.content)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {}})

    real = httpx.AsyncClient

    def factory(*a, **kw):
        kw["transport"] = httpx.MockTransport(handler)
        return real(*a, **kw)

    monkeypatch.setattr(gw.httpx, "AsyncClient", factory)
    return seen


def _post(obj):
    client = TestClient(gw.app, raise_server_exceptions=False)
    return client.post("/mcp", content=json.dumps(obj).encode(), headers=AUTH)


def test_policed_pairs_cover_the_default_profile():
    assert gw.READ_TOOLS == {"search", "fetch", "search_thoughts", "list_thoughts"}
    assert gw.WRITE_TOOLS == {"capture_thought", "ingest_url", "ingest_urls"}


@pytest.mark.parametrize("case", sorted(WRONG_TYPE))
def test_wrong_type_policed_argument_is_refused_never_forwarded(upstream, case):
    tool, args = WRONG_TYPE[case]
    r = _post(_call(tool, args))
    assert r.status_code < 500, r.status_code
    assert r.json().get("error", {}).get("code") == -32602, r.text[:200]
    assert upstream == [], f"{case}: forwarded {upstream}"


def test_correct_types_are_forwarded_with_policy(upstream):
    r = _post(_call("capture_thought", {"content": "c", "metadata_extra": {"share": "local"}}))
    assert r.status_code == 200
    extra = json.loads(upstream[0])["params"]["arguments"]["metadata_extra"]
    assert extra == {"share": "cloud", "origin": "cloud"}


def test_absent_or_null_filter_is_accepted(upstream):
    r = _post(_call("search_thoughts", {"query": "q", "metadata_filter": None}))
    assert r.status_code == 200
    assert json.loads(upstream[0])["params"]["arguments"]["metadata_filter"] == {"share": "cloud"}


def test_tools_list_inside_a_batch_is_refused(upstream):
    r = _post([{"jsonrpc": "2.0", "id": 1, "method": "ping"},
               {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}])
    assert 400 <= r.status_code < 500
    assert isinstance(r.json().get("error"), dict)
    assert upstream == []


HOP = [("connection", "keep-alive, x-named-by-connection"),
       ("keep-alive", "timeout=5"), ("proxy-connection", "keep-alive"),
       ("transfer-encoding", "chunked"), ("te", "trailers"), ("trailer", "x-t"),
       ("upgrade", "h2c"), ("proxy-authorization", "Basic eA=="),
       ("proxy-authenticate", "Basic"), ("content-encoding", "gzip"),
       ("x-named-by-connection", "v"), ("x-kept", "v")]


def _req(headers):
    scope = {"type": "http", "method": "POST", "path": "/mcp", "query_string": b"",
             "headers": [(k.encode(), v.encode()) for k, v in headers]}
    return Request(scope)


@pytest.mark.parametrize("name", [n for n, _ in HOP if n != "x-kept"])
def test_hop_by_hop_and_connection_named_headers_are_not_forwarded(name):
    for spelled in (name, name.upper()):
        headers = [(spelled if n == name else n, v) for n, v in HOP]
        out = {k.lower() for k in gw._upstream_headers(_req(headers))}
        assert name not in out, (spelled, sorted(out))
    assert "x-kept" in {k.lower() for k in gw._upstream_headers(_req(HOP))}
