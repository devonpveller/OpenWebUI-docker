"""cf-gateways: the hardening gaps the ac-gw-headers review left, mnemory door.

1. A request body over GATEWAY_MAX_BODY_BYTES is refused with 413 and never
   forwarded (declared Content-Length, and a chunked body that declares none).
2. A JSON number that overflows a double (1e400) is refused, never re-emitted
   upstream as the non-JSON token Infinity.
3. (Open Brain only - metadata_extra.source has no mnemory counterpart; see the
   cf-gateways findings.)
4. The tools/list filter fails CLOSED: an upstream reply it cannot filter
   advertises NOTHING instead of passing through unfiltered.

Plus the ordinary traffic that must behave exactly as before.

The upstream is a stub (httpx.MockTransport). Every test here fails against the
gateway at 0fb1c0c except the ones named test_ordinary_* / test_valid_*.

Run: python -m pytest memory/mnemory-gateway -q
"""
import importlib.util
import json
import os
import pathlib

import httpx
import pytest
from starlette.testclient import TestClient

GATEWAY_KEY = "gw-test-key"
os.environ.setdefault("MNEMORY_URL", "http://stub-mnemory:8050")
os.environ["MNEMORY_KEY"] = "upstream-test-key"
os.environ["GATEWAY_KEY"] = GATEWAY_KEY
os.environ["BOUND_USER_ID"] = "bound-user@example.test"

_APP = pathlib.Path(__file__).with_name("app.py")


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _APP)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gw = _load("mnemory_gateway_app_hardening")

AUTH = {"authorization": f"Bearer {GATEWAY_KEY}", "content-type": "application/json"}
CAP = 4 * 1024 * 1024

_UPSTREAM_TOOLS = [{"name": n, "inputSchema": {}} for n in (
    "search_memories", "find_memories", "list_memories", "add_memory",
    "add_memories", "list_categories", "initialize_memory", "ask_memories",
    "get_core_memories", "get_recent_memories", "update_memory", "delete_memory",
    "delete_memories", "save_artifact")]
_ADVERTISED = {"search_memories", "find_memories", "list_memories", "add_memory",
               "add_memories", "list_categories", "initialize_memory"}
_FULL_LIST = {"jsonrpc": "2.0", "id": 7, "result": {"tools": _UPSTREAM_TOOLS}}
_TOOLS_LIST = json.dumps({"jsonrpc": "2.0", "id": 7, "method": "tools/list"})
_BLOCKED_NAMES = (b"delete_memory", b"ask_memories", b"get_core_memories")


def _call(name, arguments, rpc_id=1):
    return json.dumps({"jsonrpc": "2.0", "id": rpc_id, "method": "tools/call",
                       "params": {"name": name, "arguments": arguments}})


