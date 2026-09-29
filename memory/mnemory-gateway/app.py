"""mnemory-cloud-gateway

A privacy-enforcing reverse proxy that sits in front of mnemory's MCP
endpoint for CLOUD services (Claude Code, etc.). Local/trusted clients on
llm-net keep talking to mnemory directly and are unaffected.

Policy (privacy-first, default-deny):
  * Every cloud READ is force-filtered to labels.share == "cloud".
    mnemory enforces this filter server-side (verified), so memories
    without share=cloud (all personal/local memories by default) are
    physically not returned.
  * Tools that cannot take a labels filter and would dump core/recent
    text (initialize_memory, get_core_memories, get_recent_memories) are
    BLOCKED. initialize_memory returns a benign empty core so the client
    proceeds and uses the filtered search path instead.
  * Every cloud WRITE is stamped labels.origin=cloud, share=cloud
    (cloud-created => shareable to all, per policy). The "personal"
    category is stripped from cloud writes.
  * Mutating/destructive tools (update/delete/artifacts) are BLOCKED for
    cloud (least-trust). user_id / agent_id arguments are stripped so a
    cloud client cannot pivot to another user or an agent silo; identity
    is fixed by the gateway via headers.
  * tools/list is filtered to the cloud allow-list so the model never
    sees blocked tools.

The cloud client authenticates to the gateway with GATEWAY_KEY and never
holds the real mnemory key. The gateway injects the mnemory key +
X-User-Id upstream.
"""
import json
import math
import os
import re

import httpx
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse, Response
from starlette.routing import Route

MNEMORY_URL = os.environ["MNEMORY_URL"].rstrip("/")          # http://mnemory:8050
MNEMORY_KEY = os.environ["MNEMORY_KEY"]                       # real mnemory API key
GATEWAY_KEY = os.environ["GATEWAY_KEY"]                       # key cloud clients use
BOUND_USER = os.environ["BOUND_USER_ID"]                      # mnemory user_id to bind
SHARE_VALUE = os.environ.get("SHARE_LABEL_VALUE", "cloud")

# REQUEST-SIZE CAP. A request body larger than GATEWAY_MAX_BODY_BYTES (default
# 4 MiB = 4194304 bytes) is refused with HTTP 413 and a JSON-RPC error, and
# nothing is forwarded. It is checked against a declared Content-Length before
# the body is read, and again while it is read (a chunked body declares none),
# so the gateway never buffers more than the cap. 4 MiB is far above any real
# MCP call (mnemory's own MAX_INPUT_LENGTH is 400000 characters, which JSON
# escaping can at most sextuple to ~2.4 MB). A value that is not a positive
# integer stops the gateway at start rather than running uncapped.
MAX_BODY_BYTES = int(os.environ.get("GATEWAY_MAX_BODY_BYTES", "4194304"))
if MAX_BODY_BYTES <= 0:
    raise ValueError("GATEWAY_MAX_BODY_BYTES must be a positive integer")

# Tools a cloud client may call. Reads get a forced share filter; writes
# get forced origin/share stamping.
# find_memories/search_memories/list_memories return a structured memory
# list and accept a labels filter (enforced server-side), so injecting
# share=cloud is a real control. ask_memories is intentionally NOT here:
# it returns LLM-synthesized free text — the least auditable surface — so
# privacy-first => block it.
READ_TOOLS = {"search_memories", "find_memories", "list_memories"}
WRITE_TOOLS = {"add_memory", "add_memories"}
PASS_TOOLS = {"list_categories"}
ALLOWED_TOOLS = READ_TOOLS | WRITE_TOOLS | PASS_TOOLS
# Everything else (ask_memories, initialize_memory, get_core_memories,
# get_recent_memories, update_memory, delete_memory, delete_memories,
# *_artifact*) is denied.

_STRIP_ARGS = ("user_id", "agent_id")


def _force_read_labels(args: dict) -> dict:
    labels = dict(args.get("labels") or {})
    labels.pop("origin", None)          # client may not request other origins
    labels["share"] = SHARE_VALUE       # non-overridable
    args["labels"] = labels
    for k in _STRIP_ARGS:
        args.pop(k, None)
    return args


