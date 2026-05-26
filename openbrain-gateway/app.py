"""openbrain-cloud-gateway

A privacy-enforcing reverse proxy that sits in front of Open Brain's CORE
MCP endpoint (openbrain-mcp) for CLOUD services (Claude Code, ChatGPT, etc).
Local/trusted clients on obnet/llm-net keep talking to openbrain-mcp
directly and are unaffected.

Modelled on ../mnemory-gateway/app.py — see that file for the prior art.
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

# Tools a cloud client may call. Reads get a forced metadata_filter;
# writes get forced metadata_extra stamping. Everything else (including
# thought_stats — an aggregate that can't be filtered cleanly) is denied.
READ_TOOLS = {"search", "fetch", "search_thoughts", "list_thoughts"}
WRITE_TOOLS = {"capture_thought", "ingest_url", "ingest_urls"}
ALLOWED_TOOLS = READ_TOOLS | WRITE_TOOLS


def _force_read_filter(args: dict) -> dict:
    md = dict(args.get("metadata_filter") or {})
    md["share"] = SHARE_VALUE        # non-overridable
    args["metadata_filter"] = md
    return args


def _force_write_extra(args: dict) -> dict:
    md = dict(args.get("metadata_extra") or {})
    md["origin"] = "cloud"
    md["share"] = SHARE_VALUE
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
        name = params.get("name")
        args = params.get("arguments") or {}

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
            method, f"{OPENBRAIN_URL}/",
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
