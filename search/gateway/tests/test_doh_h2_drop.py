"""search-post-gate3: a DoH connection that goes away mid-body, other lookups multiplexed on it.

The gate keeps ONE long-lived DohResolver (one httpx client, one HTTP/2 connection) and lets
GATE_FETCH_CONCURRENCY lookups share it. When the DoH server closes that connection after the
response headers, the lookups written to it afterwards fail in httpcore's HTTP/2 WRITE path with
``anyio.EndOfStream`` - neither an ``httpx.HTTPError`` nor an ``h2`` error - and the gate answered
500 (measured by the search-post-gate2 tester: 4 of 200 fetches under uvicorn, 20 of 40 follow-ups
at the Fetcher, 5 of 15 at the resolver). TestClient does not hit the window, so these tests drive
the resolver, the Fetcher and a real uvicorn server directly. The contract (resolve.py): every
failure of the network call is a ``Refused``.

Rounds: DOH_DROP_ROUNDS (default 12 resolver / 10 fetcher / 8 uvicorn); the tester's loop is 40.
"""

from __future__ import annotations

import asyncio
import collections
import os
import socket
import threading
import time

import httpx
import pytest
import uvicorn

from fakes import FakeEngine, FakeIntake, FakeInternet
from gate_support import CALLERS, INTAKE_KEY, _env
from gateway.egress.fetch import Fetcher, FetchError, FetchPolicy
from gateway.egress.guard import Refused
from gateway.egress.resolve import DohResolver
from h2_doh import H2DohServer
from test_doh_h2 import DOH_URL, TARGET, doh, internet  # noqa: F401  (the lab fixtures)

from conftest import FakeRedis

ROUNDS = int(os.environ.get("DOH_DROP_ROUNDS", "0"))
# the later lookups start while the first connection is still open and about to go away (it closes
# 0.5 s after the first request); the server is healthy by then, so a NEW connection would answer
OFFSETS = [0.40 + 0.01 * i for i in range(4)]


async def _resolve(r: DohResolver) -> str:
    try:
        await r.resolve("public.example.com")
        return "ok"
    except Refused:
        return "refused"
    except BaseException as e:  # noqa: BLE001
        return f"LEAK {type(e).__module__}.{type(e).__name__}"


async def _fetch(f: Fetcher) -> str:
    try:
        await f.fetch("http://public.example.com/page")
        return "ok"
    except Refused as e:
        return f"refused/{e.kind}"
    except FetchError:
        return "error"
    except BaseException as e:  # noqa: BLE001
        return f"LEAK {type(e).__module__}.{type(e).__name__}"


async def test_lookups_multiplexed_on_a_dropped_connection_are_refused_not_raised(
        internet: FakeInternet, doh: H2DohServer) -> None:  # noqa: F811
    seen: collections.Counter[str] = collections.Counter()
    for _ in range(ROUNDS or 12):
        doh.mode = "fin-mid-body"
        r = DohResolver(DOH_URL, internet.url, 5.0)
        try:
            async def later(d: float, r: DohResolver = r) -> str:
                await asyncio.sleep(d)
                doh.mode = "ok"
                return await _resolve(r)
            for res in await asyncio.gather(_resolve(r), *[later(d) for d in OFFSETS]):
                seen[res] += 1
            doh.mode = "ok"
            for res in await asyncio.gather(*[_resolve(r) for _ in range(4)]):   # no gap
                seen[res] += 1
        finally:
            await r.aclose()
    print(f"\nRESOLVER rounds: {dict(seen)}")
    assert not [k for k in seen if k.startswith("LEAK")], dict(seen)
    assert seen["refused"] >= 1, "the drop must actually have happened"


async def test_fetcher_after_a_dropped_connection_never_raises_past_the_contract(
        internet: FakeInternet, doh: H2DohServer) -> None:  # noqa: F811
    """The gate's own code path (fetch_and_screen catches only Refused and FetchError)."""
    seen: collections.Counter[str] = collections.Counter()
    for _ in range(ROUNDS or 10):
        doh.mode = "fin-mid-body"
        r = DohResolver(DOH_URL, internet.url, 5.0)
        f = Fetcher(FetchPolicy(proxy_url=internet.url, max_bytes=2 * 1024 * 1024, timeout_s=8,
                                max_redirects=5, ports=frozenset({80, 443}),
                                content_types=frozenset({"text/html"})), r)
        try:
            first = await _fetch(f)              # the DoH connection goes away mid-body
            doh.mode = "ok"
            rest = await asyncio.gather(*[_fetch(f) for _ in range(4)])   # 4 concurrent, no gap
            for x in [first, *rest]:
                seen[x] += 1
        finally:
            await r.aclose()
    print(f"\nFETCHER rounds (1 during the drop + 4 concurrent after): {dict(seen)}")
    assert not [k for k in seen if k.startswith("LEAK")], dict(seen)


def test_uvicorn_never_answers_500_after_a_dropped_doh_connection(
        tmp_path, monkeypatch: pytest.MonkeyPatch, doh: H2DohServer) -> None:  # noqa: F811
    """The production shape: the gate on uvicorn (one event loop), real HTTP, concurrent fetches."""
    engine, net = FakeEngine().start(), FakeInternet().start()
    intake = FakeIntake(INTAKE_KEY).start()
    net.tunnels[TARGET] = ("127.0.0.1", doh.port)
    for k, v in _env(tmp_path, engine, net, intake,
                     {"GATE_DOH_URL": DOH_URL, "GATE_FETCH_TIMEOUT_SECONDS": "8"}).items():
        monkeypatch.setenv(k, v)
    from gateway import config, main
    config.get_settings.cache_clear()
    monkeypatch.setattr(main, "make_redis", lambda *a, **k: FakeRedis())
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    server = uvicorn.Server(uvicorn.Config(main.app, host="127.0.0.1", port=port,
                                           log_level="warning", access_log=False))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    while not server.started:
        time.sleep(0.05)
    codes: collections.Counter[tuple[int, str]] = collections.Counter()
    hdr = {"Authorization": f"Bearer {CALLERS['owui']}"}

    async def drive() -> None:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=20,
                                     trust_env=False) as c:
            async def post() -> tuple[int, str]:
                r = await c.post("/v1/fetch", json={"url": "http://public.example.com/page"},
                                 headers=hdr)
                return (r.status_code, r.json().get("status") if r.status_code == 200 else "-")

            async def later(d: float) -> tuple[int, str]:
                await asyncio.sleep(d)
                doh.mode = "ok"
                return await post()
            for rnd in range(ROUNDS or 8):
                doh.mode = "fin-mid-body"
                offs = [0.40 + 0.01 * ((rnd + i) % 25) for i in range(4)]
                for res in await asyncio.gather(post(), *[later(d) for d in offs]):
                    codes[res] += 1
                await asyncio.sleep(0.6)
    try:
        asyncio.run(drive())
    finally:
        server.should_exit = True
        t.join(10)
        engine.stop()
        net.stop()
        intake.stop()
        config.get_settings.cache_clear()
    print(f"\nUVICORN rounds (1 fetch whose DoH drops mid-body + 4 in flight): {dict(codes)}")
    assert not [c for c in codes if c[0] == 500], dict(codes)
