"""cf-gateways: the four hardening gaps the ac-gw-headers review left, Open Brain door.

1. A request body over GATEWAY_MAX_BODY_BYTES is refused with 413 and never
   forwarded (declared Content-Length, and a chunked body that declares none).
2. A JSON number that overflows a double (1e400) is refused, never re-emitted
   upstream as the non-JSON token Infinity.
3. metadata_extra.source (the row's provenance) cannot be set by the caller:
   the gateway removes it so openbrain-mcp's own per-tool constant is stored.
4. The tools/list filter fails CLOSED: an upstream reply it cannot filter
   advertises NOTHING instead of passing through unfiltered.

Plus the ordinary traffic that must behave exactly as before.

The upstream is a stub (httpx.MockTransport). Every test here fails against the
gateway at 0fb1c0c except the ones named test_ordinary_* / test_valid_*.

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

_APP = pathlib.Path(__file__).with_name("app.py")


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _APP)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gw = _load("openbrain_gateway_app_hardening")

AUTH = {"authorization": f"Bearer {GATEWAY_KEY}", "content-type": "application/json"}
CAP = 4 * 1024 * 1024

# Every tool openbrain-mcp registers that the default cloud profile must hide,
# next to the ones it allows.
_UPSTREAM_TOOLS = [{"name": n, "inputSchema": {}} for n in (
    "search", "fetch", "search_thoughts", "list_thoughts", "capture_thought",
    "ingest_url", "ingest_urls", "thought_stats", "create_thread",
    "agent_memory_recall", "get_suggestions")]
_ALLOWED = {"search", "fetch", "search_thoughts", "list_thoughts",
            "capture_thought", "ingest_url", "ingest_urls"}
_FULL_LIST = {"jsonrpc": "2.0", "id": 7, "result": {"tools": _UPSTREAM_TOOLS}}
_TOOLS_LIST = json.dumps({"jsonrpc": "2.0", "id": 7, "method": "tools/list"})


def _call(name, arguments, rpc_id=1):
    return json.dumps({"jsonrpc": "2.0", "id": rpc_id, "method": "tools/call",
                       "params": {"name": name, "arguments": arguments}})


@pytest.fixture
def upstream(monkeypatch):
    """A stub upstream. `seen` records (method, body); set `.reply` to a
    callable returning the httpx.Response to answer with."""
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
    body = _call("search_thoughts", {"query": "x" * CAP}).encode()
    assert len(body) > CAP
    r = TestClient(gw.app).post("/mcp", content=body, headers=AUTH)
    assert r.status_code == 413, (r.status_code, r.text[:200])
    assert r.json()["error"]["code"] == -32600
    assert upstream == []


def test_chunked_body_over_cap_is_refused_413_never_forwarded(upstream):
    # A generator body is sent chunked: no Content-Length to check up front.
    def chunks():
        yield b'{"jsonrpc":"2.0","id":1,"method":"ping","pad":"'
        for _ in range(5):
            yield b"x" * (1024 * 1024)
        yield b'"}'
    r = TestClient(gw.app).post("/mcp", content=chunks(), headers=AUTH)
    assert r.status_code == 413, (r.status_code, r.text[:200])
    assert upstream == []


def test_valid_body_at_the_cap_is_forwarded(upstream):
    head = _call("search_thoughts", {"query": ""})
    pad = CAP - len(head.encode())
    body = _call("search_thoughts", {"query": "x" * pad}).encode()
    assert len(body) == CAP
    r = TestClient(gw.app).post("/mcp", content=body, headers=AUTH)
    assert r.status_code == 200
    assert len(upstream) == 1


def test_cap_is_configurable(monkeypatch):
    monkeypatch.setenv("GATEWAY_MAX_BODY_BYTES", "100")
    small = _load("openbrain_gateway_app_smallcap")
    assert small.MAX_BODY_BYTES == 100
    r = TestClient(small.app).post(
        "/mcp", content=_call("search_thoughts", {"query": "x" * 200}).encode(),
        headers=AUTH)
    assert r.status_code == 413


@pytest.mark.parametrize("value", ["0", "-1", "lots"])
def test_bad_cap_value_stops_the_gateway_at_start(monkeypatch, value):
    monkeypatch.setenv("GATEWAY_MAX_BODY_BYTES", value)
    with pytest.raises(ValueError):
        _load("openbrain_gateway_app_badcap")


def test_ordinary_unauthenticated_oversized_body_is_401(upstream):
    r = TestClient(gw.app).post("/mcp", content=b"x" * (CAP + 10),
                                headers={"authorization": "Bearer wrong"})
    assert r.status_code == 401
    assert upstream == []


# --- 2. numbers that overflow to infinity -------------------------------------

@pytest.mark.parametrize("num", ["1e400", "-1e400", "1E999", "123456789e400"])
def test_overflowing_number_in_body_is_refused(upstream, num):
    body = ('{"jsonrpc":"2.0","id":1,"method":"tools/call","params":'
            '{"name":"search_thoughts","arguments":{"query":"q","limit":' + num + '}}}')
    r = TestClient(gw.app).post("/mcp", content=body.encode(), headers=AUTH)
    assert r.status_code == 400, (r.status_code, r.text[:200])
    assert r.json()["error"]["code"] == -32700
    assert upstream == []


def test_overflowing_number_in_string_encoded_policed_arg_is_refused(upstream):
    body = _call("search_thoughts", {"query": "q", "metadata_filter": '{"n": 1e400}'})
    r = TestClient(gw.app).post("/mcp", content=body.encode(), headers=AUTH)
    assert r.json()["error"]["code"] == -32602
    assert upstream == []


def test_valid_finite_numbers_are_forwarded_as_valid_json(upstream):
    body = ('{"jsonrpc":"2.0","id":1,"method":"tools/call","params":'
            '{"name":"search_thoughts","arguments":{"query":"q","limit":5,'
            '"threshold":0.25,"big":1e300,"tiny":1e-400}}}')
    r = TestClient(gw.app).post("/mcp", content=body.encode(), headers=AUTH)
    assert r.status_code == 200
    (_, raw), = upstream
    assert b"Infinity" not in raw and b"NaN" not in raw
    args = json.loads(raw)["params"]["arguments"]
    assert (args["limit"], args["threshold"], args["big"]) == (5, 0.25, 1e300)


# --- 3. metadata_extra.source is the server's, not the caller's -----------------

@pytest.mark.parametrize("tool,base", [
    ("capture_thought", {"content": "c"}),
    ("ingest_url", {"url": "https://example.org/"}),
    ("ingest_urls", {"urls": ["https://example.org/"]}),
])
def test_caller_source_is_removed_from_every_write(upstream, tool, base):
    args = dict(base, metadata_extra={"source": "gmail", "topic": "t"})
    TestClient(gw.app).post("/mcp", content=_call(tool, args).encode(), headers=AUTH)
    md = _sent(upstream)["params"]["arguments"]["metadata_extra"]
    assert "source" not in md, md
    assert md == {"topic": "t", "origin": "cloud", "share": "cloud"}


def test_caller_source_in_string_encoded_metadata_extra_is_removed(upstream):
    args = {"content": "c", "metadata_extra": '{"source": "deep-research"}'}
    TestClient(gw.app).post("/mcp", content=_call("capture_thought", args).encode(),
                            headers=AUTH)
    md = _sent(upstream)["params"]["arguments"]["metadata_extra"]
    assert md == {"origin": "cloud", "share": "cloud"}


def test_caller_source_inside_a_batch_is_removed(upstream):
    body = "[" + _call("capture_thought", {"content": "c",
                                           "metadata_extra": {"source": "gmail"}}) + "]"
    TestClient(gw.app).post("/mcp", content=body.encode(), headers=AUTH)
    (_, raw), = upstream
    assert "source" not in json.loads(raw)[0]["params"]["arguments"]["metadata_extra"]


def test_valid_write_without_source_is_forwarded_unchanged(upstream):
    TestClient(gw.app).post("/mcp", content=_call("capture_thought", {"content": "c"}).encode(),
                            headers=AUTH)
    assert _sent(upstream)["params"]["arguments"] == {
        "content": "c", "metadata_extra": {"origin": "cloud", "share": "cloud"}}


def test_valid_read_filter_may_still_name_source(upstream):
    # Filtering BY provenance narrows a read; only the write stamp is policed.
    TestClient(gw.app).post("/mcp", content=_call(
        "search_thoughts", {"query": "q", "metadata_filter": {"source": "mcp"}}).encode(),
        headers=AUTH)
    assert _sent(upstream)["params"]["arguments"]["metadata_filter"] == {
        "source": "mcp", "share": "cloud"}


# --- 4. tools/list fails closed -------------------------------------------------

def _names(payload):
    return {t["name"] for t in payload["result"]["tools"]}


def _list(upstream, reply):
    upstream.reply = reply
    return TestClient(gw.app).post("/mcp", content=_TOOLS_LIST.encode(), headers=AUTH)


UNFILTERABLE_JSON = {
    # application/json that is not JSON at all
    "garbage": (200, "application/json", b"{not json"),
    # a tool entry that is not an object (base: AttributeError -> passthrough)
    "non-object-tool": (200, "application/json", json.dumps(
        {"jsonrpc": "2.0", "id": 7, "result": {"tools": ["x"] + _UPSTREAM_TOOLS}}).encode()),
    # tools is not a list
    "tools-not-a-list": (200, "application/json", json.dumps(
        {"jsonrpc": "2.0", "id": 7, "result": {"tools": {t["name"]: t for t in _UPSTREAM_TOOLS}}}
    ).encode()),
    # result is not an object
    "result-not-object": (200, "application/json", json.dumps(
        {"jsonrpc": "2.0", "id": 7, "result": [_UPSTREAM_TOOLS]}).encode()),
    # a JSON body the upstream labelled with another content type
    "full-list-as-text-plain": (200, "text/plain", json.dumps(_FULL_LIST).encode()),
    "full-list-no-content-type": (200, "", json.dumps(_FULL_LIST).encode()),
    # a non-standard constant in the reply
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
    assert names <= _ALLOWED, f"{shape}: advertised {sorted(names - _ALLOWED)}"
    assert b"thought_stats" not in r.content and b"create_thread" not in r.content


def test_unfilterable_error_status_reply_is_502_and_advertises_nothing(upstream):
    r = _list(upstream, lambda req: httpx.Response(
        500, content=json.dumps(_UPSTREAM_TOOLS).encode(),
        headers={"content-type": "text/html"}))
    assert r.status_code == 502
    assert r.json()["error"]["code"] == -32603
    assert b"thought_stats" not in r.content


def test_unparseable_sse_data_line_advertises_nothing(upstream):
    sse = ("event: message\n"
           "data: {broken " + json.dumps(_UPSTREAM_TOOLS) + "\n\n").encode()
    r = _list(upstream, lambda req: httpx.Response(
        200, content=sse, headers={"content-type": "text/event-stream"}))
    assert b"thought_stats" not in r.content
    data = [ln for ln in r.text.splitlines() if ln.startswith("data:")]
    assert len(data) == 1 and _names(json.loads(data[0][5:])) == set()


def test_sse_non_object_tool_advertises_only_allowed(upstream):
    payload = {"jsonrpc": "2.0", "id": 7, "result": {"tools": [1] + _UPSTREAM_TOOLS}}
    sse = ("event: message\ndata: " + json.dumps(payload) + "\n\n").encode()
    r = _list(upstream, lambda req: httpx.Response(
        200, content=sse, headers={"content-type": "text/event-stream"}))
    assert b"thought_stats" not in r.content


# --- ordinary tools/list, unchanged ---------------------------------------------

def test_ordinary_json_tools_list_is_filtered_to_the_allow_list(upstream):
    r = _list(upstream, lambda req: httpx.Response(200, json=_FULL_LIST))
    assert r.status_code == 200
    assert _names(r.json()) == _ALLOWED
    assert r.json()["id"] == 7


def test_ordinary_sse_tools_list_is_filtered_and_keeps_its_framing(upstream):
    sse = ("event: message\ndata: " + json.dumps(_FULL_LIST) + "\n\n").encode()
    r = _list(upstream, lambda req: httpx.Response(
        200, content=sse, headers={"content-type": "text/event-stream"}))
    assert r.headers["content-type"].startswith("text/event-stream")
    lines = r.text.splitlines()
    assert lines[0] == "event: message"
    data = [ln for ln in lines if ln.startswith("data:")]
    assert _names(json.loads(data[0][5:])) == _ALLOWED


def test_ordinary_sse_notification_before_the_reply_passes(upstream):
    note = {"jsonrpc": "2.0", "method": "notifications/message",
            "params": {"level": "info", "data": "hi"}}
    sse = ("event: message\ndata: " + json.dumps(note) + "\n\n"
           "event: message\ndata: " + json.dumps(_FULL_LIST) + "\n\n").encode()
    r = _list(upstream, lambda req: httpx.Response(
        200, content=sse, headers={"content-type": "text/event-stream"}))
    data = [json.loads(ln[5:]) for ln in r.text.splitlines() if ln.startswith("data:")]
    assert data[0] == note
    assert _names(data[1]) == _ALLOWED


def test_ordinary_tools_list_error_reply_passes_through(upstream):
    err = {"jsonrpc": "2.0", "id": 7, "error": {"code": -32000, "message": "no session"}}
    r = _list(upstream, lambda req: httpx.Response(400, json=err))
    assert r.status_code == 400
    assert r.json() == err


def test_ordinary_tools_call_is_forwarded_with_policy_applied(upstream):
    r = TestClient(gw.app).post("/mcp", content=_call(
        "search_thoughts", {"query": "q", "metadata_filter": {"share": "local"}}).encode(),
        headers=AUTH)
    assert r.status_code == 200
    assert _sent(upstream)["params"]["arguments"] == {
        "query": "q", "metadata_filter": {"share": "cloud"}}


# --- attempt-1 findings: malformed tool ENTRIES, and three unpinned claims ------

_BAD_ENTRIES = [
    {"name": ["thought_stats"]},            # unhashable name (list)   - X1
    {"name": {"n": "thought_stats"}},       # unhashable name (object) - X1
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
    assert _names(r.json()) == _ALLOWED
    assert all(isinstance(t["name"], str) for t in r.json()["result"]["tools"])
    assert b"thought_stats" not in r.content


def test_malformed_tool_entries_sse_are_dropped_not_500(upstream):
    sse = ("event: message\ndata: " + json.dumps(_malformed_entries_payload())
           + "\n\n").encode()
    r = _list(upstream, lambda req: httpx.Response(
        200, content=sse, headers={"content-type": "text/event-stream"}))
    assert r.status_code == 200, (r.status_code, r.text[:200])
    data = [ln for ln in r.text.splitlines() if ln.startswith("data:")]
    assert _names(json.loads(data[0][5:])) == _ALLOWED
    assert b"thought_stats" not in r.content


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
    assert b"thought_stats" not in r.content
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
    assert _names(body) == _ALLOWED
    assert all(t["description"] == "bad " + _SURR for t in body["result"]["tools"])


def test_surrogate_in_kept_tool_description_sse_path(upstream):
    sse = ("event: message\ndata: " + json.dumps(_surrogate_list()) + "\n\n").encode()
    r = _list(upstream, lambda req: httpx.Response(
        200, content=sse, headers={"content-type": "text/event-stream"}))
    assert r.status_code == 200, (r.status_code, r.text[:200])
    data = [ln for ln in r.text.splitlines() if ln.startswith("data:")]
    assert _names(json.loads(data[0][5:])) == _ALLOWED


def test_invalid_utf8_on_an_sse_line_is_not_a_500(upstream):
    # CESU-8 surrogate bytes on a non-data line: the text decode replaces them.
    sse = (b"event: message\n: \xed\xa0\x80\ndata: " + json.dumps(_FULL_LIST).encode() + b"\n\n")
    r = _list(upstream, lambda req: httpx.Response(
        200, content=sse, headers={"content-type": "text/event-stream"}))
    assert r.status_code == 200
    data = [ln for ln in r.text.splitlines() if ln.startswith("data:")]
    assert _names(json.loads(data[0][5:])) == _ALLOWED


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
    assert body["id"] == _SURR and _names(body) == _ALLOWED


def test_surrogate_in_a_blocked_tool_name_echoed_in_the_error(upstream):
    body = json.dumps({"jsonrpc": "2.0", "id": _SURR, "method": "tools/call",
                       "params": {"name": "x" + _SURR, "arguments": {}}}).encode()
    r = TestClient(gw.app).post("/mcp", content=body, headers=AUTH)
    assert r.status_code == 200, (r.status_code, r.text[:200])
    err = json.loads(r.content)
    assert err["error"]["code"] == -32601 and err["id"] == _SURR
    assert upstream == []


def test_valid_surrogate_argument_is_forwarded(upstream):
    r = TestClient(gw.app).post("/mcp", content=_call("search_thoughts", {"query": _SURR}).encode(),
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
    assert _names(json.loads(data[0][5:])) == _ALLOWED


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
    assert _names(body) == _ALLOWED


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
