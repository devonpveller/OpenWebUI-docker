#!/usr/bin/env python3
"""Mattermost MCP server — a permanent, dependency-free bridge so Claude Code can READ and POST
to the self-hosted Mattermost (the #claude-code operator channel + any project channel) with
native tools, instead of ad-hoc `docker exec ... curl` one-liners.

Design goals (why this shape):
  • STDLIB ONLY (urllib/json) — no pip install, works with any Python 3.8+, survives venv churn.
  • Token is READ FROM `.env` AT RUN TIME (AO_MATTERMOST_BOT_TOKEN in agent-org/docker/.env) —
    never hardcoded, never committed, same non-negotiable as scripts/notify-mattermost.sh.
  • Fail-soft: every tool returns a readable error string rather than crashing the server, so a
    down Mattermost or a bad arg never kills the session's tool.

Tools:
  • mattermost_read     — recent posts from a channel (default #claude-code), newest last, each
                          tagged OPERATOR vs me(bot) so I can spot replies; supports `limit` +
                          `since` (ms epoch) to poll only what's new.
  • mattermost_post     — post a message to a channel (default #claude-code); optional `thread_id`
                          (root post id) to reply in a thread.
  • mattermost_channels — resolve/list channel names → ids across the bot's teams.

Config via env (all optional; sensible defaults):
  MM_URL        base URL          (default http://localhost:8065)
  MM_ENV_FILE   path to the .env holding the token (default: agent-org/docker/.env in this repo)
  MM_TOKEN      bot token override (highest precedence)
  MM_TOKEN_KEY  env-file key for the DEDICATED identity (default CLAUDE_MM_BOT_TOKEN → bot-claude)
  MM_DEFAULT_CHANNEL  channel id used when a tool omits `channel` (default #claude-code id)

Identity (since 2026-07-15): like the claude-sessions bridge, this server prefers the dedicated
bot-claude token (CLAUDE_MM_BOT_TOKEN, searched in MM_ENV_FILE then the repo-root .env) so
Claude's own posts are visually distinct from agent-org's bot-pm — and so the bridge's
follow/auto-wake feature can tell Claude's posts apart from the bots it is waiting on. Falls
back to AO_MATTERMOST_BOT_TOKEN if the dedicated token is absent or rejected (401/403).

MCP stdio transport: newline-delimited JSON-RPC 2.0 (one message per line, no embedded newlines).
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

PROTOCOL_VERSION = "2024-11-05"
DEFAULT_URL = os.environ.get("MM_URL", "http://localhost:8065").rstrip("/")
DEFAULT_CHANNEL = os.environ.get("MM_DEFAULT_CHANNEL", "qqq97fwxd3f8ufenjybrf5w1yr")  # #claude-code
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_ENV_FILE = os.environ.get(
    "MM_ENV_FILE", os.path.join(_REPO_ROOT, "agent-org", "docker", ".env"))

# small caches so repeated reads don't re-resolve identities
_user_cache: dict[str, str] = {}
_me_id: str | None = None


# ── config / token ──────────────────────────────────────────────────────────
TOKEN_KEY = os.environ.get("MM_TOKEN_KEY", "CLAUDE_MM_BOT_TOKEN")
_TOKEN_ENV_FILES = [DEFAULT_ENV_FILE, os.path.join(_REPO_ROOT, ".env")]
_token_cache: str | None = None

sys.path.insert(0, os.path.join(_REPO_ROOT, "scripts", "lib"))
from mm_lib import read_env_key as _read_env_key  # noqa: E402


def _probe_ok(tok: str) -> bool:
    """True unless Mattermost explicitly rejects the token (401/403). Network/server errors
    pass — never switch identity on a transient outage."""
    req = urllib.request.Request(f"{DEFAULT_URL}/api/v4/users/me", method="GET")
    req.add_header("Authorization", f"Bearer {tok}")
    try:
        with urllib.request.urlopen(req, timeout=10):  # noqa: S310 - trusted localhost
            return True
    except urllib.error.HTTPError as e:
        return e.code not in (401, 403)
    except Exception:  # noqa: BLE001
        return True


def _token() -> str:
    global _token_cache
    # Explicit env overrides win and are re-read every call, so callers (bridge.py,
    # approval_server.py) that set/delete MM_TOKEN to steer identity keep working.
    tok = os.environ.get("MM_TOKEN") or os.environ.get("AO_MATTERMOST_BOT_TOKEN")
    if tok:
        return tok.strip()
    if _token_cache:
        return _token_cache
    dedicated = _read_env_key(TOKEN_KEY, _TOKEN_ENV_FILES)
    fallback = _read_env_key("AO_MATTERMOST_BOT_TOKEN", _TOKEN_ENV_FILES)
    if dedicated and (_probe_ok(dedicated) or not fallback):
        _token_cache = dedicated
    elif fallback:
        _token_cache = fallback
    else:
        raise RuntimeError(
            f"no Mattermost bot token (set MM_TOKEN, or {TOKEN_KEY}/AO_MATTERMOST_BOT_TOKEN "
            f"in {DEFAULT_ENV_FILE} or the repo-root .env)")
    return _token_cache


# ── HTTP ────────────────────────────────────────────────────────────────────
def _api(method: str, path: str, body: dict | None = None) -> object:
    url = f"{DEFAULT_URL}/api/v4{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {_token()}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310 - trusted localhost
        raw = resp.read().decode("utf-8", "replace")
    return json.loads(raw) if raw else {}


def _me() -> str:
    global _me_id
    if _me_id is None:
        try:
            _me_id = _api("GET", "/users/me").get("id", "")  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            _me_id = ""
    return _me_id


def _username(uid: str) -> str:
    if uid not in _user_cache:
        try:
            u = _api("GET", f"/users/{uid}")
            _user_cache[uid] = u.get("username") or u.get("nickname") or uid[:8]  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            _user_cache[uid] = uid[:8]
    return _user_cache[uid]


def _resolve_channel(channel: str | None) -> str:
    """A 26-char id is used as-is; a name is resolved across the bot's teams; None → default."""
    if not channel:
        return DEFAULT_CHANNEL
    ch = channel.lstrip("#").strip()
    if len(ch) == 26 and ch.isalnum():
        return ch
    teams = _api("GET", "/users/me/teams")
    for t in (teams if isinstance(teams, list) else []):
        try:
            c = _api("GET", f"/teams/{t['id']}/channels/name/{ch}")
            if isinstance(c, dict) and c.get("id"):
                return c["id"]
        except Exception:  # noqa: BLE001
            continue
    raise RuntimeError(f"channel not found: {channel}")


