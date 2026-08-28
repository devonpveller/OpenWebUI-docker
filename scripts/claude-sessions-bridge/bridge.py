#!/usr/bin/env python3
"""Claude-Sessions bridge — Mattermost threads ⟷ headless Claude Code sessions.

The inbound half of documentation/implementation-guide/claude-code-mattermost-bridge/DESIGN.md
(P-CCB.1 with the P-CCB.3 mid-turn approval relay built in):

  #claude-sessions channel
    ├─ ROOT post            → start a NEW `claude -p` session (session_id captured)
    ├─ reply in a thread    → RESUME that thread's session (`--resume <session_id>`)
    ├─ turn completes       → final result posted back into the thread
    ├─ gated tool mid-turn  → approval_server.py posts "🛑 Approval needed"; the operator's
    │                         in-thread `approve` / `deny` moves the turn along (fail-closed)
    └─ follow registered    → the session subscribed to another Mattermost thread (e.g. a
                              conversation with agent-org's bot-pm) via the approvals MCP
                              follow_thread tool; the bridge watches it and AUTO-WAKES the
                              session — resumes it with the new posts — when someone replies

Governance (DESIGN.md §7): operator allow-list on every inbound post; sessions run in the
default permission mode (NEVER bypassPermissions) so reads/safe commands are automatic and
everything else relays to the human; token read from agent-org/docker/.env at run time;
bridge down → nothing executes.

Stdlib only; run on the host (Claude Code CLI + auth live here):
  python scripts/claude-sessions-bridge/bridge.py

Config via env (all optional):
  BRIDGE_MM_URL            Mattermost base URL              (default http://localhost:8065)
  BRIDGE_ENV_FILE          .env with AO_MATTERMOST_BOT_TOKEN (default agent-org/docker/.env)
  BRIDGE_CHANNEL_ID        channel to watch                  (default #claude-sessions)
  BRIDGE_OPERATORS         comma-separated usernames allowed to drive sessions (default profnovice)
  BRIDGE_REPO              working directory for sessions    (default this repo)
  BRIDGE_CLAUDE_BIN        path to claude CLI                (default: PATH, then newest VS Code ext)
  BRIDGE_MODEL             model for every turn              (default: opus — see MODEL below)
  BRIDGE_MAX_BUDGET_USD    per-turn cost-estimate backstop   (default 50; "" disables)
  BRIDGE_PERMISSION_MODE   default approval level            (default "auto"; per-thread
                           override via `mode: <level>` — bypassPermissions is refused)
  BRIDGE_TURN_TIMEOUT      seconds before a turn is killed   (default 7200)
  BRIDGE_APPROVAL_TIMEOUT  seconds an approval waits         (default 1800)
  BRIDGE_POLL_INTERVAL     channel poll seconds              (default 4)
  BRIDGE_MAX_CONCURRENT    concurrent turns across threads   (default 2)
  BRIDGE_SETTING_SOURCES   claude --setting-sources          (default "user,project,local" —
                           ONE allow-list shared with interactive sessions, see SETTING_SOURCES)
  BRIDGE_WORKTREE_DEFAULT  run threads in their own git worktree (default "off";
                           per-thread override via `worktree: on|off`)
  BRIDGE_ALLOWED_TOOLS     optional extra --allowedTools floor (e.g. "Read Glob Grep")
  BRIDGE_ALLOW_SELF        "1" = treat the bot's own posts as operator input (smoke tests only)
"""

from __future__ import annotations

import glob as globmod
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import deque

try:
    # Under pythonw.exe (the Scheduled Task runs it to avoid a console window) stdout/stderr
    # are None — the file log below is then the only output.
    if sys.stdout is not None:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    if sys.stderr is not None:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
except (AttributeError, ValueError):
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(_HERE))

# Reuse the Mattermost MCP server's API/token helpers (same env-file token discipline).
if os.environ.get("BRIDGE_MM_URL"):
    os.environ.setdefault("MM_URL", os.environ["BRIDGE_MM_URL"])
if os.environ.get("BRIDGE_ENV_FILE"):
    os.environ.setdefault("MM_ENV_FILE", os.environ["BRIDGE_ENV_FILE"])

# Dedicated bot identity: prefer CLAUDE_MM_BOT_TOKEN (the bot-claude account) when present, so
# bridge traffic is visually distinct from the agent-org bot-pm. Falls back to
# AO_MATTERMOST_BOT_TOKEN (bot-pm) via the mmapi resolution below when the key is absent.
# Searched in order: BRIDGE_ENV_FILE, agent-org/docker/.env, the repo-root .env.
TOKEN_KEY = os.environ.get("BRIDGE_TOKEN_KEY", "CLAUDE_MM_BOT_TOKEN")

sys.path.insert(0, os.path.join(_REPO_ROOT, "scripts", "lib"))
import mm_lib  # noqa: E402

if not os.environ.get("MM_TOKEN"):
    _tok = mm_lib.read_env_key(
        TOKEN_KEY, mm_lib.default_env_files(os.environ.get("BRIDGE_ENV_FILE", "")))
    if _tok:
        os.environ["MM_TOKEN"] = _tok

sys.path.insert(0, os.path.join(_HERE, "..", "mattermost-mcp"))
sys.path.insert(0, _HERE)
import server as mmapi  # noqa: E402
import sessions as sessions_mod  # noqa: E402

# ── config ───────────────────────────────────────────────────────────────────
CHANNEL_ID = os.environ.get("BRIDGE_CHANNEL_ID", "6z9khgkdd7df9q454be6fimw1h")  # #claude-sessions
OPERATORS = {u.strip().lower() for u in os.environ.get("BRIDGE_OPERATORS", "profnovice").split(",") if u.strip()}

REPO = os.environ.get("BRIDGE_REPO", _REPO_ROOT)
# Pinned, NOT "whatever the CLI would pick" (operator, 2026-08-28). Passing no --model let every
# unpinned thread inherit the account's default, which now resolves to the top-tier model
# (fable, $10/$50 per Mtok) — 30 of 41 threads were silently running there. `opus` is the
# operator's choice of floor; raise a single thread with `model: fable`, drop one with
# `model: sonnet`/`haiku`, or move the whole bridge with BRIDGE_MODEL.
MODEL = os.environ.get("BRIDGE_MODEL", "opus")
# Per-turn cost-estimate cap. On a subscription nothing is billed — this is purely a
# runaway-turn backstop (a "$50" turn ≈ a huge chunk of the Max usage window), sized so it
# never fires on legitimate work. Set to "" to disable entirely.
MAX_BUDGET_USD = os.environ.get("BRIDGE_MAX_BUDGET_USD", "50")
# Default approval level for sessions (operator choice 2026-07-13): `auto` — the classifier-
# backed mode; routine actions run without asking, flagged/risky ones still relay to the
# thread via the approval server. Per-thread override: `mode: <level>` directive.
PERMISSION_MODE = os.environ.get("BRIDGE_PERMISSION_MODE", "auto")
TURN_TIMEOUT = int(os.environ.get("BRIDGE_TURN_TIMEOUT", "7200"))
APPROVAL_TIMEOUT = int(os.environ.get("BRIDGE_APPROVAL_TIMEOUT", "1800"))
POLL_INTERVAL = max(2, int(os.environ.get("BRIDGE_POLL_INTERVAL", "4")))
MAX_CONCURRENT = max(1, int(os.environ.get("BRIDGE_MAX_CONCURRENT", "2")))
# Includes `local` (operator, 2026-08-28), reversing the original "don't let interactively-saved
# allows widen the remote floor". Rationale: ONE allow-list is the dependency for both session
# kinds, so a rule added or removed in `.claude/settings.local.json` moves them together — no
# second list to keep in sync, and no class of command that works at the desk but dies remotely.
# The hard floor is unchanged and lives elsewhere: bypassPermissions is refused, every gated call
# still relays to the thread, and only BRIDGE_OPERATORS can drive a session at all.
SETTING_SOURCES = os.environ.get("BRIDGE_SETTING_SOURCES", "user,project,local")
# Worktree-per-thread (2026-08-28). OFF by default: it changes WHERE every turn runs, so
# it opts in per thread (`worktree: on`) until it has soaked. When on, a thread gets its
# own checkout + branch and can no longer collide with another session's staged work.
# Tooling and protocol: scripts/worktree/, documentation/implementation-guide/
# multi-agent-concurrency/MERGE-PROTOCOL.md.
WORKTREE_DEFAULT = os.environ.get("BRIDGE_WORKTREE_DEFAULT", "off").strip().lower() in (
    "1", "on", "true", "yes")
WORKTREE_SCRIPTS = os.path.join(_REPO_ROOT, "scripts", "worktree")
ALLOWED_TOOLS = os.environ.get("BRIDGE_ALLOWED_TOOLS", "")
ALLOW_SELF = os.environ.get("BRIDGE_ALLOW_SELF") == "1"

# ── two-lane attention model (PLAN-bridge-two-lane-attention.md) ─────────────
# Lane 1 = the operator (ungated, always first). Lane 2 = background auto-wakes (valved).
# The valve is the gate on lane 2; its state is a pure function of lane-1 activity:
#   operator message queued/running → CLOSED · agent parked on ask_user → CLOSED until
#   answered or timed out · turn ends with no parked question → OPEN.
# Nothing in lane 2 is ever discarded — only deferred, then delivered as one catch-up turn.
VALVE_ENABLED = os.environ.get("BRIDGE_VALVE", "1") != "0"
# How long `ask_user` holds a question open before returning TIMED_OUT (operator: ~30 min).
QUESTION_TIMEOUT = int(os.environ.get("BRIDGE_QUESTION_TIMEOUT", "1800"))
# On valve re-open, coalesce all held lane-2 items into ONE catch-up turn (operator choice)
# rather than replaying them one-by-one. Everything is still observed; no turn-storm.
CATCHUP_COALESCE = os.environ.get("BRIDGE_CATCHUP_COALESCE", "1") != "0"
# Phase 5 rollout: SHADOW = classify every wake and LOG what would have been suppressed, but
# wake exactly as today. Flip to 0 only once the classifier has a clean round of evidence.
CLASSIFY_SHADOW = os.environ.get("BRIDGE_CLASSIFY_SHADOW", "1") != "0"
# A lone wake younger than this (minutes) was never actually held, so it is delivered EXACTLY as
# it was before the two-lane change — no catch-up wrapper, no staleness warning. Wrapping a live
# update in "held while you were with the operator … VERIFY before acting" tells the session a
# falsehood and makes it distrust current information.
WAKE_FRESH_MIN = int(os.environ.get("BRIDGE_WAKE_FRESH_MIN", "2"))

STATE_DIR = os.environ.get("BRIDGE_STATE_DIR") or os.path.join(_HERE, "state")
STATE_FILE = os.path.join(STATE_DIR, "state.json")
AUDIT_FILE = os.path.join(STATE_DIR, "audit.jsonl")
APPROVALS_LOG = os.path.join(STATE_DIR, "approvals.jsonl")

# Matches approval_server.py — verdict replies during a running turn belong to the approval
# relay, not to the session's prompt queue.
VERDICT_RE = re.compile(r"^\s*(approve[d]?|allow|yes|ok|lgtm|deny|denied|reject|no|stop|abort)\b", re.IGNORECASE)

# Per-thread model: `model: <alias-or-id>` at the start of any message sets the model for the
# thread's subsequent turns (persisted in state). Colon/equals REQUIRED so prose that merely
# starts with the word "model" can't trigger it. A directive-only message just switches it.
MODEL_DIRECTIVE_RE = re.compile(r"^\s*model\s*[:=]\s*(\S+)\s*(.*)\Z", re.IGNORECASE | re.DOTALL)

# `sessions [filter]` as the whole message → the bridge answers directly with the session
# inventory (id + age + title, 🧵mm tag for bridge threads) — no Claude turn is spawned.
SESSIONS_CMD_RE = re.compile(r"^\s*sessions?(?:\s+(\S{1,40}))?\s*$", re.IGNORECASE)

