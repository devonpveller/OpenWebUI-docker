"""ao-queue: shedding is folded into episodes - one start, one clear - and exposed on /healthz."""

from __future__ import annotations

import asyncio

import httpx

from llm_queue.app_state import AppState
from llm_queue.config import Settings
from llm_queue.main import app
from llm_queue.shedding import ShedMonitor


class _Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def _mon(cap=128, quiet=300.0):
    c = _Clock()
    return ShedMonitor(cap=cap, clear_quiet_s=quiet, wall=c, mono=c), c


def test_thresholds_default_cap():
    m, _ = _mon()
    assert (m.alert_at, m.clear_at) == (96, 64)


def test_one_episode_for_a_burst_of_refusals():
    m, c = _mon()
    ts = [m.observe(128, refused=True) for _ in range(46)]
    assert ts.count("started") == 1 and ts.count("cleared") == 0
    assert m.current.refusals == 46 and m.refusals_total == 46
    # held drops but a refusal was recent: still the same episode
    c.t += 10
    assert m.observe(10) is None
    c.t += 300
    assert m.observe(10) == "cleared"
    assert m.current is None and m.last.refusals == 46 and m.last.peak_held == 128
    assert m.observe(10) is None  # cleared once


def test_held_threshold_starts_before_any_refusal_and_hysteresis_holds():
    m, c = _mon()
    assert m.observe(95) is None
    assert m.observe(96) == "started"
    assert m.current.trigger == "held_threshold"
    # swinging across the alert line and above clear_at is the same episode
    for h in (90, 97, 80, 99, 65):
        c.t += 60
        assert m.observe(h) is None
    c.t += 60
    assert m.observe(64) == "cleared"
    # a new crossing is a NEW episode
    assert m.observe(100) == "started" and m.current.id == 2


def test_snapshot_shape():
    m, _ = _mon(cap=8)
    m.observe(8, refused=True)
    s = m.snapshot(8)
    assert s["state"] == "shedding" and s["current"]["id"] == 1 and s["last"] is None
    assert s["cap"] == 8 and s["alert_at"] == 6 and s["clear_at"] == 4


# ---- through the app: exactly one start + one clear event, healthz shows it ----------------


class _SlowResp:
    status_code = 200
    headers = httpx.Headers({"content-type": "text/event-stream"})

    def __init__(self, gate: asyncio.Event):
        self._gate = gate

    async def aiter_raw(self):
        await self._gate.wait()
        yield b"data: [DONE]\n\n"

    async def aclose(self):
        pass


class _GatedUpstream:
    def __init__(self):
        self.gate = asyncio.Event()

    async def open_stream(self, *a, **k):
        return _SlowResp(self.gate)

    async def request(self, *a, **k):
        return httpx.Response(200, json={"data": []})

    async def aclose(self):
        pass


async def test_app_sheds_once_and_clears_once():
    s = Settings()
    s.slots, s.max_in_flight, s.backstop_depth = 2, 2, 24
    s.max_total_connections = 4
    s.shed_clear_quiet_s = 0.0
    st = AppState(s)
    st.upstream = _GatedUpstream()
    sent: list[str] = []  # the stubbed sender: every event the sink would record

    async def _emit(event, **fields):
        sent.append(event)

    st.events.emit = _emit  # type: ignore[method-assign]
    app.state.app = st
    await st.start()
    body = {"model": "qwen36-27b", "stream": False, "messages": [{"role": "user", "content": "x"}]}
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://queue", timeout=10
        ) as c:
            holders = [asyncio.create_task(c.post("/v1/chat/completions", json=body))
                       for _ in range(4)]
            for _ in range(100):
                if st.registry.held_total == 4:
                    break
                await asyncio.sleep(0.01)
            refused = await asyncio.gather(
                *(c.post("/v1/chat/completions", json=body) for _ in range(10))
            )
            assert [r.status_code for r in refused] == [503] * 10
            h = (await c.get("/healthz")).json()["shedding"]
            assert h["state"] == "shedding" and h["current"]["refusals"] == 10
            st.upstream.gate.set()
            done = await asyncio.gather(*holders)
            assert all(r.status_code == 200 for r in done)
            for _ in range(3):  # several quiet ticks: still exactly one clear
                await st.note_shed()
            h = (await c.get("/healthz")).json()["shedding"]
            assert h["state"] == "ok" and h["last"]["refusals"] == 10
        assert sent.count("shed_start") == 1
        assert sent.count("shed_clear") == 1
        assert sent.count("reject") == 10
    finally:
        await st.stop()
