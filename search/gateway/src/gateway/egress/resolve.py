"""Name resolution for the fetch leg: every A/AAAA answer is judged, one is pinned.

PROXY MODE (the default): the name is resolved by DNS-over-HTTPS, the DoH request itself going
THROUGH the egress proxy, so no lookup leaves the host outside the tunnel (the reason ingest-egress
did not resolve at all in proxy mode - and so could not pin, leaving a name that resolves to a
private address to the proxy's own judgement). The DoH endpoint is fixed configuration, never a
caller's input.
DIRECT MODE (GATE_FETCH_PROXY_URL=""): the system resolver.
"""

from __future__ import annotations

import asyncio
import base64
import socket
from typing import Protocol

import dns.exception
import dns.message
import dns.rcode
import dns.rdatatype
import httpx

from gateway.egress.guard import Refused

MAX_DOH_BYTES = 64 * 1024


class Resolver(Protocol):
    async def resolve(self, host: str) -> list[str]: ...

    async def aclose(self) -> None: ...


class DohResolver:
    def __init__(self, doh_url: str, proxy_url: str | None, timeout_s: float) -> None:
        self._url = doh_url
        self._client = httpx.AsyncClient(
            proxy=proxy_url or None,
            trust_env=False,
            follow_redirects=False,
            timeout=httpx.Timeout(timeout_s),
            headers={"Accept": "application/dns-message"},
        )

    async def _query(self, host: str, rdtype: dns.rdatatype.RdataType) -> list[str]:
        q = dns.message.make_query(host, rdtype)
        q.id = 0  # RFC 8484 4.1: cache-friendly
        param = base64.urlsafe_b64encode(q.to_wire()).rstrip(b"=").decode("ascii")
        try:
            async with self._client.stream("GET", self._url, params={"dns": param}) as r:
                if r.status_code != 200:
                    raise Refused("name resolution failed")
                body = bytearray()
                async for chunk in r.aiter_raw():
                    body += chunk
                    if len(body) > MAX_DOH_BYTES:
                        raise Refused("name resolution failed")
        except httpx.HTTPError:
            raise Refused("name resolution failed") from None
        try:
            msg = dns.message.from_wire(bytes(body))
        except dns.exception.DNSException:
            raise Refused("name resolution failed") from None
        rc = msg.rcode()
        if rc == dns.rcode.NXDOMAIN:
            return []
        if rc != dns.rcode.NOERROR:
            raise Refused("name resolution failed")
        out: list[str] = []
        for rrset in msg.answer:
            if rrset.rdtype in (dns.rdatatype.A, dns.rdatatype.AAAA):
                out.extend(r.to_text() for r in rrset)
        return out

    async def resolve(self, host: str) -> list[str]:
        a, aaaa = await asyncio.gather(self._query(host, dns.rdatatype.A),
                                       self._query(host, dns.rdatatype.AAAA))
        return a + aaaa

    async def aclose(self) -> None:
        await self._client.aclose()


class SystemResolver:
    async def resolve(self, host: str) -> list[str]:
        loop = asyncio.get_running_loop()
        try:
            infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        except socket.gaierror:
            return []
        seen: list[str] = []
        for info in infos:
            addr = str(info[4][0])
            if addr not in seen:
                seen.append(addr)
        return seen

    async def aclose(self) -> None:
        return None