# `follows` as the whole message → list the active auto-wake follows; `unfollow <id|all>` drops
# them — the operator's kill switch for a runaway wake loop. Sessions register follows through
# the approvals MCP server's follow_thread tool (handoff files: state/follow-req-*.json).
FOLLOWS_CMD_RE = re.compile(r"^\s*follows\s*$", re.IGNORECASE)
UNFOLLOW_CMD_RE = re.compile(r"^\s*unfollow\s+(fw-[0-9a-f]{4,12}|all)\s*$", re.IGNORECASE)

# Operator full-stop: purge lane 2, keep every lane-1 (operator) item, and abort the in-flight
# turn ONLY if it is a lane-2 turn. Deliberately `!stop` and NOT `/stop` — Mattermost intercepts
# a leading `/` as a slash command, so `/stop` never becomes a channel post the bridge can see.
# Must be matched BEFORE VERDICT_RE, which already claims bare `stop`/`abort` for the approval relay.
STOP_CMD_RE = re.compile(r"^\s*!\s*stop\s*$", re.IGNORECASE)

# `close` / `end session` as the whole message → DISPOSE this session's lingering setups: drop
# every follow it registered, purge its queued background wakes + buffered digest, clear a parked
# question, and mark the thread CLOSED so nothing can auto-wake it again.
#
# Why this exists (2026-07-18 incident): `!stop` only empties the QUEUE — the follow survives, so
# the next matching post re-woke a two-day-old abandoned session 11 seconds after a full stop, and
# it answered alongside the new session the operator had moved to. A session had no OFF switch;
# its only remaining activity was the wake that kept renewing its own idle window.
#
# Closing is not deletion and not a kill: the Claude session id is kept, an operator turn already
# running is never aborted, and the operator's next message in the thread REOPENS it (a human
# talking to a session obviously means it is live again).
CLOSE_CMD_RE = re.compile(r"^\s*!?\s*(?:close|end)(?:\s+(?:this\s+|the\s+)?session)?\s*$",
                          re.IGNORECASE)

# 👎 on a queued message cancels exactly that item (lazy/tombstone deletion — the worker skips it
# on dequeue). Operator reactions only; a bot's reaction can never cancel.
CANCEL_EMOJI = {"-1", "thumbsdown"}

# Per-thread approval level: `mode: <level>` (colon required, like `model:`). bypassPermissions
# is deliberately NOT reachable from a chat message — that's the bridge's hard floor.
MODE_DIRECTIVE_RE = re.compile(r"^\s*mode\s*[:=]\s*(\S+)\s*(.*)\Z", re.IGNORECASE | re.DOTALL)

# Per-thread git isolation: `worktree: on|off` (colon required, like the others). `on` gives
# this thread its own checkout + branch `work/mm-<thread8>`, so two threads editing the same
# file can never see each other's index. Persisted per thread.
WORKTREE_DIRECTIVE_RE = re.compile(r"^\s*worktree\s*[:=]\s*(\S+)\s*(.*)\Z",
                                   re.IGNORECASE | re.DOTALL)
PERMISSION_MODES = {"auto": "auto", "acceptedits": "acceptEdits", "manual": "manual",
                    "default": "manual", "dontask": "dontAsk", "plan": "plan"}

# Root-post session handoff: `handoff <session-uuid> [first prompt]` attaches the new thread to
# an EXISTING Claude Code session (e.g. one started interactively on the desktop); `fork <uuid>`
# continues a forked copy instead, leaving the original session untouched (use when the source
# session may still be driven interactively — two writers on one session id diverge).
HANDOFF_RE = re.compile(
    r"^\s*(handoff|resume|attach|fork)\s*[:=]?\s*"
    r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\s*(.*)\Z",
    re.IGNORECASE | re.DOTALL)

MM_POST_LIMIT = 12000  # Mattermost hard cap is ~16383 chars; chunk well under it

REMOTE_NOTE = (
    "You are a headless Claude Code session driven from a Mattermost thread by your operator. "
    "Your final message is posted back into that thread: write it as a self-contained, readable "
    "reply in plain markdown, no giant dumps. Permission prompts for gated tools are relayed to "
    "the operator in the thread and may take a while to be answered; if one is denied or times "
    "out, adapt or end the turn explaining exactly what you need. Prefer completing the requested "
    "step and reporting over asking questions you could answer yourself. If you post a Mattermost "
    "message that expects an ASYNC reply (e.g. to another bot such as bot-pm), do not poll for it: "
    "call the approvals MCP server's follow_thread tool with that post's id and end your turn — "
    "the bridge will automatically wake this session with the reply when it arrives. "
    "If your working directory is under `.claude/worktrees/`, you are in YOUR OWN git worktree: "
    "commit there freely, never `cd` into the main checkout to mutate it, test in your own "
    "containers (`-p test-<id>`, no host ports, images tagged `:wt-<id>`) rather than prod ones, "
    "and land your work with documentation/implementation-guide/multi-agent-concurrency/"
    "MERGE-PROTOCOL.md — take scripts/worktree/merge-lock.ps1, rebase onto development, re-run "
    "your gates, merge --no-ff with the evidence, release, then say what landed in this thread."
)


def _load_charter() -> str:
    """Optional persona charter appended to REMOTE_NOTE, so ONE bridge codebase can run multiple
    personas (e.g. a systems-administrator instance) by env alone. Backward-compatible: with neither
    env var set this returns '' and the system prompt is exactly REMOTE_NOTE (default behaviour)."""
    txt = os.environ.get("BRIDGE_APPEND_PROMPT", "") or ""
    path = os.environ.get("BRIDGE_CHARTER_FILE", "")
    if path:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                extra = fh.read()
            txt = (txt + "\n\n" + extra) if txt else extra
        except OSError:
            pass
    return txt.strip()


CHARTER = _load_charter()
SYSTEM_PROMPT = (REMOTE_NOTE + "\n\n" + CHARTER) if CHARTER else REMOTE_NOTE


# ── small utils ──────────────────────────────────────────────────────────────
LOG_FILE = os.path.join(STATE_DIR, "bridge.log")


def log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    if sys.stdout is not None:
        try:
            print(line, flush=True)
        except Exception:  # noqa: BLE001
            pass
    try:  # file log is the primary record under the Scheduled Task; simple 5MB rotation
        os.makedirs(STATE_DIR, exist_ok=True)
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > 5 * 1024 * 1024:
            os.replace(LOG_FILE, LOG_FILE + ".1")
        with open(LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def audit(event: dict) -> None:
    try:
        event["ts"] = int(time.time() * 1000)
        with open(AUDIT_FILE, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")
    except OSError:
        pass


def acquire_single_instance_lock():
    """Bind a localhost port as a cross-process mutex — two bridges would double-process every
    message. The OS releases the port automatically if the process dies, so no stale locks."""
    import socket
    port = int(os.environ.get("BRIDGE_LOCK_PORT", "48291"))
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", port))
        s.listen(1)
        return s
    except OSError:
        return None


def wait_for_mattermost() -> None:
    """Block until Mattermost answers, distinguishing 'server down' (wait — e.g. the bridge
    starts at logon before Docker brings Mattermost up) from 'token rejected' (fall back to the
    shared identity once, then keep waiting if that is also rejected)."""
    import urllib.error
    delay = 5
    while True:
        try:
            mmapi._api("GET", "/users/me")
            return
        except urllib.error.HTTPError as e:
            if e.code in (401, 403) and os.environ.get("MM_TOKEN"):
                log(f"WARNING: {TOKEN_KEY} rejected by Mattermost (HTTP {e.code}) — falling "
                    f"back to AO_MATTERMOST_BOT_TOKEN. Common cause: the Bot Accounts page "
                    f"shows a 'Token ID', which is NOT the access token (shown once at "
                    f"creation). Regenerate, update the env file, restart the bridge.")
                del os.environ["MM_TOKEN"]
                mmapi._me_id = None
                mmapi._user_cache.clear()
                continue
            if e.code in (401, 403):
                log(f"ERROR: Mattermost rejected the fallback token too (HTTP {e.code}) — "
                    f"fix the token in the env file; retrying in 60s (tokens are re-read "
                    f"from the file on each attempt).")
                time.sleep(60)
                continue
            log(f"Mattermost answered HTTP {e.code} — retrying in {delay}s")
        except Exception as e:  # noqa: BLE001 - connection refused / DNS / MM still booting
            log(f"waiting for Mattermost… ({e})")
        time.sleep(delay)
        delay = min(delay * 2, 60)


def find_claude_bin() -> str:
    env_bin = os.environ.get("BRIDGE_CLAUDE_BIN")
    if env_bin:
        if os.path.isfile(env_bin):
            return env_bin
        log(f"BRIDGE_CLAUDE_BIN points at a missing file ({env_bin}) — falling back to PATH/extension glob")
    on_path = shutil.which("claude")
    if on_path:
        return on_path
    exts = sorted(globmod.glob(os.path.join(
        os.path.expanduser("~"), ".vscode", "extensions",
        "anthropic.claude-code-*", "resources", "native-binary", "claude.exe")))
    if exts:
        return exts[-1]  # newest version sorts last
    raise RuntimeError("claude CLI not found — set BRIDGE_CLAUDE_BIN")


HEALTH_FILE = os.path.join(STATE_DIR, "health.json")
CLAIMS_FILE = os.path.join(STATE_DIR, "claimed-threads.json")


def claimed_threads() -> dict:
    """Threads CLAIMED by external (terminal/VS Code) Claude sessions
    (2026-08-24 session-separation fix): the bridge must NOT spawn or resume a
    headless session for operator messages in a claimed thread — the claiming
    session's own listener answers there. Claim = add the thread root id to
    this JSON ({root_id: {"owner": label, ...}}); re-read every poll so claims
    take effect without a bridge restart."""
    try:
        with open(CLAIMS_FILE, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}



def write_health(claude_bin: str, last_ok: float, last_err: str, fails: int) -> None:
    """Functional-health beacon for stack-watchdog.ps1. Process liveness (the
    lock port) says nothing about turns actually working — 2026-08-23 both
    bridge personas held their locks for weeks while every turn died on a
    cached claude.exe path that VS Code's extension pruning had deleted."""
    try:
        tmp = HEALTH_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"ts": int(time.time()), "pid": os.getpid(),
                       "claude_bin": claude_bin,
                       "bin_exists": os.path.isfile(claude_bin),
                       "last_turn_ok_ts": int(last_ok),
                       "last_err": (last_err or "")[:400],
                       "consecutive_failures": fails}, fh, indent=1)
        os.replace(tmp, HEALTH_FILE)
    except OSError:
        pass


def telegram_alert(text: str) -> bool:
    """Out-of-band escalation via the sysadmin Telegram channel (lock 48293's
    bot). Used when the MM-facing turn machinery itself is broken — an MM post
    would land in the same channel the operator already can't get answers in."""
    envs = mm_lib.default_env_files()
    tok = mm_lib.read_env_key("SYSADMIN_TELEGRAM_BOT_TOKEN", envs)
    chat = mm_lib.read_env_key("SYSADMIN_TELEGRAM_CHAT_ID", envs)
    if not (tok and chat):
        return False
    try:
        import urllib.request as _ur
        req = _ur.Request(f"https://api.telegram.org/bot{tok}/sendMessage",
                          data=json.dumps({"chat_id": chat, "text": text}).encode(),
                          headers={"Content-Type": "application/json"})
        _ur.urlopen(req, timeout=15).read()
        return True
    except Exception:  # noqa: BLE001 - escalation must never take the bridge down with it
        return False


def load_state() -> dict:
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {"last_seen": int(time.time() * 1000), "threads": {}, "processed": []}


def save_state(state: dict) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=1)
    os.replace(tmp, STATE_FILE)


def post(message: str, thread_id: str | None = None) -> dict:
    body = {"channel_id": CHANNEL_ID, "message": message, "props": {"from_bridge": True}}
    if thread_id:
        body["root_id"] = thread_id
    return mmapi._api("POST", "/posts", body)


def post_chunked(message: str, thread_id: str) -> None:
    text = message.strip() or "(empty result)"
    while text:
        chunk, text = text[:MM_POST_LIMIT], text[MM_POST_LIMIT:]
        post(chunk, thread_id)


