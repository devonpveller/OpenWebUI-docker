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
        name = params.get("name")
        args = params.get("arguments") or {}

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

        if name in READ_TOOLS:
            params["arguments"] = _force_read_labels(args)
        elif name in WRITE_TOOLS:
            if name == "add_memories":
                mems = args.get("memories")
                if isinstance(mems, list):
                    args["memories"] = [
                        _force_write_labels(m) if isinstance(m, dict) else m
                        for m in mems]
                for k in _STRIP_ARGS:
                    args.pop(k, None)
                params["arguments"] = args
            else:
                params["arguments"] = _force_write_labels(args)
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


def _parse_body(raw: bytes):
    """MCP streamable-http body is a single JSON-RPC object (or batch)."""
    txt = raw.decode("utf-8", "replace").strip()
    if not txt:
        return None
    return json.loads(txt)


def _upstream_headers(req):
    h = {}
    for k, v in req.headers.items():
        lk = k.lower()
        if lk in ("host", "content-length", "authorization"):
            continue
        h[k] = v
    h["Authorization"] = f"Bearer {MNEMORY_KEY}"
    h["X-User-Id"] = BOUND_USER
    h.pop("X-Agent-Id", None)
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
    out_body = body
    is_tools_list = False

    if method == "POST" and body:
        try:
            msg = _parse_body(body)
        except Exception:
            msg = None
        if isinstance(msg, list):  # JSON-RPC batch
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
            content=out_body if method in ("POST", "PUT", "PATCH") else None,
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
