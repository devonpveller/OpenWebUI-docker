"""openbrain-client — the wire protocol for first-party writes into Open Brain.

ONE place owns how we talk to the Open Brain MCP server, so the audit mirror (P6.2)
and every later memory-plane writer share a single verified envelope instead of each
hand-rolling one. The previous inline version in `audit_sink._mirror` was never
exercised against a live server (the flag has been off since it was written) and was
wrong in four independent ways — see the wire notes below.

THE WIRE (verified 2026-08-29 against three independent sources, not inferred):

  POST {AO_OPENBRAIN_URL}/                        -- root path, NOT /capture_thought
    headers: x-brain-key: <MCP_ACCESS_KEY>
             Content-Type: application/json
             Accept: application/json, text/event-stream
    body:    {"jsonrpc":"2.0","id":1,"method":"tools/call",
              "params":{"name":"capture_thought",
                        "arguments":{"content":..., "metadata_extra":{...}}}}

  - Path + auth + envelope: `openbrain-gateway/app.py:142,196` — the gateway is a
    first-party client of the same server and forwards `x-brain-key` to
    `f"{OPENBRAIN_URL}/"`. `OB1/integrations/kubernetes-deployment/index.ts:2052-2060`
    serves `app.all("*")`, so any path routes to the MCP transport, and reads
    `x-brain-key` (`:2053`) for auth.
  - No handshake needed: index.ts builds a NEW `StreamableHTTPTransport` per request
    (`:2058`), i.e. stateless — a bare `tools/call` cannot depend on a prior
    `initialize`, which is exactly what `openbrain-gateway/smoke_test.py:145-156`
    proves by capturing successfully through a proxy that opens a fresh upstream
    request per call.
  - Argument name is `metadata_extra` (`index.ts:848`, `metadataExtraArg` at `:361`),
    NOT `metadata`. A `metadata` key is not in the input schema, so the old call
    would have lost its provenance even if the path had been right.

  Responses may come back as JSON *or* as SSE (`text/event-stream`) — the gateway
  handles both (`app.py:203-223`), so we do too.

WHY WE GO DIRECT TO openbrain-mcp, NOT THE GATEWAY: `openbrain-gateway` is the CLOUD
door — obnet-only (`OB1/docker/docker-compose.yml`), Bearer-auth, compile-time tool
allowlist. agent-bridge (`ao-net` + `llm-net`) cannot resolve it at all; it shares
`llm-net` with `openbrain-mcp`, which is the first-party lane. The old default
`http://openbrain-gateway:8061` was unresolvable from the bridge, so the mirror could
only ever have failed.

BEST-EFFORT CONTRACT: `capture` returns True/False and NEVER raises. Mirroring is
observability, not correctness — an Open Brain outage must not take the bridge down or
block an audit write. Callers use the boolean to decide whether to mark an event
mirrored; anything falsy means "not durably written, leave it unmirrored".
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from ..config import Settings

log = logging.getLogger("agent_bridge.openbrain")


def _parse_jsonrpc(response: httpx.Response) -> dict[str, Any] | None:
    """Pull the JSON-RPC message out of a JSON or SSE response. None if unparseable."""
    ct = response.headers.get("content-type", "")
    if "text/event-stream" in ct:
        # Take the LAST data: line — a stream may carry progress notifications before
        # the result, and the result is what we are judging success on.
        found: dict[str, Any] | None = None
        for line in response.text.splitlines():
            if line.startswith("data:"):
                try:
                    msg = json.loads(line[5:].strip())
                except ValueError:
                    continue
                if isinstance(msg, dict) and ("result" in msg or "error" in msg):
                    found = msg
        return found
    try:
        msg = json.loads(response.text)
    except ValueError:
        return None
    return msg if isinstance(msg, dict) else None


class OpenBrainClient:
    """Minimal MCP `tools/call` client for the first-party Open Brain lane."""

    def __init__(self, settings: Settings) -> None:
        self.s = settings
        self.transport: httpx.BaseTransport | None = None   # injectable for tests

    def _headers(self) -> dict[str, str]:
        return {
            "x-brain-key": self.s.openbrain_key,
            "Content-Type": "application/json",
            # StreamableHTTP servers may answer either way; accept both or they 406.
            "Accept": "application/json, text/event-stream",
        }

    async def call_tool(
        self, name: str, arguments: dict[str, Any], *, timeout: float = 15.0
    ) -> bool:
        """Invoke an MCP tool. True ONLY on a genuinely successful write.

        Success requires all three, because any one of them alone lies:
          - HTTP 200                     (transport reached the server)
          - no top-level JSON-RPC error  (the server accepted the method + params)
          - result.isError is not true   (the TOOL itself did not fail)
        An MCP server reports tool failure as HTTP 200 with `result.isError: true`, so
        checking the status code alone would mark unwritten events as written.
        """
        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
        url = self.s.openbrain_url.rstrip("/") + "/"
        try:
            async with httpx.AsyncClient(
                timeout=timeout, transport=self.transport
            ) as c:
                r = await c.post(url, headers=self._headers(), json=body)
            if r.status_code != 200:
                log.warning("open-brain %s: HTTP %s", name, r.status_code)
                return False
            msg = _parse_jsonrpc(r)
            if msg is None:
                log.warning("open-brain %s: unparseable response", name)
                return False
            if msg.get("error"):
                log.warning("open-brain %s: json-rpc error %s", name, msg["error"])
                return False
            result = msg.get("result")
            if not isinstance(result, dict) or result.get("isError"):
                log.warning("open-brain %s: tool reported failure %s", name, result)
                return False
            return True
        except Exception as exc:  # noqa: BLE001 - best-effort lane, never propagates
            log.warning("open-brain %s failed: %s", name, exc)
            return False

    async def capture_thought(
        self,
        content: str,
        *,
        metadata_extra: dict[str, Any] | None = None,
        timeout: float = 15.0,
    ) -> bool:
        """Capture one thought. `metadata_extra` is merged into the stored row."""
        args: dict[str, Any] = {"content": content}
        if metadata_extra:
            args["metadata_extra"] = metadata_extra
        return await self.call_tool("capture_thought", args, timeout=timeout)
