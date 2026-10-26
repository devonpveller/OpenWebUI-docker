"""The guarded fetch: the gate's ONE way to read a page (audit A1/A5/B3/B4).

Per request, under ONE deadline (GATE_FETCH_TIMEOUT_SECONDS) that covers resolution, every hop and
the whole body:

  1. the URL is judged (guard.check_url: scheme, userinfo, port, internal and wildcard names,
     literal addresses)
  2. a name is resolved (resolve.py) and EVERY answer judged; one is pinned (guard.check_addresses)
  3. the request goes to the pinned address only (transport.PinnedBackend), the name kept for the
     Host header and the TLS certificate check
  4. a redirect is never followed by the HTTP client: its Location is resolved against the current
     URL and goes back to step 1, so every hop - the FINAL url included - is judged and pinned
     before it is requested; at most GATE_FETCH_MAX_REDIRECTS
  5. the final response must carry an allowed content type (an empty one is refused) and an
     encoding we can bound (identity, gzip, deflate)
  6. the body is read as it streams and cut at GATE_FETCH_MAX_BYTES - counted on the wire AND after
     decompression, so neither an oversize page nor a decompression bomb is ever held whole

A refusal raises ``Refused`` with a KIND; callers get only guard.REASONS[kind] (fixed text), never
the message, which may quote response values. A 3xx WITHOUT a Location is not a redirect: it is
returned as an answer with that status, and the gate reports it as an HTTP error. Nothing here
logs a URL.
"""

from __future__ import annotations

import asyncio
import zlib
from dataclasses import dataclass, field
from urllib.parse import urljoin

import httpcore

from gateway.egress.guard import Refused, Target, check_addresses, check_url
from gateway.egress.resolve import Resolver
from gateway.egress.transport import PinnedBackend, ProxySpec

# A common browser's headers: a distinctive user agent would fingerprint the gate across sites.
REQUEST_HEADERS = [
    (b"User-Agent", b"Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 "
                    b"Firefox/128.0"),
    (b"Accept", b"text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.5"),
    (b"Accept-Language", b"en-US,en;q=0.5"),
    (b"Accept-Encoding", b"gzip, deflate"),
    (b"Connection", b"close"),
]


class FetchError(Exception):
    """A network failure (not a policy refusal). The message is generic."""


@dataclass(frozen=True)
class FetchPolicy:
    proxy_url: str  # "" = direct
    max_bytes: int
    timeout_s: float
    max_redirects: int
    ports: frozenset[int]
    content_types: frozenset[str]


@dataclass
class Fetched:
    requested_url: str
    final_url: str
    redirects: list[str] = field(default_factory=list)
    status: int = 0
    content_type: str = ""  # the full header value (charset kept)
    body: bytes = b""
    via: str = "proxy"


def media_type(value: str) -> str:
    return value.split(";", 1)[0].strip().lower()


def _decoder(encoding: str) -> zlib._Decompress | None:
    enc = encoding.strip().lower()
    if enc in ("", "identity"):
        return None
    if enc in ("gzip", "x-gzip"):
        return zlib.decompressobj(16 + zlib.MAX_WBITS)
    if enc == "deflate":
        return zlib.decompressobj()
    raise Refused(f"content encoding {enc!r} is not supported", "encoding")


def _header(headers: list[tuple[bytes, bytes]], name: bytes) -> str | None:
    values = [v for k, v in headers if k.lower() == name]
    if not values:
        return None
    return b",".join(values).decode("latin-1")


class Fetcher:
    def __init__(self, policy: FetchPolicy, resolver: Resolver) -> None:
        self.policy = policy
        self.resolver = resolver
        self._proxy = ProxySpec(policy.proxy_url) if policy.proxy_url else None

    async def fetch(self, url: str) -> Fetched:
        try:
            async with asyncio.timeout(self.policy.timeout_s):
                return await self._fetch(url)
        except TimeoutError:
            raise Refused(
                f"time cap: no complete answer within {self.policy.timeout_s:g}s", "time") from None

    async def _pin(self, t: Target) -> str:
        if t.ip_literal is not None:
            return str(t.ip_literal)
        try:
            return check_addresses(t.host, await self.resolver.resolve(t.host))
        except Refused as e:
            raise Refused(str(e), "resolve") from None

    async def _fetch(self, url: str) -> Fetched:
        p = self.policy
        current = check_url(url, p.ports)
        out = Fetched(requested_url=current.url, final_url=current.url,
                      via="proxy" if self._proxy else "direct")
        backend = PinnedBackend(self._proxy)
        async with httpcore.AsyncConnectionPool(network_backend=backend, http1=True, http2=False,
                                                retries=0, max_connections=1) as pool:
            for hop in range(p.max_redirects + 1):
                ip = await self._pin(current)
                backend.allow(ip, current.port)
                pinned = f"[{ip}]" if ":" in ip else ip
                req_url = f"{current.scheme}://{pinned}:{current.port}{current.path_qs}"
                ext = {"sni_hostname": current.host} if current.scheme == "https" else {}
                try:
                    async with pool.stream("GET", req_url,
                                           headers=[(b"Host", current.host_header.encode("ascii")),
                                                    *REQUEST_HEADERS],
                                           extensions=ext) as resp:
                        location = _header(resp.headers, b"location")
                        if 300 <= resp.status < 400 and location:
                            if hop >= p.max_redirects:
                                raise Refused(f"more than {p.max_redirects} redirects",
                                              "redirects")
                            nxt = urljoin(current.url, location.strip())
                            try:  # every hop judged, final included
                                current = check_url(nxt, p.ports)
                            except Refused as e:
                                raise Refused(str(e), "redirect-target") from None
                            out.redirects.append(current.url)
                            out.final_url = current.url
                            continue
                        out.status = resp.status
                        out.content_type = _header(resp.headers, b"content-type") or ""
                        if resp.status >= 300:
                            return out  # no body for an error status; the gate reports it
                        mt = media_type(out.content_type)
                        if not mt or mt not in p.content_types:
                            raise Refused(f"content type {mt or '(none)'} is not allowed", "type")
                        dec = _decoder(_header(resp.headers, b"content-encoding") or "")
                        out.body = await self._read_capped(resp, dec)
                        return out
                except (httpcore.NetworkError, httpcore.ProtocolError, httpcore.TimeoutException,
                        OSError) as e:
                    raise FetchError(f"network error ({type(e).__name__})") from None
        raise Refused(f"more than {p.max_redirects} redirects", "redirects")  # pragma: no cover

    async def _read_capped(self, resp: httpcore.Response, dec: zlib._Decompress | None) -> bytes:
        cap = self.policy.max_bytes
        body = bytearray()
        wire = 0
        stream = resp.aiter_stream()
        async for chunk in stream:  # type: ignore[union-attr]
            wire += len(chunk)
            if wire > cap:
                raise Refused(f"byte cap: the body is over {cap} bytes", "size")
            if dec is None:
                body += chunk
            else:
                feed = chunk
                while feed:
                    try:
                        body += dec.decompress(feed, cap - len(body) + 1)
                    except zlib.error:
                        raise Refused("the body is not validly compressed", "encoding") from None
                    if len(body) > cap:
                        raise Refused(f"byte cap: the decoded body is over {cap} bytes", "size")
                    feed = dec.unconsumed_tail
            if len(body) > cap:
                raise Refused(f"byte cap: the body is over {cap} bytes", "size")
        return bytes(body)
