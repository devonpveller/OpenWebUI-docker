"""search-post-gate, acceptance line 2: the gate resolves public names through Mullvad's DoH.

ROOT CAUSE (measured live 2026-10-31, journal/evidence/search-post-gate/live/probes.txt): Mullvad's
DoH endpoints (dns.mullvad.net, base.dns.mullvad.net) are HTTP/2-only. The gate's DoH client spoke
HTTP/1.1 only (httpx without the ``h2`` extra), so every lookup died with RemoteProtocolError and
every /v1/fetch was refused with the ``resolve`` reason. Cloudflare answers HTTP/1.1, which is why
the same proxy "worked" for it.

The endpoint here is tests/h2_doh.py, the stand-in with Mullvad's measured shape. The CONNECT proxy
is FakeInternet with a tunnel to it: the DoH request goes THROUGH the proxy, as in compose.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import certifi
import httpx
import pytest

from fakes import PUBLIC_IP, FakeInternet
from gate_support import running_gate
from gateway.egress.guard import REASONS
from gateway.egress.resolve import DohResolver
from h2_doh import DOH_HOST, H2DohServer, make_pki

DOH_URL = f"https://{DOH_HOST}/dns-query"
TARGET = f"{DOH_HOST}:443"


@pytest.fixture
def doh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[H2DohServer]:
    """The h2-only endpoint, and a trust store that holds the lab CA (httpx reads certifi's)."""
    make_pki(tmp_path / "pki")
    bundle = tmp_path / "bundle.pem"
    bundle.write_bytes(Path(certifi.where()).read_bytes() + b"\n"
                       + (tmp_path / "pki" / "ca.pem").read_bytes())
    monkeypatch.setattr(certifi, "where", lambda: str(bundle))
    srv = H2DohServer(tmp_path / "pki").start()
    try:
        yield srv
    finally:
        srv.stop()


@pytest.fixture
def internet(doh: H2DohServer) -> Iterator[FakeInternet]:
    net = FakeInternet().start()
    net.tunnels[TARGET] = ("127.0.0.1", doh.port)
    try:
        yield net
    finally:
        net.stop()


async def test_the_standin_has_mullvads_measured_shape(internet: FakeInternet,
                                                        doh: H2DohServer) -> None:
    """An HTTP/1.1-only client gets no answer from it - the drop the live gate shows."""
    async with httpx.AsyncClient(proxy=internet.url, trust_env=False, timeout=5,
                                 headers={"Accept": "application/dns-message"}) as c:
        with pytest.raises(httpx.RemoteProtocolError):
            await c.get(DOH_URL, params={"dns": "AAABAAABAAAAAAAAB2V4YW1wbGUDY29tAAABAAE"})
    assert doh.queries == [] and internet.connects == [TARGET]


async def test_the_resolver_resolves_through_an_h2_only_doh(internet: FakeInternet,
                                                             doh: H2DohServer) -> None:
    r = DohResolver(DOH_URL, internet.url, 5.0)
    try:
        assert await r.resolve("public.example.com") == [PUBLIC_IP]
        assert await r.resolve("v6only.example.com") == ["2606:2800:21f:cb07:6820:80da:af6b:8b2c"]
    finally:
        await r.aclose()
    assert doh.alpn and set(doh.alpn) == {"h2"} and doh.http1_attempts == 0
    # every DoH connection arrived through the proxy: nothing reached the endpoint around it
    assert doh.connections >= 1 and doh.connections == internet.connects.count(TARGET)


@pytest.fixture
def gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, doh: H2DohServer):
    with running_gate(tmp_path, monkeypatch, {"GATE_DOH_URL": DOH_URL}) as g:
        g.internet.tunnels[TARGET] = ("127.0.0.1", doh.port)
        yield g


def _fetch(gate, url: str) -> dict:
    return gate.client.post("/v1/fetch", json={"url": url}, headers=gate.auth()).json()


def test_fetch_of_a_public_url_is_ok_with_the_h2_doh(gate, doh: H2DohServer) -> None:
    body = _fetch(gate, "http://public.example.com/page")
    assert body["status"] == "ok", body
    assert "ordinary article about bread" in body["text"]
    # the DoH CONNECT first, then the page by the PINNED ip - both through the proxy
    assert gate.internet.connects == [TARGET, TARGET, f"{PUBLIC_IP}:80"] or \
        gate.internet.connects[-1] == f"{PUBLIC_IP}:80"
    assert set(gate.internet.connects[:-1]) == {TARGET}
    assert {"public.example.com. IN A", "public.example.com. IN AAAA"} <= set(doh.queries)
    assert gate.internet.gets == [], "no plain-HTTP lookup went anywhere"


@pytest.mark.parametrize("host", ["private.example.com", "loopback.example.com",
                                  "mixed.example.com", "v6private.example.com",
                                  "metadata.example.com"])
def test_every_answer_is_still_judged(gate, host: str) -> None:
    body = _fetch(gate, f"http://{host}/secret")
    assert body["status"] == "refused" and body["reason"] == REASONS["resolve"], body
    assert set(gate.internet.connects) <= {TARGET}, "a refused name is never dialled"


def test_a_name_that_does_not_exist_is_refused(gate) -> None:
    body = _fetch(gate, "http://nxdomain.example.com/")
    assert body["status"] == "refused" and body["reason"] == REASONS["resolve"], body