def _force_write_labels(args: dict) -> dict:
    labels = dict(args.get("labels") or {})
    labels["origin"] = "cloud"
    labels["share"] = SHARE_VALUE
    args["labels"] = labels
    cats = args.get("categories")
    if isinstance(cats, list):
        args["categories"] = [c for c in cats if not _is_personal(c)]
    for k in _STRIP_ARGS:
        args.pop(k, None)
    return args


def _typed(v, kind: str):
    """Return (ok, value) for a policed argument: DECODE-THEN-POLICE.

    Some MCP clients send list/object arguments as JSON strings, and the
    upstream's tool layer may decode such a string itself - so a string must
    never reach it for a policed argument. A string that decodes (strictly, as
    a body would) to the expected type is replaced by the decoded value, and
    the policy is applied to that; anything else is refused (-32602).
    None/absent is accepted. kind: "object" | "str_list" | "obj_list".
    """
    if v is None:
        return True, None
    if isinstance(v, str):
        try:
            v = _strict_json(v)
        except _BodyRefused:
            return False, None
    if kind == "object":
        return isinstance(v, dict), v
    if kind == "str_list":
        return isinstance(v, list) and all(isinstance(x, str) for x in v), v
    if kind == "obj_list":
        return isinstance(v, list), v   # items are checked one by one by the caller
    raise ValueError(kind)


def _is_personal(cat) -> bool:
    """mnemory normalises a category with cat.strip().lower() and treats
    "<prefix>:<name>" as a subcategory of <prefix> (mnemory/categories.py,
    validate_categories). Match the same way: "personal", " Personal " and
    "PERSONAL:family" are all the personal category."""
    if not isinstance(cat, str):
        return False
    c = cat.strip().lower()
    return c == "personal" or c.split(":", 1)[0] == "personal"


def _normalise_policed_args(name: str, args: dict):
    """Bring every argument the policy reads or rewrites to the JSON type the
    policy expects (decoding a JSON string, see _typed), in place. Returns an
    error message for -32602, or None. The policed pairs (see
    _force_read_labels / _force_write_labels):
      search_memories, find_memories, list_memories: labels (object)
      add_memory: labels (object), categories (list of strings)
      add_memories: memories (list of objects, required); in each item
                    labels (object), categories (list of strings)
    user_id / agent_id are removed whatever their type.
    """
    def fix(d, key, kind, label):
        ok, v = _typed(d.get(key), kind)
        if not ok:
            return label
        if key in d:
            d[key] = v
        return None

    if name in READ_TOOLS or name == "add_memory":
        err = fix(args, "labels", "object", "labels must be an object")
        if err:
            return err
    if name == "add_memory":
        err = fix(args, "categories", "str_list", "categories must be a list of strings")
        if err:
            return err
    if name == "add_memories":
        if args.get("memories") is None:
            return "memories must be a list of objects"
        err = fix(args, "memories", "obj_list", "memories must be a list of objects")
        if err:
            return err
        items = []
        for m in args["memories"]:
            ok, m = _typed(m, "object")
            if not ok or m is None:
                return "memories must be a list of objects"
            for key, kind, label in (
                    ("labels", "object", "each memory's labels must be an object"),
                    ("categories", "str_list",
                     "each memory's categories must be a list of strings")):
                err = fix(m, key, kind, label)
                if err:
                    return err
            items.append(m)
        args["memories"] = items
    return None


def _rpc_error(rpc_id, code, message):
    return {"jsonrpc": "2.0", "id": rpc_id,
            "error": {"code": code, "message": message}}


def _rpc_result(rpc_id, result):
    return {"jsonrpc": "2.0", "id": rpc_id, "result": result}


def _tool_result_text(rpc_id, text):
    return _rpc_result(rpc_id, {"content": [{"type": "text", "text": text}],
                               "isError": False})


