"""Transport — a streaming reverse proxy to the upstream (design §10.2 (i)).

SSE token streams must pass through UNBUFFERED end-to-end or OWUI's live render
breaks and latency balloons (design §7.3). We open the upstream request in
streaming mode, relay status + headers immediately, then forward raw body chunks
as they arrive. The permit is held until the stream finishes (release happens in
the route's generator ``finally``), and aborting the stream (client disconnect)
closes the upstream connection, evicting the dead request (§10.3.4).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx

# Hop-by-hop headers we must not forward in either direction (RFC 7230 §6.1) plus
# length/encoding headers that the proxy re-frames itself.
_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
    "content-encoding",
}


def filter_request_headers(headers: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in headers.items() if k.lower() not in _HOP_BY_HOP}


def filter_response_headers(headers: httpx.Headers) -> dict[str, str]:
    return {k: v for k, v in headers.items() if k.lower() not in _HOP_BY_HOP}


class Upstream:
    """One pooled httpx client per upstream base URL."""

    def __init__(self, timeout_s: float) -> None:
        self._timeout = httpx.Timeout(timeout_s, connect=10.0)
        self._clients: dict[str, httpx.AsyncClient] = {}

    def _client(self, base_url: str) -> httpx.AsyncClient:
        client = self._clients.get(base_url)
        if client is None:
            client = httpx.AsyncClient(base_url=base_url, timeout=self._timeout)
            self._clients[base_url] = client
        return client

    @asynccontextmanager
    async def stream(
        self,
        base_url: str,
        method: str,
        path: str,
        *,
        headers: dict[str, str],
        content: bytes,
    ) -> AsyncIterator[httpx.Response]:
        """Open a streaming upstream request. Yields the response with status +
        headers populated but body not yet consumed."""
        client = self._client(base_url)
        req = client.build_request(method, path, headers=headers, content=content)
        resp = await client.send(req, stream=True)
        try:
            yield resp
        finally:
            await resp.aclose()

    async def open_stream(
        self,
        base_url: str,
        method: str,
        path: str,
        *,
        headers: dict[str, str],
        content: bytes,
    ) -> httpx.Response:
        """Open a streaming upstream request WITHOUT a context manager. The
        caller owns the lifecycle and MUST ``await resp.aclose()`` (typically in
        a StreamingResponse generator's ``finally``) — this is what lets the
        permit be held for the whole stream and the upstream connection be
        aborted on client disconnect (design §10.3.4)."""
        client = self._client(base_url)
        req = client.build_request(method, path, headers=headers, content=content)
        return await client.send(req, stream=True)

    async def request(
        self,
        base_url: str,
        method: str,
        path: str,
        *,
        headers: dict[str, str],
        content: bytes | None = None,
    ) -> httpx.Response:
        """Non-streaming passthrough (e.g. GET /v1/models)."""
        client = self._client(base_url)
        return await client.request(method, path, headers=headers, content=content)

    async def aclose(self) -> None:
        for client in self._clients.values():
            await client.aclose()
        self._clients.clear()
