#!/usr/bin/env python3
"""Permission-relay + session-tools MCP server for the Claude-Sessions bridge.

Loaded into each headless `claude -p` turn via `--permission-prompt-tool
mcp__approvals__permission_prompt`. When Claude wants a gated tool (Bash, Write, Edit, pushes,
anything not auto-allowed), Claude Code calls the `permission_prompt` tool here with
{tool_name, input, tool_use_id}. This server:

  1. posts a "🛑 Approval needed" message into the session's Mattermost thread,
  2. polls that thread for the operator's `approve` / `deny [reason]` reply,
  3. returns {"behavior":"allow","updatedInput":...} or {"behavior":"deny","message":...}.

FAIL-CLOSED: timeout, Mattermost outage, or any internal error → deny. A remote session can
never execute a gated action without an explicit in-thread approval (DESIGN.md §7.3).

It also exposes the FOLLOW tools (2026-07-15): `follow_thread` / `unfollow` / `list_follows`
let the session subscribe to a Mattermost thread (e.g. a conversation with agent-org's bot-pm)
so the bridge auto-wakes THIS session — resumes it with the new posts as the next prompt —
when someone replies after the turn has ended. Registration is a handoff file
(state/follow-req-*.json) that bridge.py ingests on its next poll; the bridge owns the follow
registry (state/state.json) and does the watching/waking.

Stdlib only. Mattermost API + token handling are reused from scripts/mattermost-mcp/server.py
(token read from agent-org/docker/.env at run time, never committed).

Config via env (injected by bridge.py through the per-turn --mcp-config file):
  BRIDGE_MM_URL            Mattermost base URL            (default http://localhost:8065)
  BRIDGE_ENV_FILE          .env holding AO_MATTERMOST_BOT_TOKEN
  BRIDGE_CHANNEL_ID        channel the session lives in
  BRIDGE_THREAD_ID         root post id of the session's thread   (required)
  BRIDGE_OPERATORS         comma-separated usernames allowed to approve (default profnovice)
  BRIDGE_APPROVAL_TIMEOUT  seconds to wait before denying          (default 1800)
  BRIDGE_APPROVALS_LOG     JSONL audit file for approval events    (optional)
  BRIDGE_ALLOW_SELF        "1" = accept verdicts from the bot user (smoke tests only)
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import uuid

# Reuse the Mattermost MCP server's API/token helpers; map bridge env onto its config knobs
# BEFORE import (it reads MM_URL / MM_ENV_FILE at import time).
if os.environ.get("BRIDGE_MM_URL"):
    os.environ.setdefault("MM_URL", os.environ["BRIDGE_MM_URL"])
if os.environ.get("BRIDGE_ENV_FILE"):
    os.environ.setdefault("MM_ENV_FILE", os.environ["BRIDGE_ENV_FILE"])

# Same dedicated-identity preference as bridge.py: CLAUDE_MM_BOT_TOKEN (bot-claude) if present,
# else fall back to AO_MATTERMOST_BOT_TOKEN (bot-pm) via mmapi's resolution. Searched in order:
# BRIDGE_ENV_FILE, agent-org/docker/.env, the repo-root .env.
_TOKEN_KEY = os.environ.get("BRIDGE_TOKEN_KEY", "CLAUDE_MM_BOT_TOKEN")
_HERE_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT_DIR = os.path.dirname(os.path.dirname(_HERE_DIR))
sys.path.insert(0, os.path.join(_REPO_ROOT_DIR, "scripts", "lib"))
import mm_lib  # noqa: E402

if not os.environ.get("MM_TOKEN"):
    _tok = mm_lib.read_env_key(
        _TOKEN_KEY, mm_lib.default_env_files(os.environ.get("BRIDGE_ENV_FILE", "")))
    if _tok:
        os.environ["MM_TOKEN"] = _tok

sys.path.insert(0, os.path.join(_HERE_DIR, "..", "mattermost-mcp"))
import server as mmapi  # noqa: E402

PROTOCOL_VERSION = "2024-11-05"
THREAD_ID = os.environ.get("BRIDGE_THREAD_ID", "")
CHANNEL_ID = os.environ.get("BRIDGE_CHANNEL_ID", "")
OPERATORS = {u.strip().lower() for u in os.environ.get("BRIDGE_OPERATORS", "profnovice").split(",") if u.strip()}
APPROVAL_TIMEOUT = int(os.environ.get("BRIDGE_APPROVAL_TIMEOUT", "1800"))
ALLOW_SELF = os.environ.get("BRIDGE_ALLOW_SELF") == "1"
APPROVALS_LOG = os.environ.get("BRIDGE_APPROVALS_LOG", "")
POLL_SECONDS = 5

APPROVE_RE = re.compile(r"^\s*(approve[d]?|allow|yes|ok|lgtm)\b", re.IGNORECASE)
DENY_RE = re.compile(r"^\s*(deny|denied|reject|no|stop|abort)\b", re.IGNORECASE)


def _audit(event: dict) -> None:
    if not APPROVALS_LOG:
        return
    try:
        event["ts"] = int(time.time() * 1000)
        with open(APPROVALS_LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(event) + "\n")
    except OSError:
        pass


def _post(message: str) -> dict:
    """Post into the session thread, tagged so the bridge never re-ingests it as operator input."""
    return mmapi._api("POST", "/posts", {
        "channel_id": CHANNEL_ID,
        "root_id": THREAD_ID,
        "message": message,
        "props": {"from_bridge": True},
    })


def _clip(text: str, n: int) -> str:
    text = str(text)
    return text if len(text) <= n else text[:n] + "…"


def _summarize_request(tool_name: str, tool_input: dict) -> str:
    """Human-readable description of what Claude wants to do — decidable from a phone."""
    if tool_name in ("Bash", "PowerShell"):
        desc = str(tool_input.get("description", "")).strip()
        head = f"Claude wants to run a **{tool_name}** command"
        head += f" — *{desc}*:" if desc else ":"
        return f"{head}\n```\n{_clip(tool_input.get('command', ''), 900)}\n```"
    if tool_name == "Write":
        content = str(tool_input.get("content") or "")
        return (f"Claude wants to **write** `{tool_input.get('file_path', '?')}` "
                f"({len(content)} chars):\n```\n{_clip(content, 500)}\n```")
    if tool_name == "Edit":
        return (f"Claude wants to **edit** `{tool_input.get('file_path', '?')}`:\n"
                f"```diff\n- {_clip(tool_input.get('old_string', ''), 300)}\n"
                f"+ {_clip(tool_input.get('new_string', ''), 300)}\n```")
    if tool_name == "NotebookEdit":
        return f"Claude wants to **edit notebook** `{tool_input.get('notebook_path', '?')}`."
    if tool_name == "Skill":
        args = str(tool_input.get("args", "")).strip()
        body = f"Claude wants to load the **{tool_input.get('skill', '?')}** skill"
        return body + (f", for:\n> {_clip(args, 400)}" if args else ".")
    if tool_name == "WebFetch":
        return (f"Claude wants to **fetch a web page**: {tool_input.get('url', '?')}\n"
                f"> {_clip(tool_input.get('prompt', ''), 200)}")
    if tool_name == "WebSearch":
        return f"Claude wants to **search the web** for: `{_clip(tool_input.get('query', '?'), 200)}`"
    if tool_name in ("Task", "Agent"):
        return (f"Claude wants to **spawn a sub-agent** — "
                f"*{_clip(tool_input.get('description', '?'), 100)}*:\n"
                f"> {_clip(tool_input.get('prompt', ''), 400)}")
    if tool_name.startswith("mcp__"):
        parts = tool_name.split("__")
        server_name = parts[1] if len(parts) > 2 else "?"
        short = parts[-1]
        args = _clip(json.dumps(tool_input, ensure_ascii=False, indent=2), 700)
        return (f"Claude wants to call **{short}** on the **{server_name}** MCP server:\n"
                f"```json\n{args}\n```")
    return (f"Claude wants to use **{tool_name}**:\n"
            f"```json\n{_clip(json.dumps(tool_input, ensure_ascii=False, indent=2), 700)}\n```")


def _wait_for_verdict(asked_at_ms: int) -> tuple[str, str]:
    """Poll the thread until an operator replies approve/deny, or timeout. Returns (verdict, reason)."""
    me = mmapi._me()
    deadline = time.time() + APPROVAL_TIMEOUT
    while time.time() < deadline:
        time.sleep(POLL_SECONDS)
        try:
            d = mmapi._api("GET", f"/posts/{THREAD_ID}/thread")
        except Exception:  # noqa: BLE001 - transient MM outage: keep waiting, fail closed at deadline
            continue
        posts = d.get("posts", {}) if isinstance(d, dict) else {}
        candidates = sorted(posts.values(), key=lambda p: p.get("create_at", 0))
        for p in candidates:
            if p.get("create_at", 0) <= asked_at_ms or p.get("type"):
                continue
            if (p.get("props") or {}).get("from_bridge"):
                continue
            uid = p.get("user_id", "")
            if uid == me:
                if not ALLOW_SELF:
                    continue
            elif mmapi._username(uid).lower() not in OPERATORS:
                continue
            msg = (p.get("message") or "").strip()
            if APPROVE_RE.match(msg):
                return "approve", msg
            if DENY_RE.match(msg):
                reason = DENY_RE.sub("", msg, count=1).strip() or "operator denied the action"
                return "deny", reason
    return "timeout", f"no operator response within {APPROVAL_TIMEOUT}s"


def permission_prompt(args: dict) -> dict:
    tool_name = str(args.get("tool_name", "?"))
    tool_input = args.get("input") if isinstance(args.get("input"), dict) else {}
    tool_use_id = args.get("tool_use_id", "")

    if not THREAD_ID or not CHANNEL_ID:
        return {"behavior": "deny", "message": "bridge misconfigured (no thread/channel) — failing closed"}

    ask = _post(
        f"🛑 **Approval needed**\n"
        f"{_summarize_request(tool_name, tool_input)}\n"
        f"Reply **approve** or **deny [reason]** — auto-denies in {APPROVAL_TIMEOUT // 60} min "
        f"if unanswered (the session then adapts or wraps up)."
    )
    asked_at = ask.get("create_at", int(time.time() * 1000)) if isinstance(ask, dict) else int(time.time() * 1000)
    _audit({"event": "approval_requested", "tool": tool_name, "tool_use_id": tool_use_id,
            "thread": THREAD_ID, "input_preview": str(tool_input)[:400]})

    verdict, detail = _wait_for_verdict(asked_at)
    _audit({"event": "approval_resolved", "tool": tool_name, "tool_use_id": tool_use_id,
            "thread": THREAD_ID, "verdict": verdict, "detail": detail[:400]})

    if verdict == "approve":
        try:
            _post(f"✅ Approved — running `{tool_name}`.")
        except Exception:  # noqa: BLE001
            pass
        return {"behavior": "allow", "updatedInput": tool_input}
    if verdict == "deny":
        try:
            _post("🚫 Denied — Claude will adapt or wrap up.")
        except Exception:  # noqa: BLE001
            pass
        return {"behavior": "deny", "message": f"operator denied: {detail}"}
    try:
        _post(f"⌛ No approval within {APPROVAL_TIMEOUT}s — denied (fail-closed). Reply in-thread to continue the work.")
    except Exception:  # noqa: BLE001
        pass
    return {"behavior": "deny", "message": detail}


# ── follow tools (auto-wake on Mattermost replies) ───────────────────────────
STATE_DIR = os.path.join(_HERE_DIR, "state")

# A follow is a SLIDING idle window, not a fixed lifetime (operator 2026-07-16: "a more realistic
# work day that resets its timer based on human engagement"). `expire_hours` is the IDLE window —
# the follow lives this long after the LAST human engagement (the operator messaging the session, or
# posting in the followed channel) and the bridge pushes its expiry forward on every engagement.
# Default is a generous work day so a normal away-day is covered without lapsing.
_FOLLOW_IDLE_HOURS = float(os.environ.get("FOLLOW_IDLE_HOURS", "10"))


def _write_follow_request(req: dict) -> None:
    """Atomic handoff file for bridge.py (write tmp + rename, so the bridge never reads a
    partial JSON). One file per request — no concurrent-append races across turns."""
    os.makedirs(STATE_DIR, exist_ok=True)
    path = os.path.join(STATE_DIR, f"follow-req-{uuid.uuid4().hex[:10]}.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(req, fh, ensure_ascii=False)
    os.replace(tmp, path)


def tool_follow(args: dict) -> str:
    if not THREAD_ID:
        return "error: follow_thread is only available to bridge sessions (no thread context)"
    channel = str(args.get("channel") or "").strip()
    if not channel:
        return "error: `channel` (name or id) is required"
    try:
        ch = mmapi._resolve_channel(channel)
    except Exception as e:  # noqa: BLE001
        return f"error: {e}"
    # The bridge will poll this channel with the same bot identity — verify readability now,
    # self-joining public channels on the fly, so a follow never registers dead.
    try:
        mmapi._api("GET", f"/channels/{ch}/posts?per_page=1")
    except urllib.error.HTTPError as e:
        if e.code == 403:
            try:
                mmapi._api("POST", f"/channels/{ch}/members", {"user_id": mmapi._me()})
                mmapi._api("GET", f"/channels/{ch}/posts?per_page=1")
            except Exception:  # noqa: BLE001
                return (f"error: this bot cannot read {channel} and could not join it — ask the "
                        f"operator to `/invite` the bot there, then call follow_thread again")
        else:
            return f"error: cannot read {channel}: HTTP {e.code}"
    except Exception as e:  # noqa: BLE001
        return f"error: cannot read {channel}: {e}"
    thread_id = str(args.get("thread_id") or "").strip()
    if thread_id:
        try:
            tp = mmapi._api("GET", f"/posts/{thread_id}")
            if not isinstance(tp, dict) or not tp.get("id"):
                return f"error: post {thread_id} not found"
            if tp.get("channel_id") != ch:
                return f"error: post {thread_id} is not in {channel}"
            thread_id = tp.get("root_id") or tp["id"]  # a reply id normalizes to its thread root
        except urllib.error.HTTPError as e:
            return f"error: cannot fetch post {thread_id}: HTTP {e.code}"
        except Exception as e:  # noqa: BLE001
            return f"error: cannot fetch post {thread_id}: {e}"
    label = channel.lstrip("#")
    try:
        cinfo = mmapi._api("GET", f"/channels/{ch}")
        if isinstance(cinfo, dict) and cinfo.get("name"):
            label = cinfo["name"]
    except Exception:  # noqa: BLE001
        pass
    wake_on = [str(u).lstrip("@").strip().lower()
               for u in (args.get("wake_on") or []) if str(u).strip()]
    try:
        hours = float(args.get("expire_hours") or _FOLLOW_IDLE_HOURS)
    except (TypeError, ValueError):
        hours = _FOLLOW_IDLE_HOURS
    hours = max(0.25, min(hours, 336.0))
    try:
        max_wakes = int(args.get("max_wakes") or 20)
    except (TypeError, ValueError):
        max_wakes = 20
    max_wakes = max(1, min(max_wakes, 100))
    one_shot = bool(args.get("one_shot"))
    now = int(time.time() * 1000)
    fid = "fw-" + uuid.uuid4().hex[:6]
    _write_follow_request({
        "action": "follow", "id": fid, "bridge_thread": THREAD_ID,
        "channel_id": ch, "channel_label": label, "thread_id": thread_id,
        "wake_on": wake_on, "note": str(args.get("note") or "")[:300],
        "created": now, "expires": now + int(hours * 3600 * 1000),
        "idle_ms": int(hours * 3600 * 1000),   # the sliding window the bridge renews on engagement
        "last_seen": now, "wakes": 0, "max_wakes": max_wakes, "one_shot": one_shot,
    })
    target = f"#{label}" + (f" thread {thread_id}" if thread_id else " (whole channel)")
    who = ", ".join("@" + u for u in wake_on) or "anyone (this bot's own posts never count)"
    return (f"follow registered: {fid} → {target}. The bridge will auto-wake THIS session with "
            f"the new message(s) whenever {who} posts there — including after this turn ends. "
            f"Limits: {'one-shot' if one_shot else f'max {max_wakes} wakes'}; lapses after "
            f"{hours:g}h of NO human engagement — the window RENEWS every time the operator messages "
            f"this session or posts in the followed channel, so it stays alive while you're working "
            f"and only expires after a full idle 'work day' of silence. Only posts made from now on "
            f"trigger. Call unfollow('{fid}') when the conversation is done.")


def tool_unfollow(args: dict) -> str:
    if not THREAD_ID:
        return "error: unfollow is only available to bridge sessions"
    fid = str(args.get("follow_id") or "").strip().lower()
    if not fid:
        return "error: `follow_id` is required ('fw-…' or 'all')"
    _write_follow_request({"action": "unfollow", "bridge_thread": THREAD_ID, "follow_id": fid})
    return f"unfollow request for '{fid}' queued — the bridge confirms in-thread within a few seconds."


def tool_list_follows(args: dict) -> str:
    try:
        with open(os.path.join(STATE_DIR, "state.json"), "r", encoding="utf-8") as fh:
            follows = (json.load(fh) or {}).get("follows", {})
    except (OSError, json.JSONDecodeError):
        follows = {}
    mine = {fid: f for fid, f in follows.items()
            if bool(args.get("all_sessions")) or f.get("bridge_thread") == THREAD_ID}
    if not mine:
        return ("(no active follows" + ("" if args.get("all_sessions") else " for this session")
                + " — a follow_thread registration can take a few seconds to appear)")
    now = time.time() * 1000
    rows = []
    for fid, f in sorted(mine.items(), key=lambda kv: kv[1].get("created", 0)):
        who = ", ".join(f.get("wake_on") or []) or "anyone"
        hrs = max(0.0, (f.get("expires", 0) - now) / 3600000)
        rows.append(f"{fid}: #{f.get('channel_label', '?')}"
                    + (f" thread {f.get('thread_id')}" if f.get("thread_id") else " (channel)")
                    + f" · wakes on {who} · {f.get('wakes', 0)}/{f.get('max_wakes', '?')} wakes"
                    + f" · expires in {hrs:.1f}h"
                    + (f" · note: {f['note']}" if f.get("note") else ""))
    return "\n".join(rows)


# ── ask_user (the deterministic "I am waiting on the human" signal) ──────────
# The ACT of calling this tool is the signal — the bridge never infers a question from prose.
# While it is parked: the valve stays shut (no background wake can mutate the session between
# the question and its answer) and the next operator message routes here as the answer.
QUESTION_TIMEOUT = int(os.environ.get("BRIDGE_QUESTION_TIMEOUT", "1800"))


def _question_marker() -> str:
    return os.path.join(STATE_DIR, f"question-{THREAD_ID}.json")


def _wait_for_answer(asked_at_ms: int) -> tuple[str, str]:
    """Poll the thread for the operator's answer and return it VERBATIM.

    Unlike _wait_for_verdict this does no pattern matching: ANY operator message is the answer.
    That is precisely what stops "yes, go" being swallowed by the approval relay's verdict
    grammar — the long-standing bug where answers to questions vanished."""
    me = mmapi._me()
    deadline = time.time() + QUESTION_TIMEOUT
    while time.time() < deadline:
        time.sleep(POLL_SECONDS)
        try:
            d = mmapi._api("GET", f"/posts/{THREAD_ID}/thread")
        except Exception:  # noqa: BLE001 - transient MM outage: keep waiting
            continue
        posts = d.get("posts", {}) if isinstance(d, dict) else {}
        for p in sorted(posts.values(), key=lambda x: x.get("create_at", 0)):
            if p.get("create_at", 0) <= asked_at_ms or p.get("type"):
                continue
            if (p.get("props") or {}).get("from_bridge"):
                continue
            uid = p.get("user_id", "")
            if uid == me:
                if not ALLOW_SELF:
                    continue
            elif mmapi._username(uid).lower() not in OPERATORS:
                continue
            msg = (p.get("message") or "").strip()
            if msg:
                return "answered", msg
    return "timeout", ""


def tool_ask_user(args: dict) -> str:
    question = str(args.get("question") or "").strip()
    if not question:
        return "error: `question` is required"
    if not THREAD_ID or not CHANNEL_ID:
        return "error: ask_user is only available to bridge sessions (no thread context)"
    ask = _post(f"❓ **Question for you**\n\n{question}\n\n"
                f"_Reply here to answer — this turn is parked and holding its full context, so "
                f"your answer continues it rather than starting a new one. Background updates "
                f"are paused until you answer (or {QUESTION_TIMEOUT // 60} min passes)._")
    asked_at = (ask.get("create_at", int(time.time() * 1000)) if isinstance(ask, dict)
                else int(time.time() * 1000))
    os.makedirs(STATE_DIR, exist_ok=True)
    marker, tmp = _question_marker(), _question_marker() + ".tmp"
    try:  # atomic write — the bridge must never read a partial marker
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"thread": THREAD_ID, "asked_at": asked_at, "question": question[:500]}, fh)
        os.replace(tmp, marker)
    except OSError:
        pass
    _audit({"event": "question_asked", "thread": THREAD_ID, "question": question[:400]})
    try:
        state, answer = _wait_for_answer(asked_at)
    finally:
        try:  # ALWAYS clear the marker, or the valve would stay shut forever
            os.remove(marker)
        except OSError:
            pass
    _audit({"event": "question_resolved", "thread": THREAD_ID, "state": state,
            "answer": answer[:400]})
    if state == "answered":
        return answer
    try:
        _post(f"⌛ No answer within {QUESTION_TIMEOUT // 60} min — continuing without one. "
              f"Background updates have resumed.")
    except Exception:  # noqa: BLE001
        pass
    return (f"TIMED_OUT: no operator answer within {QUESTION_TIMEOUT // 60} minutes. Decide how "
            f"to proceed yourself — either continue with a clearly-stated assumption, or stop "
            f"and summarise exactly what you need. Do not ask again this turn.")


FOLLOW_TOOLS = {
    "ask_user": {
        "fn": tool_ask_user,
        "description": (
            "Ask the operator a question and WAIT for their answer, returned to you verbatim. "
            "Use this whenever you genuinely cannot proceed without a human decision — it is the "
            "ONLY way to ask that actually blocks. Your turn parks with full context intact, so "
            "the answer continues this same turn rather than starting a new one, and background "
            "auto-wakes are held until you are answered (nothing is lost — they are delivered "
            "afterwards). Returns TIMED_OUT if unanswered, and you then decide how to proceed. "
            "Do NOT use it for things you can determine yourself."),
        "schema": {"type": "object", "properties": {
            "question": {"type": "string", "description": "the question, self-contained — the operator may not have your context"},
        }, "required": ["question"]},
    },
    "follow_thread": {
        "fn": tool_follow,
        "description": (
            "Follow a Mattermost thread (or whole channel) so THIS session is automatically "
            "woken — resumed with the new posts as its next prompt — when someone posts there "
            "after your turn ends. Use it right after sending a Mattermost message that expects "
            "an asynchronous reply (e.g. from another bot like bot-pm, or a human): pass the "
            "post id that mattermost_post returned as thread_id. Your own posts never trigger "
            "a wake. The registration outlives this turn; unfollow when the conversation ends."),
        "schema": {"type": "object", "properties": {
            "channel": {"type": "string", "description": "channel name or 26-char id (required)"},
            "thread_id": {"type": "string", "description": "root post id of the thread to follow; omit to follow every new post in the channel"},
            "wake_on": {"type": "array", "items": {"type": "string"}, "description": "only posts by these usernames wake the session (default: anyone but this bot)"},
            "note": {"type": "string", "description": "why you're waiting — echoed back to you on wake"},
            "expire_hours": {"type": "number", "description": "IDLE window in hours: the follow lapses only after this long with NO human engagement, and the window renews each time the operator messages the session or posts in the followed channel (default 10 = a work day, max 336)"},
            "max_wakes": {"type": "integer", "description": "auto-unfollow after this many wakes (default 20, max 100)"},
            "one_shot": {"type": "boolean", "description": "unfollow after the first wake"},
        }, "required": ["channel"]},
    },
    "unfollow": {
        "fn": tool_unfollow,
        "description": ("Remove one of this session's Mattermost follows (or 'all'). Call it when "
                        "the awaited conversation is finished so the session stops auto-waking."),
        "schema": {"type": "object", "properties": {
            "follow_id": {"type": "string", "description": "the 'fw-…' id from follow_thread, or 'all'"},
        }, "required": ["follow_id"]},
    },
    "list_follows": {
        "fn": tool_list_follows,
        "description": "List this session's active Mattermost follows (all_sessions=true for every session's).",
        "schema": {"type": "object", "properties": {
            "all_sessions": {"type": "boolean", "description": "include follows registered by other bridge sessions"},
        }},
    },
}


# ── minimal stdio MCP loop ───────────────────────────────────────────────────
def _reply(rid, result=None, error=None):
    if error is not None:
        return {"jsonrpc": "2.0", "id": rid, "error": error}
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def handle(msg: dict):
    method = msg.get("method")
    rid = msg.get("id")
    if method == "initialize":
        return _reply(rid, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "approvals", "version": "1.0.0"}})
    if method in ("notifications/initialized", "initialized"):
        return None
    if method == "ping":
        return _reply(rid, {})
    if method == "tools/list":
        tools = [{
            "name": "permission_prompt",
            "description": "Relays Claude Code permission prompts to the operator's Mattermost thread.",
            "inputSchema": {"type": "object", "properties": {
                "tool_name": {"type": "string"},
                "input": {"type": "object"},
                "tool_use_id": {"type": "string"},
            }},
        }]
        tools += [{"name": n, "description": t["description"], "inputSchema": t["schema"]}
                  for n, t in FOLLOW_TOOLS.items()]
        return _reply(rid, {"tools": tools})
    if method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name")
        if name == "permission_prompt":
            try:
                decision = permission_prompt(params.get("arguments") or {})
            except Exception as e:  # noqa: BLE001 - ANY failure denies (fail-closed)
                decision = {"behavior": "deny", "message": f"approval relay error ({e}) — failing closed"}
            return _reply(rid, {"content": [{"type": "text", "text": json.dumps(decision)}]})
        if name in FOLLOW_TOOLS:
            try:
                text = FOLLOW_TOOLS[name]["fn"](params.get("arguments") or {})
            except Exception as e:  # noqa: BLE001 - fail-soft: a broken tool must not kill the relay
                text = f"error: {e}"
            out = {"content": [{"type": "text", "text": text}]}
            if text.startswith("error:"):
                out["isError"] = True
            return _reply(rid, out)
        return _reply(rid, error={"code": -32602, "message": f"unknown tool: {name}"})
    if rid is not None:
        return _reply(rid, error={"code": -32601, "message": f"method not found: {method}"})
    return None


def main() -> None:
    # Mirror bridge.py: a present-but-invalid dedicated token must not break the approval
    # relay (which fails closed) — probe and fall back to the shared identity.
    if os.environ.get("MM_TOKEN"):
        try:
            mmapi._api("GET", "/users/me")
        except Exception:  # noqa: BLE001
            del os.environ["MM_TOKEN"]
            mmapi._me_id = None
            mmapi._user_cache.clear()
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