def _apply_policy(msg: dict):
    """Return (mutated_msg, short_circuit_response_or_None)."""
    if not isinstance(msg, dict):
        return msg, None
    method = msg.get("method")
    rpc_id = msg.get("id")

    if method == "tools/call":
        params = msg.get("params") or {}
        if not isinstance(params, dict):
            return msg, _rpc_error(rpc_id, -32602, "params must be an object")
        name = params.get("name")
        args = params.get("arguments") or {}
        if not isinstance(name, str) or not isinstance(args, dict):
            return msg, _rpc_error(
                rpc_id, -32602, "tools/call needs a string name and object arguments")

        if name == "initialize_memory":
            # Benign empty core so the cloud client proceeds gracefully.
            return msg, _tool_result_text(
                rpc_id,
                "(No shared memory context loaded. This is a cloud session: "
                "use search_memories for project/code context. Personal and "
                "local-only memories are not available here.)")

        if name not in ALLOWED_TOOLS:
            return msg, _rpc_error(
                rpc_id, -32601,
                f"Tool '{name}' is not available to cloud services "
                f"(privacy policy). Allowed: {sorted(ALLOWED_TOOLS)}.")

        bad = _normalise_policed_args(name, args)
        if bad:
            return msg, _rpc_error(rpc_id, -32602, f"{name}: {bad}")

        if name in READ_TOOLS:
            params["arguments"] = _force_read_labels(args)
        elif name in WRITE_TOOLS:
            if name == "add_memories":
                args["memories"] = [_force_write_labels(m) for m in args["memories"]]
                for k in _STRIP_ARGS:
                    args.pop(k, None)
                params["arguments"] = args
            else:
                params["arguments"] = _force_write_labels(args)
        else:  # PASS_TOOLS: identity is the gateway's here too
            for k in _STRIP_ARGS:
                args.pop(k, None)
            params["arguments"] = args
        msg["params"] = params
        return msg, None

    return msg, None


class _Unfilterable(ValueError):
    """An upstream tools/list reply the gateway cannot filter."""


def _filter_tools_list(payload):
    """Filter a tools/list reply to the allow-list. FAIL CLOSED: raises
    _Unfilterable for any reply whose tool list cannot be read, and the caller
    then advertises NOTHING (see _closed_tools_list) instead of passing the
    upstream's reply through unfiltered. A tool entry that is not an object,
    or whose name is not a string, is dropped. A JSON-RPC error, or a message that
    is not a response at all (a notification or request the server interleaves
    on an SSE stream), carries no tool list and is returned as it is."""
    if not isinstance(payload, dict):
        raise _Unfilterable("not a JSON object")
    if "result" not in payload:
        if "error" in payload or "method" in payload:
            return payload
        raise _Unfilterable("neither a result nor an error")
    result = payload["result"]
    if not isinstance(result, dict) or not isinstance(result.get("tools"), list):
        raise _Unfilterable("result.tools is not a list")
    result["tools"] = [
        t for t in result["tools"] if isinstance(t, dict) and isinstance(t.get("name"), str) and (
            t.get("name") in ALLOWED_TOOLS
            or t.get("name") == "initialize_memory")]
    return payload


def _closed_tools_list(rpc_id):
    """What the client gets when the upstream's tools/list reply cannot be
    filtered: an EMPTY tool list, never the unfiltered reply."""
    return _rpc_result(rpc_id, {"tools": []})


# SSE line terminators per the HTML Living Standard (server-sent events):
# CRLF, CR or LF - and NOTHING else. str.splitlines() also splits at U+2028,
# U+2029, U+0085 (and more), which JSON allows raw inside a string: it cut a
# valid data line in two and let the tail out unparsed (attempt-4 X6).
_SSE_EOL = re.compile(r"\r\n|\r|\n")

# The only `id:` / `retry:` values the gateway re-emits. An id is re-emitted
# (it is the client's Last-Event-ID for resumability) only if it is 1-128
# printable ASCII characters with no space: nothing any line splitter - the
# spec's CR/LF, or str.splitlines() / httpx's LineDecoder, which also split at
# VT, FF, FS/GS/RS, U+0085, U+2028, U+2029 (attempt-5 X7) - could split, and
# no control character. retry: 1-10 ASCII digits. Anything else is dropped.
_SSE_SAFE_ID = re.compile(r"[\x21-\x7e]{1,128}")
_SSE_SAFE_RETRY = re.compile(r"[0-9]{1,10}")


