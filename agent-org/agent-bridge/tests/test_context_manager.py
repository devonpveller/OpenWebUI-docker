"""Hierarchical, bounded, relevance-selected conversation context (operator's refinement):
thread = immediate, channel = higher-level background; budgeted so it never overwhelms the window;
the channel layer is prioritized to what's relevant to the query."""

from __future__ import annotations

from app.modules.context_manager import ContextManager


def test_thread_is_immediate_channel_is_background():
    cm = ContextManager()
    cm.remember("ch", "t1", "operator", "add a hello function to the calculator")
    cm.remember("ch", "t1", "po", "on it")
    cm.remember("ch", "t2", "operator", "a message in a different thread")
    out = cm.build("ch", "t1", query="continue please")
    # the current thread is the immediate layer; other threads are channel-level background
    assert "THIS THREAD" in out and "add a hello function" in out
    assert "ELSEWHERE IN THIS CHANNEL" in out and "different thread" in out


def test_relevance_prioritized_within_budget():
    # one old-but-relevant turn + lots of recent noise; a small channel budget must keep the
    # relevant one (relevance is prioritized, then recency fills the rest).
    cm = ContextManager(channel_chars=80)
    cm.remember("ch", "old", "operator", "the calculator runs on port 3000 specifically")
    for i in range(10):
        cm.remember("ch", f"n{i}", "po", f"noise message number {i} about the weather today")
    out = cm.build("ch", "cur", query="what port does the calculator use")
    assert "calculator runs on port 3000" in out


def test_output_is_budget_bounded():
    cm = ContextManager(thread_chars=100, channel_chars=100)
    for i in range(50):
        cm.remember("ch", "t", "operator", f"turn {i} " + "x" * 50)
    out = cm.build("ch", "t", query="q")
    assert len(out) < 400          # bounded — nowhere near 50 turns × ~57 chars


def test_empty_history_is_safe():
    assert "start of the conversation" in ContextManager().build("ch", "t", query="hi")


def test_memory_is_capped_per_channel():
    cm = ContextManager(max_log_per_channel=20)
    for i in range(100):
        cm.remember("ch", "t", "operator", f"m{i}")
    assert len(cm._log["ch"]) == 20   # oldest evicted; bounded memory
