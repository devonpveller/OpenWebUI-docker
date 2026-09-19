"""Memory-plane Phase 0.1 — the Open Brain wire protocol.

THE POINT OF THIS FILE: the mirror has never run. `AO_OPENBRAIN_MIRROR_ENABLED` has been
false since P6.2 was written, so the request it would send was never once observed by a
server. Four independent things were wrong with it, and no test could have noticed,
because there was no test that asserted the ENVELOPE — only that nothing blew up.

So these tests assert the exact bytes on the wire against the shape three sources agree
on (`openbrain-gateway/app.py:142,196`, `openbrain-gateway/smoke_test.py:145-156`,
`OB1/integrations/kubernetes-deployment/index.ts:848,2052-2060`):

    POST {url}/  ·  x-brain-key  ·  tools/call  ·  arguments.metadata_extra

`test_red_first_the_old_envelope_is_rejected` is the RED-first record: it reproduces what
the pre-fix `_mirror` sent and asserts a strict server REFUSES it. It fails against the
old implementation and passes against the new one, which is the only way to show these
tests test the bugs rather than the fix.
"""

from __future__ import annotations

import json

import httpx

from app.config import Settings
from app.modules.openbrain_client import OpenBrainClient


def _settings(**kw) -> Settings:
    base = {"openbrain_url": "http://openbrain-mcp:8000", "openbrain_key": "secret-key"}
    return Settings(_env_file=None, **{**base, **kw})


def _recorder(status: int = 200, payload: dict | None = None, *, sse: bool = False):
    """A MockTransport that records the one request it sees and replies as told."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["path"] = request.url.path
        seen["headers"] = {k.lower(): v for k, v in request.headers.items()}
        seen["body"] = json.loads(request.content)
        body = payload if payload is not None else {"jsonrpc": "2.0", "id": 1, "result": {"content": []}}
        if sse:
            return httpx.Response(
                status,
                text="event: message\ndata: " + json.dumps(body) + "\n\n",
                headers={"content-type": "text/event-stream"},
            )
        return httpx.Response(status, json=body)

    return httpx.MockTransport(handler), seen


# ── the envelope ─────────────────────────────────────────────────────────────
async def test_capture_sends_the_verified_mcp_envelope():
    c = OpenBrainClient(_settings())
    c.transport, seen = _recorder()

    assert await c.capture_thought("hello", metadata_extra={"source": "agent-org"}) is True

    # Path: root, NOT /capture_thought. index.ts serves app.all("*") and the gateway — a
    # first-party client of the same server — posts to f"{OPENBRAIN_URL}/".
    assert seen["method"] == "POST"
    assert seen["path"] == "/"
    assert "capture_thought" not in seen["url"]
    # Auth: x-brain-key (index.ts:2053). Bearer is the CLOUD gateway's door, not this one.
    assert seen["headers"]["x-brain-key"] == "secret-key"
    # StreamableHTTP servers 406 without both accept types.
    assert "application/json" in seen["headers"]["accept"]
    assert "text/event-stream" in seen["headers"]["accept"]
    # Body: a JSON-RPC tools/call, not a bare REST payload.
    body = seen["body"]
    assert body["jsonrpc"] == "2.0"
    assert body["method"] == "tools/call"
    assert body["params"]["name"] == "capture_thought"
    assert body["params"]["arguments"]["content"] == "hello"
    # `metadata_extra` — the name in the tool's inputSchema (index.ts:848). `metadata` is
    # not in the schema, so the old call silently carried no provenance at all.
    assert body["params"]["arguments"]["metadata_extra"] == {"source": "agent-org"}
    assert "metadata" not in body["params"]["arguments"]


def _strict_server():
    """A server that accepts ONLY the verified envelope — the four ways the pre-fix call
    was wrong are the four ways this handler says no."""

    def strict(request: httpx.Request) -> httpx.Response:
        if request.url.path != "/":
            return httpx.Response(404, json={"error": "no such route"})
        if request.headers.get("x-brain-key") != "secret-key":
            return httpx.Response(401, json={"error": "unauthorized"})
        body = json.loads(request.content)
        if body.get("method") != "tools/call":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1,
                                             "error": {"code": -32600, "message": "not jsonrpc"}})
        args = body["params"]["arguments"]
        if "metadata" in args:  # not in the tool's inputSchema
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1,
                                             "error": {"code": -32602, "message": "unknown arg"}})
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {"content": []}})

    return httpx.MockTransport(strict)


async def test_the_new_client_satisfies_a_strict_server():
    c = OpenBrainClient(_settings())
    c.transport = _strict_server()
    assert await c.capture_thought("x", metadata_extra={"kind": "k"}) is True


async def test_red_first_the_old_envelope_is_rejected_by_the_same_server():
    """RED FIRST, kept in the suite rather than only in a commit message.

    This sends what `audit_sink._mirror` ACTUALLY SENT before this phase — verbatim, from
    the pre-fix source — at the same strict server the test above satisfies. It is
    rejected. That pins the claim "these tests test the bugs, not the fix": the old shape
    fails and the new shape passes against one unchanged server, and neither can silently
    start agreeing later.

    The pre-fix call (agent-org/agent-bridge/app/modules/audit_sink.py @ 8d71ae7):

        await c.post(f"{openbrain_url.rstrip('/')}/capture_thought",
                     headers={"x-brain-key": openbrain_key},
                     json={"content": text, "metadata": {...}})
    """
    transport = _strict_server()
    async with httpx.AsyncClient(transport=transport) as c:
        r = await c.post(
            "http://openbrain-mcp:8000/capture_thought",          # bug 1: wrong path
            headers={"x-brain-key": "secret-key"},
            json={"content": "x", "metadata": {"source": "agent-org"}},   # bug 4: wrong arg
        )
    assert r.status_code == 404, "the old path must not be accepted"

    # And even at the right path, the old BODY is not a JSON-RPC call at all.
    async with httpx.AsyncClient(transport=transport) as c:
        r = await c.post(
            "http://openbrain-mcp:8000/",
            headers={"x-brain-key": "secret-key"},
            json={"content": "x", "metadata": {"source": "agent-org"}},
        )
    assert r.json().get("error", {}).get("code") == -32600

    # bug 3: the old DEFAULT host was the cloud gateway, which the bridge cannot resolve.
    assert Settings(_env_file=None).openbrain_url != "http://openbrain-gateway:8061"


async def test_default_url_is_the_first_party_lane_not_the_cloud_gateway():
    """openbrain-gateway is obnet-only + Bearer + allowlisted; agent-bridge (ao-net +
    llm-net) cannot resolve it. The default must be the lane it CAN reach."""
    assert Settings(_env_file=None).openbrain_url == "http://openbrain-mcp:8000"


async def test_trailing_slash_is_normalised_not_doubled():
    c = OpenBrainClient(_settings(openbrain_url="http://openbrain-mcp:8000/"))
    c.transport, seen = _recorder()
    await c.capture_thought("x")
    assert seen["url"] == "http://openbrain-mcp:8000/"


async def test_metadata_extra_omitted_when_empty():
    """The arg is optional (metadataExtraArg is .optional()); don't send an empty object."""
    c = OpenBrainClient(_settings())
    c.transport, seen = _recorder()
    await c.capture_thought("x")
    assert "metadata_extra" not in seen["body"]["params"]["arguments"]