def _sse_filter_tools_list(raw: bytes, list_id) -> str:
    """Re-serialise an SSE tools/list reply, FAIL CLOSED. Parsed the way an
    EventSource client parses it: UTF-8 decode with replacement (never the
    charset the upstream declares - utf-7 etc. can decode to a lone surrogate,
    attempt-3 X4; replacement never yields one), ONE leading BOM stripped
    (attempt-4 X5), lines split on CR/LF only, events ended by a blank line (or
    the end of the body), a field's value after one optional space, the last
    `event`/`id`/`retry` winning, data lines joined with LF (collected in a
    list and joined once - attempt-5 X8 was a quadratic join).

    NOTHING upstream-chosen is copied (attempt-5 X7). Per event the gateway
    writes only:
      * `event: message` - when the event's type is `message` (explicitly or by
        default). An event of any other type is dropped whole: MCP puts its
        messages in `message` events, and a type is never echoed.
      * `id: <v>` / `retry: <n>` - only values matching _SSE_SAFE_ID /
        _SSE_SAFE_RETRY.
      * data, re-serialised by json.dumps (ASCII) or empty:
          - no data field: the event is kept without data (id / retry only);
          - data that is empty or whitespace (an MCP resumability "priming"
            event, `id: n` + `data:`): an empty `data:` - it carries no tool,
            and a client does not take it for the tools/list response;
          - a JSON-RPC response (result/error) whose id is NOT this tools/list
            request's: dropped - it is not this request's answer and must not
            be turned into one;
          - a notification / request (`method`, no result/error): passed,
            re-serialised;
          - this request's response: filtered to the allow-list;
          - anything unparseable or unfilterable: the EMPTY tool list.
    Comments, unknown fields and anything unparsed are dropped, never copied."""
    text = raw.decode("utf-8", "replace")
    if text.startswith("\ufeff"):
        text = text[1:]
    events, cur = [], []
    for line in _SSE_EOL.split(text):
        if line == "":
            if cur:
                events.append(cur)
            cur = []
        else:
            cur.append(line)
    if cur:  # a last event with no blank line after it: closed off here
        events.append(cur)

    out = []
    for ev in events:
        etype, typed, eid, retry, data = "message", False, None, None, None
        for line in ev:
            if line.startswith(":"):
                continue  # comment
            name, sep, value = line.partition(":")
            if sep and value.startswith(" "):
                value = value[1:]
            if name == "data":
                if data is None:
                    data = []
                data.append(value)
            elif name == "event":
                etype, typed = value, True
            elif name == "id":
                eid = value
            elif name == "retry":
                retry = value
            # any other field name is ignored, as a client ignores it
        if etype != "message":
            continue
        fields = []
        if typed:  # keep a valid upstream's framing byte for byte
            fields.append("event: message")
        if eid is not None and _SSE_SAFE_ID.fullmatch(eid):
            fields.append(f"id: {eid}")
        if retry is not None and _SSE_SAFE_RETRY.fullmatch(retry):
            fields.append(f"retry: {retry}")
        if data is not None:
            joined = "\n".join(data)
            if not joined.strip():
                fields.append("data: ")
            else:
                p = _sse_event_payload(joined, list_id)
                if p is None:
                    continue  # a response to another request: not this answer
                fields.append("data: " + json.dumps(p))
        if fields:
            out.append("\n".join(fields) + "\n\n")
    return "".join(out)


def _sse_event_payload(data: str, list_id):
    """The re-serialisable payload for one event's data, or None to drop it."""
    try:
        msg = _strict_json(data)
    except Exception:
        return _closed_tools_list(list_id)
    if isinstance(msg, dict) and ("result" in msg or "error" in msg) \
            and msg.get("id") != list_id:
        return None
    try:
        return _filter_tools_list(msg)
    except Exception:  # anything unfilterable -> advertise nothing
        return _closed_tools_list(list_id)


class _BodyRefused(ValueError):
    """A request body the gateway cannot apply its policy to."""


def _no_constant(name):
    raise _BodyRefused(f"non-standard JSON constant {name}")


def _finite_float(s: str) -> float:
    """A JSON number too large for a double (1e400, -1e999) parses to +/-inf,
    which json.dumps would re-emit as the non-JSON token Infinity. Refuse it:
    the gateway only forwards what it can re-serialise as valid JSON."""
    v = float(s)
    if not math.isfinite(v):
        raise _BodyRefused(f"number out of range ({s[:32]})")
    return v


def _strict_json(txt: str):
    """json.loads under the gateway's strict rules (no BOM, no NaN/Infinity,
    no number that overflows to infinity, bounded nesting); raises
    _BodyRefused. Used for the request body and for string-encoded
    policed arguments alike."""
    if txt.startswith("\ufeff"):
        raise _BodyRefused("byte-order mark")
    try:
        return json.loads(txt, parse_constant=_no_constant,
                          parse_float=_finite_float)
    except _BodyRefused:
        raise
    except (ValueError, RecursionError) as e:
        raise _BodyRefused(f"not a JSON document ({e.__class__.__name__})")


