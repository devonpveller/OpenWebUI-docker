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
import os

import httpx
from starlette.applications import Starlette
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route

MNEMORY_URL = os.environ["MNEMORY_URL"].rstrip("/")          # http://mnemory:8050
MNEMORY_KEY = os.environ["MNEMORY_KEY"]                       # real mnemory API key
GATEWAY_KEY = os.environ["GATEWAY_KEY"]                       # key cloud clients use
BOUND_USER = os.environ["BOUND_USER_ID"]                      # mnemory user_id to bind
SHARE_VALUE = os.environ.get("SHARE_LABEL_VALUE", "cloud")

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
        args["categories"] = [c for c in cats if c != "personal"]
    for k in _STRIP_ARGS:
        args.pop(k, None)
    return args


def _is_obj(v) -> bool:
    return v is None or isinstance(v, dict)


def _is_str_list(v) -> bool:
    return v is None or (isinstance(v, list) and all(isinstance(x, str) for x in v))


def _policed_type_error(name: str, args: dict):
    """Every argument the policy reads or rewrites must ALREADY have the JSON
    type the policy expects, or the call is refused (-32602).

    mnemory's FastMCP JSON-decodes a STRING argument whose parameter is not
    typed str, so a list or object sent string-encoded would skip the policy
    here and still arrive at mnemory as a list or object. Never coerced here,
    never forwarded. The policed pairs (see _force_read_labels /
    _force_write_labels):
      search_memories, find_memories, list_memories: labels (object)
      add_memory: labels (object), categories (list of strings)
      add_memories: memories (list of objects), and in each item labels
                    (object), categories (list of strings)
    user_id / agent_id are removed whatever their type.
    """
    if name in READ_TOOLS or name == "add_memory":
        if not _is_obj(args.get("labels")):
            return "labels must be an object"
    if name == "add_memory" and not _is_str_list(args.get("categories")):
        return "categories must be a list of strings"
    if name == "add_memories":
        mems = args.get("memories")
        if not (isinstance(mems, list) and all(isinstance(m, dict) for m in mems)):
            return "memories must be a list of objects"
        for m in mems:
            if not _is_obj(m.get("labels")):
                return "each memory's labels must be an object"
            if not _is_str_list(m.get("categories")):
                return "each memory's categories must be a list of strings"
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

        bad = _policed_type_error(name, args)
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


def _filter_tools_list(payload: dict) -> dict:
    try:
        tools = payload["result"]["tools"]
    except (KeyError, TypeError):
        return payload
    payload["result"]["tools"] = [
        t for t in tools if t.get("name") in ALLOWED_TOOLS
        or t.get("name") == "initialize_memory"]
    return payload


class _BodyRefused(ValueError):
    """A request body the gateway cannot apply its policy to."""


def _no_constant(name):
    raise _BodyRefused(f"non-standard JSON constant {name}")


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
        msg = json.loads(raw.decode("utf-8"), parse_constant=_no_constant)
    except _BodyRefused:
        raise
    except (UnicodeDecodeError, ValueError, RecursionError) as e:
        raise _BodyRefused(f"not a UTF-8 JSON document ({e.__class__.__name__})")
    if isinstance(msg, dict):
        return msg
    if isinstance(msg, list) and msg and all(isinstance(m, dict) for m in msg):
        return msg
    raise _BodyRefused("not a JSON-RPC object or a non-empty batch of objects")


def _refuse(reason: str):
    return JSONResponse(
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


async def health(_request):
    return PlainTextResponse("ok")


async def mcp(request):
    # Authenticate the cloud client against the gateway key.
    auth = request.headers.get("authorization", "")
    if auth != f"Bearer {GATEWAY_KEY}":
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    method = request.method
    body = await request.body()
    up_headers = _upstream_headers(request)

    short_circuit = None
    out_body = None  # only bytes re-serialised below ever go upstream
    is_tools_list = False

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
            mm, sc = _apply_policy(msg)
            if sc is not None:
                short_circuit = sc
            else:
                out_body = json.dumps(mm).encode()

    if short_circuit is not None:
        return JSONResponse(short_circuit)

    up_headers.pop("content-length", None)
    timeout = httpx.Timeout(300.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        upstream = await client.request(
            method, f"{MNEMORY_URL}/mcp",
            content=out_body,
            headers=up_headers,
            params=dict(request.query_params))

        ct = upstream.headers.get("content-type", "")
        # tools/list: filter advertised tools to the cloud allow-list.
        if is_tools_list and "application/json" in ct:
            try:
                payload = _filter_tools_list(json.loads(upstream.content))
                return JSONResponse(payload, status_code=upstream.status_code)
            except Exception:
                pass
        if is_tools_list and "text/event-stream" in ct:
            txt = upstream.text
            out_lines = []
            for line in txt.splitlines():
                if line.startswith("data:"):
                    try:
                        p = _filter_tools_list(json.loads(line[5:].strip()))
                        out_lines.append("data: " + json.dumps(p))
                        continue
                    except Exception:
                        pass
                out_lines.append(line)
            return Response("\n".join(out_lines) + "\n",
                            status_code=upstream.status_code,
                            media_type="text/event-stream")

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