# ── success is narrower than "HTTP 200" ──────────────────────────────────────
async def test_sse_response_is_understood():
    """StreamableHTTP may answer as an event stream; the gateway handles both, so do we."""
    c = OpenBrainClient(_settings())
    c.transport, _ = _recorder(sse=True)
    assert await c.capture_thought("x") is True


async def test_tool_level_error_at_http_200_is_a_failure():
    """MCP reports TOOL failure as 200 + result.isError. Trusting the status code alone is
    how unwritten events get marked written."""
    c = OpenBrainClient(_settings())
    c.transport, _ = _recorder(payload={"jsonrpc": "2.0", "id": 1,
                                        "result": {"isError": True, "content": [{"text": "boom"}]}})
    assert await c.capture_thought("x") is False


async def test_jsonrpc_error_at_http_200_is_a_failure():
    c = OpenBrainClient(_settings())
    c.transport, _ = _recorder(payload={"jsonrpc": "2.0", "id": 1,
                                        "error": {"code": -32602, "message": "bad params"}})
    assert await c.capture_thought("x") is False


async def test_non_200_is_a_failure():
    c = OpenBrainClient(_settings())
    c.transport, _ = _recorder(status=500)
    assert await c.capture_thought("x") is False


async def test_unparseable_body_is_a_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>not json</html>",
                              headers={"content-type": "text/html"})

    c = OpenBrainClient(_settings())
    c.transport = httpx.MockTransport(handler)
    assert await c.capture_thought("x") is False


# ── best-effort: never raises ────────────────────────────────────────────────
async def test_transport_error_returns_false_and_never_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("open brain is down", request=request)

    c = OpenBrainClient(_settings())
    c.transport = httpx.MockTransport(handler)
    assert await c.capture_thought("x") is False


async def test_timeout_returns_false_and_never_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow", request=request)

    c = OpenBrainClient(_settings())
    c.transport = httpx.MockTransport(handler)
    assert await c.capture_thought("x") is False