def _parse_body(raw: bytes):
    """Parse the MCP streamable-http body STRICTLY: one JSON-RPC object, or a
    non-empty batch of objects, as plain UTF-8 JSON.

    FAIL CLOSED. The upstream's own parser accepts more than this (a byte-order
    mark, other encodings), so a body this function cannot parse is REFUSED,
    never forwarded as received: the gateway only ever sends upstream what it
    parsed here and re-serialised after policy. Raises _BodyRefused.
    """
    if raw.startswith(b"\xef\xbb\xbf"):
        raise _BodyRefused("byte-order mark")
    try:
        txt = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise _BodyRefused("not a UTF-8 JSON document (UnicodeDecodeError)")
    msg = _strict_json(txt)
    if isinstance(msg, dict):
        return msg
    if isinstance(msg, list) and msg and all(isinstance(m, dict) for m in msg):
        return msg
    raise _BodyRefused("not a JSON-RPC object or a non-empty batch of objects")


def _json_response(payload, status_code=200):
    """Every JSON reply the gateway BUILDS goes through here (never starlette's
    JSONResponse). JSONResponse renders with ensure_ascii=False and then encodes
    UTF-8, so a lone surrogate ("\\ud800") in any string it carries - a kept tool
    description from the upstream, a request id, a tool name echoed in an error -
    raised UnicodeEncodeError and the client got a 500. Same bytes as
    JSONResponse for everything it could render; a payload it could not is
    re-serialised with ensure_ascii=True (valid JSON, the surrogate \\u-escaped)."""
    try:
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False,
                          separators=(",", ":")).encode("utf-8")
    except UnicodeEncodeError:
        body = json.dumps(payload, ensure_ascii=True, allow_nan=False,
                          separators=(",", ":")).encode("ascii")
    return Response(body, status_code=status_code, media_type="application/json")


def _refuse(reason: str):
    return _json_response(
        _rpc_error(None, -32700, f"Request refused by the gateway: {reason}."),
        status_code=400)


# Request headers mnemory reads to decide WHO the caller is, or WHETHER it is
# authenticated (mnemory/server.py, APIKeyMiddleware: dispatch, _extract_token,
# _set_identity_from_headers). A client copy of any of them is dropped, compared
# case-insensitively, and the gateway then sets its own values exactly once.
# Header names are case-insensitive on the wire, so a client "x-user-id" next to
# the gateway's "X-User-Id" is two values of ONE header, and the upstream reads
# whichever arrives first.
#   authorization           the API key / JWT (the gateway's key replaces it)
#   x-api-key               the alternative API key header
#   cookie                  mnemory_exchange_session / cognis_session carry an
#                           authenticated identity of their own
#   x-user-id               user identity when the key is not user-mapped
#   x-openwebui-user-email  the fallback user identity
#   x-agent-id              agent scope (the cloud door binds none)
# host and content-length are transport headers httpx recomputes.
_DROP_HEADERS = frozenset((
    "host", "content-length",
    "authorization", "x-api-key", "cookie",
    "x-user-id", "x-openwebui-user-email", "x-agent-id",
))


# Hop-by-hop headers (RFC 9110 section 7.6.1) describe the CLIENT's connection,
# not the request, and the gateway frames the bytes it rebuilt itself: none of
# them is forwarded, nor any header the client's Connection header names.
# content-encoding goes too - the body sent upstream is the gateway's own plain
# JSON, never the client's encoding of it.
_HOP_BY_HOP = frozenset((
    "connection", "keep-alive", "proxy-connection", "transfer-encoding", "te",
    "trailer", "upgrade", "proxy-authorization", "proxy-authenticate",
    "content-encoding",
))


def _connection_named(req) -> set:
    names = set()
    for v in req.headers.getlist("connection"):
        names.update(t.strip().lower() for t in v.split(",") if t.strip())
    return names


def _upstream_headers(req):
    drop = _DROP_HEADERS | _HOP_BY_HOP | _connection_named(req)
    h = {}
    for k, v in req.headers.items():
        if k.lower() in drop:
            continue
        h[k] = v
    h["Authorization"] = f"Bearer {MNEMORY_KEY}"
    h["X-User-Id"] = BOUND_USER
    return h