# ── tools ───────────────────────────────────────────────────────────────────
def tool_read(args: dict) -> str:
    ch = _resolve_channel(args.get("channel"))
    limit = max(1, min(int(args.get("limit", 15)), 60))
    since = args.get("since")
    exclude_self = bool(args.get("exclude_self", False))
    d = _api("GET", f"/channels/{ch}/posts?per_page={limit}")
    posts = d.get("posts", {}) if isinstance(d, dict) else {}
    order = d.get("order", []) if isinstance(d, dict) else []
    me = _me()
    rows = []
    for pid in reversed(order):  # oldest → newest
        p = posts.get(pid, {})
        if p.get("type"):  # skip system join/leave posts
            continue
        ca = p.get("create_at", 0)
        if since is not None and ca <= int(since):
            continue
        uid = p.get("user_id", "")
        from_webhook = bool((p.get("props") or {}).get("from_webhook"))
        who = "me(bot)" if (uid == me or from_webhook) else f"OPERATOR:{_username(uid)}"
        if exclude_self and who == "me(bot)":
            continue
        msg = (p.get("message") or "").strip()
        rows.append(f"[{ca}] {who}: {msg}")
    if not rows:
        return f"(no messages{' since ' + str(since) if since else ''} in {ch})"
    return "\n".join(rows)


def tool_post(args: dict) -> str:
    msg = args.get("message")
    if not msg:
        return "error: `message` is required"
    ch = _resolve_channel(args.get("channel"))
    # `from_claude` marks posts Claude itself sent through this MCP — the claude-sessions
    # bridge's follow/auto-wake scanner skips them so a session can never wake on its own
    # replies (loop guard that holds even under the bot-pm token fallback).
    body = {"channel_id": ch, "message": str(msg), "props": {"from_claude": True}}
    if args.get("thread_id"):
        body["root_id"] = str(args["thread_id"])
    r = _api("POST", "/posts", body)
    pid = r.get("id", "") if isinstance(r, dict) else ""
    return f"posted to {ch} (post id {pid}, root {r.get('root_id') or '-'})" if pid else "post failed"


