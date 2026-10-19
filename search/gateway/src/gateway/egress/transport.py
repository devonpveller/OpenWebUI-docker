"""The pinned connection: the gate dials ONLY an address the guard judged, never a name.

``PinnedBackend`` is an httpcore network backend. The fetch asks for ``https://<ip>:<port>/...``
with the real name in the Host header and as the TLS server name (httpcore's ``sni_hostname``
request extension, so the certificate is still checked against the NAME). The backend:

  * refuses any (host, port) the fetch did not ``allow()`` first - a second line behind the guard:
    even a bug that built a URL from an unjudged host cannot open a socket to it;
  * in PROXY mode opens a TCP connection to the proxy and asks ``CONNECT <ip>:<port>`` - the proxy
    is handed an IP literal, so it has nothing to resolve and no name to rebind; plain-http targets
    use the same tunnel, so the pin holds for both schemes;
  * in DIRECT mode connects to the IP itself.

(httpx's own proxy support cannot do this: for a tunnelled request httpcore sets the TLS server
name to the URL's host, which here is the IP, so the certificate check would fail. Hence the
tunnel is built below the connection layer, where ``sni_hostname`` is honoured.)
"""

from __future__ import annotations

import base64
import ipaddress
import typing
from urllib.parse import unquote, urlsplit

import httpcore
from httpcore import AsyncNetworkBackend, AsyncNetworkStream

from gateway.egress.guard import Refused

MAX_CONNECT_REPLY = 16 * 1024


class ProxySpec:
    def __init__(self, url: str) -> None:
        parts = urlsplit(url)
        if parts.scheme != "http" or not parts.hostname:
            raise ValueError("the egress proxy must be an http:// URL with a host")
        self.host = parts.hostname
        self.port = parts.port or 8080
        self.auth: str | None = None
        if parts.username is not None:
            cred = f"{unquote(parts.username)}:{unquote(parts.password or '')}"
            self.auth = base64.b64encode(cred.encode("utf-8")).decode("ascii")


def _authority(host: str, port: int) -> str:
    return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"


class PinnedBackend(AsyncNetworkBackend):
    def __init__(self, proxy: ProxySpec | None) -> None:
        self._inner = httpcore.AnyIOBackend()
        self._proxy = proxy
        self._allowed: set[tuple[str, int]] = set()
        self.dialled: list[str] = []  # what this backend opened (tests read it)

    def allow(self, ip: str, port: int) -> None:
        self._allowed.add((str(ipaddress.ip_address(ip)), port))

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: typing.Iterable[typing.Any] | None = None,
    ) -> AsyncNetworkStream:
        try:
            canon = str(ipaddress.ip_address(host.strip("[]")))
        except ValueError:
            raise Refused("refused: the connection was not to a pinned address") from None
        if (canon, port) not in self._allowed:
            raise Refused("refused: the connection was not to a pinned address")
        self.dialled.append(_authority(canon, port))
        if self._proxy is None:
            return await self._inner.connect_tcp(canon, port, timeout=timeout)
        stream = await self._inner.connect_tcp(self._proxy.host, self._proxy.port, timeout=timeout)
        try:
            target = _authority(canon, port)
            req = f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n"
            if self._proxy.auth:
                req += f"Proxy-Authorization: Basic {self._proxy.auth}\r\n"
            await stream.write((req + "\r\n").encode("ascii"), timeout=timeout)
            reply = b""
            while b"\r\n\r\n" not in reply:
                piece = await stream.read(4096, timeout=timeout)
                if not piece:
                    raise httpcore.ConnectError("the proxy closed the tunnel")
                reply += piece
                if len(reply) > MAX_CONNECT_REPLY:
                    raise httpcore.ConnectError("the proxy reply is too long")
            head, _, rest = reply.partition(b"\r\n\r\n")
            status = head.split(b"\r\n", 1)[0].split(b" ")
            if len(status) < 2 or status[1] != b"200":
                raise httpcore.ConnectError("the proxy refused the tunnel")
            if rest:  # nothing may arrive before we speak
                raise httpcore.ConnectError("the proxy sent bytes before the request")
        except BaseException:
            await stream.aclose()
            raise
        return stream

    async def connect_unix_socket(self, path: str, timeout: float | None = None,
                                  socket_options: typing.Iterable[typing.Any] | None = None,
                                  ) -> AsyncNetworkStream:
        raise Refused("refused: unix sockets are never a target")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)