class _TooLarge(Exception):
    """The request body exceeds MAX_BODY_BYTES."""


async def _read_capped(request) -> bytes:
    """Read the request body, never buffering more than MAX_BODY_BYTES.
    Raises _TooLarge on a declared Content-Length over the cap (before
    reading anything) or once the bytes actually read pass it."""
    declared = request.headers.get("content-length")
    if (declared is not None and declared.strip().isdigit()
            and int(declared) > MAX_BODY_BYTES):
        raise _TooLarge()
    buf = bytearray()
    async for chunk in request.stream():
        buf += chunk
        if len(buf) > MAX_BODY_BYTES:
            raise _TooLarge()
    return bytes(buf)


async def health(_request):
    return PlainTextResponse("ok")


async def mcp(request):
    # Authenticate the cloud client against the gateway key.
    auth = request.headers.get("authorization", "")
    if auth != f"Bearer {GATEWAY_KEY}":
        return _json_response({"error": "unauthorized"}, status_code=401)

    method = request.method
    try:
        body = await _read_capped(request)
    except _TooLarge:
        return _json_response(
            _rpc_error(None, -32600,
                       f"Request refused by the gateway: body larger than "
                       f"{MAX_BODY_BYTES} bytes."),
            status_code=413)
    up_headers = _upstream_headers(request)

    short_circuit = None
    out_body = None  # only bytes re-serialised below ever go upstream
    is_tools_list = False
    list_id = None

    if body and method != "POST":
        return _refuse(f"{method} with a body")
    if method == "POST":
        try:
            msg = _parse_body(body)
        except _BodyRefused as e:
            return _refuse(str(e))
        if isinstance(msg, list):  # JSON-RPC batch
            # tools/list is filtered on the way back only for a single request,
            # so inside a batch it is refused rather than answered unfiltered.
            if any(m.get("method") == "tools/list" for m in msg):
                return _refuse("tools/list inside a batch")
            mutated, sc = [], None
            for m in msg:
                mm, r = _apply_policy(m)
                mutated.append(mm)
                if r is not None and sc is None:
                    sc = r
            if sc is not None:
                short_circuit = sc
            else:
                out_body = json.dumps(mutated).encode()
        elif isinstance(msg, dict):
            if msg.get("method") == "tools/list":
                is_tools_list = True
                list_id = msg.get("id")
            mm, sc = _apply_policy(msg)
            if sc is not None:
                short_circuit = sc
            else:
                out_body = json.dumps(mm).encode()

    if short_circuit is not None:
        return _json_response(short_circuit)

    up_headers.pop("content-length", None)
    timeout = httpx.Timeout(300.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        upstream = await client.request(
            method, f"{MNEMORY_URL}/mcp",
            content=out_body,
            headers=up_headers,
            params=dict(request.query_params))

        ct = upstream.headers.get("content-type", "")
        # tools/list: filter advertised tools to the cloud allow-list, FAIL
        # CLOSED - a reply that cannot be filtered is replaced, never passed
        # through (it would advertise every upstream tool).
        if is_tools_list and "text/event-stream" in ct.lower():
            return Response(_sse_filter_tools_list(upstream.content, list_id),
                            status_code=upstream.status_code,
                            media_type="text/event-stream")
        if is_tools_list:
            try:
                payload = _filter_tools_list(_strict_json(
                    upstream.content.decode("utf-8", "replace")))
            except Exception:  # anything unfilterable -> advertise nothing
                if 200 <= upstream.status_code < 300:
                    return _json_response(_closed_tools_list(list_id))
                return _json_response(
                    _rpc_error(list_id, -32603,
                               "upstream tools/list reply could not be filtered; "
                               "nothing is advertised"),
                    status_code=502)
            return _json_response(payload, status_code=upstream.status_code)

        passthru = {k: v for k, v in upstream.headers.items()
                    if k.lower() not in ("content-length", "content-encoding",
                                         "transfer-encoding", "connection")}
        return Response(upstream.content, status_code=upstream.status_code,
                        headers=passthru,
                        media_type=ct or "application/json")


app = Starlette(routes=[
    Route("/health", health, methods=["GET"]),
    Route("/mcp", mcp, methods=["GET", "POST", "DELETE"]),
])
