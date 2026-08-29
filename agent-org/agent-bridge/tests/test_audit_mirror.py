"""Memory-plane Phase 0.1 — the audit mirror's contract (P6.2).

The wire itself is covered in test_openbrain_client.py. This file covers what the SINK
promises regardless of what the wire does:

  1. with the flag off (the default) NOTHING goes out — this phase must not change live
     behaviour, so a network call in the default configuration is a failure;
  2. kinds outside `_MIRROR_KINDS` are never mirrored;
  3. `Event.mirrored` flips ONLY when the write actually landed;
  4. mirroring is best-effort — no failure mode propagates out of `log()`.

(3) is the one with teeth: an event marked mirrored is a claim of durable provenance in
an append-only audit log. Marking an event mirrored after a 500 does not lose data
loudly, it loses it silently and then asserts it did not.
"""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import select

from app.config import Settings
from app.modules.audit_sink import AuditSink
from app.models import Event


def _settings(**kw) -> Settings:
    base = {
        "openbrain_mirror_enabled": True,
        "openbrain_url": "http://openbrain-mcp:8000",
        "openbrain_key": "k",
    }
    return Settings(_env_file=None, **{**base, **kw})


def _transport(status: int = 200, payload: dict | None = None):
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        body = payload if payload is not None else {"jsonrpc": "2.0", "id": 1, "result": {"content": []}}
        return httpx.Response(status, json=body)

    return httpx.MockTransport(handler), calls


async def _mirrored(db, event_id: int) -> bool:
    async with db.session_factory() as s:
        row = (await s.execute(select(Event).where(Event.id == event_id))).scalar_one()
        return row.mirrored


# ── the default configuration makes no network call at all ───────────────────
async def test_disabled_by_default_makes_no_outbound_call(db):
    """AO_OPENBRAIN_MIRROR_ENABLED defaults false and Phase 0 does not change that."""
    s = Settings(_env_file=None)
    assert s.openbrain_mirror_enabled is False

    sink = AuditSink(db, s)
    sink.openbrain.transport, calls = _transport()
    ev = await sink.log("concern_posted", effort_id="e1", payload={"a": 1})

    assert calls == []                      # nothing left the process
    assert await _mirrored(db, ev) is False


async def test_non_mirror_kind_is_untouched_even_when_enabled(db):
    sink = AuditSink(db, _settings())
    sink.openbrain.transport, calls = _transport()
    ev = await sink.log("some_other_kind", effort_id="e1")

    assert calls == []
    assert await _mirrored(db, ev) is False


async def test_mirror_kind_is_sent_when_enabled(db):
    sink = AuditSink(db, _settings())
    sink.openbrain.transport, calls = _transport()
    ev = await sink.log("operator_decision", effort_id="e1", payload={"d": "yes"})

    assert len(calls) == 1
    assert await _mirrored(db, ev) is True


# ── mirrored=True is a claim that must be earned ─────────────────────────────
@pytest.mark.parametrize(
    "status,payload",
    [
        (500, None),                                                          # server error
        (401, None),                                                          # bad key
        (200, {"jsonrpc": "2.0", "id": 1, "error": {"code": -32602, "message": "bad"}}),
        (200, {"jsonrpc": "2.0", "id": 1, "result": {"isError": True}}),      # tool failed
    ],
    ids=["http-500", "http-401", "jsonrpc-error", "tool-isError"],
)
async def test_mirrored_stays_false_when_the_write_did_not_land(db, status, payload):
    sink = AuditSink(db, _settings())
    sink.openbrain.transport, _ = _transport(status, payload)
    ev = await sink.log("kill_switch", effort_id="e1")

    assert await _mirrored(db, ev) is False


# ── best-effort: a mirror failure never reaches the caller ───────────────────
async def test_transport_failure_does_not_propagate_out_of_log(db):
    """An Open Brain outage must not fail an audit write — the audit log is the
    load-bearing record; the mirror is a convenience on top of it."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    sink = AuditSink(db, _settings())
    sink.openbrain.transport = httpx.MockTransport(handler)

    ev = await sink.log("floor_change", effort_id="e1")     # must not raise
    assert isinstance(ev, int)
    assert await _mirrored(db, ev) is False
    # and the event itself is still durably recorded
    assert len(await sink.replay("e1")) == 1


async def test_event_is_recorded_even_when_the_mirror_is_broken(db):
    """The append-only log is written BEFORE the mirror is attempted, so a mirror that
    fails for any reason still leaves a complete audit trail."""
    sink = AuditSink(db, _settings())
    sink.openbrain.transport, _ = _transport(503)
    await sink.log("pattern_proposed", effort_id="e2", payload={"p": 1})

    rows = await sink.replay("e2")
    assert [r["kind"] for r in rows] == ["pattern_proposed"]
