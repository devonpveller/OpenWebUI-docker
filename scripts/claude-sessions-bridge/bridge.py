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
  BRIDGE_MODEL             model override (e.g. haiku)       (default: CLI default)
  BRIDGE_MAX_BUDGET_USD    per-turn cost-estimate backstop   (default 50; "" disables)
  BRIDGE_PERMISSION_MODE   default approval level            (default "auto"; per-thread
                           override via `mode: <level>` — bypassPermissions is refused)
  BRIDGE_TURN_TIMEOUT      seconds before a turn is killed   (default 7200)
  BRIDGE_APPROVAL_TIMEOUT  seconds an approval waits         (default 1800)
  BRIDGE_POLL_INTERVAL     channel poll seconds              (default 4)
  BRIDGE_MAX_CONCURRENT    concurrent turns across threads   (default 2)
  BRIDGE_SETTING_SOURCES   claude --setting-sources          (default "user,project" — excludes
                           settings.local.json so interactively-saved allows don't widen remote floor)
  BRIDGE_ALLOWED_TOOLS     optional extra --allowedTools floor (e.g. "Read Glob Grep")
  BRIDGE_ALLOW_SELF        "1" = treat the bot's own posts as operator input (smoke tests only)
"""

from __future__ import annotations

import glob as globmod
import json
import os
import queue
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


def _find_env_token(key: str, candidates: list[str]) -> str:
    for path in candidates:
        if not path:
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    if line.startswith(key + "="):
                        tok = line.split("=", 1)[1].strip().strip('"').strip("'").replace("\r", "")
                        if tok:
                            return tok
        except OSError:
            continue
    return ""


TOKEN_ENV_CANDIDATES = [
    os.environ.get("BRIDGE_ENV_FILE", ""),
    os.path.join(_REPO_ROOT, "agent-org", "docker", ".env"),
    os.path.join(_REPO_ROOT, ".env"),
]
if not os.environ.get("MM_TOKEN"):
    _tok = _find_env_token(TOKEN_KEY, TOKEN_ENV_CANDIDATES)
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
MODEL = os.environ.get("BRIDGE_MODEL", "")
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
SETTING_SOURCES = os.environ.get("BRIDGE_SETTING_SOURCES", "user,project")
ALLOWED_TOOLS = os.environ.get("BRIDGE_ALLOWED_TOOLS", "")
ALLOW_SELF = os.environ.get("BRIDGE_ALLOW_SELF") == "1"

STATE_DIR = os.path.join(_HERE, "state")
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

# Per-thread approval level: `mode: <level>` (colon required, like `model:`). bypassPermissions
# is deliberately NOT reachable from a chat message — that's the bridge's hard floor.
MODE_DIRECTIVE_RE = re.compile(r"^\s*mode\s*[:=]\s*(\S+)\s*(.*)\Z", re.IGNORECASE | re.DOTALL)
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
    "the bridge will automatically wake this session with the reply when it arrives."
)


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
    if os.environ.get("BRIDGE_CLAUDE_BIN"):
        return os.environ["BRIDGE_CLAUDE_BIN"]
    on_path = shutil.which("claude")
    if on_path:
        return on_path
    exts = sorted(globmod.glob(os.path.join(
        os.path.expanduser("~"), ".vscode", "extensions",
        "anthropic.claude-code-*", "resources", "native-binary", "claude.exe")))
    if exts:
        return exts[-1]  # newest version sorts last
    raise RuntimeError("claude CLI not found — set BRIDGE_CLAUDE_BIN")


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


def run_turn(claude_bin: str, thread_root: str, prompt: str, session_id: str | None,
             fork: bool = False, model: str = "", on_event=None, title: str = "",
             permission_mode: str = "") -> dict:
    """Run one headless turn, streaming NDJSON events. `on_event` receives each event as it
    arrives (used for the mid-turn progress log); the final `result` event is returned as the
    turn's result dict (same shape as --output-format json)."""
    # Session display name = the thread's title, so the /resume picker reads like a task list
    # ("mm fix the gateway config…") instead of opaque ids.
    name = f"mm {title[:48]}" if title else f"mm-{thread_root[:8]}"
    cmd = [claude_bin, "-p", "--output-format", "stream-json", "--verbose",
           "--permission-prompt-tool", "mcp__approvals__permission_prompt",
           "--mcp-config", write_mcp_config(thread_root),
           "--append-system-prompt", REMOTE_NOTE,
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
    proc = subprocess.Popen(cmd, cwd=REPO, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
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


# ── per-thread worker ────────────────────────────────────────────────────────
class Bridge:
    def __init__(self) -> None:
        self.claude_bin = find_claude_bin()
        self.state = load_state()
        self.state_lock = threading.Lock()
        self.queues: dict[str, queue.Queue] = {}
        self.running: set[str] = set()          # thread_roots with a turn in flight
        self.running_lock = threading.Lock()
        self.sem = threading.Semaphore(MAX_CONCURRENT)
        self.processed: deque[str] = deque(self.state.get("processed", []), maxlen=500)
        self.first_poll = True
        self.state.setdefault("follows", {})   # fid → follow record (see follow_matches)
        self._team: str | None = None          # cached team name for permalinks

    # -- worker ---------------------------------------------------------------
    def ensure_worker(self, thread_root: str) -> None:
        if thread_root in self.queues:
            return
        self.queues[thread_root] = queue.Queue()
        t = threading.Thread(target=self.worker, args=(thread_root,), daemon=True,
                             name=f"worker-{thread_root[:8]}")
        t.start()

    def worker(self, thread_root: str) -> None:
        q = self.queues[thread_root]
        while True:
            prompt, trigger_post = q.get()
            with self.sem:
                with self.running_lock:
                    self.running.add(thread_root)
                try:
                    self.execute(thread_root, prompt, trigger_post)
                except Exception as e:  # noqa: BLE001 - a broken turn must not kill the worker
                    log(f"worker {thread_root[:8]} error: {e}")
                    try:
                        post(f"❌ Bridge error running this turn: `{e}`", thread_root)
                    except Exception:  # noqa: BLE001
                        pass
                finally:
                    with self.running_lock:
                        self.running.discard(thread_root)

    def execute(self, thread_root: str, prompt: str, trigger_post: str = "") -> None:
        with self.state_lock:
            meta = dict(self.state["threads"].get(thread_root, {}))
        session_id = meta.get("session_id")

        thread_model = meta.get("model", "")
        thread_mode = meta.get("mode", "")
        changed = []
        while True:  # consume leading directives in any order: `model: …`, `mode: …`
            m = MODEL_DIRECTIVE_RE.match(prompt)
            if m:
                thread_model = m.group(1)
                prompt = m.group(2).strip()
                changed.append(f"model → `{thread_model}`")
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
            break
        if changed:
            with self.state_lock:
                entry = self.state["threads"].setdefault(thread_root, {})
                if thread_model:
                    entry["model"] = thread_model
                if thread_mode:
                    entry["mode"] = thread_mode
                save_state(self.state)
        if not prompt:  # directive-only (or directive-error) message: no turn to run
            if changed:
                post("🎛️ " + " · ".join(changed) + " — applies from the next message.",
                     thread_root)
            return
        mode_label = thread_mode or PERMISSION_MODE

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
                      f"`{model_label}`, approvals `{mode_label}`, working dir `{REPO}`. "
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
        resp = run_turn(self.claude_bin, thread_root, prompt, session_id, fork=fork,
                        model=thread_model or MODEL, on_event=on_event, title=title,
                        permission_mode=mode_label)
        dur = int(time.time() - t0)
        if progress["post_id"] and progress["lines"]:
            try:  # final flush so the log is complete, and mark the turn done
                patch_post(progress["post_id"], render_progress("✔ turn complete — result below."))
            except Exception:  # noqa: BLE001
                pass
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
            post_chunked(f"{label}\n{reason}{hint}\n\n{footer}", thread_root)
        else:
            post_chunked(f"{result_text}\n\n---\n{footer}", thread_root)
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
                     "stops that session's.")
        return "\n".join(lines)

    def _drop_follow(self, fid: str, f: dict, why: str) -> None:
        with self.state_lock:
            self.state.get("follows", {}).pop(fid, None)
            save_state(self.state)
        audit({"event": "follow_dropped", "follow": fid, "why": why})
        if f.get("bridge_thread"):
            try:
                post(f"📡 Follow `{fid}` ({self._follow_label(f)}) {why}.", f["bridge_thread"])
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
            record = {k: req.get(k) for k in (
                "id", "bridge_thread", "channel_id", "channel_label", "thread_id", "wake_on",
                "note", "created", "expires", "last_seen", "wakes", "max_wakes", "one_shot")}
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
            for p in posts:
                for fid in fids:
                    f = follows[fid]
                    if follow_matches(f, p, me):
                        b = wake_batches.setdefault(f["bridge_thread"],
                                                    {"posts": {}, "follows": set()})
                        b["posts"][p["id"]] = p
                        b["follows"].add(fid)
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
        rows = sorted(batch["posts"].values(), key=lambda p: p.get("create_at", 0))
        fids = sorted(batch["follows"])
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
        self.queues[bthread].put((prompt, note_id))

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
            log(f"queueing message in thread {thread_root[:8]} ({len(msg)} chars)")
            audit({"event": "message_received", "thread": thread_root, "post": pid,
                   "user": username, "chars": len(msg)})
            self.ensure_worker(thread_root)
            self.queues[thread_root].put((msg, pid))
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
        errors = 0
        while True:
            try:
                self.poll_once(me)
                self.ingest_follow_requests()
                self.poll_follows(me)
                errors = 0
            except Exception as e:  # noqa: BLE001 - transient MM/network outage must not kill the bridge
                errors += 1
                log(f"poll error #{errors}: {e}")
            time.sleep(min(60, POLL_INTERVAL + min(errors, 6) * 5))


if __name__ == "__main__":
    _lock = acquire_single_instance_lock()
    if _lock is None:
        log("another bridge instance is already running (lock port busy) — exiting.")
        sys.exit(0)
    Bridge().run()
