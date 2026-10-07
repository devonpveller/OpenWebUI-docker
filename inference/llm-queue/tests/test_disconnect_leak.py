"""ao-queue (2026-10-07): a held connection must be released PROMPTLY when a stream ends early.

gym-002 saw llm-queue's held connections climb 63 -> 126 of the 128 cap, then shed with 503
`queue_connections_exhausted`. Every leaked rid the reaper later reclaimed (1,200 s TTL) had a
`queue_finish` event - 200 or 502 - so the response generator's `finally` RAN but stopped before
`release_connection`.

Why: uvicorn (0.54) advertises ASGI spec_version 2.3, so Starlette's StreamingResponse runs the body
inside a task group that also listens for `http.disconnect`. When the client goes (mid-stream, or
right after the last chunk), that task group CANCELS the streaming task. The generator's `finally`
then runs inside a cancelled scope, and its first real suspension raises CancelledError. Live, that
is the events-store write in `emit("finish")` (aiosqlite; httpcore shields its own close), which
runs after the `queue_finish` log line and before `release_connection` - so the slot is skipped.
A close that suspends (any non-shielded transport) would skip the model permit as well.

These tests drive the real ASGI app with a spec_version 2.3 scope, a client that disconnects, a fake
upstream (whose `aclose()` either suspends, or not - httpcore shields its close), and an events
sink backed by SQLite (live sets LLM_QUEUE_EVENTS_DB_PATH). They assert the held set, the model
permits and the waiters are back at baseline within a second - not after the reaper's TTL (set
to an hour here so it cannot mask the leak).
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from llm_queue.app_state import AppState
from llm_queue.config import Settings
from llm_queue.main import app

_CHUNKS = [
    b'data: {"choices":[{"delta":{"content":"a"}}]}\n\n',
    b'data: {"choices":[{"delta":{"content":"b"}}]}\n\n',
    b'data: {"choices":[{"delta":{"content":"c"}}]}\n\n',
    b"data: [DONE]\n\n",
]


class _Resp:
    def __init__(self, chunks, *, fail_after=None, delay=0.01, aclose_suspends=True):
        self.status_code = 200
        self.headers = httpx.Headers({"content-type": "text/event-stream"})
        self._chunks = chunks
        self._fail_after = fail_after
        self._delay = delay
        self._aclose_suspends = aclose_suspends
        self.closed = False

    async def aiter_raw(self):
        for i, c in enumerate(self._chunks):
            if self._fail_after is not None and i >= self._fail_after:
                raise httpx.RemoteProtocolError("peer closed connection mid-body")
            await asyncio.sleep(self._delay)
            yield c

    async def aclose(self):
        # A transport close that suspends is a cancellation checkpoint; httpcore's is shielded.
        if self._aclose_suspends:
            await asyncio.sleep(0)
        self.closed = True


class _Upstream:
    def __init__(self, **resp_kw):
        self.resp_kw = resp_kw
        self.opened: list[_Resp] = []

    async def open_stream(self, base_url, method, path, *, headers, content):
        await asyncio.sleep(0)
        r = _Resp(_CHUNKS, **self.resp_kw)
        self.opened.append(r)
        return r

    async def request(self, *a, **k):
        return httpx.Response(200, json={"data": []})

    async def aclose(self):
        pass


async def _state(tmp_path, **resp_kw) -> AppState:
    s = Settings()
    s.slots, s.max_in_flight = 2, 2
    s.events_db_path = str(tmp_path / "events.db")
    s.conn_ttl_s = 3600.0  # the reaper must NOT be what frees the slot here
    st = AppState(s)
    st.upstream = _Upstream(**resp_kw)
    app.state.app = st
    await st.start()
    return st


def _scope(spec_version: str = "2.3") -> dict:
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": spec_version},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/v1/chat/completions",
        "raw_path": b"/v1/chat/completions",
        "query_string": b"",
        "root_path": "",
        "headers": [(b"content-type", b"application/json"), (b"host", b"queue")],
        "client": ("127.0.0.1", 5000),
        "server": ("queue", 8080),
        "app": app,
        "state": {},
    }


async def _drive(*, stream: bool, disconnect_after_chunks: int | None, spec_version="2.3"):
    """Run one request through the ASGI app. The client sends `http.disconnect` once it has
    received `disconnect_after_chunks` non-empty body chunks (None = never disconnect)."""
    body = json.dumps({"model": "qwen36-27b", "stream": stream,
                       "messages": [{"role": "user", "content": "hi"}]}).encode()
    got = {"chunks": 0, "status": None, "done": False}
    gone = asyncio.Event()
    sent_req = False

    async def receive():
        nonlocal sent_req
        if not sent_req:
            sent_req = True
            return {"type": "http.request", "body": body, "more_body": False}
        await gone.wait()
        return {"type": "http.disconnect"}

    async def send(msg):
        if gone.is_set():
            raise OSError("client gone")  # uvicorn raises ClientDisconnected after a disconnect
        if msg["type"] == "http.response.start":
            got["status"] = msg["status"]
        elif msg["type"] == "http.response.body":
            if msg.get("body"):
                got["chunks"] += 1
                if disconnect_after_chunks is not None and got["chunks"] >= disconnect_after_chunks:
                    gone.set()
            if not msg.get("more_body"):
                got["done"] = True
                if disconnect_after_chunks is not None:
                    gone.set()

    try:
        await asyncio.wait_for(app(_scope(spec_version), receive, send), timeout=5)
    except (OSError, asyncio.CancelledError, Exception):  # noqa: BLE001 - the client is gone
        pass
    return got


async def _settled(st: AppState, within_s=1.0) -> dict:
    loop = asyncio.get_running_loop()
    end = loop.time() + within_s
    mq = st.registry.queue_for("qwen36-27b")
    while loop.time() < end:
        if st.registry.held_total == 0 and mq.held() == 0:
            break
        await asyncio.sleep(0.02)
    return {"held": st.registry.held_total, "in_flight": mq.held()}


@pytest.mark.parametrize("spec", ["2.3", "2.4"])
async def test_client_abort_mid_stream_releases_held_connection(tmp_path, spec):
    st = await _state(tmp_path)
    try:
        got = await _drive(stream=True, disconnect_after_chunks=1, spec_version=spec)
        assert got["status"] == 200
        assert await _settled(st) == {"held": 0, "in_flight": 0}
        assert all(r.closed for r in st.upstream.opened)  # upstream stream aborted too
    finally:
        await st.stop()


async def test_client_drop_right_after_last_chunk_releases(tmp_path):
    """The 200-status leak: the caller reads `[DONE]` and hangs up while the generator is still in
    its `finally` (live: 415 of the reaped rids had finished 200). The upstream close here does
    not suspend (as httpcore's), so what is cancelled is the events-store write - the live path."""
    st = await _state(tmp_path, aclose_suspends=False)
    try:
        await _drive(stream=True, disconnect_after_chunks=len(_CHUNKS))
        assert await _settled(st) == {"held": 0, "in_flight": 0}
    finally:
        await st.stop()


async def test_upstream_failure_mid_stream_releases(tmp_path):
    """Upstream dies mid-body (httpx RemoteProtocolError); the client is still there."""
    st = await _state(tmp_path, fail_after=1)
    try:
        got = await _drive(stream=True, disconnect_after_chunks=None)
        assert got["done"] is True
        assert await _settled(st) == {"held": 0, "in_flight": 0}
    finally:
        await st.stop()


async def test_upstream_failure_then_client_drop_releases(tmp_path):
    """Upstream dies mid-body AND the client hangs up on the error frame (LiteLLM does)."""
    st = await _state(tmp_path, fail_after=1)
    try:
        await _drive(stream=True, disconnect_after_chunks=2)
        assert await _settled(st) == {"held": 0, "in_flight": 0}
    finally:
        await st.stop()


async def test_non_stream_client_abort_mid_body_releases(tmp_path):
    st = await _state(tmp_path)
    try:
        await _drive(stream=False, disconnect_after_chunks=1)
        assert await _settled(st) == {"held": 0, "in_flight": 0}
    finally:
        await st.stop()


async def test_many_aborts_stay_under_cap(tmp_path):
    """Sustained abort load: 3x the cap of aborted streams never exhausts the cap."""
    st = await _state(tmp_path)
    st.settings.max_total_connections = 8
    try:
        for _ in range(24):
            got = await _drive(stream=True, disconnect_after_chunks=1)
            assert got["status"] == 200  # never a 503 connections-exhausted
        assert await _settled(st) == {"held": 0, "in_flight": 0}
    finally:
        await st.stop()
