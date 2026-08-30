"""Agent-memory write paths (memory-plane PLAN §2.1, §2.5).

The builders are pure, so most of this needs no transport at all. The half that does uses
httpx.MockTransport, the house pattern — a real client, a fake wire.

The FAIL-SOFT LAW gets its own group and it is the one that matters: Open Brain being down
must never change the outcome of an effort. A test suite that only proves the happy path
would let a raise slip into a code path that runs at the end of every effort.
"""
import httpx
import pytest

from app.config import Settings
from app.modules.openbrain_memory import (
    CONTENT_MAX,
    SUMMARY_MAX,
    OpenBrainMemory,
    build_constraint_memory,
    build_outcome_memory,
)


def _settings(**kw):
    s = Settings()
    s.openbrain_key = kw.pop("key", "test-key")
    s.memory_writeback_enabled = kw.pop("enabled", True)
    return s


# ── the payload the server will actually receive ─────────────────────────────
def test_outcome_payload_carries_the_locked_defaults_by_omission():
    """§1's defaults are the SERVER's to apply, and the client must not restate them.

    A payload that spelled out review_status='pending' would keep working if the server's
    default changed, and the two would drift apart silently. The absence is the assertion.
    """
    p = build_outcome_memory(effort_id="e1", project="proj", succeeded=True, head="did a thing")
    for owned_by_the_server in (
        "review_status", "visibility", "provenance_status", "confidence",
        "can_use_as_evidence", "can_use_as_instruction", "requires_user_confirmation",
        "exposure",
    ):
        assert owned_by_the_server not in p, owned_by_the_server


def test_outcome_type_is_output_on_success_and_failure_otherwise():
    assert build_outcome_memory(effort_id="e1", project="p", succeeded=True)["memory_type"] == "output"
    # The more useful of the two: a corpus of successes only cannot warn anyone.
    assert build_outcome_memory(effort_id="e1", project="p", succeeded=False)["memory_type"] == "failure"


def test_outcome_is_idempotent_by_effort():
    # _finish_effort is reachable more than once for one effort (re-close, abort after
    # finish). Two calls must be one memory.
    a = build_outcome_memory(effort_id="e1", project="p", succeeded=True, head="x")
    b = build_outcome_memory(effort_id="e1", project="p", succeeded=False, head="y")
    assert a["idempotency_key"] == b["idempotency_key"] == "outcome-e1"


def test_constraint_is_idempotent_by_constraint_not_by_effort():
    # A second effort hitting the same wall must not write a second memory for it.
    a = build_constraint_memory(constraint_id="c1", effort_id="e1", project="p", text="t")
    b = build_constraint_memory(constraint_id="c1", effort_id="e2", project="p", text="t")
    assert a["idempotency_key"] == b["idempotency_key"] == "constraint-c1"


def test_source_refs_are_carried_into_the_content():
    p = build_outcome_memory(
        effort_id="e1", project="p", succeeded=True, head="h",
        pr_url="https://example/pr/1", effort_url="https://example/effort/1",
    )
    assert "https://example/pr/1" in p["content"]
    assert "https://example/effort/1" in p["content"]


def test_long_text_is_marked_truncated_not_silently_cut():
    # A reviewer must be able to tell a truncated memory from a short one, or they read the
    # end of the clip as the end of the thought.
    p = build_outcome_memory(effort_id="e1", project="p", succeeded=True, head="x" * 5000)
    assert len(p["summary"]) <= SUMMARY_MAX
    assert len(p["content"]) <= CONTENT_MAX
    assert "[truncated]" in p["content"]


def test_a_short_memory_is_not_marked_truncated():
    p = build_outcome_memory(effort_id="e1", project="p", succeeded=True, head="short")
    assert "[truncated]" not in p["content"]


# ── the taint stamp (§1.1) ───────────────────────────────────────────────────
def test_taint_is_reported_not_guessed():
    clean = build_outcome_memory(effort_id="e1", project="p", succeeded=True)
    dirty = build_outcome_memory(effort_id="e1", project="p", succeeded=True, tainted=True)
    assert clean["tainted"] is False
    assert dirty["tainted"] is True
    # And the module never sends an exposure of its own: the DOOR stamps it, and the value
    # a caller could send is ignored server-side anyway.
    assert "exposure" not in clean and "exposure" not in dirty


def test_both_builders_stamp_taint():
    c = build_constraint_memory(constraint_id="c1", effort_id="e1", project="p", text="t", tainted=True)
    assert c["tainted"] is True


# ── the wire ─────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_a_successful_write_returns_true():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1,
                                         "result": {"content": [{"type": "text", "text": "Stored memory m-1"}]}})

    m = OpenBrainMemory(_settings())
    m.transport = httpx.MockTransport(handler)
    assert await m.write_effort_outcome(effort_id="e1", project="p", succeeded=True) is True


@pytest.mark.asyncio
async def test_a_tool_level_failure_is_NOT_reported_as_success():
    # An MCP server reports tool failure as HTTP 200 with result.isError - checking the
    # status code alone would mark unwritten memories as written.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1,
                                         "result": {"isError": True, "content": []}})

    m = OpenBrainMemory(_settings())
    m.transport = httpx.MockTransport(handler)
    assert await m.write_effort_outcome(effort_id="e1", project="p", succeeded=True) is False


@pytest.mark.asyncio
async def test_the_flag_defaults_off_and_off_means_no_request():
    called = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        called["n"] += 1
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {"content": []}})

    m = OpenBrainMemory(_settings(enabled=False))
    m.transport = httpx.MockTransport(handler)
    assert await m.write_effort_outcome(effort_id="e1", project="p", succeeded=True) is False
    assert called["n"] == 0, "a disabled write path must not reach the network"


@pytest.mark.asyncio
async def test_an_empty_key_refuses_rather_than_writing_unauthenticated():
    called = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        called["n"] += 1
        return httpx.Response(401)

    m = OpenBrainMemory(_settings(key=""))
    m.transport = httpx.MockTransport(handler)
    assert await m.write_effort_outcome(effort_id="e1", project="p", succeeded=True) is False
    assert called["n"] == 0


# ── THE FAIL-SOFT LAW ────────────────────────────────────────────────────────
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "boom",
    [
        httpx.ConnectError("Name or service not known"),
        httpx.ReadTimeout("timed out"),
        httpx.RemoteProtocolError("server disconnected"),
    ],
)
async def test_open_brain_being_down_NEVER_raises(boom):
    """The law: a memory write must never take the bridge down or change an outcome.

    This runs at the end of every effort. A raise here would turn a finished effort into a
    crashed one, and the failure would look like the effort's, not the memory plane's.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        raise boom

    m = OpenBrainMemory(_settings())
    m.transport = httpx.MockTransport(handler)
    assert await m.write_effort_outcome(effort_id="e1", project="p", succeeded=True) is False


@pytest.mark.asyncio
async def test_a_garbage_response_does_not_raise():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>not json at all</html>")

    m = OpenBrainMemory(_settings())
    m.transport = httpx.MockTransport(handler)
    assert await m.write_effort_outcome(effort_id="e1", project="p", succeeded=True) is False


@pytest.mark.asyncio
async def test_an_http_500_does_not_raise():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    m = OpenBrainMemory(_settings())
    m.transport = httpx.MockTransport(handler)
    assert await m.write_constraint(
        constraint_id="c1", effort_id="e1", project="p", text="t"
    ) is False
