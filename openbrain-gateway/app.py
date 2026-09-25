"""openbrain-cloud-gateway

A privacy-enforcing reverse proxy that sits in front of Open Brain's CORE
MCP endpoint (openbrain-mcp) for CLOUD services (Claude Code, ChatGPT, etc).
Local/trusted clients on obnet/llm-net keep talking to openbrain-mcp
directly and are unaffected.

Modelled on ../memory/mnemory-gateway/app.py — see that file for the prior art.
The mechanic is identical, swapping mnemory's `labels` for Open Brain's
`metadata` JSONB:

  * Every cloud READ is force-filtered to metadata.share == "cloud".
    Open Brain's MCP server enforces this filter server-side via
    `metadata @> $::jsonb` (see kubernetes-deployment/index.ts), so
    rows without share=cloud (all personal/local thoughts and sources
    by default) are physically not returned.
  * Tools that cannot take a metadata filter and would dump aggregates
    (`thought_stats`) are BLOCKED for cloud.
  * Every cloud WRITE is stamped metadata.origin=cloud, share=cloud
    (cloud-created => shareable to all, per policy).
  * tools/list is filtered to the cloud allow-list so the model never
    sees blocked tools.
  * The cloud client authenticates to the gateway with GATEWAY_KEY and
    never holds the real openbrain x-brain-key. The gateway injects the
    real key upstream.

The Open Brain *extensions* server (openbrain-ext, 39 tools across CRM /
family-calendar / household / meal-planning / job-hunt) is intentionally
NOT exposed by this gateway: those datasets are personal-by-design and
have no cloud surface. If a cloud-allowed extension is added later, give
it the same metadata_filter / metadata_extra treatment and add it here.
"""
import json
import os

import httpx
from starlette.applications import Starlette
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route

OPENBRAIN_URL = os.environ["OPENBRAIN_URL"].rstrip("/")     # http://openbrain-mcp:8000
OPENBRAIN_KEY = os.environ["OPENBRAIN_KEY"]                 # real x-brain-key
GATEWAY_KEY = os.environ["GATEWAY_KEY"]                     # key cloud clients use
SHARE_VALUE = os.environ.get("SHARE_LABEL_VALUE", "cloud")

# --- PROFILE (memory-plane PLAN §1.4) ---------------------------------------
#
# This image now runs as MORE THAN ONE DOOR. The cloud door (:8061) is unchanged; a second
# instance (openbrain-ops-gateway, 127.0.0.1:8062) serves host processes with the
# agent-memory tools allowlisted.
#
# EVERY DEFAULT BELOW REPRODUCES THE CLOUD DOOR'S PREVIOUS BEHAVIOUR EXACTLY, so the
# existing instance needs no env change. That is a requirement, not a nicety: the cloud door
# is live, and a regression there is a containment failure rather than a bug. The
# byte-for-byte equivalence is asserted in smoke_test.py rather than argued for here.
GATEWAY_PROFILE = os.environ.get("GATEWAY_PROFILE", "cloud")


def _tool_set(var: str, default: set) -> set:
    """Comma-separated env override, or the default. Empty string means EMPTY, not default -
    a profile that deliberately allows no writes must be able to say so."""
    raw = os.environ.get(var)
    if raw is None:
        return set(default)
    return {t.strip() for t in raw.split(",") if t.strip()}


# Tools a client may call. Reads get a forced filter; writes get forced stamping.
# Everything else (including thought_stats — an aggregate that can't be filtered cleanly)
# is denied. This stays an ALLOW-LIST: default-deny, and tools/list is filtered to it, so
# adding a tool on openbrain-mcp does NOT expose it here.
READ_TOOLS = _tool_set("GATEWAY_READ_TOOLS", {"search", "fetch", "search_thoughts", "list_thoughts"})
WRITE_TOOLS = _tool_set("GATEWAY_WRITE_TOOLS", {"capture_thought", "ingest_url", "ingest_urls"})
ALLOWED_TOOLS = READ_TOOLS | WRITE_TOOLS

# The forced read filter and write stamp, as field/value pairs.
#
# The ops profile does NOT rely on the read filter for its exposure boundary: the
# agent-memory recall path forces the exposure plane server-side from its own door value and
# ignores anything a caller sends (agent-memory.ts, performRecall). A gateway-applied filter
# is belt-and-braces there. It is load-bearing for the CLOUD profile, whose tools accept a
# caller-supplied metadata_filter.
READ_FILTER_FIELD = os.environ.get("GATEWAY_READ_FILTER_FIELD", "share")
READ_FILTER_VALUE = os.environ.get("GATEWAY_READ_FILTER_VALUE", SHARE_VALUE)
WRITE_ORIGIN = os.environ.get("GATEWAY_WRITE_ORIGIN", "cloud")
WRITE_STAMP_FIELD = os.environ.get("GATEWAY_WRITE_STAMP_FIELD", "share")
WRITE_STAMP_VALUE = os.environ.get("GATEWAY_WRITE_STAMP_VALUE", SHARE_VALUE)

# NB (Integrated Knowledge System / guardrail 5): the research-thread and
# suggestion tools (create_thread, list_threads, get_thread_sources,
# add_to_thread, remove_from_thread, get_suggestions, accept_suggestion,
# hide_suggestion, get_hidden_suggestions, restore_suggestion,
# capture_with_thread) are intentionally ABSENT from ALLOWED_TOOLS. They
# are personal/local like the extensions server and stay off the cloud
# surface by default. This is an allow-list (default-deny + tools/list is
# filtered to it), so adding tools on openbrain-mcp does NOT expose them to
# cloud — they only appear here if explicitly listed. To expose a read
# thread tool later, add it to READ_TOOLS so it gets the metadata_filter.


def _force_read_filter(args: dict) -> dict:
    md = dict(args.get("metadata_filter") or {})
    md[READ_FILTER_FIELD] = READ_FILTER_VALUE        # non-overridable
    args["metadata_filter"] = md
    return args


def _force_write_extra(args: dict) -> dict:
    md = dict(args.get("metadata_extra") or {})
    md["origin"] = WRITE_ORIGIN
    md[WRITE_STAMP_FIELD] = WRITE_STAMP_VALUE
    args["metadata_extra"] = md
    return args


def _rpc_error(rpc_id, code, message):
    return {"jsonrpc": "2.0", "id": rpc_id,
            "error": {"code": code, "message": message}}


def _rpc_result(rpc_id, result):
    return {"jsonrpc": "2.0", "id": rpc_id, "result": result}


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

        if name not in ALLOWED_TOOLS:
            return msg, _rpc_error(
                rpc_id, -32601,
                f"Tool '{name}' is not available to cloud services "
                f"(privacy policy). Allowed: {sorted(ALLOWED_TOOLS)}.")

        if name in READ_TOOLS:
            params["arguments"] = _force_read_filter(args)
        elif name in WRITE_TOOLS:
            params["arguments"] = _force_write_extra(args)
        msg["params"] = params
        return msg, None

    return msg, None


def _filter_tools_list(payload: dict) -> dict:
    try:
        tools = payload["result"]["tools"]
    except (KeyError, TypeError):
        return payload
    payload["result"]["tools"] = [
        t for t in tools if t.get("name") in ALLOWED_TOOLS]
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


def _upstream_headers(req):
    h = {}
    for k, v in req.headers.items():
        lk = k.lower()
        if lk in ("host", "content-length", "authorization", "x-brain-key"):
            continue
        h[k] = v
    h["x-brain-key"] = OPENBRAIN_KEY
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
            method, f"{OPENBRAIN_URL}/",
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