def patch_post(post_id: str, message: str) -> None:
    """Edit a post in place — used for the evolving mid-turn progress log (edits don't
    trigger notifications, so the operator isn't pinged for every step)."""
    mmapi._api("PUT", f"/posts/{post_id}/patch", {"message": message[:15800]})


def react(post_id: str, emoji: str) -> None:
    """Silent acknowledgment — a reaction instead of a noisy 'resuming…' post."""
    try:
        mmapi._api("POST", "/reactions", {"user_id": mmapi._me(), "post_id": post_id,
                                          "emoji_name": emoji})
    except Exception:  # noqa: BLE001
        pass


def unreact(post_id: str, emoji: str) -> None:
    try:
        mmapi._api("DELETE", f"/users/{mmapi._me()}/posts/{post_id}/reactions/{emoji}")
    except Exception:  # noqa: BLE001
        pass


def _short(text: str, n: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[:n] + "…"


def _clip(text: str, n: int) -> str:
    text = str(text)
    return text if len(text) <= n else text[:n] + "…"


def follow_matches(follow: dict, p: dict, me: str) -> bool:
    """True when Mattermost post `p` should wake `follow`'s session. Never wakes on: system
    posts, the bridge's own identity (`me`), bridge-tagged posts, posts Claude itself sent
    through the mattermost MCP (props.from_claude — loop guard even when that MCP falls back
    to the bot-pm token), or webhook posts (alerters would self-notify forever)."""
    if p.get("type"):
        return False
    props = p.get("props") or {}
    if props.get("from_bridge") or props.get("from_claude") or props.get("from_webhook"):
        return False
    if p.get("user_id", "") == me:
        return False
    if int(p.get("create_at", 0)) <= int(follow.get("last_seen", 0)):
        return False
    tid = follow.get("thread_id") or ""
    if tid and p.get("root_id") != tid and p.get("id") != tid:
        return False
    wake_on = [str(u).lower().lstrip("@") for u in follow.get("wake_on") or []]
    if wake_on and mmapi._username(p.get("user_id", "")).lower() not in wake_on:
        return False
    return True


def _progress_lines(ev: dict) -> list[str]:
    """Compact human-readable lines for a stream-json assistant event (narration + tool calls)."""
    if ev.get("type") != "assistant":
        return []
    lines = []
    for block in (ev.get("message") or {}).get("content") or []:
        btype = block.get("type")
        if btype == "text":
            t = (block.get("text") or "").strip()
            if t:
                lines.append(f"💬 {_short(t, 160)}")
        elif btype == "tool_use":
            name = block.get("name", "?")
            if name in ("TodoWrite",):  # bookkeeping noise
                continue
            inp = block.get("input") or {}
            detail = ""
            if name in ("Bash", "PowerShell"):
                detail = inp.get("description") or inp.get("command", "")
            elif name in ("Read", "Write", "Edit"):
                detail = os.path.basename(str(inp.get("file_path", "")))
            elif name in ("Grep", "Glob"):
                detail = inp.get("pattern", "")
            elif name in ("Task", "Agent"):
                detail = inp.get("description", "")
            elif name == "WebFetch":
                detail = inp.get("url", "")
            elif name == "WebSearch":
                detail = inp.get("query", "")
            elif name == "Skill":
                detail = inp.get("skill", "")
            elif name.startswith("mcp__"):
                name = name.split("__")[-1]
            lines.append(f"🔧 {name}" + (f" — {_short(detail, 90)}" if detail else ""))
    return lines


# ── turn execution ───────────────────────────────────────────────────────────
def write_mcp_config(thread_root: str) -> str:
    cfg_path = os.path.join(STATE_DIR, f"mcp-{thread_root}.json")
    cfg = {"mcpServers": {"approvals": {
        "type": "stdio",
        "command": sys.executable,
        "args": [os.path.join(_HERE, "approval_server.py")],
        "env": {
            "BRIDGE_MM_URL": mmapi.DEFAULT_URL,
            "BRIDGE_ENV_FILE": mmapi.DEFAULT_ENV_FILE,
            "BRIDGE_CHANNEL_ID": CHANNEL_ID,
            "BRIDGE_THREAD_ID": thread_root,
            "BRIDGE_OPERATORS": ",".join(sorted(OPERATORS)),
            "BRIDGE_APPROVAL_TIMEOUT": str(APPROVAL_TIMEOUT),
            "BRIDGE_QUESTION_TIMEOUT": str(QUESTION_TIMEOUT),
            "BRIDGE_APPROVALS_LOG": APPROVALS_LOG,
            "BRIDGE_ALLOW_SELF": "1" if ALLOW_SELF else "0",
            "BRIDGE_TOKEN_KEY": TOKEN_KEY,
        },
    }}}
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(cfg_path, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=1)
    return cfg_path


def kill_tree(proc: subprocess.Popen) -> None:
    """claude spawns child processes (MCP servers, shells) — kill the whole tree on Windows."""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                           capture_output=True, timeout=30, check=False)
        else:
            proc.kill()
    except Exception:  # noqa: BLE001
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass


