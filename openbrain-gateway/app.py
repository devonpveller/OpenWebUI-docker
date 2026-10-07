"""openbrain-cloud-gateway

A privacy-enforcing reverse proxy that sits in front of Open Brain's CORE
MCP endpoint (openbrain-mcp) for CLOUD services (Claude Code, ChatGPT, etc).
Local/trusted clients on obnet/llm-net keep talking to openbrain-mcp
directly and are unaffected.

The policy is enforced on Open Brain's `metadata` JSONB:

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
import hashlib
import json
import math
import os
import re
import sys
import threading
import time
from datetime import datetime, timezone

import httpx
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse, Response
from starlette.routing import Route

OPENBRAIN_URL = os.environ["OPENBRAIN_URL"].rstrip("/")     # http://openbrain-mcp:8000
OPENBRAIN_KEY = os.environ["OPENBRAIN_KEY"]                 # real x-brain-key
GATEWAY_KEY = os.environ["GATEWAY_KEY"]                     # key cloud clients use
SHARE_VALUE = os.environ.get("SHARE_LABEL_VALUE", "cloud")

# REQUEST-SIZE CAP. A request body larger than GATEWAY_MAX_BODY_BYTES (default
# 4 MiB = 4194304 bytes) is refused with HTTP 413 and a JSON-RPC error, and
# nothing is forwarded. It is checked against a declared Content-Length before
# the body is read, and again while it is read (a chunked body declares none),
# so the gateway never buffers more than the cap. 4 MiB is far above any real
# MCP call (a 400000-character input, which JSON escaping can at most
# sextuple to ~2.4 MB, still fits). A value that is not a positive
# integer stops the gateway at start rather than running uncapped.
MAX_BODY_BYTES = int(os.environ.get("GATEWAY_MAX_BODY_BYTES", "4194304"))
if MAX_BODY_BYTES <= 0:
    raise ValueError("GATEWAY_MAX_BODY_BYTES must be a positive integer")

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
    # `source` is the row's PROVENANCE (who wrote it: "mcp" for capture_thought,
    # "ingest_url" for the ingest tools, "gmail"/"deep-research" for the
    # server's own producers). openbrain-mcp spreads metadata_extra AFTER its
    # own constant ({source: "mcp", ...metadata_extra}), so a caller's key would
    # win. The gateway removes it: what is stored is always the server's own
    # per-tool constant, exactly as for a call that never sent one.
    md.pop("source", None)
    md["origin"] = WRITE_ORIGIN
    # The `share` stamp is also what confines openbrain-mcp's source DEDUP to rows this
    # door can read (eh-ingest R1: OB1 ingest-egress.ts dedupShareScope). Without it a
    # cloud ingest would dedup against - and reveal the id of - a private row.
    md[WRITE_STAMP_FIELD] = WRITE_STAMP_VALUE
    args["metadata_extra"] = md
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

        # The policed arguments must be JSON objects: a JSON string that decodes
        # (strictly) to an object is replaced by the object and then policed;
        # anything else is refused (-32602), never forwarded. Policed pairs:
        # every READ_TOOLS tool -> metadata_filter; every WRITE_TOOLS tool ->
        # metadata_extra (see _force_read_filter / _force_write_extra).
        for tools, key in ((READ_TOOLS, "metadata_filter"), (WRITE_TOOLS, "metadata_extra")):
            if name in tools:
                ok, v = _typed(args.get(key), "object")
                if not ok:
                    return msg, _rpc_error(rpc_id, -32602, f"{name}: {key} must be an object")
                if key in args:
                    args[key] = v

        if name in READ_TOOLS:
            params["arguments"] = _force_read_filter(args)
        elif name in WRITE_TOOLS:
            params["arguments"] = _force_write_extra(args)
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
            t.get("name") in ALLOWED_TOOLS)]
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
    drop = {"host", "content-length", "authorization", "x-brain-key"}
    drop |= _HOP_BY_HOP | _connection_named(req)
    h = {}
    for k, v in req.headers.items():
        if k.lower() in drop:
            continue
        h[k] = v
    h["x-brain-key"] = OPENBRAIN_KEY
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

# --- AUDIT LOG (egress-hardening item eh-ingest, 2026-10-07) ----------------
#
# An append-only JSONL record of what clients DO through this door: one line per
# HTTP request to /mcp, allowed or refused. It records WHO (the key id - a label
# plus a short SHA-256 fingerprint of the configured key, NEVER the key - and the
# client address), WHAT (the JSON-RPC method names and tool names), the DECISION
# (allowed / refused and why, as a fixed code), the RESULT (HTTP status, upstream
# status, ok / error / unknown) and the SIZE (request and response bytes, time).
#
# IT NEVER RECORDS ARGUMENTS OR PAYLOADS. No argument value, no body text, no
# result text and no error message reaches the line. A tool name is logged only when
# it is on this door's allow-list (ALLOWED_TOOLS) and a method name only when it is a
# known MCP method (_KNOWN_METHODS); anything else - a refused tool included - is
# logged as "<unknown>", never verbatim, so a caller-chosen "name" cannot carry a
# payload (or a secret) into the record. The record is built from a fixed set of
# fields, never by copying the request.
#
# Bounded: the file rotates at GATEWAY_AUDIT_LOG_MAX_BYTES (default 5 MiB) into
# GATEWAY_AUDIT_LOG_BACKUPS numbered files (default 4: <path>.1 .. <path>.4, oldest
# dropped), so the log never holds more than (backups + 1) x max bytes.
#
# GATEWAY_AUDIT_LOG unset or empty = OFF (the unit tests and a bare `docker run`).
# Set = ON, and a path that cannot be opened for append STOPS THE GATEWAY AT START
# (a door that was told to keep a record and cannot is a deploy bug, not something
# to discover later). A write that fails at RUN time (disk full) is reported on
# stderr and the request is still served: the record is evidence, not the gate.
# Compose binds the directory under the backups tree, which the NAS sync mirrors.
AUDIT_LOG_PATH = os.environ.get("GATEWAY_AUDIT_LOG", "").strip()
AUDIT_MAX_BYTES = int(os.environ.get("GATEWAY_AUDIT_LOG_MAX_BYTES", str(5 * 1024 * 1024)))
AUDIT_BACKUPS = int(os.environ.get("GATEWAY_AUDIT_LOG_BACKUPS", "4"))
if AUDIT_MAX_BYTES <= 0 or AUDIT_BACKUPS < 0:
    raise ValueError("GATEWAY_AUDIT_LOG_MAX_BYTES must be > 0 and GATEWAY_AUDIT_LOG_BACKUPS >= 0")
KEY_ID = (os.environ.get("GATEWAY_KEY_ID", "").strip()
          or f"{GATEWAY_PROFILE}-{hashlib.sha256(GATEWAY_KEY.encode()).hexdigest()[:8]}")

_KNOWN_METHODS = frozenset((
    "initialize", "ping", "tools/list", "tools/call",
    "resources/list", "resources/read", "resources/templates/list",
    "resources/subscribe", "resources/unsubscribe",
    "prompts/list", "prompts/get", "completion/complete", "logging/setLevel",
    "roots/list", "sampling/createMessage", "elicitation/create",
    "notifications/initialized", "notifications/cancelled", "notifications/progress",
    "notifications/roots/list_changed", "notifications/message",
))
_AUDIT_LIST_CAP = 50


def _known(v, known):
    """v itself only if it is one of the known names; else "<unknown>"."""
    return v if isinstance(v, str) and v in known else "<unknown>"


class AuditLog:
    """Append-only, size-rotated JSONL file. Pure stdlib; one lock per file."""

    def __init__(self, path: str, max_bytes: int, backups: int):
        self.path = path
        self.max_bytes = max_bytes
        self.backups = backups
        self._lock = threading.Lock()
        if path:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            with open(path, "a", encoding="utf-8"):
                pass  # raises here, at start, if it cannot be appended to

    def _rotate(self):
        if self.backups == 0:
            os.remove(self.path)
            return
        oldest = f"{self.path}.{self.backups}"
        if os.path.exists(oldest):
            os.remove(oldest)
        for i in range(self.backups - 1, 0, -1):
            src = f"{self.path}.{i}"
            if os.path.exists(src):
                os.replace(src, f"{self.path}.{i + 1}")
        os.replace(self.path, f"{self.path}.1")

    def write(self, record: dict):
        if not self.path:
            return
        line = json.dumps(record, ensure_ascii=True, separators=(",", ":")) + "\n"
        try:
            with self._lock:
                try:
                    size = os.path.getsize(self.path)
                except OSError:
                    size = 0
                if size and size + len(line) > self.max_bytes:
                    self._rotate()
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(line)
        except OSError as e:
            print(f"openbrain-gateway: audit log write failed ({e.__class__.__name__}); "
                  "request served without a record", file=sys.stderr, flush=True)


AUDIT = AuditLog(AUDIT_LOG_PATH, AUDIT_MAX_BYTES, AUDIT_BACKUPS)


def _audit_calls(msg):
    """(methods, tools) named by a parsed body - names only, never arguments."""
    msgs = msg if isinstance(msg, list) else [msg]
    methods, tools = [], []
    for m in msgs[:_AUDIT_LIST_CAP]:
        if not isinstance(m, dict):
            continue
        meth = m.get("method")
        methods.append(_known(meth, _KNOWN_METHODS))
        if meth == "tools/call":
            params = m.get("params")
            tools.append(_known(params.get("name") if isinstance(params, dict) else None,
                                ALLOWED_TOOLS))
    return methods, tools


def _audit_result(body: bytes, content_type: str) -> str:
    """ok / error / unknown for a JSON or SSE reply, read for STRUCTURE only."""
    msgs = []
    try:
        if "text/event-stream" in content_type.lower():
            for line in _SSE_EOL.split(body.decode("utf-8", "replace")):
                if line.startswith("data:"):
                    try:
                        msgs.append(json.loads(line[5:].strip()))
                    except ValueError:
                        pass
        elif body:
            v = json.loads(body.decode("utf-8", "replace"))
            msgs = v if isinstance(v, list) else [v]
    except ValueError:
        return "unknown"
    seen_result = False
    for m in msgs:
        if not isinstance(m, dict):
            continue
        if "error" in m:
            return "error"
        r = m.get("result")
        if isinstance(r, dict) and r.get("isError") is True:
            return "error"
        if "result" in m:
            seen_result = True
    return "ok" if seen_result else "unknown"


async def health(_request):
    return PlainTextResponse("ok")


async def mcp(request):
    """The door, with its audit record: exactly one line per request."""
    au = {"key_ok": False, "methods": [], "tools": [], "refusal": None,
          "upstream_status": None, "req_bytes": 0}
    t0 = time.monotonic()
    status, resp_bytes, result = 500, 0, "exception"
    try:
        resp = await _mcp_inner(request, au)
        status = resp.status_code
        body = getattr(resp, "body", b"") or b""
        resp_bytes = len(body)
        result = ("refused" if au["refusal"] else
                  _audit_result(body, resp.headers.get("content-type", "")))
        return resp
    finally:
        AUDIT.write({
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "profile": GATEWAY_PROFILE,
            "key_id": KEY_ID if au["key_ok"] else None,
            "client": request.client.host if request.client else None,
            "http_method": request.method,
            "methods": au["methods"],
            "tools": au["tools"],
            "decision": "refused" if au["refusal"] else "allowed",
            "refusal": au["refusal"],
            "status": status,
            "upstream_status": au["upstream_status"],
            "result": result,
            "req_bytes": au["req_bytes"],
            "resp_bytes": resp_bytes,
            "ms": int((time.monotonic() - t0) * 1000),
        })


async def _mcp_inner(request, au):
    # Authenticate the cloud client against the gateway key.
    auth = request.headers.get("authorization", "")
    if auth != f"Bearer {GATEWAY_KEY}":
        au["refusal"] = "unauthorized"
        return _json_response({"error": "unauthorized"}, status_code=401)
    au["key_ok"] = True

    method = request.method
    try:
        body = await _read_capped(request)
    except _TooLarge:
        au["refusal"] = "too_large"
        declared = request.headers.get("content-length", "")
        au["req_bytes"] = int(declared) if declared.strip().isdigit() else None
        return _json_response(
            _rpc_error(None, -32600,
                       f"Request refused by the gateway: body larger than "
                       f"{MAX_BODY_BYTES} bytes."),
            status_code=413)
    au["req_bytes"] = len(body)
    up_headers = _upstream_headers(request)

    short_circuit = None
    out_body = None  # only bytes re-serialised below ever go upstream
    is_tools_list = False
    list_id = None

    if body and method != "POST":
        au["refusal"] = "body_refused"
        return _refuse(f"{method} with a body")
    if method == "POST":
        try:
            msg = _parse_body(body)
        except _BodyRefused as e:
            au["refusal"] = "body_refused"
            return _refuse(str(e))
        au["methods"], au["tools"] = _audit_calls(msg)
        if isinstance(msg, list):  # JSON-RPC batch
            # tools/list is filtered on the way back only for a single request,
            # so inside a batch it is refused rather than answered unfiltered.
            if any(m.get("method") == "tools/list" for m in msg):
                au["refusal"] = "body_refused"
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
        code = (short_circuit.get("error") or {}).get("code")
        au["refusal"] = "tool_not_allowed" if code == -32601 else "bad_arguments"
        return _json_response(short_circuit)

    up_headers.pop("content-length", None)
    timeout = httpx.Timeout(300.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        upstream = await client.request(
            method, f"{OPENBRAIN_URL}/",
            content=out_body,
            headers=up_headers,
            params=dict(request.query_params))
        au["upstream_status"] = upstream.status_code

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