@pytest.fixture
def upstream(monkeypatch):
    """A stub upstream. It records (method, body); set `.reply` to a callable
    returning the httpx.Response to answer with."""
    class Stub(list):
        reply = staticmethod(lambda req: httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": {}}))

    seen = Stub()

    def handler(request: httpx.Request):
        seen.append((request.method, request.content))
        return seen.reply(request)

    real = httpx.AsyncClient

    def factory(*a, **kw):
        kw["transport"] = httpx.MockTransport(handler)
        return real(*a, **kw)

    monkeypatch.setattr(gw.httpx, "AsyncClient", factory)
    return seen


def _sent(upstream):
    (_, body), = upstream
    return json.loads(body)


# --- 1. request-size cap ----------------------------------------------------

def test_default_cap_is_4_mib():
    assert gw.MAX_BODY_BYTES == CAP


def test_body_over_cap_is_refused_413_never_forwarded(upstream):
    body = _call("search_memories", {"query": "x" * CAP}).encode()
    assert len(body) > CAP
    r = TestClient(gw.app).post("/mcp", content=body, headers=AUTH)
    assert r.status_code == 413, (r.status_code, r.text[:200])
    assert r.json()["error"]["code"] == -32600
    assert upstream == []


def test_chunked_body_over_cap_is_refused_413_never_forwarded(upstream):
    def chunks():
        yield b'{"jsonrpc":"2.0","id":1,"method":"ping","pad":"'
        for _ in range(5):
            yield b"x" * (1024 * 1024)
        yield b'"}'
    r = TestClient(gw.app).post("/mcp", content=chunks(), headers=AUTH)
    assert r.status_code == 413, (r.status_code, r.text[:200])
    assert upstream == []


def test_valid_body_at_the_cap_is_forwarded(upstream):
    head = _call("search_memories", {"query": ""})
    pad = CAP - len(head.encode())
    body = _call("search_memories", {"query": "x" * pad}).encode()
    assert len(body) == CAP
    r = TestClient(gw.app).post("/mcp", content=body, headers=AUTH)
    assert r.status_code == 200
    assert len(upstream) == 1


def test_cap_is_configurable(monkeypatch):
    monkeypatch.setenv("GATEWAY_MAX_BODY_BYTES", "100")
    small = _load("mnemory_gateway_app_smallcap")
    assert small.MAX_BODY_BYTES == 100
    r = TestClient(small.app).post(
        "/mcp", content=_call("search_memories", {"query": "x" * 200}).encode(),
        headers=AUTH)
    assert r.status_code == 413


@pytest.mark.parametrize("value", ["0", "-1", "lots"])
def test_bad_cap_value_stops_the_gateway_at_start(monkeypatch, value):
    monkeypatch.setenv("GATEWAY_MAX_BODY_BYTES", value)
    with pytest.raises(ValueError):
        _load("mnemory_gateway_app_badcap")


def test_ordinary_unauthenticated_oversized_body_is_401(upstream):
    r = TestClient(gw.app).post("/mcp", content=b"x" * (CAP + 10),
                                headers={"authorization": "Bearer wrong"})
    assert r.status_code == 401
    assert upstream == []


# --- 2. numbers that overflow to infinity -------------------------------------

@pytest.mark.parametrize("num", ["1e400", "-1e400", "1E999", "123456789e400"])
def test_overflowing_number_in_body_is_refused(upstream, num):
    body = ('{"jsonrpc":"2.0","id":1,"method":"tools/call","params":'
            '{"name":"search_memories","arguments":{"query":"q","limit":' + num + '}}}')
    r = TestClient(gw.app).post("/mcp", content=body.encode(), headers=AUTH)
    assert r.status_code == 400, (r.status_code, r.text[:200])
    assert r.json()["error"]["code"] == -32700
    assert upstream == []


def test_overflowing_number_in_string_encoded_policed_arg_is_refused(upstream):
    body = _call("add_memories", {"memories": '[{"content": "c", "n": 1e400}]'})
    r = TestClient(gw.app).post("/mcp", content=body.encode(), headers=AUTH)
    assert r.json()["error"]["code"] == -32602
    assert upstream == []


def test_valid_finite_numbers_are_forwarded_as_valid_json(upstream):
    body = ('{"jsonrpc":"2.0","id":1,"method":"tools/call","params":'
            '{"name":"search_memories","arguments":{"query":"q","limit":5,'
            '"threshold":0.25,"big":1e300,"tiny":1e-400}}}')
    r = TestClient(gw.app).post("/mcp", content=body.encode(), headers=AUTH)
    assert r.status_code == 200
    (_, raw), = upstream
    assert b"Infinity" not in raw and b"NaN" not in raw
    args = json.loads(raw)["params"]["arguments"]
    assert (args["limit"], args["threshold"], args["big"]) == (5, 0.25, 1e300)


# --- 4. tools/list fails closed -------------------------------------------------

def _names(payload):
    return {t["name"] for t in payload["result"]["tools"]}


def _list(upstream, reply):
    upstream.reply = reply
    return TestClient(gw.app).post("/mcp", content=_TOOLS_LIST.encode(), headers=AUTH)


UNFILTERABLE_JSON = {
    "garbage": (200, "application/json", b"{not json"),
    "non-object-tool": (200, "application/json", json.dumps(
        {"jsonrpc": "2.0", "id": 7, "result": {"tools": ["x"] + _UPSTREAM_TOOLS}}).encode()),
    "tools-not-a-list": (200, "application/json", json.dumps(
        {"jsonrpc": "2.0", "id": 7, "result": {"tools": {t["name"]: t for t in _UPSTREAM_TOOLS}}}
    ).encode()),
    "result-not-object": (200, "application/json", json.dumps(
        {"jsonrpc": "2.0", "id": 7, "result": [_UPSTREAM_TOOLS]}).encode()),
    "full-list-as-text-plain": (200, "text/plain", json.dumps(_FULL_LIST).encode()),
    "full-list-no-content-type": (200, "", json.dumps(_FULL_LIST).encode()),
    "nan-in-reply": (200, "application/json",
                     json.dumps(_FULL_LIST).encode()[:-1] + b', "x": NaN}'),
}


@pytest.mark.parametrize("shape", sorted(UNFILTERABLE_JSON))
def test_unfilterable_tools_list_reply_advertises_nothing_blocked(upstream, shape):
    status, ct, content = UNFILTERABLE_JSON[shape]
    headers = {"content-type": ct} if ct else {}
    r = _list(upstream, lambda req: httpx.Response(status, content=content, headers=headers))
    assert r.status_code == 200, (r.status_code, r.text[:200])
    names = _names(r.json())
    assert names <= _ADVERTISED, f"{shape}: advertised {sorted(names - _ADVERTISED)}"
    for blocked in _BLOCKED_NAMES:
        assert blocked not in r.content, (shape, blocked)


def test_unfilterable_error_status_reply_is_502_and_advertises_nothing(upstream):
    r = _list(upstream, lambda req: httpx.Response(
        500, content=json.dumps(_UPSTREAM_TOOLS).encode(),
        headers={"content-type": "text/html"}))
    assert r.status_code == 502
    assert r.json()["error"]["code"] == -32603
    assert b"delete_memory" not in r.content


def test_unparseable_sse_data_line_advertises_nothing(upstream):
    sse = ("event: message\n"
           "data: {broken " + json.dumps(_UPSTREAM_TOOLS) + "\n\n").encode()
    r = _list(upstream, lambda req: httpx.Response(
        200, content=sse, headers={"content-type": "text/event-stream"}))
    assert b"delete_memory" not in r.content
    data = [ln for ln in r.text.splitlines() if ln.startswith("data:")]
    assert len(data) == 1 and _names(json.loads(data[0][5:])) == set()


def test_sse_non_object_tool_advertises_only_allowed(upstream):
    payload = {"jsonrpc": "2.0", "id": 7, "result": {"tools": [1] + _UPSTREAM_TOOLS}}
    sse = ("event: message\ndata: " + json.dumps(payload) + "\n\n").encode()
    r = _list(upstream, lambda req: httpx.Response(
        200, content=sse, headers={"content-type": "text/event-stream"}))
    assert b"delete_memory" not in r.content


# --- ordinary traffic, unchanged -------------------------------------------------

def test_ordinary_json_tools_list_is_filtered_to_the_allow_list(upstream):
    r = _list(upstream, lambda req: httpx.Response(200, json=_FULL_LIST))
    assert r.status_code == 200
    assert _names(r.json()) == _ADVERTISED
    assert r.json()["id"] == 7


def test_ordinary_sse_tools_list_is_filtered_and_keeps_its_framing(upstream):
    sse = ("event: message\ndata: " + json.dumps(_FULL_LIST) + "\n\n").encode()
    r = _list(upstream, lambda req: httpx.Response(
        200, content=sse, headers={"content-type": "text/event-stream"}))
    assert r.headers["content-type"].startswith("text/event-stream")
    lines = r.text.splitlines()
    assert lines[0] == "event: message"
    data = [ln for ln in lines if ln.startswith("data:")]
    assert _names(json.loads(data[0][5:])) == _ADVERTISED


def test_ordinary_sse_notification_before_the_reply_passes(upstream):
    note = {"jsonrpc": "2.0", "method": "notifications/message",
            "params": {"level": "info", "data": "hi"}}
    sse = ("event: message\ndata: " + json.dumps(note) + "\n\n"
           "event: message\ndata: " + json.dumps(_FULL_LIST) + "\n\n").encode()
    r = _list(upstream, lambda req: httpx.Response(
        200, content=sse, headers={"content-type": "text/event-stream"}))
    data = [json.loads(ln[5:]) for ln in r.text.splitlines() if ln.startswith("data:")]
    assert data[0] == note
    assert _names(data[1]) == _ADVERTISED


def test_ordinary_tools_list_error_reply_passes_through(upstream):
    err = {"jsonrpc": "2.0", "id": 7, "error": {"code": -32000, "message": "no session"}}
    r = _list(upstream, lambda req: httpx.Response(400, json=err))
    assert r.status_code == 400
    assert r.json() == err


def test_ordinary_tools_call_is_forwarded_with_policy_applied(upstream):
    r = TestClient(gw.app).post("/mcp", content=_call(
        "list_memories", {"user_id": "u", "labels": {"share": "local"}}).encode(),
        headers=AUTH)
    assert r.status_code == 200
    assert _sent(upstream)["params"]["arguments"] == {"labels": {"share": "cloud"}}


def test_ordinary_write_is_stamped_as_before(upstream):
    TestClient(gw.app).post("/mcp", content=_call(
        "add_memory", {"content": "c", "categories": ["personal", "work"]}).encode(),
        headers=AUTH)
    assert _sent(upstream)["params"]["arguments"] == {
        "content": "c", "labels": {"origin": "cloud", "share": "cloud"},
        "categories": ["work"]}


# --- attempt-1 findings: malformed tool ENTRIES, and three unpinned claims ------

_BAD_ENTRIES = [
    {"name": ["delete_memory"]},            # unhashable name (list)   - X1
    {"name": {"n": "delete_memory"}},       # unhashable name (object) - X1
    {"name": 7},                          # hashable, not a string
    {"name": None},
    {"description": "no name at all"},
]


def _malformed_entries_payload():
    return {"jsonrpc": "2.0", "id": 7,
            "result": {"tools": _BAD_ENTRIES + _UPSTREAM_TOOLS}}


def test_malformed_tool_entries_json_are_dropped_not_500(upstream):
    r = _list(upstream, lambda req: httpx.Response(200, json=_malformed_entries_payload()))
    assert r.status_code == 200, (r.status_code, r.text[:200])
    assert _names(r.json()) == _ADVERTISED
    assert all(isinstance(t["name"], str) for t in r.json()["result"]["tools"])
    assert b"delete_memory" not in r.content


def test_malformed_tool_entries_sse_are_dropped_not_500(upstream):
    sse = ("event: message\ndata: " + json.dumps(_malformed_entries_payload())
           + "\n\n").encode()
    r = _list(upstream, lambda req: httpx.Response(
        200, content=sse, headers={"content-type": "text/event-stream"}))
    assert r.status_code == 200, (r.status_code, r.text[:200])
    data = [ln for ln in r.text.splitlines() if ln.startswith("data:")]
    assert _names(json.loads(data[0][5:])) == _ADVERTISED
    assert b"delete_memory" not in r.content


def test_any_exception_while_filtering_fails_closed(upstream, monkeypatch):
    # The handler, not only _filter_tools_list, is total: an unexpected error
    # inside the filter still advertises nothing (JSON and SSE paths).
    def boom(_payload):
        raise TypeError("unexpected")
    monkeypatch.setattr(gw, "_filter_tools_list", boom)
    r = _list(upstream, lambda req: httpx.Response(200, json=_FULL_LIST))
    assert r.status_code == 200 and _names(r.json()) == set()
    sse = ("event: message\ndata: " + json.dumps(_FULL_LIST) + "\n\n").encode()
    r = _list(upstream, lambda req: httpx.Response(
        200, content=sse, headers={"content-type": "text/event-stream"}))
    data = [ln for ln in r.text.splitlines() if ln.startswith("data:")]
    assert _names(json.loads(data[0][5:])) == set()


def test_declared_over_cap_length_is_refused_before_reading(monkeypatch):
    # M04: the declared Content-Length alone refuses; not one byte is read.
    import asyncio
    read = []

    class FakeRequest:
        headers = {"content-length": str(gw.MAX_BODY_BYTES + 1)}

        async def stream(self):
            read.append(True)
            yield b"{}"

    with pytest.raises(gw._TooLarge):
        asyncio.run(gw._read_capped(FakeRequest()))
    assert read == []


def test_declared_under_cap_length_is_read(monkeypatch):
    import asyncio

    class FakeRequest:
        headers = {"content-length": "2"}

        async def stream(self):
            yield b"{}"

    assert asyncio.run(gw._read_capped(FakeRequest())) == b"{}"


def test_reply_with_neither_result_nor_error_advertises_nothing(upstream):
    # M18: a reply carrying no result/error/method is unfilterable - even when
    # it smuggles a tool list somewhere other than result.tools.
    reply = {"jsonrpc": "2.0", "id": 7, "tools": _UPSTREAM_TOOLS}
    r = _list(upstream, lambda req: httpx.Response(200, json=reply))
    assert r.status_code == 200
    assert _names(r.json()) == set()
    assert b"delete_memory" not in r.content
    with pytest.raises(gw._Unfilterable):
        gw._filter_tools_list({"jsonrpc": "2.0", "id": 7})


@pytest.mark.parametrize("token", ["NaN", "Infinity", "1e400"])
def test_sse_data_line_is_parsed_strictly(upstream, token):
    # M20: the SSE branch uses the strict parser - a data line a lenient
    # json.loads would accept (and then filter) is replaced by the empty list.
    line = json.dumps(_FULL_LIST)[:-1] + ', "x": ' + token + "}"
    sse = ("event: message\ndata: " + line + "\n\n").encode()
    r = _list(upstream, lambda req: httpx.Response(
        200, content=sse, headers={"content-type": "text/event-stream"}))
    data = [ln for ln in r.text.splitlines() if ln.startswith("data:")]
    assert _names(json.loads(data[0][5:])) == set()


# --- attempt-2 findings X2/X3: parses, but cannot be re-serialised as UTF-8 ------
#
# A lone surrogate ("\ud800", sent as the JSON escape) parses fine, but starlette's
# JSONResponse renders with ensure_ascii=False and then encodes UTF-8 - which
# raises. Every reply the gateway builds must survive it, on every path.

_SURR = "\ud800"


def _json_reply(payload, status=200):
    # json.dumps default (ensure_ascii=True) - the upstream sends the escape.
    return lambda req: httpx.Response(status, content=json.dumps(payload).encode(),
                                      headers={"content-type": "application/json"})


def _surrogate_list():
    tools = [dict(t) for t in _UPSTREAM_TOOLS]
    for t in tools:
        t["description"] = "bad " + _SURR
    return {"jsonrpc": "2.0", "id": 7, "result": {"tools": tools}}


def test_surrogate_in_kept_tool_description_json_path(upstream):
    r = _list(upstream, _json_reply(_surrogate_list()))
    assert r.status_code == 200, (r.status_code, r.text[:200])
    body = json.loads(r.content)
    assert _names(body) == _ADVERTISED
    assert all(t["description"] == "bad " + _SURR for t in body["result"]["tools"])


def test_surrogate_in_kept_tool_description_sse_path(upstream):
    sse = ("event: message\ndata: " + json.dumps(_surrogate_list()) + "\n\n").encode()
    r = _list(upstream, lambda req: httpx.Response(
        200, content=sse, headers={"content-type": "text/event-stream"}))
    assert r.status_code == 200, (r.status_code, r.text[:200])
    data = [ln for ln in r.text.splitlines() if ln.startswith("data:")]
    assert _names(json.loads(data[0][5:])) == _ADVERTISED


def test_invalid_utf8_on_an_sse_line_is_not_a_500(upstream):
    # CESU-8 surrogate bytes on a non-data line: the text decode replaces them.
    sse = (b"event: message\n: \xed\xa0\x80\ndata: " + json.dumps(_FULL_LIST).encode() + b"\n\n")
    r = _list(upstream, lambda req: httpx.Response(
        200, content=sse, headers={"content-type": "text/event-stream"}))
    assert r.status_code == 200
    data = [ln for ln in r.text.splitlines() if ln.startswith("data:")]
    assert _names(json.loads(data[0][5:])) == _ADVERTISED


def _list_with_id(upstream, rid, reply):
    upstream.reply = reply
    body = json.dumps({"jsonrpc": "2.0", "id": rid, "method": "tools/list"}).encode()
    return TestClient(gw.app).post("/mcp", content=body, headers=AUTH)


def test_surrogate_request_id_on_the_empty_list_reply(upstream):
    r = _list_with_id(upstream, _SURR, lambda req: httpx.Response(
        200, content=b"{not json", headers={"content-type": "application/json"}))
    assert r.status_code == 200, (r.status_code, r.text[:200])
    body = json.loads(r.content)
    assert body["id"] == _SURR and body["result"]["tools"] == []


def test_surrogate_request_id_on_the_502_reply(upstream):
    r = _list_with_id(upstream, _SURR, lambda req: httpx.Response(
        500, content=b"<html>", headers={"content-type": "text/html"}))
    assert r.status_code == 502
    assert json.loads(r.content)["id"] == _SURR


def test_surrogate_request_id_on_the_filtered_reply(upstream):
    reply = dict(_FULL_LIST, id=_SURR)
    r = _list_with_id(upstream, _SURR, _json_reply(reply))
    assert r.status_code == 200, (r.status_code, r.text[:200])
    body = json.loads(r.content)
    assert body["id"] == _SURR and _names(body) == _ADVERTISED


def test_surrogate_in_a_blocked_tool_name_echoed_in_the_error(upstream):
    body = json.dumps({"jsonrpc": "2.0", "id": _SURR, "method": "tools/call",
                       "params": {"name": "x" + _SURR, "arguments": {}}}).encode()
    r = TestClient(gw.app).post("/mcp", content=body, headers=AUTH)
    assert r.status_code == 200, (r.status_code, r.text[:200])
    err = json.loads(r.content)
    assert err["error"]["code"] == -32601 and err["id"] == _SURR
    assert upstream == []


def test_valid_surrogate_argument_is_forwarded(upstream):
    r = TestClient(gw.app).post("/mcp", content=_call("search_memories", {"query": _SURR}).encode(),
                                headers=AUTH)
    assert r.status_code == 200
    assert _sent(upstream)["params"]["arguments"]["query"] == _SURR


def test_valid_non_ascii_reply_bytes_unchanged(upstream):
    # Replies starlette COULD render keep its exact bytes: compact separators,
    # raw UTF-8 (not \u-escaped), same content type.
    reply = dict(_FULL_LIST)
    reply["result"] = {"tools": [dict(t, description="caf\u00e9") for t in _UPSTREAM_TOOLS]}
    r = _list(upstream, _json_reply(reply))
    assert r.headers["content-type"] == "application/json"
    assert "caf\u00e9".encode("utf-8") in r.content and b"\\u00e9" not in r.content
    assert b'"jsonrpc":"2.0"' in r.content


# --- attempt-3 findings: X4 (declared upstream charset on SSE) and mutant N06 ------

_CHARSET_PROBES = {
    # (declared charset, extra non-data line bytes) - each decodes to a lone
    # surrogate under the DECLARED charset (httpx upstream.text).
    "utf7-comment": ("utf-7", b": x+2AA-y"),
    "utf7-event": ("utf-7", b"event: +2AA-"),
    "unicode-escape-comment": ("unicode_escape", b": \\ud800"),
    "raw-unicode-escape-id": ("raw_unicode_escape", b"id: \\udfff"),
}


@pytest.mark.parametrize("probe", sorted(_CHARSET_PROBES))
def test_sse_declared_charset_cannot_make_a_500(upstream, probe):
    charset, line = _CHARSET_PROBES[probe]
    sse = line + b"\ndata: " + json.dumps(_FULL_LIST).encode() + b"\n\n"
    r = _list(upstream, lambda req: httpx.Response(
        200, content=sse, headers={"content-type": f"text/event-stream; charset={charset}"}))
    assert r.status_code == 200, (probe, r.status_code, r.text[:200])
    text = r.content.decode("utf-8")            # strictly valid UTF-8
    data = [ln for ln in text.splitlines() if ln.startswith("data:")]
    if probe == "utf7-event":
        # attempt 6: the event's type is "+2AA-", not "message", so the whole
        # event is dropped (a type is never echoed) - nothing is advertised.
        assert data == [] and "event:" not in text
        return
    assert _names(json.loads(data[0][5:])) == _ADVERTISED


def test_json_declared_utf7_surrogate_in_kept_field_is_served(upstream):
    # The JSON branch decodes the reply as UTF-8 itself and ignores the declared
    # charset (a utf-7 reading would yield a lone surrogate): 200, filtered,
    # valid UTF-8, and the description arrives as the bytes the upstream sent.
    tools = [dict(t, description="d") for t in _UPSTREAM_TOOLS]
    raw = json.dumps({"jsonrpc": "2.0", "id": 7, "result": {"tools": tools}})
    raw = raw.replace('"description": "d"', '"description": "+2AA-"').encode()
    r = _list(upstream, lambda req: httpx.Response(
        200, content=raw, headers={"content-type": "application/json; charset=utf-7"}))
    assert r.status_code == 200, (r.status_code, r.text[:200])
    body = json.loads(r.content.decode("utf-8"))
    assert {t["description"] for t in body["result"]["tools"]} == {"+2AA-"}
    assert _names(body) == _ADVERTISED


def test_fallback_output_is_strictly_valid_utf8():
    # N06: the ensure_ascii fallback must emit valid UTF-8 (in fact ASCII), not
    # e.g. surrogatepass bytes that json.loads would still accept.
    resp = gw._json_response({"id": "\ud800", "d": "caf\u00e9 \udfff"}, status_code=502)
    body = bytes(resp.body)
    body.decode("utf-8")                         # strict: raises on invalid UTF-8
    assert body.isascii()
    assert json.loads(body) == {"id": "\ud800", "d": "caf\u00e9 \udfff"}
    assert resp.status_code == 502
    assert resp.headers["content-type"] == "application/json"


def test_surrogate_replies_through_the_handler_are_strict_utf8(upstream):
    r = _list_with_id(upstream, "\ud800", _json_reply(_surrogate_list()))
    assert r.status_code == 200
    r.content.decode("utf-8")


# --- attempt-4 findings: SSE framing (X5 BOM, X6 Unicode line breaks) and the
# --- decode itself (survivors Y2 Y4-Y10) ------------------------------------------

import re as _re  # noqa: E402


def _client_events(raw: bytes):
    """What an EventSource-conformant client dispatches: UTF-8 decode with
    replacement, one leading BOM stripped, lines on CR/LF only, data lines
    joined with LF, an event dispatched at a blank line."""
    t = raw.decode("utf-8", "replace")
    if t.startswith("\ufeff"):
        t = t[1:]
    events, data = [], []
    for line in _re.split(r"\r\n|\r|\n", t):
        if line == "":
            if data:
                events.append("\n".join(data))
            data = []
            continue
        if line.startswith(":"):
            continue
        f, _, v = line.partition(":")
        if v.startswith(" "):
            v = v[1:]
        if f == "data":
            data.append(v)
    return events


def _client_advertised(raw: bytes):
    names = set()
    for d in _client_events(raw):
        try:
            for t in json.loads(d)["result"]["tools"]:
                names.add(t.get("name"))
        except Exception:
            pass
    return names


def _sse(upstream, raw, ct="text/event-stream"):
    return _list(upstream, lambda req: httpx.Response(200, content=raw, headers={"content-type": ct}))


_J = json.dumps(_FULL_LIST).encode()


def _assert_sse_closed(r, expect):
    assert r.status_code == 200, (r.status_code, r.text[:200])
    body = r.content
    body.decode("utf-8")
    assert _client_advertised(body) == expect, _client_advertised(body)
    assert b"delete_memory" not in body


@pytest.mark.parametrize("raw", [
    b"\xef\xbb\xbfdata: " + _J + b"\n\n",
    b"\xef\xbb\xbfevent: message\ndata: " + _J + b"\n\n",
], ids=["bom-data-first", "bom-event-first"])
def test_sse_leading_bom_is_stripped_and_the_list_filtered(upstream, raw):
    # X5: a kept BOM hid the first line from the gateway; a client strips it.
    _assert_sse_closed(_sse(upstream, raw), _ADVERTISED)


def test_sse_only_one_bom_is_stripped(upstream):
    # The spec strips ONE; after it, "\ufeffdata" is an unknown field name, so a
    # client dispatches nothing - the gateway must not "repair" it either.
    r = _sse(upstream, b"\xef\xbb\xbf\xef\xbb\xbfdata: " + _J + b"\n\n")
    _assert_sse_closed(r, set())
    assert b"data:" not in r.content


@pytest.mark.parametrize("sep", ["\u2028", "\u2029", "\u0085"], ids=["u2028", "u2029", "u0085"])
def test_sse_unicode_line_separator_inside_a_valid_string(upstream, sep):
    # X6: JSON allows these raw in a string; they are NOT SSE line breaks.
    tools = [dict(t, description="a" + sep + "b") for t in _UPSTREAM_TOOLS]
    payload = {"jsonrpc": "2.0", "id": 7, "result": {"tools": tools}}
    raw = b"data: " + json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n\n"
    r = _sse(upstream, raw)
    _assert_sse_closed(r, _ADVERTISED)
    ev = json.loads(_client_events(r.content)[0])
    assert {t["description"] for t in ev["result"]["tools"]} == {"a" + sep + "b"}
    assert sep.encode("utf-8") not in r.content  # re-serialised ASCII, never copied


@pytest.mark.parametrize("eol", [b"\r", b"\r\n", b"\n"], ids=["cr", "crlf", "lf"])
def test_sse_cr_crlf_lf_line_endings(upstream, eol):
    raw = b"event: message" + eol + b"data: " + _J + eol + eol
    r = _sse(upstream, raw)
    _assert_sse_closed(r, _ADVERTISED)
    assert r.content.startswith(b"event: message\ndata: ")


def test_sse_multi_line_data_event_is_joined_then_filtered(upstream):
    # A client joins data lines with LF; JSON whitespace allows the break.
    text = json.dumps(_FULL_LIST, indent=1).encode()
    raw = b"event: message\n" + b"".join(b"data: " + ln + b"\n" for ln in text.split(b"\n")) + b"\n"
    r = _sse(upstream, raw)
    _assert_sse_closed(r, _ADVERTISED)
    assert r.content.count(b"data:") == 1


def test_sse_comments_and_unknown_fields_are_not_copied(upstream):
    raw = (b": delete_memory in a comment\n"
           b"foo: delete_memory in an unknown field\n"
           b"event: message\ndata: " + _J + b"\n\n")
    r = _sse(upstream, raw)
    _assert_sse_closed(r, _ADVERTISED)
    assert b"comment" not in r.content and b"foo" not in r.content


def test_sse_trailing_event_without_a_blank_line(upstream):
    r = _sse(upstream, b"event: message\ndata: " + _J)
    _assert_sse_closed(r, _ADVERTISED)
    assert r.content.endswith(b"\n\n")


def test_sse_unterminated_garbage_is_not_copied(upstream):
    r = _sse(upstream, b"event: message\ndata: " + _J + b"\n\n" + b"delete_memory tail")
    _assert_sse_closed(r, _ADVERTISED)


def test_sse_content_type_is_matched_case_insensitively(upstream):
    r = _sse(upstream, b"data: " + _J + b"\n\n", ct="Text/Event-Stream")
    assert r.headers["content-type"].startswith("text/event-stream")
    _assert_sse_closed(r, _ADVERTISED)


def test_valid_sse_event_id_and_data_keep_their_framing(upstream):
    raw = b"event: message\nid: 1\ndata: " + _J + b"\n\n"
    r = _sse(upstream, raw)
    filtered = json.loads(_J)
    filtered["result"]["tools"] = [t for t in filtered["result"]["tools"]
                                   if t["name"] in _ADVERTISED]
    assert r.content == b"event: message\nid: 1\ndata: " + json.dumps(filtered).encode() + b"\n\n"


# Raw (unescaped) UTF-8 non-ASCII must come through exactly - pins the decode
# (a latin-1 / cp1252 decode turns it into mojibake: survivors Y4, Y5, Y10).
_RAW_DESC = "caf\u00e9 \u65e5\u672c \U0001f600 \u00a0"


def _raw_utf8_payload():
    tools = [dict(t, description=_RAW_DESC) for t in _UPSTREAM_TOOLS]
    return json.dumps({"jsonrpc": "2.0", "id": 7, "result": {"tools": tools}},
                      ensure_ascii=False).encode("utf-8")


def test_valid_raw_utf8_json_reply_is_decoded_as_utf8(upstream):
    raw = _raw_utf8_payload()
    assert not raw.isascii()
    r = _list(upstream, lambda req: httpx.Response(
        200, content=raw, headers={"content-type": "application/json"}))
    body = json.loads(r.content.decode("utf-8"))
    assert _names(body) == _ADVERTISED
    assert {t["description"] for t in body["result"]["tools"]} == {_RAW_DESC}


def test_valid_raw_utf8_sse_reply_is_decoded_as_utf8(upstream):
    r = _sse(upstream, b"event: message\ndata: " + _raw_utf8_payload() + b"\n\n")
    ev = json.loads(_client_events(r.content)[0])
    assert {t["description"] for t in ev["result"]["tools"]} == {_RAW_DESC}
    assert {t["name"] for t in ev["result"]["tools"]} == _ADVERTISED


# An invalid UTF-8 byte inside a kept string is REPLACED (U+FFFD) and the list
# still served - neither dropped (errors="ignore": Y7), nor escaped into
# invalid JSON (backslashreplace: Y8), nor fatal (strict: Y1, Y2).
def _bad_byte_payload():
    return _J.replace(b'"inputSchema": {}}', b'"inputSchema": {}, "description": "x\xffy"}')


def test_invalid_utf8_byte_in_a_json_reply_is_replaced(upstream):
    r = _list(upstream, lambda req: httpx.Response(
        200, content=_bad_byte_payload(), headers={"content-type": "application/json"}))
    body = json.loads(r.content.decode("utf-8"))
    assert _names(body) == _ADVERTISED
    assert {t["description"] for t in body["result"]["tools"]} == {"x\ufffdy"}


def test_invalid_utf8_byte_in_an_sse_reply_is_replaced(upstream):
    r = _sse(upstream, b"event: message\ndata: " + _bad_byte_payload() + b"\n\n")
    ev = json.loads(_client_events(r.content)[0])
    assert {t["name"] for t in ev["result"]["tools"]} == _ADVERTISED
    assert {t["description"] for t in ev["result"]["tools"]} == {"x\ufffdy"}


# Behaviour change, recorded (findings F8): a JSON reply that is not plain UTF-8
# - BOM-prefixed, or UTF-16/32 - is unfilterable and advertises NOTHING. At base
# json.loads(bytes) auto-detected those encodings.
@pytest.mark.parametrize("raw", [b"\xef\xbb\xbf" + _J, _J.decode().encode("utf-16"),
                                 _J.decode().encode("utf-32")], ids=["bom", "utf16", "utf32"])
def test_non_utf8_json_reply_advertises_nothing(upstream, raw):
    r = _list(upstream, lambda req: httpx.Response(
        200, content=raw, headers={"content-type": "application/json"}))
    assert r.status_code == 200
    assert _names(json.loads(r.content)) == set()


# --- attempt-5 findings: X7 (event/id content), X8 (quadratic join), priming ------


def _splitlines_client_advertised(raw: bytes):
    """A str.splitlines()-based SSE client, as httpx-sse 0.4.0/0.4.1 (httpx's
    LineDecoder splits like splitlines: VT, FF, FS/GS/RS, U+0085, U+2028,
    U+2029 too) with the mcp 1.x rule: `message` events with non-empty data."""
    names, etype, data = [], "message", []
    for line in raw.decode("utf-8", "replace").lstrip("\ufeff").splitlines() + [""]:
        if line == "":
            if data and etype == "message" and "\n".join(data):
                try:
                    names += [t.get("name") for t in json.loads("\n".join(data))["result"]["tools"]]
                except Exception:
                    pass
            etype, data = "message", []
            continue
        if line.startswith(":"):
            continue
        f, _, v = line.partition(":")
        v = v[1:] if v.startswith(" ") else v
        if f == "data":
            data.append(v)
        elif f == "event":
            etype = v
    return set(names)


_SPLITTERS = {"u2028": "\u2028", "u2029": "\u2029", "u0085": "\u0085", "vt": "\x0b",
              "ff": "\x0c", "fs": "\x1c", "gs": "\x1d", "rs": "\x1e"}
_FULL_TXT = json.dumps(_FULL_LIST)


@pytest.mark.parametrize("field", ["id", "event"])
@pytest.mark.parametrize("sep", sorted(_SPLITTERS))
def test_event_and_id_values_cannot_inject_a_line(upstream, field, sep):
    # X7: `id: x<sep>data: {full list}<sep>` - a splitlines client read the
    # injected data line when the value was copied verbatim.
    ch = _SPLITTERS[sep]
    raw = (f"{field}: x{ch}data: {_FULL_TXT}{ch}\n" f"data: {_FULL_TXT}\n\n").encode("utf-8")
    r = _sse(upstream, raw)
    assert r.status_code == 200
    assert b"delete_memory" not in r.content
    assert "delete_memory" not in _splitlines_client_advertised(r.content)
    assert "delete_memory" not in _client_advertised(r.content)
    assert ch.encode("utf-8") not in r.content
    if field == "id":   # unsafe id dropped, the data still served filtered
        assert _client_advertised(r.content) == _ADVERTISED
        assert b"id:" not in r.content
    else:               # a non-"message" type: the whole event is dropped
        assert r.content == b""


@pytest.mark.parametrize("field", ["id", "event"])
def test_a_full_list_as_the_event_or_id_value_is_never_copied(upstream, field):
    r = _sse(upstream, f"{field}: {_FULL_TXT}\ndata: {_FULL_TXT}\n\n".encode())
    assert b"delete_memory" not in r.content


@pytest.mark.parametrize("bad", ["a\x00b", "a\x01b", "a\x7fb", "a b", "caf\u00e9", "x" * 129, ""],
                         ids=["nul", "soh", "del", "space", "non-ascii", "too-long", "empty"])
def test_unsafe_ids_are_dropped(upstream, bad):
    r = _sse(upstream, f"id: {bad}\nevent: message\ndata: {_FULL_TXT}\n\n".encode("utf-8"))
    assert b"id:" not in r.content
    assert r.content.startswith(b"event: message\ndata: ")


def test_a_safe_id_is_kept_exactly_and_the_last_one_wins(upstream):
    r = _sse(upstream, f"id: first\nid: a-1_B:9.z~\ndata: {_FULL_TXT}\n\n".encode())
    assert r.content.startswith(b"id: a-1_B:9.z~\ndata: ")


@pytest.mark.parametrize("value,kept", [("3000", True), ("30x", False), ("\u0663", False),
                                        ("12345678901", False), ("", False), (" 5", False)])
def test_retry_only_ascii_digits(upstream, value, kept):
    r = _sse(upstream, f"retry: {value}\ndata: {_FULL_TXT}\n\n".encode("utf-8"))
    assert (b"retry: 3000\n" in r.content) is kept
    if not kept:
        assert b"retry" not in r.content


def test_event_type_needs_exactly_one_optional_space(upstream):
    # "event:  message" has type " message" (one space removed), not
    # "message": a client ignores it, and the gateway drops it whole.
    r = _sse(upstream, f"event:  message\ndata: {_FULL_TXT}\n\n".encode())
    assert r.content == b""
    r = _sse(upstream, f"event:message\ndata: {_FULL_TXT}\n\n".encode())
    assert r.content.startswith(b"event: message\ndata: ")


def test_data_lines_are_joined_with_lf_not_concatenated(upstream):
    # A number split over two data lines is "1\n2" to a client: invalid JSON,
    # so the empty list - never the "12" a ""-join would make of it.
    head = json.dumps(_FULL_LIST)[:-1] + ', "n": 1'
    r = _sse(upstream, f"data: {head}\ndata: 2}}\n\n".encode())
    assert _client_advertised(r.content) == set()
    ev = json.loads(_client_events(r.content)[0])
    assert ev["result"]["tools"] == []


def test_data_join_is_linear(upstream):
    # X8: 80k data lines in one event (~8 MB) - was 315 s with a quadratic join.
    import time
    lines = ['{"jsonrpc": "2.0", "id": 7, "result": {"tools": [']
    lines += ['{"name": "x%06d", "description": "%s"},' % (i, "p" * 64) for i in range(80000)]
    lines += ['{"name": "delete_memory"}]}}']
    raw = ("".join("data: " + ln + "\n" for ln in lines) + "\n").encode()
    t0 = time.perf_counter()
    r = _sse(upstream, raw)
    took = time.perf_counter() - t0
    assert r.status_code == 200
    assert took < 10, f"{took:.1f}s"
    assert b"delete_memory" not in r.content


# P1 - an MCP resumability "priming" event (`id: n` + empty data) comes first.
@pytest.mark.parametrize("priming", [b"id: 1\ndata: \n\n", b"id: 1\ndata:\n\n", b"id: 1\ndata\n\n",
                                     b"id: 1\ndata:   \n\n"], ids=["space", "colon", "bare", "blank"])
def test_priming_event_then_the_real_result(upstream, priming):
    raw = priming + b"id: 2\nevent: message\ndata: " + _J + b"\n\n"
    r = _sse(upstream, raw)
    assert r.content.startswith(b"id: 1\ndata: \n\nevent: message\nid: 2\ndata: ")
    assert _client_advertised(r.content) == _ADVERTISED
    assert _splitlines_client_advertised(r.content) == _ADVERTISED
    assert b"delete_memory" not in r.content


def test_an_event_without_data_is_kept_without_data(upstream):
    r = _sse(upstream, b"id: 5\n\ndata: " + _J + b"\n\n")
    assert r.content.startswith(b"id: 5\n\ndata: ")
    assert _client_advertised(r.content) == _ADVERTISED


def test_a_response_to_another_request_is_dropped_not_rewritten(upstream):
    other = json.dumps({"jsonrpc": "2.0", "id": 99, "result": {"tools": _UPSTREAM_TOOLS}})
    raw = f"data: {other}\n\ndata: {_FULL_TXT}\n\n".encode()
    r = _sse(upstream, raw)
    assert r.content.count(b"data:") == 1
    assert b"99" not in r.content and b"delete_memory" not in r.content
    assert _client_advertised(r.content) == _ADVERTISED
