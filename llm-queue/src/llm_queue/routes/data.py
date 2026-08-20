"""Data plane — admission + transparent streaming proxy.

POST /v1/chat/completions (and /v1/embeddings) flow:
  1. reserve a global connection slot (hard FD cap, §10.3.3)
  2. enqueue → admission DECISION (honest 429 before any bytes, §4.5)
  3. await dispatch (heartbeating if the caller wants a stream, §10.4),
     racing client-disconnect eviction (§10.3.4)
  4. open the upstream stream and relay bytes UNBUFFERED, holding the permit
     until the stream completes (§7.3)

Everything else (GET /v1/models, etc.) is a plain pass-through with no admission.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from ..app_state import AppState
from ..logging import get_logger
from ..models import Cancelled, Rejected
from ..scheduler import ModelQueue, Waiter
from ..transport import filter_request_headers, filter_response_headers

router = APIRouter(tags=["data"])
log = get_logger("llm_queue.data")


class _ReleasingStreamingResponse(StreamingResponse):
    """A StreamingResponse that CLOSES its body iterator when streaming ends — including on client
    disconnect. Starlette 1.3.1's `stream_response` does NOT aclose the generator on disconnect (it
    lets `send()` raise and abandons the `async for`), so the generator's `finally` — where llm-queue
    releases its held connection slot — is left to async-generator GC (delayed / effectively never
    under load). That is the connection-leak the reaper backstops; this closes it at the source so the
    slot is freed immediately on disconnect, not up to a reap-interval later. aclose() on an already-
    exhausted generator (normal completion) is a harmless no-op, and the release is idempotent."""

    async def stream_response(self, send) -> None:  # type: ignore[override]
        try:
            await super().stream_response(send)
        finally:
            aclose = getattr(self.body_iterator, "aclose", None)
            if aclose is not None:
                await aclose()

# Nginx/uvicorn's "client closed request" — the request was abandoned while queued.
_CLIENT_CLOSED = 499
_DISCONNECT_POLL_S = 0.5


def _state(request: Request) -> AppState:
    return request.app.state.app


# Keys callers present that carry NO identity (LiteLLM's configured api_key, or a
# placeholder). When the attributed key is one of these we fall back to the body
# `user` field (which LiteLLM DOES forward — it's a standard OpenAI param), so an
# identifying caller still gets real attribution. See _attribute_key + design §10.3.2.
_SENTINEL_KEYS = {"dummy", "not-needed", "no-key", "not-required", "none", "not_required", ""}


def _parse_body(raw: bytes) -> tuple[str | None, bool, str | None]:
    try:
        data = json.loads(raw)
        user = data.get("user")
        return data.get("model"), bool(data.get("stream", False)), (str(user) if user else None)
    except Exception:
        return None, False, None


def _extract_auth_key(request: Request, key_header: str) -> str | None:
    raw = request.headers.get(key_header, "")
    if key_header.lower() == "authorization" and raw.lower().startswith("bearer "):
        raw = raw[7:]
    raw = raw.strip()
    return raw or None


def _attribute_key(auth_key: str | None, body_user: str | None) -> str | None:
    """The caller identity priority is derived from (design §10.3.2 — server-side,
    never a client X-Priority header). Precedence: a real Authorization key
    (direct callers) → the OpenAI `user` body field (survives LiteLLM forwarding)
    → the sentinel/None (→ default class). LiteLLM's permissive openai-client path
    strips the caller's Authorization to `dummy`, so per-caller priority through
    the gateway needs either a caller-set `user` or the future master_key/virtual
    keys; direct callers and `user`-setting callers attribute correctly today."""
    if auth_key and auth_key.lower() not in _SENTINEL_KEYS:
        return auth_key
    if body_user:
        return body_user
    return auth_key


def _rejection_response(r: Rejected) -> JSONResponse:
    return JSONResponse(
        r.body(),
        status_code=r.status_code,
        headers={"Retry-After": str(r.retry_after_s)},
    )


def _queue_headers(waiter: Waiter, mq: ModelQueue) -> dict[str, str]:
    return {
        "X-Queue-Wait": f"{waiter.wait_seconds:.2f}",
        "X-Queue-Position": str(waiter.position_at_enqueue),
        "X-Queue-Avg-T": f"{mq.avg_t:.2f}",
    }


async def _await_dispatch_or_disconnect(request: Request, mq: ModelQueue, waiter: Waiter) -> None:
    """Await dispatch for a NON-streaming request, evicting on client disconnect.
    Raises Cancelled if the client left (no slot burned) or the request was
    cancelled by an operator."""
    dt = asyncio.ensure_future(mq.await_dispatch(waiter))
    while True:
        done, _ = await asyncio.wait({dt}, timeout=_DISCONNECT_POLL_S)
        if dt in done:
            dt.result()  # raises Cancelled if evicted; returns if dispatched
            return
        if await request.is_disconnected():
            evicted = await mq.cancel_waiting(waiter)
            if not evicted:
                # Dispatched in the race — reclaim the permit we never used.
                try:
                    await dt
                    await mq.release(waiter, record_duration=False)
                except (Cancelled, Rejected):
                    pass
            else:
                try:
                    await dt
                except (Cancelled, Rejected):
                    pass
            raise Cancelled()


@router.post("/v1/chat/completions")
async def chat_completions(request: Request) -> Response:
    return await _admit_and_proxy(request, "/v1/chat/completions")


@router.post("/v1/embeddings")
async def embeddings(request: Request) -> Response:
    # Routed through the queue only once embed's LiteLLM api_base is repointed
    # (P4); harmless before then (it simply never receives traffic).
    return await _admit_and_proxy(request, "/v1/embeddings")


async def _admit_and_proxy(request: Request, upstream_path: str) -> Response:
    state = _state(request)
    raw = await request.body()
    model, want_stream, body_user = _parse_body(raw)
    model_name = model or "qwen36-27b"
    key = _attribute_key(_extract_auth_key(request, state.settings.key_header), body_user)
    # DEBUG (LLM_QUEUE_LOG_LEVEL=DEBUG): dump incoming headers so we can see what
    # the caller-identity signal actually is after LiteLLM forwarding.
    log.debug("incoming_headers", headers={k: v for k, v in request.headers.items()})
    cls = state.policy.classify(key)
    mq = state.registry.queue_for(model)
    upstream_base = state.registry.upstream_for(model)
    rid = uuid.uuid4().hex[:12]
    waiter = Waiter(id=rid, key=key or "anon", model=model_name, cls=cls, seq=state.next_seq())

    # Hard global connection cap (independent of per-service budget).
    try:
        await state.registry.reserve_connection(rid, model_name)
    except Rejected as r:
        await state.events.emit(
            "reject", ts=time.time(), request_id=rid, key=waiter.key, model=model_name,
            prio=cls.rank, status=r.status_code, reason=r.type,
        )
        return _rejection_response(r)

    # Admission decision (honest 429 before any bytes).
    try:
        await mq.enqueue(waiter)
    except Rejected as r:
        await state.registry.release_connection(rid)
        await state.events.emit(
            "reject", ts=time.time(), request_id=rid, key=waiter.key, model=model_name,
            prio=cls.rank, est_wait_s=r.projected_wait_s, depth=r.queue_depth,
            status=r.status_code, reason=r.type,
        )
        return _rejection_response(r)

    fwd_headers = filter_request_headers(dict(request.headers))

    if want_stream:
        return _ReleasingStreamingResponse(
            _stream_waiting_then_proxy(request, state, mq, waiter, upstream_base, upstream_path,
                                       fwd_headers, raw, rid),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # Non-streaming: block (no heartbeat possible) racing disconnect, then relay.
    try:
        await _await_dispatch_or_disconnect(request, mq, waiter)
    except Cancelled:
        await state.registry.release_connection(rid)
        return Response(status_code=_CLIENT_CLOSED)

    await state.events.emit(
        "admit", ts=time.time(), request_id=rid, key=waiter.key, model=model_name,
        prio=cls.rank, wait_s=waiter.wait_seconds, est_wait_s=waiter.est_at_enqueue,
    )
    try:
        resp = await state.upstream.open_stream(
            upstream_base, "POST", upstream_path, headers=fwd_headers, content=raw
        )
    except Exception as exc:  # noqa: BLE001
        await mq.release(waiter, record_duration=False)
        await state.registry.release_connection(rid)
        await state.events.emit(
            "finish", ts=time.time(), request_id=rid, key=waiter.key, model=model_name,
            prio=cls.rank, status=502, reason="upstream_error",
        )
        return JSONResponse(
            {"error": {"type": "upstream_error", "message": f"llm-queue: {exc}"}},
            status_code=502,
        )

    headers = filter_response_headers(resp.headers)
    headers.update(_queue_headers(waiter, mq))

    async def relay() -> AsyncIterator[bytes]:
        ok = True
        try:
            async for chunk in resp.aiter_raw():
                yield chunk
        except Exception:  # noqa: BLE001
            ok = False
            raise
        finally:
            await resp.aclose()
            await mq.release(waiter, record_duration=ok)
            await state.registry.release_connection(rid)
            await state.events.emit(
                "finish", ts=time.time(), request_id=rid, key=waiter.key, model=model_name,
                prio=cls.rank, wait_s=waiter.wait_seconds, duration_s=_elapsed(waiter),
                est_wait_s=waiter.est_at_enqueue, status=resp.status_code,
            )

    return _ReleasingStreamingResponse(
        relay(),
        status_code=resp.status_code,
        headers=headers,
        media_type=resp.headers.get("content-type"),
    )


def _elapsed(waiter: Waiter) -> float | None:
    if waiter.started_monotonic is None:
        return None
    return round(time.monotonic() - waiter.started_monotonic, 2)


async def _stream_waiting_then_proxy(
    request: Request,
    state: AppState,
    mq: ModelQueue,
    waiter: Waiter,
    upstream_base: str,
    upstream_path: str,
    fwd_headers: dict[str, str],
    raw: bytes,
    rid: str,
) -> AsyncIterator[bytes]:
    """Streaming path: heartbeat SSE comments while queued (so idle-read timeouts
    don't fire on a long wait, §10.4), then relay the upstream token stream. The
    connection slot + permit are released exactly once in the outer finally."""
    conn_held = True
    dispatched = False
    resp = None
    ok = True
    cls = waiter.cls
    model_name = waiter.model
    try:
        dt = asyncio.ensure_future(mq.await_dispatch(waiter))
        try:
            while True:
                done, _ = await asyncio.wait({dt}, timeout=state.settings.sse_heartbeat_s)
                if dt in done:
                    dt.result()  # raises Cancelled if evicted
                    dispatched = True
                    break
                waited = time.monotonic() - waiter.enqueued_monotonic
                hb = f": queued position {waiter.position_at_enqueue}, waited {waited:.0f}s\n\n"
                yield hb.encode()
        except Cancelled:
            return  # client gone / cancelled while waiting — outer finally cleans up

        await state.events.emit(
            "admit", ts=time.time(), request_id=rid, key=waiter.key, model=model_name,
            prio=cls.rank, wait_s=waiter.wait_seconds, est_wait_s=waiter.est_at_enqueue,
        )
        resp = await state.upstream.open_stream(
            upstream_base, "POST", upstream_path, headers=fwd_headers, content=raw
        )
        async for chunk in resp.aiter_raw():
            yield chunk
    except asyncio.CancelledError:
        ok = False
        raise
    except Exception as exc:  # noqa: BLE001
        ok = False
        # Headers (200) already sent — surface the failure as an SSE error frame.
        msg = json.dumps({"error": {"type": "upstream_error", "message": f"llm-queue: {exc}"}})
        yield f"data: {msg}\n\n".encode()
    finally:
        if resp is not None:
            await resp.aclose()
        if dispatched:
            await mq.release(waiter, record_duration=ok)
            await state.events.emit(
                "finish", ts=time.time(), request_id=rid, key=waiter.key, model=model_name,
                prio=cls.rank, wait_s=waiter.wait_seconds, duration_s=_elapsed(waiter),
                est_wait_s=waiter.est_at_enqueue, status=200 if ok else 502,
            )
        else:
            await mq.cancel_waiting(waiter)
            await state.events.emit(
                "cancel", ts=time.time(), request_id=rid, key=waiter.key, model=model_name,
                prio=cls.rank, reason="client_disconnect_or_cancel",
            )
        if conn_held:
            await state.registry.release_connection(rid)


# ---- pass-through (no admission) -----------------------------------------


@router.api_route("/v1/models", methods=["GET"])
async def passthrough_models(request: Request) -> Response:
    state = _state(request)
    fwd_headers = filter_request_headers(dict(request.headers))
    resp = await state.upstream.request(
        state.settings.upstream_base_url, "GET", "/v1/models", headers=fwd_headers
    )
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        headers=filter_response_headers(resp.headers),
        media_type=resp.headers.get("content-type"),
    )
