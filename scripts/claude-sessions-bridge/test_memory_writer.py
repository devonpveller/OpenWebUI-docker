"""Tests for the bridge's agent-memory writer (memory-plane §2.4, §2.5).

The bridge has no test suite, so this is a standalone file runnable with plain pytest:

    python -m pytest scripts/claude-sessions-bridge/test_memory_writer.py -q

§2.5 asks for a unit test of the rollup builder as a pure function. The success-parsing rule
is tested too, because "did the write happen" is the question this plane has already got
wrong once - an MCP server reports tool failure as HTTP 200.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import memory_writer as mw  # noqa: E402


# ── the rollup builder ───────────────────────────────────────────────────────
def test_rollup_lists_what_happened_rather_than_summarising_it():
    # Summarising here would mean a model compressing its own transcript unsupervised.
    p = mw.build_session_rollup("thr-1", ["did a thing", "then another"])
    assert "- did a thing" in p["content"]
    assert "- then another" in p["content"]


def test_rollup_is_idempotent_per_thread():
    a = mw.build_session_rollup("thr-1", ["x"])
    b = mw.build_session_rollup("thr-1", ["x", "y"])
    assert a["idempotency_key"] == b["idempotency_key"] == "claude-session-thr-1"


def test_rollup_survives_an_empty_session():
    # A session can be closed having done nothing. That must produce a valid payload, not a
    # crash on the close path.
    p = mw.build_session_rollup("thr-1", [])
    assert p["summary"] and p["content"]
    assert p["memory_type"] == "work_log"


def test_rollup_ignores_blank_and_non_string_turns():
    p = mw.build_session_rollup("thr-1", ["real", "  ", None, 7, ""])
    assert "- real" in p["content"]
    assert "- 7" not in p["content"]


def test_rollup_omits_everything_the_server_owns():
    # §1's write defaults are the server's. A client that restated them would keep working
    # if a default changed, and the two would drift silently.
    p = mw.build_session_rollup("thr-1", ["x"])
    for owned in ("review_status", "visibility", "exposure", "provenance_status",
                  "can_use_as_instruction", "requires_user_confirmation"):
        assert owned not in p


def test_long_sessions_are_marked_truncated():
    p = mw.build_session_rollup("thr-1", [f"turn number {i} " + "x" * 200 for i in range(50)])
    assert len(p["content"]) <= mw.CONTENT_MAX
    assert "[truncated]" in p["content"]


def test_turn_log_is_idempotent_per_turn():
    a = mw.build_turn_log("thr-1", "text", turn_id="t-9")
    assert a["idempotency_key"] == "claude-turn-t-9"


# ── the flags ────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("value,expected", [
    ("", False), ("0", False), ("false", False), ("no", False),
    ("1", True), ("true", True), ("TRUE", True), ("yes", True),
])
def test_flags_default_off_and_parse_conservatively(monkeypatch, value, expected):
    monkeypatch.setenv("CLAUDE_MEMORY_ROLLUP_ENABLED", value)
    assert mw.rollup_enabled() is expected


def test_flags_are_off_when_unset(monkeypatch):
    monkeypatch.delenv("CLAUDE_MEMORY_ROLLUP_ENABLED", raising=False)
    monkeypatch.delenv("CLAUDE_MEMORY_TURNLOG_ENABLED", raising=False)
    assert mw.rollup_enabled() is False
    assert mw.turnlog_enabled() is False


# ── "did the write actually happen" ──────────────────────────────────────────
def test_a_tool_level_failure_is_not_success():
    # HTTP 200 with result.isError is how MCP reports tool failure. Checking the status code
    # alone would mark unwritten memories as written.
    assert mw._write_succeeded('{"jsonrpc":"2.0","id":1,"result":{"isError":true}}') is False


def test_a_jsonrpc_error_is_not_success():
    assert mw._write_succeeded('{"jsonrpc":"2.0","id":1,"error":{"code":-32601}}') is False


def test_a_real_success_is_success():
    assert mw._write_succeeded(
        '{"jsonrpc":"2.0","id":1,"result":{"content":[{"type":"text","text":"Stored"}]}}'
    ) is True


def test_an_sse_framed_success_is_parsed():
    # StreamableHTTP may answer as server-sent events; the body is not bare JSON.
    body = 'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"content":[]}}\n\n'
    assert mw._write_succeeded(body) is True


def test_garbage_is_not_success():
    for junk in ("", "   ", "<html>nope</html>", "null", "[]"):
        assert mw._write_succeeded(junk) is False


# ── never raises ─────────────────────────────────────────────────────────────
def test_no_key_means_no_write_and_no_exception(monkeypatch):
    monkeypatch.setenv("OPS_GATEWAY_KEY", "")
    monkeypatch.setattr(mw, "_ops_key", lambda: "")
    assert mw.write_memory({"workspace_id": "ai-stack"}) is False


def test_an_unreachable_door_returns_false_rather_than_raising(monkeypatch):
    """The law: a memory write must never take the bridge down.

    The bridge runs unattended and has no supervisor that would notice a traceback here.
    """
    monkeypatch.setattr(mw, "_ops_key", lambda: "key")
    # A port nothing listens on, with the real urllib path exercised.
    monkeypatch.setattr(mw, "OPS_DOOR", "http://127.0.0.1:9")
    assert mw.write_memory({"workspace_id": "ai-stack"}) is False