def _run_worktree_script(script: str, args: list[str], timeout: int = 900) -> tuple[int, str]:
    """Run one of scripts/worktree/*.ps1 and return (exit_code, combined output).

    Never raises: worktree provisioning failing must produce a readable in-thread message,
    not a bridge traceback."""
    cmd = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
           os.path.join(WORKTREE_SCRIPTS, script)] + args
    try:
        p = subprocess.run(cmd, cwd=_REPO_ROOT, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
    except Exception as e:  # noqa: BLE001
        return 1, f"{script} could not be run: {e}"
    return p.returncode, ((p.stdout or "") + "\n" + (p.stderr or "")).strip()


def worktree_id(thread_root: str) -> str:
    """One worktree per THREAD, stable across resumes — the thread is the unit of work."""
    return "mm-" + thread_root[:8].lower()


def run_turn(claude_bin: str, thread_root: str, prompt: str, session_id: str | None,
             fork: bool = False, model: str = "", on_event=None, title: str = "",
             permission_mode: str = "", on_proc=None, cwd: str = "") -> dict:
    """Run one headless turn, streaming NDJSON events. `on_event` receives each event as it
    arrives (used for the mid-turn progress log); the final `result` event is returned as the
    turn's result dict (same shape as --output-format json)."""
    # Session display name = the thread's title, so the /resume picker reads like a task list
    # ("mm fix the gateway config…") instead of opaque ids.
    name = f"mm {title[:48]}" if title else f"mm-{thread_root[:8]}"
    cmd = [claude_bin, "-p", "--output-format", "stream-json", "--verbose",
           "--permission-prompt-tool", "mcp__approvals__permission_prompt",
           "--mcp-config", write_mcp_config(thread_root),
           "--append-system-prompt", SYSTEM_PROMPT,
           "-n", name]
    if SETTING_SOURCES:
        cmd += ["--setting-sources", SETTING_SOURCES]
    if permission_mode and permission_mode != "bypassPermissions":  # hard floor, belt+braces
        cmd += ["--permission-mode", permission_mode]
    if model:
        cmd += ["--model", model]
    if MAX_BUDGET_USD:
        cmd += ["--max-budget-usd", MAX_BUDGET_USD]
    if ALLOWED_TOOLS:
        cmd += ["--allowedTools", ALLOWED_TOOLS]
    if session_id:
        cmd += ["--resume", session_id]
        if fork:
            cmd += ["--fork-session"]

    t0 = time.time()
    proc = subprocess.Popen(cmd, cwd=(cwd or REPO), stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if on_proc is not None:  # register with the bridge so `!stop` can abort a lane-2 turn
        try:
            on_proc(proc)
        except Exception:  # noqa: BLE001
            pass
    killer = threading.Timer(TURN_TIMEOUT, lambda: kill_tree(proc))
    killer.daemon = True
    killer.start()
    stderr_chunks: list[bytes] = []
    drain = threading.Thread(target=lambda: stderr_chunks.append(proc.stderr.read() or b""),
                             daemon=True)
    drain.start()
    try:
        proc.stdin.write(prompt.encode("utf-8"))
        proc.stdin.close()
    except OSError:
        pass

    result = None
    try:
        for raw in proc.stdout:
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(ev, dict):
                continue
            if ev.get("type") == "result":
                result = ev
            elif on_event is not None:
                try:
                    on_event(ev)
                except Exception:  # noqa: BLE001 - progress rendering must never kill a turn
                    pass
        proc.wait(timeout=60)
    except Exception:  # noqa: BLE001
        kill_tree(proc)
    finally:
        killer.cancel()

    if isinstance(result, dict):
        return result
    if time.time() - t0 >= TURN_TIMEOUT - 2:
        return {"is_error": True, "result": f"turn timed out after {TURN_TIMEOUT}s and was "
                                            f"killed; reply in-thread to resume the session"}
    stderr_txt = (stderr_chunks[0] if stderr_chunks else b"").decode("utf-8", "replace").strip()
    return {"is_error": True,
            "result": f"claude exited {proc.returncode} without a result: {stderr_txt[-600:]}"}


# ── question parking (deterministic "the agent is waiting on the human") ─────
# approval_server.py's ask_user tool writes this marker while a question is open and removes it
# when answered/timed out. The bridge never INFERS a question from prose (ends-with-"?" is a
# heuristic, not determinism) — the marker's existence IS the signal.
def question_marker(thread_root: str) -> str:
    return os.path.join(STATE_DIR, f"question-{thread_root}.json")


def question_parked(thread_root: str) -> dict | None:
    """The parked-question marker, or None.

    Self-healing: a hard-killed approval server (taskkill, crash, reboot) can leave the marker
    behind, and a stale marker would hold the valve shut FOREVER — background traffic would
    never resume. So a marker older than the question timeout plus slack is treated as dead and
    removed. The valve must never be able to latch closed."""
    path = question_marker(thread_root)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            rec = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    asked_at = int(rec.get("asked_at") or 0)
    if asked_at and time.time() * 1000 - asked_at > (QUESTION_TIMEOUT + 300) * 1000:
        log(f"clearing stale question marker for thread {thread_root[:8]} "
            f"(older than {QUESTION_TIMEOUT + 300}s — approval server likely died)")
        audit({"event": "question_marker_stale", "thread": thread_root, "asked_at": asked_at})
        try:
            os.remove(path)
        except OSError:
            pass
        return None
    return rec


# ── post classification (decision-required vs progress) ──────────────────────
# Structural markers only, and ONLY as a fallback: the authoritative signal is props.ao_class,
# stamped by the producer. Prose-matching is a guess and is treated as such.
DECISION_RE = re.compile(
    r"(reply\s+`?\s*approve|`approve\s|approve\s*\|\s*modify\s*\|\s*abort|\bmodify\b\s*\|\s*\babort\b"
    r"|say\s+\*{0,2}merge it|merge gate|\bCONCERN\b|state\s*=\s*frozen|\bfrozen\b|⛔"
    r"|needs your attention|escalat)", re.IGNORECASE)


def classify_post(p: dict) -> tuple[str, str]:
    """→ (class, why). FAIL OPEN: anything unstamped and unrecognised is a DECISION.

    Under-waking means a MISSED GATE, which is the dangerous direction — a false wake is only
    noise. So ambiguity always resolves to 'decision'."""
    props = p.get("props") or {}
    stamped = str(props.get("ao_class") or "").strip().lower()
    if stamped in ("decision", "progress"):
        return stamped, "props.ao_class"
    msg = (p.get("message") or "")
    if DECISION_RE.search(msg):
        return "decision", "prose-match"
    # Recognised progress narration — the only case we dare call non-actionable without a stamp.
    if re.search(r"(opened\s+\[?`?effort|readiness|dispatch|plan approved|archived|delivered"
                 r"|agent-bridge online|✅|📋)", msg, re.IGNORECASE):
        return "progress", "prose-match"
    return "decision", "unclassified — failing open"


# ── two-lane queue ───────────────────────────────────────────────────────────
class _Item:
    """One unit of work. `kind` decides its lane; `post_id` links it to the Mattermost message
    that spawned it (for the ⏳/🚫 receipts and for 👎 cancellation)."""
    __slots__ = ("kind", "prompt", "post_id", "meta")

    def __init__(self, kind: str, prompt: str, post_id: str = "", meta: dict | None = None):
        self.kind, self.prompt, self.post_id, self.meta = kind, prompt, post_id, meta or {}


class _ThreadQueue:
    """Lane 1 (operator, ungated) + lane 2 (background, valved), one condition variable.

    Deliberately NOT a PriorityQueue: `!stop` must purge lane 2 *only*, and the staleness guard
    must re-validate lane 2 *as a collection* at release time. Both are trivial with two real
    queues and awkward with one priority queue.
    """

    def __init__(self) -> None:
        self.cv = threading.Condition()
        self.lane1: deque[_Item] = deque()
        self.lane2: deque[_Item] = deque()
        self.valve_open = True
        self.cancelled: set[str] = set()

    def put_user(self, item: _Item) -> None:
        with self.cv:
            self.lane1.append(item)
            if VALVE_ENABLED:
                self.valve_open = False  # lane-1 activity closes the valve
            self.cv.notify_all()

    def put_wake(self, item: _Item) -> None:
        with self.cv:
            self.lane2.append(item)
            self.cv.notify_all()

    def set_valve(self, is_open: bool) -> None:
        with self.cv:
            self.valve_open = bool(is_open) or not VALVE_ENABLED
            self.cv.notify_all()

    def purge_lane2(self) -> int:
        with self.cv:
            n = len(self.lane2)
            self.lane2.clear()
            return n

    def cancel(self, post_id: str) -> None:
        with self.cv:
            self.cancelled.add(post_id)

    def depth(self) -> tuple[int, int]:
        with self.cv:
            return len(self.lane1), len(self.lane2)

    def empty(self) -> bool:
        with self.cv:
            return not self.lane1 and not self.lane2

    def get_nowait(self) -> _Item:
        """Non-blocking introspection: lane 1 first, then lane 2, ignoring the valve."""
        with self.cv:
            if self.lane1:
                return self.lane1.popleft()
            if self.lane2:
                return self.lane2.popleft()
        raise IndexError("both lanes are empty")

    def pending_post_ids(self) -> list[str]:
        with self.cv:
            return [i.post_id for i in list(self.lane1) + list(self.lane2) if i.post_id]

    def _take_locked(self) -> tuple[_Item, list[_Item]] | None:
        """The admission POLICY, with self.cv held. None = nothing admissible right now.

        Kept separate from the blocking in get() so the policy is a pure, thread-free function
        that tests can drive directly."""
        if self.lane1:                       # lane 1 is ungated and always wins
            return self.lane1.popleft(), []
        if self.lane2 and (self.valve_open or not VALVE_ENABLED):
            item = self.lane2.popleft()
            extras: list[_Item] = []
            if CATCHUP_COALESCE:             # everything held rides out as ONE catch-up turn
                while self.lane2:
                    extras.append(self.lane2.popleft())
            return item, extras
        return None

    def try_get(self) -> tuple[_Item, list[_Item]] | None:
        with self.cv:
            return self._take_locked()

    def get(self) -> tuple[_Item, list[_Item]]:
        """Block until an item is admissible (see _take_locked for the policy)."""
        with self.cv:
            while True:
                got = self._take_locked()
                if got is not None:
                    return got
                self.cv.wait(timeout=5)


# ── per-thread worker ────────────────────────────────────────────────────────
class Bridge:
    def __init__(self) -> None:
        self.claude_bin = find_claude_bin()
        self.turn_fail_count = 0
        self.last_turn_ok_ts = 0.0
        self.last_turn_err = ""
        self._last_tg_alert = 0.0
        self._last_health_write = 0.0
        self.state = load_state()
        self.state_lock = threading.Lock()
        self.queues: dict[str, _ThreadQueue] = {}
        self.running: set[str] = set()          # thread_roots with a turn in flight
        self.running_lock = threading.Lock()
        self.sem = threading.Semaphore(MAX_CONCURRENT)
        self.procs: dict[str, subprocess.Popen] = {}   # in-flight turn, for !stop abort
        self.proc_kind: dict[str, str] = {}            # "user" | "wake" — never abort a user turn
        self.proc_post: dict[str, str] = {}            # trigger post of the in-flight turn (👎)
        self.cancel_inflight: set[str] = set()         # threads whose running turn was 👎'd
        self.proc_lock = threading.Lock()
        self.digest: dict[str, list[str]] = {}         # thread → buffered PROGRESS lines
        self.processed: deque[str] = deque(self.state.get("processed", []), maxlen=500)
        self.first_poll = True
        self.state.setdefault("follows", {})   # fid → follow record (see follow_matches)
        self._team: str | None = None          # cached team name for permalinks

    # -- worker ---------------------------------------------------------------
    def ensure_worker(self, thread_root: str) -> None:
        if thread_root in self.queues:
            return
        self.queues[thread_root] = _ThreadQueue()
        t = threading.Thread(target=self.worker, args=(thread_root,), daemon=True,
                             name=f"worker-{thread_root[:8]}")
        t.start()

    def _park_watcher(self, thread_root: str, release_once, stop_evt: threading.Event) -> None:
        """Release this turn's concurrency slot the moment it parks on a question.

        A parked `ask_user` is idle-waiting, not computing — with MAX_CONCURRENT=2 a 30-minute
        park would otherwise hold HALF the bridge's total turn capacity and starve every other
        thread. The slot is released exactly once (whichever comes first: park, or turn end)."""
        while not stop_evt.wait(3):
            if question_parked(thread_root):
                release_once("parked on a question")
                return

    def ensure_claude_bin(self) -> None:
        """Self-heal the cached claude path. VS Code prunes old extension dirs
        on auto-update, so a path resolved at startup can vanish mid-life
        (2026-08-23: both personas dead for weeks exactly this way). One cheap
        stat per call; raises only when NOTHING resolves any more."""
        if os.path.isfile(self.claude_bin):
            return
        old = self.claude_bin
        self.claude_bin = find_claude_bin()
        log(f"claude binary healed: {old} -> {self.claude_bin} (old path pruned)")
        audit({"event": "claude_bin_healed", "old": old, "new": self.claude_bin})

    def note_turn(self, ok: bool, err: str = "", infra: bool = False) -> None:
        """Track turn outcomes for the health beacon; escalate persistent or
        infrastructure failures OUT-OF-BAND (Telegram), because when turns are
        broken the MM channel itself is the thing the operator can't use."""
        if ok:
            self.turn_fail_count = 0
            self.last_turn_ok_ts = time.time()
            self.last_turn_err = ""
        else:
            self.turn_fail_count += 1
            self.last_turn_err = err
            if (infra or self.turn_fail_count >= 2) and time.time() - self._last_tg_alert > 1800:
                self._last_tg_alert = time.time()
                persona = "sysadmin" if os.environ.get("BRIDGE_LOCK_PORT") == "48292" else "claude-sessions"
                telegram_alert(f"[ai-stack] {persona} bridge: {self.turn_fail_count} consecutive "
                               f"failed turn(s){' (infra: spawn failure)' if infra else ''}; "
                               f"last error: {err[:200]}")
        write_health(self.claude_bin, self.last_turn_ok_ts, self.last_turn_err, self.turn_fail_count)

    def worker(self, thread_root: str) -> None:
        q = self.queues[thread_root]
        while True:
            item, extras = q.get()

            # 👎 tombstone — skip without running, flip the ⏳ to 🚫.
            if item.post_id and item.post_id in q.cancelled:
                log(f"skipping cancelled item in thread {thread_root[:8]} ({item.kind})")
                audit({"event": "turn_cancelled", "thread": thread_root, "post": item.post_id,
                       "kind": item.kind})
                unreact(item.post_id, "hourglass_flowing_sand")
                react(item.post_id, "no_entry_sign")
                continue

            prompt = item.prompt
            if item.kind == "wake":
                prompt = self._compose_wake_prompt(thread_root, item, extras)
                if prompt is None:  # every held item went stale — nothing worth a turn
                    continue

            self.sem.acquire()
            released = {"done": False}
            sem_lock = threading.Lock()

            def release_once(why: str = "turn ended") -> None:
                with sem_lock:
                    if released["done"]:
                        return
                    released["done"] = True
                try:
                    self.sem.release()
                    log(f"thread {thread_root[:8]}: concurrency slot released ({why})")
                except ValueError:  # noqa: PERF203 - over-release guard
                    pass

            stop_evt = threading.Event()
            watcher = threading.Thread(target=self._park_watcher,
                                       args=(thread_root, release_once, stop_evt),
                                       daemon=True, name=f"park-{thread_root[:8]}")
            watcher.start()
            with self.running_lock:
                self.running.add(thread_root)
            try:
                self.execute(thread_root, prompt, item.post_id, kind=item.kind)
                self.note_turn(ok=True)
            except Exception as e:  # noqa: BLE001 - a broken turn must not kill the worker
                log(f"worker {thread_root[:8]} error: {e}")
                self.note_turn(ok=False, err=str(e),
                               infra=isinstance(e, (FileNotFoundError, RuntimeError))
                               or "WinError 2" in str(e))
                try:
                    post(f"❌ Bridge error running this turn: `{e}`", thread_root)
                except Exception:  # noqa: BLE001
                    pass
            finally:
                stop_evt.set()
                release_once()
                with self.running_lock:
                    self.running.discard(thread_root)
                with self.proc_lock:
                    self.procs.pop(thread_root, None)
                    self.proc_kind.pop(thread_root, None)
                    self.proc_post.pop(thread_root, None)
                # THE CONTROL LAW: the valve reopens only when the agent is not waiting on the
                # human and no operator work is left. A parked question keeps it shut so no
                # background wake can mutate the session between a question and its answer.
                pending_user, _ = q.depth()
                if question_parked(thread_root):
                    q.set_valve(False)
                elif pending_user == 0:
                    q.set_valve(True)

    def _full_stop(self, thread_root: str, pid: str) -> None:
        """Operator full stop (`!stop`): purge lane 2, keep EVERY lane-1 item, and abort the
        in-flight turn only if it is a lane-2 turn. The operator's own work is never killed."""
        self.ensure_worker(thread_root)
        q = self.queues[thread_root]
        purged = q.purge_lane2()
        kept, _ = q.depth()
        dropped = len(self.digest.pop(thread_root, []))
        aborted = False
        with self.proc_lock:
            proc, kind = self.procs.get(thread_root), self.proc_kind.get(thread_root, "")
        if proc is not None and kind == "wake":
            kill_tree(proc)
            aborted = True
        if not question_parked(thread_root) and kept == 0:
            q.set_valve(True)  # backlog is gone; let future background traffic flow again
        bits = [f"purged **{purged}** queued background wake(s)"]
        if dropped:
            bits.append(f"dropped **{dropped}** buffered progress line(s)")
        bits.append(f"kept **{kept}** of your message(s)")
        bits.append("aborted the running background turn" if aborted
                    else ("left your in-flight turn running" if proc is not None
                          else "nothing was running"))
        # A full stop empties the QUEUE; it does not unsubscribe. Saying so here is the difference
        # between "I stopped it" and the session being auto-woken again a minute later.
        with self.state_lock:
            live = [fid for fid, f in self.state.get("follows", {}).items()
                    if f.get("bridge_thread") == thread_root]
        if live:
            bits.append(f"⚠️ **{len(live)}** follow(s) still live ({', '.join('`' + x + '`' for x in sorted(live))})"
                        f" — this session can still be auto-woken; say `close` to dispose it")
        log(f"full stop in thread {thread_root[:8]}: purged={purged} kept={kept} aborted={aborted}")
        audit({"event": "full_stop", "thread": thread_root, "post": pid, "purged": purged,
               "kept": kept, "aborted": aborted})
        react(pid, "octagonal_sign")
        try:
            post("🛑 **Full stop** — " + " · ".join(bits) + ".", thread_root)
        except Exception:  # noqa: BLE001
            pass

    def _compose_wake_prompt(self, thread_root: str, item: _Item,
                             extras: list[_Item]) -> str | None:
        """Build ONE catch-up turn from every held lane-2 item, with the staleness guard applied.

        Deferral MANUFACTURES staleness: by construction a held wake is older when it is finally
        released, so a "plan approved" held through a long operator conversation may refer to an
        effort that was aborted during it. Validation therefore happens HERE, at release time —
        never at enqueue time, because the world moved while the item waited.
        """
        held = [item] + [e for e in extras if not (e.post_id and e.post_id in
                                                   self.queues[thread_root].cancelled)]
        now_ms = int(time.time() * 1000)
        # FAST PATH — nothing was actually held. One fresh wake, no coalescing, no buffered
        # digest: deliver it verbatim, exactly as the pre-change bridge did. The catch-up and
        # staleness framing is only honest when something really did wait.
        lone_age = max(0, (now_ms - int(item.meta.get("created_at") or now_ms)) // 60000)
        if (len(held) == 1 and not self.digest.get(thread_root)
                and lone_age <= WAKE_FRESH_MIN):
            return item.prompt
        terminal_re = re.compile(r"\b(aborted|archived|cancell?ed|closed|merged|abandoned|"
                                 r"superseded|resolved)\b", re.IGNORECASE)
        effort_re = re.compile(r"effort-[A-Za-z0-9_-]+")

        # An effort named with a terminal state in ANY held post is no longer blocking — every
        # earlier decision post about it is downgraded rather than acted on.
        settled: set[str] = set()
        for h in held:
            body = h.meta.get("body", "")
            if terminal_re.search(body):
                settled.update(effort_re.findall(body))

        live, stale, oldest = [], [], 0
        for h in held:
            body = h.meta.get("body", "")
            age_min = max(0, (now_ms - int(h.meta.get("created_at") or now_ms)) // 60000)
            oldest = max(oldest, age_min)
            efforts = set(effort_re.findall(body))
            superseded = bool(efforts & settled) and not terminal_re.search(body)
            tag = f"[posted {age_min} min ago"
            if superseded:
                tag += " · ⚠️ SUPERSEDED — that effort reached a terminal state in a later post"
            tag += "]"
            (stale if superseded else live).append(f"{tag}\n{h.prompt}")

        if not live and not stale:
            return None
        if not live:  # everything held went stale — record it, don't burn a turn on dead state
            log(f"thread {thread_root[:8]}: all {len(stale)} held wake(s) went stale — no turn")
            audit({"event": "wake_all_stale", "thread": thread_root, "count": len(stale)})
            return None

        digest = self.digest.pop(thread_root, [])
        waited = oldest > WAKE_FRESH_MIN  # did anything ACTUALLY sit behind a closed valve?
        head = (f"📡 **Catch-up** — {len(held)} background update(s) held while you were with "
                f"the operator, delivered as one turn."
                if waited else
                f"📡 **{len(held)} background updates** — delivered together.")
        parts = [head, "\n\n".join(live)]
        if stale:
            parts.append("⚠️ **Also held, but now stale** (do NOT act on these; they are context "
                         "only):\n\n" + "\n\n".join(stale))
        if digest:
            parts.append(f"📝 **Progress since the last wake ({len(digest)} post(s))** — FYI, "
                         f"nothing blocking:\n" + "\n".join(f"- {d}" for d in digest[-40:]))
        # Only tell the session to distrust what it is reading when something genuinely waited.
        # Asking it to re-verify LIVE updates makes it hedge and burn tool calls on current state.
        if waited or stale:
            parts.append("Some of the above waited while you were with the operator — check the "
                         "stated ages and confirm any referenced effort is still in a blocking "
                         "state before acting on it.")
        return "\n\n".join(parts)

    def execute(self, thread_root: str, prompt: str, trigger_post: str = "",
                kind: str = "user") -> None:
        with self.state_lock:
            meta = dict(self.state["threads"].get(thread_root, {}))
        session_id = meta.get("session_id")

        thread_model = meta.get("model", "")
        if thread_model.lower() in ("default", "reset"):
            thread_model = ""  # stored by an old `model: default` — never a real CLI alias
        thread_mode = meta.get("mode", "")
        thread_worktree = bool(meta.get("worktree_enabled", WORKTREE_DEFAULT))
        changed = []
        while True:  # consume leading directives in any order: `model: …`, `mode: …`
            m = MODEL_DIRECTIVE_RE.match(prompt)
            if m:
                requested = m.group(1)
                prompt = m.group(2).strip()
                # `model: default` (or `reset`) DROPS the thread's pin and returns it to the
                # bridge default — it is not an alias the CLI knows, so it must never be
                # forwarded as `--model default`.
                thread_model = "" if requested.lower() in ("default", "reset") else requested
                changed.append(f"model → `{thread_model or MODEL or 'CLI default'}`")
                continue
            m = MODE_DIRECTIVE_RE.match(prompt)
            if m:
                requested = m.group(1)
                prompt = m.group(2).strip()
                if requested.lower() in ("bypasspermissions", "bypass"):
                    post("🚫 `bypassPermissions` is not available through the bridge — that's "
                         "the hard floor. Valid: auto, acceptEdits, manual, dontAsk, plan.",
                         thread_root)
                    continue
                resolved = PERMISSION_MODES.get(requested.lower())
                if resolved is None:
                    post(f"⚠️ Unknown approval mode `{requested}` — valid: auto, acceptEdits, "
                         f"manual, dontAsk, plan. Keeping "
                         f"`{thread_mode or PERMISSION_MODE}`.", thread_root)
                    continue
                thread_mode = resolved
                changed.append(f"approvals → `{resolved}`")
                continue
            m = WORKTREE_DIRECTIVE_RE.match(prompt)
            if m:
                requested = m.group(1).lower()
                prompt = m.group(2).strip()
                if requested in ("on", "1", "true", "yes"):
                    thread_worktree = True
                elif requested in ("off", "0", "false", "no"):
                    thread_worktree = False
                else:
                    post(f"⚠️ `worktree: {requested}` is not valid — use `on` or `off`. Keeping "
                         f"`{'on' if thread_worktree else 'off'}`.", thread_root)
                    continue
                changed.append(f"worktree → `{'on' if thread_worktree else 'off'}`")
                continue
            break
        if changed:
            with self.state_lock:
                entry = self.state["threads"].setdefault(thread_root, {})
                entry["model"] = thread_model  # "" = unpinned (a `model: default` reset)
                if thread_mode:
                    entry["mode"] = thread_mode
                entry["worktree_enabled"] = thread_worktree
                save_state(self.state)
        if not prompt:  # directive-only (or directive-error) message: no turn to run
            if changed:
                post("🎛️ " + " · ".join(changed) + " — applies from the next message.",
                     thread_root)
            return
        mode_label = thread_mode or PERMISSION_MODE

        # Where this turn runs. Resolved BEFORE the header so the operator is told the truth
        # about the working directory, and fail-closed: if an opted-in thread cannot get its
        # worktree, the turn does not run in the shared checkout pretending to be isolated.
        run_cwd = REPO
        if thread_worktree:
            run_cwd, wt_err = self.ensure_worktree(thread_root)
            if not run_cwd:
                post("🚫 **Could not provision this thread's worktree** — not running the turn "
                     "in the shared checkout, because that is the collision `worktree: on` "
                     "exists to prevent.\n```\n" + wt_err + "\n```\nFix it, or reply "
                     "`worktree: off` to run in the main checkout deliberately.", thread_root)
                return

        fork = False
        handoff = None
        if not session_id:
            handoff = HANDOFF_RE.match(prompt)
        model_label = thread_model or MODEL or "default"
        # Transitions (new session, handoff) get an informative post; ordinary continuations
        # get a silent ⏳ reaction on the operator's message instead — no "resuming…" noise.
        header = ""
        if handoff:
            verb, session_id = handoff.group(1).lower(), handoff.group(2).lower()
            fork = verb == "fork"
            prompt = handoff.group(3).strip() or (
                "Summarize where this session left off and what, if anything, is still in flight.")
            header = (f"🔗 Attaching this thread to session `{session_id[:8]}`"
                      + (" as a **forked copy** — the original session is untouched."
                         if fork else " — continuing it from here.")
                      + f" Model `{model_label}`, approvals `{mode_label}`.")
        elif not session_id:
            header = (f"🧵 Starting a **new Claude session** for this thread — model "
                      f"`{model_label}`, approvals `{mode_label}`, working dir `{run_cwd}`"
                      + (f" (its own worktree, branch `work/{worktree_id(thread_root)}`)"
                         if run_cwd != REPO else "") + ". "
                      + ("Routine actions run automatically; anything the safety classifier "
                         "flags pauses here for your approve/deny." if mode_label == "auto"
                         else "Gated actions pause here for your approve/deny."))
        if trigger_post:
            react(trigger_post, "hourglass_flowing_sand")

        # Live progress: an activity-log post, edited in place (edits don't notify). Created
        # lazily on the first TOOL action — a trivial reply produces no extra post at all.
        progress = {"lines": [], "last_edit": 0.0, "post_id": None, "header": header}
        if header:
            p = post(header, thread_root)
            progress["post_id"] = p.get("id") if isinstance(p, dict) else None

        def render_progress(suffix: str = "") -> str:
            shown = progress["lines"][-12:]
            elided = len(progress["lines"]) - len(shown)
            parts = [progress["header"]] if progress["header"] else []
            if elided > 0:
                parts.append(f"(… {elided} earlier steps)")
            if shown:
                parts.append("\n".join(shown))
            if suffix:
                parts.append(suffix)
            return "\n\n".join(parts)

        def on_event(ev: dict) -> None:
            new_lines = [ln for ln in _progress_lines(ev)
                         if not progress["lines"] or ln != progress["lines"][-1]]
            if not new_lines:
                return
            progress["lines"].extend(new_lines)
            now = time.time()
            if progress["post_id"] is None:
                if not any(ln.startswith("🔧") for ln in progress["lines"]):
                    return  # narration only so far — not worth a post
                try:
                    p = post(render_progress(), thread_root)
                    progress["post_id"] = p.get("id") if isinstance(p, dict) else None
                    progress["last_edit"] = now
                except Exception:  # noqa: BLE001
                    pass
                return
            if now - progress["last_edit"] >= 8:
                progress["last_edit"] = now
                try:
                    patch_post(progress["post_id"], render_progress())
                except Exception:  # noqa: BLE001
                    pass

        # First message in a thread becomes its durable human label — shown in state.json and
        # as the session's name in the /resume picker.
        with self.state_lock:
            entry = self.state["threads"].setdefault(thread_root, {})
            if not entry.get("title") and prompt:
                entry["title"] = _short(prompt.splitlines()[0] if prompt.splitlines() else prompt, 80)
                save_state(self.state)
            title = entry.get("title", "")

        audit({"event": "turn_started", "thread": thread_root, "resume": bool(session_id),
               "handoff": bool(handoff), "fork": fork, "model": model_label,
               "mode": mode_label, "prompt_preview": prompt[:300]})
        t0 = time.time()
        def register_proc(proc: subprocess.Popen) -> None:
            with self.proc_lock:
                self.procs[thread_root] = proc
                self.proc_kind[thread_root] = kind
                self.proc_post[thread_root] = trigger_post

        self.ensure_claude_bin()  # heal a pruned path BEFORE spawning, never after a failed turn
        resp = run_turn(self.claude_bin, thread_root, prompt, session_id, fork=fork,
                        model=thread_model or MODEL, on_event=on_event, title=title,
                        permission_mode=mode_label, on_proc=register_proc, cwd=run_cwd)
        dur = int(time.time() - t0)
        if progress["post_id"] and progress["lines"]:
            try:  # final flush so the log is complete, and mark the turn done
                patch_post(progress["post_id"], render_progress("✔ turn complete — result below."))
            except Exception:  # noqa: BLE001
                pass
        # A 👎 landing after the turn already started aborts it (see poll_cancellations). The
        # kill makes run_turn return a generic error — translate it into an honest receipt so
        # the operator sees "cancelled", not "turn failed".
        with self.proc_lock:
            was_cancelled = thread_root in self.cancel_inflight
            self.cancel_inflight.discard(thread_root)
        if was_cancelled:
            if trigger_post:
                unreact(trigger_post, "hourglass_flowing_sand")
                react(trigger_post, "no_entry_sign")
            audit({"event": "turn_cancelled_inflight", "thread": thread_root,
                   "post": trigger_post, "seconds": dur})
            try:
                post(f"🚫 **Cancelled** — you 👎'd this message, so the running turn was stopped "
                     f"after {dur}s. Nothing further was done.", thread_root)
            except Exception:  # noqa: BLE001
                pass
            return
        if trigger_post:  # flip the ⏳ ack to the outcome
            unreact(trigger_post, "hourglass_flowing_sand")
            react(trigger_post, "x" if resp.get("is_error") else "white_check_mark")

        new_sid = resp.get("session_id")
        if new_sid:
            with self.state_lock:
                entry = self.state["threads"].setdefault(thread_root, {})
                entry["session_id"] = new_sid
                entry["updated"] = int(time.time() * 1000)
                save_state(self.state)

        result_text = str(resp.get("result") or "").strip()
        cost = resp.get("total_cost_usd")
        denials = resp.get("permission_denials") or []
        # the [model:…] suffix reports what ACTUALLY ran (from the turn's usage), so the operator
        # can track behavior/experience per model across threads
        models = ",".join(re.sub(r"-\d{8}$", "", m) for m in (resp.get("modelUsage") or {}))
        footer_bits = [f"`session {str(new_sid or session_id or '?')[:8]}`", f"{dur}s"]
        if isinstance(cost, (int, float)):
            footer_bits.append(f"${cost:.2f}")
        if denials:
            footer_bits.append(f"{len(denials)} denied tool call(s)")
        footer_bits.append(f"[model:{models or 'unknown'}]")
        footer = " · ".join(footer_bits)

        # A classifier denial never reaches the approval relay: in `auto` mode the classifier
        # returns allow / ask / DENY itself, and only `ask` calls the permission-prompt tool.
        # So an in-thread `approve` is powerless against one — which "N denied tool call(s)"
        # alone doesn't tell the operator (2026-08-28: a whole deploy stalled on exactly this).
        # Auto mode only: under manual/acceptEdits a denial IS the operator's own verdict.
        deny_note = ""
        if denials and mode_label == "auto":
            names = sorted({str(d.get("tool_name") or d.get("tool") or "")
                            for d in denials if isinstance(d, dict)} - {""})
            what = f" ({', '.join(names)})" if names else ""
            deny_note = (
                f"\n\n🚫 **{len(denials)} tool call(s) blocked by the auto-mode classifier**"
                f"{what} — that gate denies outright, so it never reached this thread's "
                f"approve/deny relay and replying `approve` cannot lift it. To let the session "
                f"run it: add a permission rule to `.claude/settings.local.json` — the same "
                f"allow-list your interactive sessions use — then reply `continue`. "
                f"(Setting sources: `{SETTING_SOURCES}`.)")

        if resp.get("is_error"):
            subtype = str(resp.get("subtype") or "")
            reason = result_text or "(the CLI returned no error text)"
            hint = ""
            if "budget" in subtype.lower() or (isinstance(cost, (int, float))
                                               and cost >= float(MAX_BUDGET_USD or "0")):
                hint = (f"\n💡 This turn hit the per-turn budget cap (${MAX_BUDGET_USD}). Reply "
                        f"`continue` to pick up where it left off, switch this thread to a "
                        f"cheaper model (`model: haiku`), or raise `BRIDGE_MAX_BUDGET_USD`.")
            elif "max_turns" in subtype.lower():
                hint = "\n💡 The turn hit its internal iteration limit — reply `continue` to keep going."
            elif "timed out" in reason:
                hint = "\n💡 Reply in this thread to resume the session where it left off."
            label = f"❌ **Turn failed** — `{subtype or 'error'}`"
            post_chunked(f"{label}\n{reason}{hint}{deny_note}\n\n{footer}", thread_root)
        else:
            post_chunked(f"{result_text}{deny_note}\n\n---\n{footer}", thread_root)
        audit({"event": "turn_completed", "thread": thread_root, "session": new_sid,
               "seconds": dur, "cost_usd": cost, "is_error": bool(resp.get("is_error")),
               "denials": len(denials)})

    # -- follows: auto-wake on replies in other Mattermost threads -------------
    def _team_name(self) -> str:
        if self._team is None:
            try:
                teams = mmapi._api("GET", "/users/me/teams")
                if isinstance(teams, list) and teams:
                    self._team = teams[0].get("name") or ""
            except Exception:  # noqa: BLE001 - stay None → retried on the next call
                pass
        return self._team or ""

    def _follow_label(self, f: dict) -> str:
        label = f"#{f.get('channel_label') or str(f.get('channel_id', '?'))[:8]}"
        tid = f.get("thread_id")
        if tid:
            team = self._team_name()
            label += (f" · [thread]({mmapi.DEFAULT_URL}/{team}/pl/{tid})" if team
                      else f" · thread `{tid[:8]}`")
        return label

    def follows_listing(self) -> str:
        with self.state_lock:
            follows = {fid: dict(f) for fid, f in self.state.get("follows", {}).items()}
            threads = {k: dict(v) for k, v in self.state.get("threads", {}).items()}
        if not follows:
            return "📡 No active follows."
        now = time.time() * 1000
        lines = [f"📡 **Active follows ({len(follows)})**"]
        for fid, f in sorted(follows.items(), key=lambda kv: kv[1].get("created", 0)):
            title = (threads.get(f.get("bridge_thread", "")) or {}).get(
                "title", str(f.get("bridge_thread", "?"))[:8])
            who = ", ".join("@" + u for u in f.get("wake_on") or []) or "anyone"
            hrs = max(0.0, (f.get("expires", 0) - now) / 3600000)
            lines.append(f"- `{fid}` → {self._follow_label(f)} · wakes on {who} · "
                         f"{f.get('wakes', 0)}/{f.get('max_wakes', '?')} wakes · "
                         f"expires in {hrs:.0f}h · session “{title}”"
                         + (f" · {f['note']}" if f.get("note") else ""))
        lines.append("`unfollow <id>` stops one; `unfollow all` (inside a session's thread) "
                     "stops that session's; `close` in a session's thread disposes everything "
                     "it left running.")
        return "\n".join(lines)

    def _renew_follows(self, now_ms: int, *, bridge_thread: str | None = None,
                       channel_id: str | None = None) -> list[str]:
        """Slide a follow's idle window forward on HUMAN engagement (operator 2026-07-16: "a more
        realistic work day that resets its timer based on human engagement"). A follow lives as long
        as the operator keeps engaging — with the SESSION (messaging bot-claude in its bridge_thread)
        or with the followed CHANNEL (posting there directly) — and lapses only after a full idle
        window ('a work day') of real silence. Only ever pushes `expires` FORWARD; never shortens.
        Returns the fids actually renewed (for logging/tests)."""
        renewed: list[str] = []
        with self.state_lock:
            threads = self.state["threads"]
            for fid, f in self.state.get("follows", {}).items():
                if bridge_thread is not None and f.get("bridge_thread") != bridge_thread:
                    continue
                # A closed session's follows never renew — otherwise engaging the TOPIC (posting
                # in the followed channel) keeps a disposed session's window sliding forward.
                # Read inline: state_lock is not reentrant, so thread_closed() would deadlock.
                if (threads.get(f.get("bridge_thread") or "") or {}).get("closed"):
                    continue
                if channel_id is not None and f.get("channel_id") != channel_id:
                    continue
                idle = int(f.get("idle_ms") or (int(f.get("expires", 0)) - int(f.get("created", 0))))
                if idle <= 0:
                    continue
                if now_ms + idle > int(f.get("expires", 0)):
                    f["expires"] = now_ms + idle
                    renewed.append(fid)
            if renewed:
                save_state(self.state)
        return renewed

    def _drop_follow(self, fid: str, f: dict, why: str, *, notify: bool = True) -> None:
        with self.state_lock:
            self.state.get("follows", {}).pop(fid, None)
            save_state(self.state)
        audit({"event": "follow_dropped", "follow": fid, "why": why})
        if notify and f.get("bridge_thread"):
            try:
                post(f"📡 Follow `{fid}` ({self._follow_label(f)}) {why}.", f["bridge_thread"])
            except Exception:  # noqa: BLE001
                pass

    # -- session closure: the OFF switch for a session's lingering setups ------
    def thread_closed(self, thread_root: str) -> int:
        """Timestamp the operator closed this session, or 0 when it is live."""
        with self.state_lock:
            return int((self.state["threads"].get(thread_root) or {}).get("closed") or 0)

    def _reopen_thread(self, thread_root: str) -> bool:
        """A human messaging a closed session reopens it. Returns True if it WAS closed."""
        with self.state_lock:
            entry = self.state["threads"].get(thread_root)
            if not entry or not entry.get("closed"):
                return False
            entry.pop("closed", None)
            save_state(self.state)
        log(f"thread {thread_root[:8]}: reopened by an operator message")
        audit({"event": "session_reopened", "thread": thread_root})
        return True

    def ensure_worktree(self, thread_root: str) -> tuple[str, str]:
        """Return (path, error) for this thread's worktree, provisioning it on first use.

        ("", error) means provisioning FAILED and the caller must NOT fall back to the shared
        checkout: running there is precisely the collision this feature exists to remove, and
        a silent fallback would look like isolation while providing none."""
        wid = worktree_id(thread_root)
        with self.state_lock:
            path = (self.state["threads"].get(thread_root) or {}).get("worktree", "")
        if path and os.path.isdir(path):
            # Cheap freshness pass: the operator may have edited .env since this was created.
            _run_worktree_script("sync-worktree-env.ps1", ["-Id", wid, "-Quiet"], timeout=120)
            return path, ""

        rc, out = _run_worktree_script("new-worktree.ps1", [
            "-Id", wid, "-OwnerKind", "bridge", "-OwnerRef", thread_root,
            "-Thread", thread_root, "-Reuse", "-Json"])
        path = ""
        if rc == 0:
            # -Json prints one compact object; take the last line that parses as one.
            for line in reversed([ln for ln in out.splitlines() if ln.strip()]):
                try:
                    path = (json.loads(line) or {}).get("path", "")
                except Exception:  # noqa: BLE001
                    continue
                if path:
                    break
        if not path or not os.path.isdir(path):
            return "", (out or "no output")[-900:]
        with self.state_lock:
            self.state["threads"].setdefault(thread_root, {})["worktree"] = path
            save_state(self.state)
        log(f"thread {thread_root[:8]}: worktree ready at {path}")
        audit({"event": "worktree_created", "thread": thread_root, "path": path, "id": wid})
        return path, ""

    def _retire_worktree(self, thread_root: str) -> str:
        """Close-time cleanup. Removes ONLY a worktree that holds nothing unlanded — the
        script refuses (exit 2) otherwise and we report that instead of forcing, because it
        may hold the only copy of someone's work."""
        with self.state_lock:
            path = (self.state["threads"].get(thread_root) or {}).get("worktree", "")
        if not path:
            return ""
        rc, out = _run_worktree_script("remove-worktree.ps1", ["-Id", worktree_id(thread_root)])
        if rc == 0:
            with self.state_lock:
                entry = self.state["threads"].setdefault(thread_root, {})
                entry.pop("worktree", None)
                save_state(self.state)
            return "removed its worktree"
        if rc == 2:
            return (f"**kept** its worktree `{path}` — it still holds uncommitted or unmerged "
                    f"work (land it, or remove it with `-Force`)")
        return f"could not remove its worktree (`{(out or '')[:150]}`)"

    def _close_session(self, thread_root: str, pid: str) -> None:
        """`close` / `end session` — dispose everything this session left running.

        Order matters: mark CLOSED first, so a follow poll racing this call can neither wake the
        session nor register a new follow for it while we tear down."""
        with self.state_lock:
            entry = self.state["threads"].setdefault(thread_root, {})
            entry["closed"] = int(time.time() * 1000)
            save_state(self.state)
            follows = {fid: dict(f) for fid, f in self.state.get("follows", {}).items()
                       if f.get("bridge_thread") == thread_root}
        # 1. Follows — the actual cause of zombie re-engagement. One summary post, not N.
        for fid, f in follows.items():
            self._drop_follow(fid, f, "dropped: the session was closed", notify=False)
        # 2. Queued background wakes + the buffered progress digest.
        self.ensure_worker(thread_root)
        q = self.queues[thread_root]
        purged = q.purge_lane2()
        kept, _ = q.depth()
        dropped_digest = len(self.digest.pop(thread_root, []))
        # 3. A parked question would otherwise hold this session's valve shut forever.
        unparked = False
        if question_parked(thread_root):
            try:
                os.remove(question_marker(thread_root))
                unparked = True
            except OSError:
                pass
        # 4. A background turn is work nobody asked for in a session being disposed — abort it.
        #    An OPERATOR turn is their own work and is never killed (same law as `!stop`).
        aborted = False
        with self.proc_lock:
            proc, kind = self.procs.get(thread_root), self.proc_kind.get(thread_root, "")
        if proc is not None and kind == "wake":
            kill_tree(proc)
            aborted = True
        if kept == 0:
            q.set_valve(True)
        bits = [f"dropped **{len(follows)}** follow(s)"
                + (" (" + ", ".join(f"`{x}`" for x in sorted(follows)) + ")" if follows else ""),
                f"purged **{purged}** queued background wake(s)"]
        if dropped_digest:
            bits.append(f"dropped **{dropped_digest}** buffered progress line(s)")
        if unparked:
            bits.append("cleared a parked question (that turn will time out)")
        if aborted:
            bits.append("aborted the running background turn")
        elif proc is not None:
            bits.append("left **your** in-flight turn running")
        if kept:
            bits.append(f"kept **{kept}** of your queued message(s) — they will still run")
        # 5. The thread's worktree, if it had one. Removed only when it holds nothing unlanded.
        wt_note = self._retire_worktree(thread_root)
        if wt_note:
            bits.append(wt_note)
        log(f"session closed in thread {thread_root[:8]}: follows={len(follows)} "
            f"purged={purged} kept={kept} aborted={aborted}")
        audit({"event": "session_closed", "thread": thread_root, "post": pid,
               "follows": sorted(follows), "purged": purged, "kept": kept, "aborted": aborted})
        react(pid, "wave")
        try:
            post("👋 **Session closed** — " + " · ".join(bits)
                 + ".\n\nNothing can auto-wake this session again. Its Claude session id is kept, "
                   "so just message this thread to reopen and continue where it left off.",
                 thread_root)
        except Exception:  # noqa: BLE001
            pass

    def ingest_follow_requests(self) -> None:
        """Adopt the handoff files the approval server writes (one JSON per follow/unfollow
        request) into the state registry, confirming in the session's thread."""
        for path in sorted(globmod.glob(os.path.join(STATE_DIR, "follow-req-*.json"))):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    req = json.load(fh)
            except json.JSONDecodeError:  # writer renames into place, so this is corrupt, not partial
                log(f"dropping unreadable follow request {os.path.basename(path)}")
                req = None
            except OSError:
                continue
            if isinstance(req, dict):
                try:
                    self._apply_follow_request(req)
                except Exception as e:  # noqa: BLE001
                    log(f"follow request {os.path.basename(path)} failed: {e}")
            try:
                os.remove(path)
            except OSError:
                pass

    def _apply_follow_request(self, req: dict) -> None:
        action = req.get("action")
        bthread = str(req.get("bridge_thread") or "")
        if action == "follow" and bthread and req.get("id") and req.get("channel_id"):
            fid = str(req["id"])
            # A CLOSED session may not re-arm itself. Without this, the last turn of a session
            # being closed could register a follow after the teardown and resurrect the zombie.
            if self.thread_closed(bthread):
                log(f"follow {fid} refused — thread {bthread[:8]} is closed")
                audit({"event": "follow_refused_closed", "follow": fid, "thread": bthread})
                return
            record = {k: req.get(k) for k in (
                "id", "bridge_thread", "channel_id", "channel_label", "thread_id", "wake_on",
                "note", "created", "expires", "idle_ms", "last_seen", "wakes", "max_wakes",
                "one_shot")}
            # The sliding idle window the bridge renews on human engagement — derive it for follow
            # requests written before this field existed (window = original expires − created).
            if not record.get("idle_ms"):
                record["idle_ms"] = max(0, int(record.get("expires", 0))
                                        - int(record.get("created", 0)))
            with self.state_lock:
                follows = self.state.setdefault("follows", {})
                # same session re-following the same target replaces, never double-wakes
                dupes = [k for k, f in follows.items()
                         if f.get("bridge_thread") == bthread
                         and f.get("channel_id") == record["channel_id"]
                         and (f.get("thread_id") or "") == (record.get("thread_id") or "")]
                for k in dupes:
                    follows.pop(k, None)
                follows[fid] = record
                save_state(self.state)
            who = ", ".join("@" + u for u in record.get("wake_on") or []) or "anyone"
            hrs = max(0.0, (record.get("expires", 0) - record.get("created", 0)) / 3600000)
            log(f"follow {fid} registered for thread {bthread[:8]} → "
                f"#{record.get('channel_label')}/{str(record.get('thread_id') or '')[:8]}")
            audit({"event": "follow_registered", "follow": fid, "thread": bthread,
                   "channel": record["channel_id"],
                   "followed_thread": record.get("thread_id") or ""})
            post(f"📡 **Following** {self._follow_label(record)}"
                 + (f" (replaces `{dupes[0]}`)" if dupes else "")
                 + f" — this session auto-wakes on new posts from {who} "
                 f"(up to {record.get('max_wakes')} wakes, expires in {hrs:.0f}h)."
                 + (f"\n> {record['note']}" if record.get("note") else "")
                 + f"\n`unfollow {fid}` stops it.", bthread)
        elif action == "unfollow" and bthread:
            want = str(req.get("follow_id") or "all").lower()
            with self.state_lock:
                follows = {fid: dict(f) for fid, f in self.state.get("follows", {}).items()}
            targets = [fid for fid, f in follows.items()
                       if f.get("bridge_thread") == bthread and (want == "all" or fid == want)]
            if not targets:
                post(f"📡 Unfollow: no matching follow (`{want}`) for this session.", bthread)
            for fid in targets:
                self._drop_follow(fid, follows[fid], "unfollowed by the session")

    def poll_follows(self, me: str) -> None:
        """Watch every followed channel/thread; wake the owning session on matching posts."""
        with self.state_lock:
            follows = {fid: dict(f) for fid, f in self.state.get("follows", {}).items()}
        if not follows:
            return
        now_ms = int(time.time() * 1000)
        for fid, f in list(follows.items()):
            if int(f.get("expires", 0)) <= now_ms:
                self._drop_follow(fid, f, "expired — no further auto-wakes")
                follows.pop(fid)
        if not follows:
            return
        by_channel: dict[str, list[str]] = {}
        for fid, f in follows.items():
            if f.get("channel_id"):
                by_channel.setdefault(f["channel_id"], []).append(fid)
        wake_batches: dict[str, dict] = {}  # bridge_thread → {"posts": {pid: post}, "follows": set}
        advanced = False
        for ch, fids in by_channel.items():
            since = min(int(follows[fid].get("last_seen", 0)) for fid in fids)
            try:
                d = mmapi._api("GET", f"/channels/{ch}/posts?since={since}")
            except Exception as e:  # noqa: BLE001 - one unreadable channel must not stall the rest
                log(f"follow poll: channel {ch[:8]} unreadable: {e}")
                continue
            posts = list((d.get("posts") or {}).values()) if isinstance(d, dict) else []
            if not posts:
                continue
            posts.sort(key=lambda p: p.get("create_at", 0))
            newest = posts[-1].get("create_at", 0)
            op_engaged = 0        # newest genuine HUMAN-operator post here → renews this channel
            for p in posts:
                pr = p.get("props") or {}
                if not (pr.get("from_bridge") or pr.get("from_webhook") or pr.get("from_claude")) \
                        and mmapi._username(p.get("user_id", "")).lower() in OPERATORS:
                    op_engaged = max(op_engaged, p.get("create_at", 0))
                for fid in fids:
                    f = follows[fid]
                    if follow_matches(f, p, me):
                        b = wake_batches.setdefault(f["bridge_thread"],
                                                    {"posts": {}, "follows": set()})
                        b["posts"][p["id"]] = p
                        b["follows"].add(fid)
            if op_engaged:        # the operator engaged the followee directly — slide its window
                self._renew_follows(op_engaged, channel_id=ch)
            for fid in fids:
                if newest > int(follows[fid].get("last_seen", 0)):
                    advanced = True
                    follows[fid]["last_seen"] = newest
                    with self.state_lock:
                        live = self.state.get("follows", {}).get(fid)
                        if live:
                            live["last_seen"] = newest
        if advanced:
            with self.state_lock:
                save_state(self.state)
        for bthread, batch in wake_batches.items():
            try:
                self._dispatch_wake(bthread, batch, follows)
            except Exception as e:  # noqa: BLE001
                log(f"follow wake failed for thread {bthread[:8]}: {e}")

    def _dispatch_wake(self, bthread: str, batch: dict, follows: dict) -> None:
        # Belt and braces: closing drops the follows, so this should be unreachable — but a wake
        # batch computed just before the teardown must not land in a session the operator closed.
        if self.thread_closed(bthread):
            log(f"wake suppressed — thread {bthread[:8]} is closed")
            audit({"event": "wake_suppressed_closed", "thread": bthread,
                   "follows": sorted(batch["follows"])})
            return
        rows = sorted(batch["posts"].values(), key=lambda p: p.get("create_at", 0))
        fids = sorted(batch["follows"])
        # Phase 5 — classify FIRST, so a progress-only batch need not cost a wake at all.
        classed = [(p,) + classify_post(p) for p in rows]
        decisions = [c for c in classed if c[1] == "decision"]
        mode = str((follows.get(fids[0]) or {}).get("wake_on_class") or "all").lower()
        if not decisions:
            if CLASSIFY_SHADOW or mode == "all":
                # SHADOW: wake exactly as today, but record what would have been suppressed.
                # This is the evidence needed before trusting the classifier to drop wakes.
                log(f"[shadow] thread {bthread[:8]}: {len(rows)} progress post(s) would have "
                    f"been suppressed under wake_on=decision")
                audit({"event": "wake_shadow_suppressed", "thread": bthread,
                       "count": len(rows), "why": [c[2] for c in classed]})
            else:
                for p, _k, _w in classed:
                    self.digest.setdefault(bthread, []).append(
                        f"@{mmapi._username(p.get('user_id', ''))}: "
                        f"{_short((p.get('message') or '').strip(), 160)}")
                log(f"thread {bthread[:8]}: {len(rows)} progress post(s) buffered — no wake")
                audit({"event": "wake_suppressed_progress", "thread": bthread,
                       "count": len(rows)})
                return
        dropped, status = [], []
        with self.state_lock:
            for fid in fids:
                live = self.state.get("follows", {}).get(fid)
                if not live:
                    continue
                live["wakes"] = int(live.get("wakes", 0)) + 1
                if live.get("one_shot"):
                    self.state["follows"].pop(fid, None)
                    dropped.append(f"`{fid}` was one-shot and is now removed")
                elif live["wakes"] >= int(live.get("max_wakes", 20)):
                    self.state["follows"].pop(fid, None)
                    dropped.append(f"`{fid}` hit its wake cap ({live['max_wakes']}) and is now removed")
                else:
                    status.append(f"{fid}: {live['wakes']}/{live.get('max_wakes', 20)} wakes used")
            save_state(self.state)
        label = self._follow_label(follows[fids[0]])
        senders = sorted({"@" + mmapi._username(p.get("user_id", "")) for p in rows})
        note_txt = (f"📡 **Auto-wake** — {', '.join(senders)} posted in {label} "
                    f"(follow {', '.join('`' + x + '`' for x in fids)}). Waking this session…")
        if dropped:
            note_txt += "\n" + " · ".join(dropped)
        note_id = ""
        try:
            np = post(note_txt, bthread)
            note_id = np.get("id", "") if isinstance(np, dict) else ""
        except Exception as e:  # noqa: BLE001 - a dead thread still gets its wake attempt
            log(f"wake note post failed for thread {bthread[:8]}: {e}")
        lines = []
        for p in rows[:10]:
            ts = time.strftime("%H:%M", time.localtime(p.get("create_at", 0) / 1000))
            lines.append(f"@{mmapi._username(p.get('user_id', ''))} [{ts}]: "
                         f"{_clip((p.get('message') or '').strip(), 1800)}")
        if len(rows) > 10:
            lines.append(f"(+{len(rows) - 10} more — read the followed thread for the rest)")
        notes = "; ".join(f["note"] for f in (follows[x] for x in fids) if f.get("note"))
        prompt = (
            f"📡 AUTO-WAKE from your Mattermost follow {', '.join(fids)}"
            + (f" ({notes})" if notes else "")
            + f" — new post(s) in {label}:\n\n" + "\n".join(lines)
            + "\n\n(This wake was generated automatically by the bridge because you follow that "
              "thread — the human operator did not send it and may not be watching. Act on the "
              "update: use the mattermost tools to read more context or reply in the followed "
              "thread if a reply is expected, and summarize anything the operator must know in "
              "your final message. "
            + ("This follow is now removed. " if dropped and not status else "")
            + (f"Follow status: {'; '.join(status)} — call the approvals unfollow tool when the "
               f"conversation is done. " if status else "")
            + ")")
        log(f"follow wake → thread {bthread[:8]} ({len(rows)} post(s), follows {','.join(fids)})")
        audit({"event": "follow_wake", "thread": bthread, "follows": fids,
               "posts": [p.get("id") for p in rows]})
        self.ensure_worker(bthread)
        # Lane 2 — valved. Carries the age + body the staleness guard re-validates at RELEASE.
        self.queues[bthread].put_wake(_Item("wake", prompt, note_id, {
            "created_at": rows[-1].get("create_at", int(time.time() * 1000)),
            "body": "\n".join((p.get("message") or "") for p in rows),
            "klass": "decision" if decisions else "progress",
        }))

    def _operator_thumbsdown(self, pid: str) -> bool:
        """True when an OPERATOR (never a bot) has 👎'd this post."""
        try:
            rs = mmapi._api("GET", f"/posts/{pid}/reactions")
        except Exception:  # noqa: BLE001 - transient failure just retries next cycle
            return False
        if not isinstance(rs, list):
            return False
        for r in rs:
            if str(r.get("emoji_name", "")).lower() not in CANCEL_EMOJI:
                continue
            if mmapi._username(r.get("user_id", "")).lower() in OPERATORS:
                return True
        return False

    def poll_cancellations(self) -> None:
        """👎 on a queued message cancels exactly that item (tombstone; the worker skips it).

        Deliberately BOUNDED: only items still PENDING in a lane are checked — usually 0–3, and
        zero API calls when the queues are empty. An unbounded per-post reaction poll is exactly
        how this bridge has hit `WinError 10055` (socket exhaustion) before."""
        for thread_root, q in list(self.queues.items()):
            # (1) Items still WAITING in a lane — tombstoned, skipped on dequeue.
            for pid in q.pending_post_ids():
                if pid in q.cancelled or not self._operator_thumbsdown(pid):
                    continue
                q.cancel(pid)
                log(f"👎 cancel — item {pid[:8]} in thread {thread_root[:8]} will be skipped")
                audit({"event": "item_cancelled", "thread": thread_root, "post": pid})
            # (2) The turn already RUNNING. Without this, 👎 is near-useless: the worker sits
            # blocked in get(), so a lane-1 message is dequeued in MICROSECONDS while this poll
            # runs every ~4s — a plain message could essentially never be caught while queued.
            # Here the operator is explicitly cancelling their OWN work, so aborting is sanctioned
            # (unlike !stop, which never kills a user turn).
            with self.proc_lock:
                proc = self.procs.get(thread_root)
                pid = self.proc_post.get(thread_root, "")
                already = thread_root in self.cancel_inflight
            if proc is None or not pid or already:
                continue
            if self._operator_thumbsdown(pid):
                with self.proc_lock:
                    self.cancel_inflight.add(thread_root)
                log(f"👎 cancel — aborting the running turn in thread {thread_root[:8]}")
                audit({"event": "inflight_cancel_requested", "thread": thread_root, "post": pid})
                kill_tree(proc)

    # -- poll loop ------------------------------------------------------------
    def poll_once(self, me: str) -> None:
        last_seen = int(self.state.get("last_seen", 0))
        d = mmapi._api("GET", f"/channels/{CHANNEL_ID}/posts?since={last_seen}")
        posts = d.get("posts", {}) if isinstance(d, dict) else {}
        new_last = last_seen
        for p in sorted(posts.values(), key=lambda x: x.get("create_at", 0)):
            ca = p.get("create_at", 0)
            pid = p.get("id", "")
            new_last = max(new_last, ca)
            if ca <= last_seen or pid in self.processed or p.get("type"):
                continue
            self.processed.append(pid)
            if (p.get("props") or {}).get("from_bridge") or (p.get("props") or {}).get("from_webhook"):
                continue
            uid = p.get("user_id", "")
            username = mmapi._username(uid).lower()
            if uid == me:
                if not ALLOW_SELF:
                    continue
            elif username not in OPERATORS:
                log(f"ignored post from non-operator {username}")
                audit({"event": "ignored_non_operator", "user": username, "post": pid})
                continue
            msg = (p.get("message") or "").strip()
            if not msg:
                continue
            thread_root = p.get("root_id") or pid
            claims = claimed_threads()
            if thread_root in claims:
                log(f"thread {thread_root[:8]} claimed by "
                    f"{claims[thread_root].get('owner', 'external session')} — leaving to it")
                audit({"event": "claimed_thread_skipped", "thread": thread_root,
                       "owner": claims[thread_root].get("owner", "")})
                continue
            # HUMAN ENGAGEMENT with the session renews its follows' idle window (a live operator
            # message means "still working" — keep the auto-wake alive; the bot's own posts never
            # count, which uid != me guarantees).
            if uid != me:
                self._renew_follows(ca, bridge_thread=thread_root)
            cmd = SESSIONS_CMD_RE.match(msg)
            if cmd:  # inventory command — answered by the bridge itself, no Claude turn
                arg = cmd.group(1) or ""
                flt, limit = arg, 30
                if arg.isdigit():  # `sessions 100` = deeper listing, no filter
                    flt, limit = "", min(int(arg), 200)
                with self.state_lock:
                    threads_snapshot = dict(self.state["threads"])
                try:
                    post_chunked(sessions_mod.listing_text(REPO, threads_snapshot, flt, limit),
                                 thread_root)
                except Exception as e:  # noqa: BLE001
                    log(f"sessions command failed: {e}")
                audit({"event": "sessions_command", "post": pid, "filter": flt, "limit": limit})
                continue
            if FOLLOWS_CMD_RE.match(msg):  # follow inventory — answered by the bridge itself
                try:
                    post_chunked(self.follows_listing(), thread_root)
                except Exception as e:  # noqa: BLE001
                    log(f"follows command failed: {e}")
                audit({"event": "follows_command", "post": pid})
                continue
            unf = UNFOLLOW_CMD_RE.match(msg)
            if unf:  # operator kill switch: by id from anywhere, `unfollow all` in a session's thread
                want = unf.group(1).lower()
                with self.state_lock:
                    follows = {fid: dict(f) for fid, f in self.state.get("follows", {}).items()}
                targets = [fid for fid, f in follows.items()
                           if fid == want or (want == "all" and f.get("bridge_thread") == thread_root)]
                if not targets:
                    post(f"📡 No matching follow (`{want}`) — `follows` lists the active ones.",
                         thread_root)
                for fid in targets:
                    self._drop_follow(fid, follows[fid], "unfollowed by the operator")
                react(pid, "white_check_mark")
                continue
            if STOP_CMD_RE.match(msg):  # BEFORE the verdict check — `stop` is a verdict word
                self._full_stop(thread_root, pid)
                continue
            if CLOSE_CMD_RE.match(msg):  # the session OFF switch — disposes follows + queue
                self._close_session(thread_root, pid)
                continue
            # A parked ask_user question OWNS the next operator message: it is the answer, and
            # approval_server's poller reads it straight from the thread. Enqueuing it here would
            # double-run it — and the VERDICT_RE branch below would swallow "yes, go" entirely,
            # which is exactly the bug that made answers to questions disappear.
            if question_parked(thread_root):
                log(f"thread {thread_root[:8]}: message routed to the parked question")
                audit({"event": "question_answered", "thread": thread_root, "post": pid,
                       "user": username, "chars": len(msg)})
                react(pid, "speech_balloon")
                continue
            with self.running_lock:
                turn_in_flight = thread_root in self.running
            if turn_in_flight and VERDICT_RE.match(msg):
                continue  # approval verdict — consumed by the approval relay, not a new prompt
            # Transparency on catch-up: messages that queued while the bridge was down still
            # run, but the operator is told the bridge is acting on backlog, not live input.
            if self.first_poll and time.time() * 1000 - ca > 15 * 60 * 1000:
                try:
                    post("🔁 Bridge back online — catching up on messages posted while it "
                         "was down.", thread_root)
                except Exception:  # noqa: BLE001
                    pass
            # A human prompting a closed session obviously means it is live again. Only a real
            # prompt reopens — the meta-commands above (`sessions`, `follows`, `!stop`, …) are
            # about a session, not to it, and must not resurrect one.
            if self._reopen_thread(thread_root):
                try:
                    post("↩️ **Session reopened** — it resumes where it left off. It has no "
                         "follows: it will only wake for you until it registers new ones.",
                         thread_root)
                except Exception:  # noqa: BLE001
                    pass
            log(f"queueing message in thread {thread_root[:8]} ({len(msg)} chars)")
            audit({"event": "message_received", "thread": thread_root, "post": pid,
                   "user": username, "chars": len(msg)})
            self.ensure_worker(thread_root)
            self.queues[thread_root].put_user(_Item("user", msg, pid))  # lane 1 — closes the valve
        if new_last != last_seen:
            with self.state_lock:
                self.state["last_seen"] = new_last
                self.state["processed"] = list(self.processed)
                save_state(self.state)
        self.first_poll = False

    def run(self) -> None:
        os.makedirs(STATE_DIR, exist_ok=True)
        wait_for_mattermost()  # handles MM-still-booting AND invalid-token fallback
        me = mmapi._me()
        who = mmapi._username(me)
        # Best-effort channel self-join so a freshly-created dedicated bot starts working the
        # moment its token lands in the env file (needs team membership; log guidance if not).
        try:
            mmapi._api("POST", f"/channels/{CHANNEL_ID}/members", {"user_id": me})
        except Exception as e:  # noqa: BLE001
            log(f"note: could not self-join channel ({e}) — if @{who} is a fresh bot, add it "
                f"to the team + #claude-sessions once (System Console → Team, or /invite @{who})")
        log(f"claude-sessions bridge up — posting as @{who}, channel {CHANNEL_ID}, repo {REPO}, "
            f"claude {self.claude_bin}, operators {sorted(OPERATORS)}"
            f"{' [ALLOW_SELF — TEST MODE]' if ALLOW_SELF else ''}")
        audit({"event": "bridge_started", "channel": CHANNEL_ID, "repo": REPO,
               "allow_self": ALLOW_SELF})
        write_health(self.claude_bin, self.last_turn_ok_ts, self.last_turn_err, self.turn_fail_count)
        errors = 0
        while True:
            try:
                self.poll_once(me)
                self.ingest_follow_requests()
                self.poll_follows(me)
                self.poll_cancellations()
                errors = 0
            except Exception as e:  # noqa: BLE001 - transient MM/network outage must not kill the bridge
                errors += 1
                log(f"poll error #{errors}: {e}")
            # idle heartbeat: heal a pruned claude path while nobody is talking and
            # keep the health beacon fresh for the watchdog's functional check.
            if time.time() - self._last_health_write > 60:
                self._last_health_write = time.time()
                try:
                    self.ensure_claude_bin()
                except Exception as e:  # noqa: BLE001
                    log(f"claude binary UNRESOLVABLE: {e}")
                write_health(self.claude_bin, self.last_turn_ok_ts, self.last_turn_err, self.turn_fail_count)
            time.sleep(min(60, POLL_INTERVAL + min(errors, 6) * 5))


if __name__ == "__main__":
    _lock = acquire_single_instance_lock()
    if _lock is None:
        log("another bridge instance is already running (lock port busy) — exiting.")
        sys.exit(0)
    Bridge().run()
