"""End-to-end admission + proxy through the real ASGI app, against a fake
upstream. Proves the P1 acceptance criteria without Docker or a real GPU:
  - bursts below capacity → 0 × 429 (all queued + served)
  - bursts above the backstop → clean, structured 429s (never a fake 200)
  - never more than N requests reach the upstream at once
  - SSE streams pass through
  - X-Queue-* headers ride on the response
"""

import asyncio

import httpx

from llm_queue.app_state import AppState
from llm_queue.config import Settings
from llm_queue.main import app


class FakeResp:
    def __init__(self, parent, chunks, status=200, delay=0.05):
        self._parent = parent
        self._chunks = chunks
        self.status_code = status
        self.headers = httpx.Headers({"content-type": "text/event-stream"})
        self._delay = delay
        self._closed = False

    async def aiter_raw(self):
        for c in self._chunks:
            await asyncio.sleep(self._delay)
            yield c

    async def aclose(self):
        if not self._closed:
            self._closed = True
            self._parent.inflight -= 1


class FakeUpstream:
    """Records peak concurrent in-flight to prove the N cap holds."""

    def __init__(self, delay=0.05):
        self.inflight = 0
        self.max_inflight = 0
        self.delay = delay

    async def open_stream(self, base_url, method, path, *, headers, content):
        self.inflight += 1
        self.max_inflight = max(self.max_inflight, self.inflight)
        chunks = [
            b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n',
            b"data: [DONE]\n\n",
        ]
        return FakeResp(self, chunks, delay=self.delay)

    async def request(self, base_url, method, path, *, headers, content=None):
        return httpx.Response(200, json={"object": "list", "data": []})

    async def aclose(self):
        pass


def _make_app(**over):
    s = Settings()
    for k, v in over.items():
        setattr(s, k, v)
    state = AppState(s)
    fake = FakeUpstream()
    state.upstream = fake
    app.state.app = state
    return state, fake


def _client():
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://queue"
    )


_BODY = {"model": "qwen36-27b", "messages": [{"role": "user", "content": "hi"}]}


async def test_non_stream_proxies_and_sets_queue_headers():
    state, _ = _make_app(slots=3, max_in_flight=4, backstop_depth=24)
    await state.start()
    async with _client() as c:
        r = await c.post("/v1/chat/completions", json=_BODY)
    assert r.status_code == 200
    assert "X-Queue-Wait" in r.headers
    assert "X-Queue-Position" in r.headers
    assert "X-Queue-Avg-T" in r.headers
    await state.stop()


async def test_stream_passthrough():
    state, _ = _make_app(slots=3, max_in_flight=4)
    await state.start()
    body = dict(_BODY, stream=True)
    async with _client() as c:
        async with c.stream("POST", "/v1/chat/completions", json=body) as r:
            assert r.status_code == 200
            data = b""
            async for chunk in r.aiter_raw():
                data += chunk
    assert b"[DONE]" in data
    await state.stop()


async def test_burst_below_capacity_zero_429():
    # capacity = N(4) + backstop(24) = 28; fire 24 → all served, 0 rejected.
    state, fake = _make_app(slots=3, max_in_flight=4, backstop_depth=24)
    await state.start()
    async with _client() as c:
        results = await asyncio.gather(
            *(c.post("/v1/chat/completions", json=_BODY) for _ in range(24))
        )
    codes = [r.status_code for r in results]
    assert codes.count(429) == 0
    assert codes.count(200) == 24
    assert fake.max_inflight <= 4  # N cap held the whole time
    await state.stop()


async def test_burst_above_backstop_clean_rejections():
    state, fake = _make_app(slots=3, max_in_flight=4, backstop_depth=24)
    await state.start()
    async with _client() as c:
        results = await asyncio.gather(
            *(c.post("/v1/chat/completions", json=_BODY) for _ in range(48))
        )
    codes = [r.status_code for r in results]
    served = codes.count(200)
    rejected = codes.count(429)
    assert served + rejected == 48
    assert rejected > 0  # overflow shed honestly
    assert fake.max_inflight <= 4
    # Every rejection carries the structured, actionable body (design §4.5).
    for r in results:
        if r.status_code == 429:
            body = r.json()
            assert body["error"]["type"] in {"queue_over_depth", "queue_over_budget"}
            assert "retry_after_s" in body["error"]
            assert r.headers.get("Retry-After") is not None
            assert "slots" in body["error"]
    await state.stop()


async def test_observe_routes_are_readonly_and_present():
    state, _ = _make_app(slots=3, max_in_flight=4)
    await state.start()
    async with _client() as c:
        q = await c.get("/observe/queue")
        s = await c.get("/observe/queue/stats")
        e = await c.get("/observe/queue/estimate", params={"key": "owui-chat"})
        # Mutating verbs must NOT exist under /observe (only GET reads bridge).
        mutate = await c.post("/observe/queue/abc/priority", json={"rank": 0})
    assert q.status_code == 200 and "models" in q.json()
    assert s.status_code == 200
    assert e.status_code == 200 and e.json()["priority_class"] == "owui-chat"
    assert mutate.status_code in (404, 405)  # no mutating route under /observe
    await state.stop()


async def test_priority_class_attributed_from_key():
    state, _ = _make_app(slots=3, max_in_flight=4)
    await state.start()
    async with _client() as c:
        # owui-chat must outrank a batch/default caller in the estimate readout.
        chat = await c.get("/observe/queue/estimate", params={"key": "owui-chat"})
        batch = await c.get("/observe/queue/estimate", params={"key": "ob-entity"})
    assert chat.json()["rank"] < batch.json()["rank"]
    await state.stop()


async def test_global_connection_cap():
    # Hard FD/socket safety valve, independent of per-model depth (§10.3.3).
    state, _ = _make_app(slots=1, max_in_flight=1, backstop_depth=100, max_total_connections=5)
    await state.start()
    async with _client() as c:
        results = await asyncio.gather(
            *(c.post("/v1/chat/completions", json=_BODY) for _ in range(20))
        )
    codes = [r.status_code for r in results]
    assert any(r.status_code == 503 for r in results)
    for r in results:
        if r.status_code == 503:
            assert r.json()["error"]["type"] == "queue_connections_exhausted"
    assert codes.count(200) + codes.count(503) == 20
    await state.stop()