def tool_channels(args: dict) -> str:
    query = (args.get("query") or "").lower().strip()
    teams = _api("GET", "/users/me/teams")
    out = []
    for t in (teams if isinstance(teams, list) else []):
        chans = _api("GET", f"/users/me/teams/{t['id']}/channels")
        for c in (chans if isinstance(chans, list) else []):
            name = c.get("name", "")
            disp = c.get("display_name", "")
            if query and query not in name.lower() and query not in disp.lower():
                continue
            out.append(f"{c.get('id')}  #{name}  ({disp})")
    return "\n".join(out) if out else "(no channels matched)"


TOOLS = {
    "mattermost_read": {
        "fn": tool_read,
        "description": ("Read recent messages from a Mattermost channel (default the #claude-code "
                        "operator channel), oldest→newest. Each line is tagged OPERATOR:<user> or "
                        "me(bot) so you can spot the operator's replies. Use `since` (ms-epoch from "
                        "a prior read's last timestamp) to poll only new messages."),
        "schema": {
            "type": "object",
            "properties": {
                "channel": {"type": "string", "description": "channel id or name; omit for #claude-code"},
                "limit": {"type": "integer", "description": "max messages (1-60, default 15)"},
                "since": {"type": "integer", "description": "only messages with create_at > this (ms epoch)"},
                "exclude_self": {"type": "boolean", "description": "drop the bot's own/webhook posts"},
            },
        },
    },
    "mattermost_post": {
        "fn": tool_post,
        "description": ("Post a message to a Mattermost channel (default #claude-code). Optional "
                        "`thread_id` (a root post id) to reply inside a thread. Posts as the bot."),
        "schema": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "the message text (markdown ok)"},
                "channel": {"type": "string", "description": "channel id or name; omit for #claude-code"},
                "thread_id": {"type": "string", "description": "root post id to reply under (optional)"},
            },
            "required": ["message"],
        },
    },
    "mattermost_channels": {
        "fn": tool_channels,
        "description": "List/resolve channels the bot can see (id, name, display) — optional `query` filter.",
        "schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "case-insensitive name filter"}},
        },
    },
}


# ── JSON-RPC / MCP loop ───────────────────────────────────────────────────────
def _result(rid, result):
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def _error(rid, code, message):
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def handle(msg: dict):
    method = msg.get("method")
    rid = msg.get("id")
    if method == "initialize":
        return _result(rid, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "mattermost", "version": "1.0.0"},
        })
    if method in ("notifications/initialized", "initialized"):
        return None
    if method == "ping":
        return _result(rid, {})
    if method == "tools/list":
        return _result(rid, {"tools": [
            {"name": n, "description": t["description"], "inputSchema": t["schema"]}
            for n, t in TOOLS.items()]})
    if method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        tool = TOOLS.get(name)
        if not tool:
            return _error(rid, -32602, f"unknown tool: {name}")
        try:
            text = tool["fn"](args)
            return _result(rid, {"content": [{"type": "text", "text": text}]})
        except urllib.error.HTTPError as e:  # surface API errors as tool content, not a crash
            body = e.read().decode("utf-8", "replace")[:300] if hasattr(e, "read") else ""
            return _result(rid, {"content": [{"type": "text", "text": f"HTTP {e.code}: {body}"}],
                                 "isError": True})
        except Exception as e:  # noqa: BLE001
            return _result(rid, {"content": [{"type": "text", "text": f"error: {e}"}],
                                 "isError": True})
    if rid is not None:
        return _error(rid, -32601, f"method not found: {method}")
    return None


def main() -> None:
    # MCP frames are UTF-8 JSON; Windows stdio defaults to cp1252, which
    # mangled every emoji/em-dash on its way to Mattermost (operator report
    # 2026-08-23: posts rendering as mojibake). Force UTF-8 both directions.
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        resp = handle(msg)
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
