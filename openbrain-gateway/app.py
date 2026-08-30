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


# --- AUDIT (dark-factory-unification U5) ------------------------------------
#
# U5 is validated by "an agent instructed to ... reach personal-plane data is mechanically
# stopped AND the attempt is visible in an audit record". This door was doing the first
# half only. A denied tool got a JSON-RPC -32601 and nothing else happened: no row, no
# line, nothing. From outside, an agent probing for `search_thoughts` and an agent that
# never called were indistinguishable, and the operator is not reading this as it lands.
#
# WHAT THIS IS, AND WHAT IT IS NOT. It is one JSON line per governance-relevant decision on
# the container's stdout, which compose keeps (json-file, 10m x 3) and `docker logs` reads.
# It is NOT the durable audit table - a denied call never reaches openbrain-mcp, so there
# is no connection to write a row on and no memory to hang it off. The calls that DO reach
# the server get their durable row there (agent_memory_audit_events, event_type
# 'recall_requested'); this covers the ones that are stopped before they arrive.
#
# VALUES ARE TRUNCATED AND ALLOWLISTED. This line is durable and travels; the one thing it
# must never do is become the place a caller's payload gets copied to. Only the tool name,
# the field names in play, and the exposure/share LABELS are ever written - never content,
# never a whole argument blob.
AUDIT_SCHEMA = "openbrain-gateway.audit.v1"


def _audit(event: str, **fields) -> None:
    rec = {"audit": AUDIT_SCHEMA, "event": event, "profile": GATEWAY_PROFILE}
    rec.update(fields)
    print(json.dumps(rec, sort_keys=True), flush=True)


def _label(v) -> str:
    """A caller-supplied value, made safe to write into a durable line."""
    return str(v)[:64]


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
        name = params.get("name")
        args = params.get("arguments") or {}

        if name not in ALLOWED_TOOLS:
            # THE DENIAL IS THE INTERESTING EVENT. It is the shape an agent reaching past
            # its allowlist makes - the ops door serves agent_memory_* and nothing else, so
            # a `search_thoughts` here is a probe at the personal plane by definition.
            _audit("tool_denied", tool=_label(name),
                   allowed=sorted(ALLOWED_TOOLS))
            return msg, _rpc_error(
                rpc_id, -32601,
                f"Tool '{name}' is not available to cloud services "
                f"(privacy policy). Allowed: {sorted(ALLOWED_TOOLS)}.")

        if name in READ_TOOLS:
            # Detect BEFORE forcing. _force_read_filter is kept pure - its output is
            # asserted byte-for-byte by smoke_test.py --defaults, and an audit call inside
            # it would fire during that check for no reason.
            attempted = (args.get("metadata_filter") or {}).get(READ_FILTER_FIELD)
            if attempted is not None and attempted != READ_FILTER_VALUE:
                _audit("read_filter_override", tool=_label(name),
                       field=READ_FILTER_FIELD, attempted=_label(attempted),
                       forced=READ_FILTER_VALUE)
            # The agent-memory recall tools take `exposure` as a FIRST-CLASS argument
            # rather than through metadata_filter, and the server drops it (agent-memory.ts
            # performRecall forces the plane from its own door value). Passing it through
            # is deliberate: the server is the boundary, and it writes the durable audit
            # row. What was missing is that the door saw the attempt first and said nothing.
            asked = args.get("exposure")
            if asked is not None:
                asked_list = asked if isinstance(asked, list) else [asked]
                if any(_label(e) != READ_FILTER_VALUE for e in asked_list):
                    _audit("exposure_override_attempt", tool=_label(name),
                           requested=[_label(e) for e in asked_list[:8]],
                           door=READ_FILTER_VALUE)
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
