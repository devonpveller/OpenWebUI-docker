"""Agent-memory writes from the claude-sessions bridge (memory-plane PLAN §2.4).

THROUGH THE OPS DOOR, not through openbrain-mcp. The bridge is a HOST process and
openbrain-mcp publishes no host port — it is reachable only from inside the container
networks. The plan calls the "direct REST" write path a v1 planning bug for exactly that
reason. The ops door (127.0.0.1:8062) exists to give host callers a lane, and it means no
host process ever holds the raw MCP_ACCESS_KEY: this module authenticates with
OPS_GATEWAY_KEY, and the door forwards the real key upstream itself.

STDLIB ONLY, and it follows `telegram_alert`'s template line for line: urllib, a 15s
timeout, and an except that swallows everything. The bridge has no test suite and runs
unattended; a memory write must never take it down. Every function here returns a bool and
raises nothing.

BOTH FLAGS DEFAULT OFF. `CLAUDE_MEMORY_ROLLUP_ENABLED` for the per-session rollup,
`CLAUDE_MEMORY_TURNLOG_ENABLED` for the per-turn work_log — the second is much chattier and
is opt-in separately on purpose.

The door stamps `exposure` and runs the PII demote gate, so nothing here decides exposure.
It also applies §1's write defaults, so nothing here sets review_status either: these
memories arrive pending and a human confirms them or they stay out of the default recall.
"""
import json
import os

OPS_DOOR = os.environ.get("CLAUDE_MEMORY_OPS_URL", "http://127.0.0.1:8062")
WORKSPACE = "ai-stack"
PROJECT = "claude-sessions"

SUMMARY_MAX = 300
CONTENT_MAX = 4000
TIMEOUT_S = 15


def _clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    # Marked, not silently cut - a reviewer must be able to tell a truncated memory from a
    # short one, or the ellipsis reads as the end of the thought.
    return text[: limit - 15].rstrip() + " …[truncated]"


def rollup_enabled() -> bool:
    return os.environ.get("CLAUDE_MEMORY_ROLLUP_ENABLED", "").strip().lower() in ("1", "true", "yes")


def turnlog_enabled() -> bool:
    return os.environ.get("CLAUDE_MEMORY_TURNLOG_ENABLED", "").strip().lower() in ("1", "true", "yes")


def build_session_rollup(thread_root: str, turns: list, *, session_id: str = "") -> dict:
    """The writeback payload for one closed session. PURE — no network, no clock, no env.

    `turns` is a list of short strings, oldest first. The rollup is deliberately a LIST OF
    WHAT HAPPENED rather than a summary of it: summarising here would mean a model
    compressing its own transcript unsupervised, and the plan's write-back rules say compact
    summaries and source refs, never raw transcripts or reasoning traces.
    """
    kept = [t.strip() for t in (turns or []) if isinstance(t, str) and t.strip()]
    head = kept[0] if kept else "session closed with no recorded turns"
    body = "\n".join(f"- {t}" for t in kept) if kept else "(no recorded turns)"
    return {
        "workspace_id": WORKSPACE,
        "project_id": PROJECT,
        "summary": _clip(f"claude-sessions {thread_root}: {head}", SUMMARY_MAX),
        "content": _clip(f"Session rollup for thread {thread_root}\n{body}", CONTENT_MAX),
        "memory_type": "work_log",
        # One memory per THREAD, so a session closed twice does not write it twice.
        "idempotency_key": f"claude-session-{thread_root}",
        "metadata": {
            "runtime_name": "claude-sessions-bridge",
            "task_id": thread_root,
            "session_id": session_id,
            "source": "session_rollup",
        },
    }


def build_turn_log(thread_root: str, text: str, *, turn_id: str) -> dict:
    """One turn, as a lightweight work_log. PURE."""
    return {
        "workspace_id": WORKSPACE,
        "project_id": PROJECT,
        "summary": _clip(f"claude-sessions {thread_root}: {text}", SUMMARY_MAX),
        "content": _clip(text, CONTENT_MAX),
        "memory_type": "work_log",
        "idempotency_key": f"claude-turn-{turn_id}",
        "metadata": {
            "runtime_name": "claude-sessions-bridge",
            "task_id": thread_root,
            "source": "turn_log",
        },
    }


def _ops_key() -> str:
    key = os.environ.get("OPS_GATEWAY_KEY", "").strip()
    if key:
        return key
    # Same mechanism the bridge already uses for MM_TOKEN and the Telegram token, so the key
    # lives in one place rather than being exported into yet another service's environment.
    try:
        import mm_lib  # type: ignore

        return mm_lib.read_env_key("OPS_GATEWAY_KEY", mm_lib.default_env_files()) or ""
    except Exception:  # noqa: BLE001
        return ""


def write_memory(payload: dict) -> bool:
    """POST one agent_memory_writeback through the ops door. Never raises.

    Returns True only on a genuinely successful write: HTTP 200, no JSON-RPC error, and no
    `result.isError`. An MCP server reports tool failure as HTTP 200 with isError, so a
    status-code check alone would report unwritten memories as written — the same trap the
    audit mirror's first version fell into.
    """
    key = _ops_key()
    if not key:
        return False
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "agent_memory_writeback", "arguments": payload},
    }
    try:
        import urllib.request as _ur

        req = _ur.Request(
            OPS_DOOR.rstrip("/") + "/mcp",
            data=json.dumps(body).encode(),
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                # StreamableHTTP servers answer either way; accept both or they 406.
                "Accept": "application/json, text/event-stream",
            },
        )
        with _ur.urlopen(req, timeout=TIMEOUT_S) as resp:
            if resp.status != 200:
                return False
            raw = resp.read().decode("utf-8", "replace")
    except Exception:  # noqa: BLE001 - a memory write must never take the bridge down
        return False
    return _write_succeeded(raw)


def _write_succeeded(raw: str) -> bool:
    """Parse an MCP response body and decide whether the WRITE happened. PURE.

    Split out from the transport so the three-way success rule is testable without a socket -
    which is the part that has been got wrong before.
    """
    text = (raw or "").strip()
    # StreamableHTTP may answer as SSE; take the last data: line.
    if text.startswith("event:") or text.startswith("data:"):
        data_lines = [ln[5:].strip() for ln in text.splitlines() if ln.startswith("data:")]
        text = data_lines[-1] if data_lines else ""
    try:
        msg = json.loads(text)
    except (ValueError, TypeError):
        return False
    if not isinstance(msg, dict) or msg.get("error"):
        return False
    result = msg.get("result")
    return isinstance(result, dict) and not result.get("isError")
