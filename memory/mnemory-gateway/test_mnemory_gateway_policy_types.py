"""The mnemory cloud gateway polices arguments by their DECODED type, refuses
tools/list inside a batch, and forwards no hop-by-hop header.

mnemory's FastMCP JSON-decodes a STRING argument whose parameter is not typed
str, and some MCP clients send lists/objects as JSON strings. So an argument
the policy inspects or rewrites (labels, categories, memories and each
memory's labels/categories) that arrives as a JSON string decoding to the
expected type is decoded, policed and forwarded TYPED; anything else is
refused with -32602 and never forwarded. mnemory never receives a string for
a policed argument.

Run: python -m pytest memory/mnemory-gateway -q
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
os.environ.setdefault("MNEMORY_URL", "http://stub-mnemory:8050")
os.environ["MNEMORY_KEY"] = "upstream-test-key"
os.environ["GATEWAY_KEY"] = GATEWAY_KEY
os.environ["BOUND_USER_ID"] = "bound-user@example.test"

_spec = importlib.util.spec_from_file_location(
    "mnemory_gateway_app_types", pathlib.Path(__file__).with_name("app.py"))
gw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gw)

AUTH = {"authorization": f"Bearer {GATEWAY_KEY}", "content-type": "application/json"}

_LOCAL_LABELS = {"share": "local", "origin": "local"}
_ITEM = {"content": "c", "labels": _LOCAL_LABELS, "categories": ["personal"]}

# (tool, arguments) where a policed argument is neither the expected type nor
# a JSON string that strictly decodes to it.
WRONG_TYPE = {
    "search_memories-labels-plainstr": ("search_memories", {"query": "q", "labels": "share=local"}),
    "find_memories-labels-str-of-list": ("find_memories", {"question": "q", "labels": "[1]"}),
    "list_memories-labels-list": ("list_memories", {"labels": [["share", "local"]]}),
    "list_memories-labels-bom-str": ("list_memories", {"labels": "\ufeff" + json.dumps(_LOCAL_LABELS)}),
    "add_memory-labels-nan-str": ("add_memory", {"content": "c", "labels": '{"share": NaN}'}),
    "add_memory-categories-plainstr": ("add_memory", {"content": "c", "categories": "personal"}),
    "add_memory-categories-str-of-str": ("add_memory", {"content": "c", "categories": '"personal"'}),
    "add_memory-categories-str-of-obj": ("add_memory", {"content": "c", "categories": '{"a": 1}'}),
    "add_memory-categories-nonstr-item": ("add_memory", {"content": "c", "categories": [["personal"]]}),
    "add_memory-categories-double-encoded": ("add_memory", {"content": "c", "categories": json.dumps(json.dumps(["personal"]))}),
    "add_memories-memories-missing": ("add_memories", {}),
    "add_memories-memories-plainstr": ("add_memories", {"memories": "not json"}),
    "add_memories-memories-str-of-obj": ("add_memories", {"memories": json.dumps(_ITEM)}),
    "add_memories-item-number": ("add_memories", {"memories": [1]}),
    "add_memories-item-plainstr": ("add_memories", {"memories": ["not json"]}),
    "add_memories-item-labels-plainstr": ("add_memories", {"memories": [
        {"content": "c", "labels": "share=local"}]}),
    "add_memories-item-categories-plainstr": ("add_memories", {"memories": [
        {"content": "c", "categories": "personal"}]}),
}

_STAMPED = {"share": "cloud", "origin": "cloud"}

# (tool, arguments, check(forwarded_arguments)) - a JSON-string policed
# argument that decodes to the expected type: forwarded TYPED and policed.
DECODED = {
    "search_memories-labels-str": (
        "search_memories", {"query": "q", "labels": json.dumps(_LOCAL_LABELS)},
        lambda a: a["labels"] == {"share": "cloud"}),
    "find_memories-labels-str": (
        "find_memories", {"question": "q", "labels": json.dumps(_LOCAL_LABELS)},
        lambda a: a["labels"] == {"share": "cloud"}),
    "list_memories-labels-str": (
        "list_memories", {"labels": json.dumps(_LOCAL_LABELS)},
        lambda a: a["labels"] == {"share": "cloud"}),
    "add_memory-labels-str": (
        "add_memory", {"content": "c", "labels": json.dumps(_LOCAL_LABELS)},
        lambda a: a["labels"] == _STAMPED),
    "add_memory-categories-str": (
        "add_memory", {"content": "c", "categories": '["personal", "work"]'},
        lambda a: a["categories"] == ["work"]),
    "add_memories-memories-str": (
        "add_memories", {"memories": json.dumps([_ITEM])},
        lambda a: a["memories"] == [{"content": "c", "labels": _STAMPED, "categories": []}]),
    "add_memories-item-str": (
        "add_memories", {"memories": [json.dumps(_ITEM)]},
        lambda a: a["memories"] == [{"content": "c", "labels": _STAMPED, "categories": []}]),
    "add_memories-item-labels-str": (
        "add_memories", {"memories": [{"content": "c", "labels": json.dumps(_LOCAL_LABELS)}]},
        lambda a: a["memories"][0]["labels"] == _STAMPED),
    "add_memories-item-categories-str": (
        "add_memories", {"memories": [{"content": "c", "categories": '["personal"]'}]},
        lambda a: a["memories"][0]["categories"] == []),
}

# mnemory normalises categories with strip().lower() and "<prefix>:<name>" is
# a subcategory (mnemory/categories.py, validate_categories).
PERSONAL_SPELLINGS = ["personal", "Personal", "PERSONAL", " personal ", "personal:family",
                      "Personal:Family", " PERSONAL:x "]


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


@pytest.mark.parametrize("case", sorted(WRONG_TYPE))
def test_wrong_type_policed_argument_is_refused_never_forwarded(upstream, case):
    tool, args = WRONG_TYPE[case]
    r = _post(_call(tool, args))
    assert r.status_code < 500, r.status_code
    assert r.json().get("error", {}).get("code") == -32602, r.text[:200]
    assert upstream == [], f"{case}: forwarded {upstream}"


@pytest.mark.parametrize("case", sorted(WRONG_TYPE))
def test_wrong_type_policed_argument_is_refused_inside_a_batch(upstream, case):
    tool, args = WRONG_TYPE[case]
    ok = _call("list_categories", {}, rpc_id=9)
    r = _post([ok, _call(tool, args)])
    assert r.status_code < 500, r.status_code
    assert r.json().get("error", {}).get("code") == -32602, r.text[:200]
    assert upstream == []


@pytest.mark.parametrize("case", sorted(DECODED))
def test_json_string_argument_is_decoded_policed_and_forwarded_typed(upstream, case):
    tool, args, check = DECODED[case]
    r = _post(_call(tool, args))
    assert r.status_code == 200, r.text[:200]
    (sent,) = upstream
    fwd = json.loads(sent)["params"]["arguments"]
    for v in fwd.values():
        assert not (isinstance(v, str) and v.startswith(("[", "{"))), f"{case}: string forwarded {fwd}"
    assert check(fwd), f"{case}: {fwd}"


@pytest.mark.parametrize("spelled", PERSONAL_SPELLINGS)
def test_personal_category_stripped_in_every_spelling(upstream, spelled):
    _post(_call("add_memory", {"content": "c", "categories": [spelled, "work"]}))
    _post(_call("add_memories", {"memories": [{"content": "c", "categories": [spelled]}]}))
    one, many = (json.loads(b)["params"]["arguments"] for b in upstream)
    assert one["categories"] == ["work"]
    assert many["memories"][0]["categories"] == []


def test_non_personal_prefix_lookalikes_are_kept(upstream):
    keep = ["personality", "work", "project:personal", "personal-x"]
    _post(_call("add_memory", {"content": "c", "categories": keep}))
    assert json.loads(upstream[0])["params"]["arguments"]["categories"] == keep


def test_correct_types_are_forwarded_with_policy(upstream):
    r = _post(_call("add_memories", {"memories": [dict(_ITEM)]}))
    assert r.status_code == 200
    item = json.loads(upstream[0])["params"]["arguments"]["memories"][0]
    assert item["labels"] == {"share": "cloud", "origin": "cloud"}
    assert item["categories"] == []


def test_null_labels_and_categories_are_accepted(upstream):
    r = _post(_call("add_memory", {"content": "c", "labels": None, "categories": None}))
    assert r.status_code == 200
    args = json.loads(upstream[0])["params"]["arguments"]
    assert args["labels"] == {"share": "cloud", "origin": "cloud"}


def test_tools_list_inside_a_batch_is_refused(upstream):
    r = _post([{"jsonrpc": "2.0", "id": 1, "method": "ping"},
               {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}])
    assert 400 <= r.status_code < 500
    assert isinstance(r.json().get("error"), dict)
    assert upstream == []


def test_single_tools_list_still_forwarded_and_filtered(upstream, monkeypatch):
    r = _post({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert r.status_code == 200
    assert len(upstream) == 1


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
