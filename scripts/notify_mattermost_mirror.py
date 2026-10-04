#!/usr/bin/env python3
"""The conversation mirror behind scripts/notify-mattermost.sh (harness item mm-mirror).

Each VS Code Claude Code session's #claude-sessions thread becomes a readable copy of the
conversation: the operator's prompts and Claude's reply text, each exactly once, in order,
WITHOUT an @-mention. The operator is @-mentioned only when they are needed:
  - PreToolUse(AskUserQuestion): the question(s) and every option label + description;
  - Notification: a permission prompt or other "needs you" notification, with its message
    (idle "waiting for your input" and auth notices are not posted - they are the ping the
    operator asked to be rid of).
  A question already answered by the time it is posted carries no mention.

Never posted: tool calls, tool results, thinking, system/attachment/meta entries. Secret-shaped
strings are masked with the repo's existing redactor (little-coder/src/littlecoder/sanitize.py)
plus the shapes it misses (see redact_extra); if that redactor cannot be loaded NOTHING is
posted (fail closed - a mirror that might leak is worse than no mirror).

MODES
  spawn    - run by notify-mattermost.sh inside the hook. Reads the hook JSON from stdin, starts
             `worker` DETACHED (own process group, no inherited stdout/stderr - the hook's pipes
             are never held open), hands it the JSON and returns. Costs one interpreter start.
  worker   - does all the work, outside the hook's budget.
  watchdog - started by every worker as a SEPARATE process: kills the worker at its hard wall
             (and only that worker: it holds a handle / checks its creation time).
  seed     - `seed [--force] <transcript.jsonl | directory>...`: "start from now". Sets each
             session's high-water mark to the CURRENT end of its transcript, so nothing already
             in it is mirrored (an operator choice at landing; without it, the first event of an
             already-open session back-fills its whole transcript). Skips sessions that already
             have a mark unless --force.

ONE ORDERED OUTBOX PER SESSION, drained by one worker at a time (a lock directory
scripts/.mm-mirror/<key>.lock holding the owner's pid and its hard deadline). It has two sources:
  - THE TRANSCRIPT (the only source of conversation content): operator prompts, Claude's reply
    text, and AskUserQuestion questions - a question IS a tool_use entry, rendered from it in
    transcript order, WITH the @-mention unless its answer (the tool_result) is already in the
    transcript, in which case it is posted without one, marked answered (a stale question must
    not ping). Read from the session's high-water mark (byte offset of the last fully-mirrored
    line, scripts/.mm-mirror/<key>.json; the path is stored normalised).
  - THE SPOOL scripts/.mm-mirror/<key>.outbox/, for what the transcript does not hold: a
    permission Notification is appended there (before any lock is taken, so it is never lost),
    stamped with the transcript size at that moment, and drained at that position.
Hooks only TRIGGER a drain: Stop, PreToolUse(AskUserQuestion), Notification, SessionEnd.
  - A Stop's `last_assistant_message` is never posted. It is only a signal to keep draining (for
    up to MM_MIRROR_SETTLE_SECS) until a reply ending in it has been mirrored; a PreToolUse
    likewise waits until its tool_use_id has been posted. What misses the wait is posted by the
    next event (SessionEnd included): late, never wrong.
  - EVERY SEND IS ANNOUNCED BEFORE IT IS MADE, ONE AT A TIME: the post's marker
    (props.mm_mirror) is written to the state as `pending` first. A run that finds a pending
    marker (the previous worker was killed, timed out, or got no answer) resolves it before it
    sends anything: found means it was posted (and, for a thread root, the map line is written);
    not found means re-send (transcript items are re-derived; a spooled item is still in the
    spool). While the lookup cannot be made the run posts nothing at all. A lookup that keeps
    getting DEFINITIVE error answers (HTTP 4xx/5xx) does not stall the session for ever: after
    MM_MIRROR_PENDING_TRIES of them spanning MM_MIRROR_PENDING_SECS the post is treated as POSTED
    and logged - a possible one-message gap is preferred over a possible duplicate. Timeouts and
    refused connections do NOT count (a server too slow to answer a lookup cannot take a post
    either), so a merely slow server delays the session instead of opening a gap.
  - HARD WALL: a worker is dead by MM_MIRROR_MAX_SECS (+2 s), whatever it is doing. Every
    request's timeout is cut to the time left, and a separate watchdog PROCESS kills the worker at
    the wall - a regex or anything else holding the GIL cannot stop it. Every redaction pattern
    is linear (anchored starts; a value is scanned once, to its end) and any single run of more
    than MAX_RUN non-space characters is elided before scanning. A lock whose owner is dead (pid
    gone) or past its own deadline is taken over under a short takeover mutex, so a takeover
    never races a live worker.

THREADING is the notifier's, unchanged: the map scripts/.mm-session-threads ("<key> <root>
[<full session id>]", append-only, LAST line wins, "-" = dead root), and root creation under
the same announce lock directory the bash script uses (<map>.lock.<key> with an `at` file).

Configuration (environment; notify-mattermost.sh passes the first group):
  MM_API, MM_CHANNEL, MM_THREADS, MM_KEY, MM_FULL_SID, MM_ROOT_DIR   - from the bash front door
  MM_OPERATOR_MENTION     who to @-mention (else MM_OPERATOR_MENTION in <root>/.env)
  MM_MIRROR_MAX_SECS      worker hard wall-clock bound, lock wait included (default 240, 10-3600)
  MM_MIRROR_POST_TIMEOUT  per-HTTP-call timeout in seconds (default 15; never past the wall)
  MM_MIRROR_SETTLE_SECS   how long a Stop keeps polling for its final reply (default 30, 0-300)
  MM_MIRROR_PENDING_TRIES / MM_MIRROR_PENDING_SECS   pending-post expiry (default 5 / 1800):
                          definitive lookup errors only, see above
  MM_MIRROR_CHUNK         max characters per post (default 15000; Mattermost's limit is 16383)
  MM_MIRROR_SYNC=1        run the worker in the foreground (diagnostics only)

Stdlib only. Never raises out of main; the hook never sees a non-zero exit from here.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get("MM_ROOT_DIR") or os.path.dirname(HERE)
STATE_DIR = os.path.join(ROOT, "scripts", ".mm-mirror")
LOG = os.path.join(STATE_DIR, "mirror.log")
API = os.environ.get("MM_API", "")
CHANNEL = os.environ.get("MM_CHANNEL", "")
THREADS = os.environ.get("MM_THREADS") or os.path.join(ROOT, "scripts", ".mm-session-threads")
KEY = re.sub(r"[^0-9a-z]", "", (os.environ.get("MM_KEY") or "").lower())
FULL_SID = os.environ.get("MM_FULL_SID", "")
PROJECT = os.path.basename(ROOT.rstrip("/\\")) or "ai-stack"


def _int_env(name: str, default: int, lo: int, hi: int) -> int:
    try:
        v = int(os.environ.get(name, ""))
    except ValueError:
        return default
    return min(max(v, lo), hi)


MAX_SECS = _int_env("MM_MIRROR_MAX_SECS", 240, 10, 3600)
POST_TIMEOUT = _int_env("MM_MIRROR_POST_TIMEOUT", 15, 1, 120)
SETTLE_SECS = _int_env("MM_MIRROR_SETTLE_SECS", 30, 0, 300)
PENDING_TRIES = _int_env("MM_MIRROR_PENDING_TRIES", 5, 1, 1000)
PENDING_SECS = _int_env("MM_MIRROR_PENDING_SECS", 1800, 0, 30 * 86400)
CHUNK = _int_env("MM_MIRROR_CHUNK", 15000, 200, 16000)
ANNOUNCE_WAIT = 15          # how long root creation waits for the bash notifier's announce lock
RING = 200                  # hashes of recently mirrored replies kept for "already mirrored?"
REDACT_MARGIN = min(10.0, MAX_SECS / 3)   # wall time a run must have left to start redacting a message
MAX_RUN = 2048              # a longer run of non-space characters is elided before redaction
NOTIFY_SKIP = {"idle_prompt", "auth_success", "elicitation_complete", "elicitation_response",
               "agent_completed"}
PROMPT_HEAD = "**\U0001f9d1 Operator**"
REPLY_HEAD = "**\U0001f916 Claude**"
DEADLINE = time.time() + MAX_SECS        # the worker's hard wall (reset when the worker starts)


def left() -> float:
    return DEADLINE - time.time()


def python_exe() -> str:
    """The BASE interpreter for detached children. Under a venv (Claude Code's hook environment
    here has VIRTUAL_ENV set) `python` is the venv launcher, and a DETACHED child started through
    it took 4-27 s to reach its first statement (attempt-4 tester); the base interpreter takes
    0.07 s. The helper is stdlib-only, so the base interpreter is all it needs."""
    exe = getattr(sys, "_base_executable", "") or ""
    return exe if exe and os.path.isfile(exe) else sys.executable


WORKER = False                         # set in worker(): the wall applies to state writes


def wall_check() -> None:
    """A worker does NO work past its recorded deadline, whether or not its watchdog has started
    yet: past the wall it exits at once, without writing anything. A send in flight at that
    moment was announced (`pending`) first, so the run that takes the lock over resolves it."""
    if WORKER and time.time() > DEADLINE:
        log("worker: past its hard wall; exiting without further work")
        os._exit(4)


# ── logging (counts and reasons only - never message text, never the token) ─────────────────
def log(msg: str) -> None:
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        if os.path.exists(LOG) and os.path.getsize(LOG) > 256 * 1024:
            with open(LOG, "rb") as fh:
                tail = fh.read()[-128 * 1024:]
            with open(LOG, "wb") as fh:
                fh.write(tail)
        line = f"{time.strftime('%Y-%m-%dT%H:%M:%S')} pid={os.getpid()} key={KEY or '-'} {msg}\n".encode("utf-8")
        _append_atomic(LOG, line)
    except OSError:
        pass


def _append_atomic(path: str, data: bytes) -> None:
    """Append in ONE OS-level append. Python's (CRT's) O_APPEND on Windows is seek-then-write, so
    two workers logging at the same instant overwrote each other's line (seen as a missing `done`
    line in the suite); a handle opened with FILE_APPEND_DATA only makes the OS append atomically."""
    if os.name != "nt":
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, data)
        finally:
            os.close(fd)
        return
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    h = k32.CreateFileW(path, 0x0004, 0x7, None, 4, 0x80, None)   # FILE_APPEND_DATA, share all, OPEN_ALWAYS
    if h in (None, wintypes.HANDLE(-1).value):
        raise OSError("CreateFileW failed")
    try:
        written = wintypes.DWORD()
        k32.WriteFile(h, data, len(data), ctypes.byref(written), None)
    finally:
        k32.CloseHandle(h)


# ── redaction: the repo's existing redactor, plus the shapes it misses ───────────────────────
# EVERY PATTERN HERE IS LINEAR: starts are anchored by a lookbehind that fails inside a run, and
# every repetition is bounded, so no input can make a scan quadratic (attempt 2 measured 51 s on a
# 200k-char dotted run with an unbounded scheme pattern - with the GIL held throughout).
R = "«REDACTED»"
_RUN = re.compile(r"\S{%d,}" % (MAX_RUN + 1))
# Words that make a KEY secret-named, matched as whole components of the key name
# (DB_PASSWORD, apiKey, X-Api-Key, AccountKey), never as substrings (max_tokens, tokenizer).
# Words that make a KEY secret-named, matched against the LAST component(s) of the key name
# (DB_PASSWORD, apiKey, X-Api-Key, AccountKey, aws_session_token), never as substrings
# (max_tokens, tokenizer, tests_passed) and never in the middle (secret_name).
_STRONG = {"token", "secret", "password", "passwd", "pwd", "pass", "passphrase", "apikey", "credential",
           "credentials", "authkey", "accountkey", "privatekey", "accesskey", "secretkey", "authtoken",
           "dsn", "secretkeybase", "clientsecret"}
_WEAK = {"key", "auth"}          # PRIMARY_KEY=id is not a secret; a key-shaped value under it is
# A value runs to its closing quote, or to whitespace/;/, unquoted - no length cap (a cap left
# the tail of a long token posted). Linear: each key occurrence scans its own value once.
# key=value: the HEAD (key + separator) is matched first and the value is parsed only for a
# secret-named key - a non-secret key never consumes text (so `Id=sa;Password=x` still reaches
# Password), and only secret keys pay for a value scan (linear).
_KV_HEAD = re.compile(r"(?<![A-Za-z0-9_.\-])([A-Za-z][A-Za-z0-9_.\-]{0,63})([\"']?[ \t]{0,8}([:=])(?!=)[ \t]{0,8})")
_KV_VALUE = re.compile(r"\"(?:[^\"\\\n]|\\.)+\"|'(?:[^'\\\n]|\\.)+'|[^\s\"']+")
# key names that END in a secret word with no separator (PGPASSWORD, GHTOKEN, MYSQLPASSWORD), and
# upper-case ones ending in PASS/PWD (DBPASS) - not bypass/compass, and not PWD (the directory)
_CONCAT_STRONG = re.compile(r"(?:password|passwd|token|secret)$")
_CONCAT_UPPER = re.compile(r"[A-Z0-9]{2,64}(?:PASS|PWD)$")      # bounded: used on one key component
_NOT_SECRET_KEYS = {"bypass", "compass", "surpass", "overpass", "underpass"}
_DIR_KEYS = {"pwd", "oldpwd"}      # PWD=/path is the working directory; Pwd=x in a connection string is not
# a yaml key whose value is on the following, more-indented lines (`password:` / `token: >-`)
_YAML_KEY = re.compile(r"(?<![ \t])([ \t]{0,200})(?:-[ \t]{1,4})?([A-Za-z][A-Za-z0-9_.\-]{0,63})[ \t]{0,8}:")
_YAML_INDICATOR = re.compile(r"[|>][-+0-9]{0,3}")
_PGPASS = re.compile(r"(?m)^[^\s:]{1,253}:(?:[0-9]{1,5}|\*):[^\s:]{1,128}:[^\s:]{1,128}:(\S{1,256})[ \t]*$")
_PPK = re.compile(r"Private-Lines:[ \t]{0,4}[0-9]{1,3}[ \t]{0,4}\r?\n((?:[A-Za-z0-9+/=]{4,256}(?:\r?\n|$)){1,200})")
_SAS_SIG = re.compile(r"[?&]sig=([^&\s\"'<>]{6,512})")
_BARE_CRED = re.compile(r"(?<![A-Za-z0-9_.:/@\-])[A-Za-z0-9._\-]{1,64}:([^\s:@/]{1,128})@"
                        r"(?=[A-Za-z0-9\-]{1,63}(?:\.[A-Za-z0-9\-]{1,63}){1,10}(?::[0-9]{1,5})?(?![A-Za-z0-9.\-]))")
# Applied to a STRIPPED line of at most 2,000 chars made only of | - : space tab (checked first):
# no two adjacent unbounded whitespace runs, so a long whitespace line cannot make it quadratic
# (attempt 5's version took 33 s on 40k spaces and wedged a session).
_TABLE_SEP = re.compile(r"\|?[ \t]{0,40}:?-{3,200}:?[ \t]{0,40}(?:\|[ \t]{0,40}:?-{3,200}:?[ \t]{0,40}){0,40}\|?")
_TABLE_SEP_CHARS = frozenset("|-: \t")


def is_table_sep(line: str) -> bool:
    t = line.strip()
    return bool(t) and len(t) <= 2000 and set(t) <= _TABLE_SEP_CHARS and bool(_TABLE_SEP.fullmatch(t))
_PLAIN = re.compile(r"[a-z][a-z0-9]{0,63}(?:[_\-][a-z0-9]{1,63}){0,15}")
_DOTTED = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}(?:\.[A-Za-z_][A-Za-z0-9_]{0,63}){1,15}")
# the password runs to the LAST '@' before the host, so one containing '@' is masked whole
_URL_CRED = re.compile(r"(?<![A-Za-z0-9+.\-])[A-Za-z][A-Za-z0-9+.\-]{0,31}://([^\s:/@]{0,128}):([^\s/]{1,256})@")
_AUTH_HDR = re.compile(r"(?i)\bauthorization\b[\"']?[ \t]{0,4}[:=][ \t]{0,4}[\"']?(?:basic|bearer|token)[ \t]{1,4}"
                       r"([A-Za-z0-9+/=._~\-]{4,2048})")
_CURL_U = re.compile(r"(?<!\S)(?:-u|--user)[ \t]{1,4}([^\s:]{1,128}):(\S{1,256})")
_CLI_PASSWORD = re.compile(r"(?<!\S)--password(?:=|[ \t]{1,4})(\S{1,256})")
_DOCKER_P = re.compile(r"\b(?:docker|podman|helm)[ \t]{1,4}(?:registry[ \t]{1,4})?login\b[^\n]{0,200}?[ \t]-p[ \t]{1,4}(\S{1,256})")
_NETRC = re.compile(r"\b(?:machine|default)\b[^\n]{0,200}?(?<!\S)password[ \t]{1,4}(\S{1,512})")
_MYSQL_P = re.compile(r"\bmysql(?:dump|admin)?\b[^\n]{0,200}?[ \t]-p(\S{1,256})")
_TELEGRAM = re.compile(r"(?<![0-9])[0-9]{8,10}:([A-Za-z0-9_\-]{35})(?![A-Za-z0-9_\-])")
_DISCORD = re.compile(r"(?<![A-Za-z0-9_\-])[MNO][A-Za-z0-9_\-]{23,25}\.[A-Za-z0-9_\-]{6}\.[A-Za-z0-9_\-]{27,38}(?![A-Za-z0-9_\-])")
_MM_TOKEN = re.compile(r"(?i)(?<![a-z])token\b[^\n]{0,24}?(?<![a-z0-9])([a-z0-9]{26})(?![a-z0-9])")
_AWS_SECRET = re.compile(r"(?i)(?<![a-z])(?:aws|secret)(?![a-z])[^\n]{0,120}?(?<![A-Za-z0-9/+])([A-Za-z0-9/+]{40})(?![A-Za-z0-9/+=])")
_VENDOR = re.compile(
    r"(?<![A-Za-z0-9_\-])(?:"
    r"sk-(?:ant-|proj-)?[A-Za-z0-9_\-]{16,256}"                 # OpenAI/Anthropic/LiteLLM
    r"|(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{10,256}"           # Stripe
    r"|xox[abeprs]-[A-Za-z0-9\-]{10,256}|xapp-[A-Za-z0-9\-]{10,256}"   # Slack
    r"|hf_[A-Za-z0-9]{20,256}"                                   # Hugging Face
    r"|tskey-(?:auth-|api-|client-)?[A-Za-z0-9\-_]{10,256}"     # Tailscale
    r"|AIza[0-9A-Za-z_\-]{30,256}"                               # Google
    r"|glpat-[A-Za-z0-9_\-]{20,256}|npm_[A-Za-z0-9]{30,256}"     # GitLab, npm
    r")")
_PEM_BEGIN = re.compile(r"-----BEGIN [A-Z0-9 ]{0,40}PRIVATE KEY(?: BLOCK)?-----")
_PEM_END = re.compile(r"-----END [A-Z0-9 ]{0,40}PRIVATE KEY(?: BLOCK)?-----")
_PEM_ANY_END = re.compile(r"-----END [A-Z0-9 ]{0,40}PRIVATE KEY(?: BLOCK)?-----")
_B64_LINE = re.compile(r"[A-Za-z0-9+/=]{8,}")
_ARMOR_HDR = re.compile(r"[A-Za-z][A-Za-z0-9\-]{0,40}:[^\n]{0,200}")
# a line's decoration around a key body: blockquote '> ', `cat -n` numbers, string quotes and '+'
_LINE_PREFIX = re.compile(r"^[ \t>\"'+|,;(]{0,12}(?:[0-9]{1,7}[ \t:|]{1,4})?[ \t>\"'+|]{0,8}")
_LINE_SUFFIX = re.compile(r"[ \t\"'+,;)\\]{0,12}$")
_PLACEHOLDER = re.compile(r"(?:\$\{?[A-Za-z_][A-Za-z0-9_]{0,63}\}?|<[^<>\n]{1,64}>|\{\{[^{}\n]{1,64}\}\}|%[A-Za-z_]{1,64}%"
                          r"|([*xX.\-_])\1{2,63}|\.\.\.|true|false|null|none|nil|undefined)", re.I)


def secret_shaped(v: str) -> bool:
    """Is a value under a WEAKLY secret-named key (`*_KEY`, `auth`) worth masking? Numbers,
    booleans, plain lowercase words or identifiers, dotted names, calls and placeholders are not
    (PRIMARY_KEY=id, SORT_KEY=created_at); a key-shaped value is (AccountKey=<base64>)."""
    v = v.strip().strip("\"'")
    if not v or v.startswith(("«", "$", "<", "{", "%")):
        return False
    if re.fullmatch(r"[+-]?[0-9]{1,30}(?:\.[0-9]{1,30})?", v) or _PLACEHOLDER.fullmatch(v):
        return False
    if _PLAIN.fullmatch(v) and not (re.search(r"[0-9]", v) and len(v) >= 8):
        return False
    if "(" in v or ")" in v or _DOTTED.fullmatch(v):
        return False
    return True


def key_strength(key: str) -> str:
    """'strong' | 'weak' | '' from the key name's LAST component(s)."""
    parts = re.split(r"[_.\-]+", re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key))
    parts = [x.lower() for x in parts if x]
    if not parts:
        return ""
    if "".join(parts) in _NOT_SECRET_KEYS:
        return ""
    if parts[-1] in _STRONG or "".join(parts[-2:]) in _STRONG or "".join(parts) in _STRONG:
        return "strong"
    raw_last = re.split(r"[_.\-]+", key)[-1]
    if _CONCAT_STRONG.search(parts[-1]) or _CONCAT_UPPER.fullmatch(raw_last):
        return "strong"
    return "weak" if parts[-1] in _WEAK else ""


def _looks_secret_in_prose(v: str) -> bool:
    """For a value after ':' or a spaced '=' (where prose also lives): a secret-shaped token, or
    one with a digit in it (hunter2), is a value; words, numbers and code are not."""
    if secret_shaped(v):
        return True
    return bool(re.search(r"[0-9]", v)) and not re.fullmatch(r"[+-]?[0-9.,]{1,40}", v) and not re.search(r"[()\[\]{}]", v)


def kv_redact(text: str) -> str:
    out, pos = [], 0
    for h in _KV_HEAD.finditer(text):
        if h.start() < pos or not key_strength(h.group(1)):
            continue
        v = _KV_VALUE.match(text, h.end())
        if not v:
            continue
        new = _kv_judge(h.group(1), h.group(2), h.group(3), v.group(0))
        if new is None:
            continue
        out.append(text[pos:h.start()] + new)
        pos = v.end()
    out.append(text[pos:])
    return "".join(out)


def _kv_judge(key: str, sep: str, op: str, val: str):
    """The replacement for `key<sep>val`, or None to leave it."""
    strength = key_strength(key)
    if not strength:
        return None
    quoted = val[0] in "\"'"
    inner = val[1:-1] if quoted else val
    spaced = sep.endswith((" ", "\t"))
    loose = not quoted and (op == ":" or spaced)          # where prose also lives
    tail = ""
    if loose:
        # 'password: required, 12 chars' - a ':' value ends at ; or , (a tight KEY=a;b keeps its tail)
        cut = min([i for i in (inner.find(";"), inner.find(",")) if i >= 0], default=-1)
        if cut >= 0:
            inner, tail = inner[:cut], inner[cut:]
        core = inner.rstrip(".!?:)")
        tail = inner[len(core):] + tail
        inner = core
    v = inner.strip()
    if (not v or v.startswith(("«", "<", "$", "{{", "%")) or _PLACEHOLDER.fullmatch(v)
            or re.search(r"[()\[\]{}]", v) and not quoted):
        return None
    if key.lower() in _DIR_KEYS and re.match(r"[/~.]|[A-Za-z]:[\\/]", v):
        return None
    if strength == "weak" and not secret_shaped(v.rstrip(",;.")):
        return None
    # A secret-looking token after ':' or a spaced '=' is masked even if words follow ('Set the
    # password: Tr0ub4dor&3x and restart'); a word is prose ('password: rotated yesterday'). A
    # tight KEY=value or a quoted value is always a value: changeme and passphrases are masked.
    if strength == "strong" and loose and not _looks_secret_in_prose(v):
        return None
    q = val[0] if quoted else ""
    return key + sep + q + R + q + tail


def mask_yaml_blocks(text: str) -> str:
    """For EVERY line, at any indentation, that is a secret-named key with nothing (or only a block
    indicator) after the colon, mask the more-indented lines below it. A non-secret parent (db:,
    stringData:, environment:) masks nothing and consumes nothing, so the keys nested under it are
    still judged - here and by kv_redact (attempt 5 let the parent swallow them). Linear: one pass."""
    if ":" not in text:
        return text
    lines = text.split("\n")
    base = None                         # indentation of the secret key whose block is being masked
    for i, ln in enumerate(lines):
        body = ln.lstrip(" \t")
        indent = len(ln) - len(body)
        if base is not None:
            if not body.strip():
                continue
            if indent > base:
                lines[i] = ln[:indent] + R
                continue
            base = None
        m = _YAML_KEY.match(ln)
        if m and key_strength(m.group(2)) == "strong":
            rest = ln[m.end():].strip()
            if _YAML_INDICATOR.fullmatch(rest):
                base = indent                   # a block scalar: its lines ARE the value
            elif not rest:
                # a plain value on the next line is masked; a nested mapping or list under the key
                # (k8s `secret:` + `secretName: x`) is structure - its own keys are judged
                nxt = next((x for x in lines[i + 1:i + 50] if x.strip()), "")
                nbody = nxt.lstrip(" \t")
                if (len(nxt) - len(nbody) > indent and not nbody.startswith("- ")
                        and not _YAML_KEY.match(nxt)):
                    base = indent
    return "\n".join(lines)


def _bare_cred_sub(m: re.Match) -> str:
    if not _looks_secret_in_prose(m.group(1)):
        return m.group(0)
    return m.group(0)[: m.start(1) - m.start(0)] + R + "@"


def mask_table_columns(text: str) -> str:
    """A markdown table whose header names a secret column (| user | password |): mask that
    column's cells in the rows below it."""
    if "|" not in text:
        return text
    lines = text.split("\n")
    i = 0
    while i < len(lines) - 1:
        hdr = lines[i]
        if hdr.strip().startswith("|") and is_table_sep(lines[i + 1]):
            cells = hdr.strip().strip("|").split("|")
            cols = [j for j, c in enumerate(cells) if key_strength(re.sub(r"\s+", "_", c.strip())) == "strong"]
            k = i + 2
            while cols and k < len(lines) and lines[k].strip().startswith("|"):
                row = lines[k].strip().strip("|").split("|")
                for j in cols:
                    if j < len(row) and row[j].strip() and not _PLACEHOLDER.fullmatch(row[j].strip()):
                        row[j] = " " + R + " "
                lines[k] = "|" + "|".join(row) + "|"
                k += 1
            i = k
            continue
        i += 1
    return "\n".join(lines)


def _mask(n: int):
    def sub(m: re.Match) -> str:
        return m.group(0)[: m.start(n) - m.start(0)] + R + m.group(0)[m.end(n) - m.start(0):]
    return sub


def _aws_sub(m: re.Match) -> str:
    v = m.group(1)
    # a real secret access key mixes upper, lower and digits; identifiers and hex do not
    if not (re.search(r"[A-Z]", v) and re.search(r"[a-z]", v) and re.search(r"[0-9]", v)):
        return m.group(0)
    return m.group(0)[: m.start(1) - m.start(0)] + R + m.group(0)[m.end(1) - m.start(0):]


def _mm_token_sub(m: re.Match) -> str:
    v = m.group(1)
    if not (re.search(r"[0-9]", v) and re.search(r"[a-z]", v)):
        return m.group(0)
    return m.group(0)[: m.start(1) - m.start(0)] + R


_NL_ANY = re.compile(r"\r?\n|(?:\\r)?\\n")          # a real newline or a JSON-escaped one


def _key_body_follows(after: str) -> bool:
    """After a BEGIN marker: within the next lines - real newlines or JSON-escaped '\n' - and after
    each line's decoration (blockquote, line numbers, string quotes) and up to sixteen armor-header
    or blank lines, is there a base64 body line? Prose that merely names the marker has none."""
    hdr = 0
    for ln in _NL_ANY.split(after[:3000], maxsplit=26)[1:26]:
        t = _LINE_SUFFIX.sub("", _LINE_PREFIX.sub("", ln, count=1), count=1)
        if _B64_LINE.fullmatch(t):
            return True
        if hdr < 16 and (not t.strip() or _ARMOR_HDR.fullmatch(t.strip())):
            hdr += 1
            continue
        return False
    return False


def mask_private_keys(text: str) -> str:
    """PEM / OpenSSH / PGP private keys, in any wrapping: real lines, one-line JSON with escaped
    '\n' (a service-account file's "private_key"), blockquotes, `cat -n` numbers, string
    concatenation. Masked from the BEGIN marker to the END marker in the raw text; with no END,
    to the end of the code block, else the end of the message. A BEGIN marker with no key body
    after it (quoted in prose) is left."""
    out, pos = [], 0
    for m in _PEM_BEGIN.finditer(text):
        if m.start() < pos or not _key_body_follows(text[m.end():m.end() + 3000]):
            continue
        e = _PEM_ANY_END.search(text, m.end())
        if e:
            stop = e.end()
        else:
            fence = text.find("```", m.end())
            stop = fence if fence >= 0 else len(text)
        out.append(text[pos:m.start()] + R)
        pos = stop
    out.append(text[pos:])
    return "".join(out)


def redact_extra(text: str) -> str:
    text = _URL_CRED.sub(_mask(2), text)
    text = _AUTH_HDR.sub(_mask(1), text)
    text = _CURL_U.sub(_mask(2), text)
    text = _CLI_PASSWORD.sub(_mask(1), text)
    text = _DOCKER_P.sub(_mask(1), text)
    text = _MYSQL_P.sub(_mask(1), text)
    text = _NETRC.sub(_mask(1), text)
    text = _TELEGRAM.sub(_mask(1), text)
    text = _DISCORD.sub(R, text)
    text = _VENDOR.sub(R, text)
    text = mask_yaml_blocks(text)
    text = _PGPASS.sub(_mask(1), text)
    text = _PPK.sub(lambda m: m.group(0)[: m.start(1) - m.start(0)] + R + "\n", text)
    text = _SAS_SIG.sub(_mask(1), text)
    text = mask_table_columns(text)
    text = kv_redact(text)
    text = _BARE_CRED.sub(_bare_cred_sub, text)
    text = _MM_TOKEN.sub(_mm_token_sub, text)
    text = _AWS_SECRET.sub(_aws_sub, text)
    return text


def load_redactor():
    """little-coder's sanitize.py, loaded by path (it is stdlib-only; importing the package would
    not be), plus redact_extra. Returns a callable, or None when it cannot be loaded - the caller
    then posts nothing."""
    path = os.path.join(ROOT, "little-coder", "src", "littlecoder", "sanitize.py")
    try:
        spec = importlib.util.spec_from_file_location("_mm_mirror_sanitize", path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod        # its dataclasses resolve their module through this
        spec.loader.exec_module(mod)
        base = mod.redact_secrets
        # its private-key pattern needs BEGIN and END on real lines; mask_private_keys covers that
        # and more (escaped one-line JSON, decorated lines, truncated and PGP keys), and leaves a
        # BEGIN marker quoted in prose alone
        # its assigned_secret pattern is replaced by _KV, which judges the key and the value
        # (it masked `password = hash_password(raw)`)
        patterns = [p for k, p in mod._SECRET_PATTERNS if k not in ("private_key", "assigned_secret")]
    except Exception as exc:  # noqa: BLE001 - any failure means "no redactor"
        log(f"worker: redactor unavailable ({type(exc).__name__}); posting nothing")
        return None

    def redact(text: str) -> str:
        text = _RUN.sub(lambda m: f"«elided: a {len(m.group(0))}-character run»", text)
        text = mask_private_keys(text)
        text = base(text)
        for p in patterns:
            text = p.sub(R, text)
        return redact_extra(text)
    return redact


_CODE = re.compile(r"```.*?(?:```|\Z)|`[^`\n]{1,2000}`", re.S)
_MENTION = re.compile(r"(?<![A-Za-z0-9_.@/\-])@(?=[A-Za-z0-9_][A-Za-z0-9_.\-]{0,63}(?![A-Za-z0-9_.\-]))(?![A-Za-z0-9_.\-]{1,64}/)")


def defang(text: str) -> str:
    """A mirrored reply must never ping anyone: a REAL mention - '@' at the start of a word, outside
    code - gets a zero-width space after the '@' (renders the same, mentions nobody). Code spans and
    blocks, e-mail addresses, git@host, @scope/pkg are left byte-for-byte as they were."""
    out, pos = [], 0
    for m in _CODE.finditer(text):
        out.append(_MENTION.sub("@\u200b", text[pos:m.start()]))
        out.append(m.group(0))
        pos = m.end()
    out.append(_MENTION.sub("@\u200b", text[pos:]))
    return "".join(out)


# ── the transcript ───────────────────────────────────────────────────────────────────────────
_WRAPPERS = re.compile(r"<(ide_opened_file|ide_selection|ide_diagnostics|system-reminder)>.*?</\1>",
                       re.DOTALL)


def _prompt_text(d: dict) -> str | None:
    if d.get("isMeta") or d.get("isCompactSummary") or d.get("isVisibleInTranscriptOnly") \
            or d.get("isSidechain"):
        return None
    origin = d.get("origin")
    if isinstance(origin, dict) and origin.get("kind") and origin.get("kind") != "human":
        return None          # task notifications, peer messages, ...
    content = (d.get("message") or {}).get("content")
    if isinstance(content, str):
        parts = [content]
    elif isinstance(content, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            return None
        parts = [b.get("text") or "" for b in content if isinstance(b, dict) and b.get("type") == "text"]
    else:
        return None
    text = _WRAPPERS.sub("", "\n\n".join(parts)).strip()
    if not text:
        return None
    if text.startswith(("<command-", "<local-command-", "<task-notification", "<bash-")):
        return None          # slash-command plumbing and harness wrappers, not something typed
    if not isinstance(origin, dict) and text.startswith("[Request interrupted"):
        return None
    return text


def _reply_text(d: dict) -> str | None:
    if d.get("isSidechain") or d.get("isApiErrorMessage"):
        return None
    msg = d.get("message") or {}
    if msg.get("model") == "<synthetic>":
        return None
    content = msg.get("content")
    if isinstance(content, str):
        texts = [content]
    elif isinstance(content, list):
        texts = [b.get("text") or "" for b in content if isinstance(b, dict) and b.get("type") == "text"]
    else:
        return None
    text = "\n\n".join(t.strip() for t in texts if t and t.strip())
    return text or None


def _questions(d: dict) -> list[dict]:
    """AskUserQuestion tool_use blocks of an assistant entry: [{"id", "input"}]."""
    if d.get("isSidechain"):
        return []
    content = (d.get("message") or {}).get("content")
    if not isinstance(content, list):
        return []
    return [{"id": str(b.get("id") or ""), "input": b.get("input") or {}} for b in content
            if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") == "AskUserQuestion"]


def classify(d: dict) -> list:
    """-> [("prompt"|"reply", text) | ("question", {"id", "input"})]; unknown shapes give []."""
    t = d.get("type")
    if t == "user":
        s = _prompt_text(d)
        return [("prompt", s)] if s else []
    if t == "assistant":
        s = _reply_text(d)
        return ([("reply", s)] if s else []) + [("question", q) for q in _questions(d)]
    return []


def read_new(path: str, offset: int):
    """Complete lines after `offset` -> ([(start, end, uid, kind, payload)], end_of_last_complete_line).
    A trailing line without its newline is left for the next run (it may still be being written)."""
    out = []
    with open(path, "rb") as fh:
        fh.seek(offset)
        data = fh.read()
    pos = offset
    for raw in data.split(b"\n")[:-1]:
        start, pos = pos, pos + len(raw) + 1
        if not raw.strip():
            continue
        try:
            d = json.loads(raw.decode("utf-8"))
            got = classify(d) if isinstance(d, dict) else None
        except Exception as exc:  # noqa: BLE001 - a bad line is skipped, never fatal
            log(f"skip transcript line at byte {start}: {type(exc).__name__}")
            continue
        uid = str(d.get("uuid") or f"off{start}") if isinstance(d, dict) else f"off{start}"
        for i, (kind, payload) in enumerate(got or []):
            out.append((start, pos, uid if i == 0 else f"{uid}:{i}", kind, payload))
    return out, pos


def answered(path: str, start: int, tool_use_id: str) -> bool:
    """Is the tool_result for `tool_use_id` in the transcript after `start`?"""
    if not tool_use_id:
        return False
    pat = re.compile(rb'"tool_use_id"\s*:\s*"' + re.escape(tool_use_id.encode()) + rb'"')
    try:
        with open(path, "rb") as fh:
            fh.seek(start)
            return bool(pat.search(fh.read()))
    except OSError:
        return False


def norm_path(p: str) -> str:
    return os.path.normcase(os.path.abspath(p)) if p else ""


def _norm(s: str) -> str:
    return " ".join((s or "").split())


def reply_hash(text: str) -> str:
    """A hash of a reply's normalised last 200 characters: the 'already mirrored?' signal (never
    content). The payload's final text ends with the transcript entry's text, so both reduce to
    the same tail."""
    return hashlib.sha1(_norm(text)[-200:].encode("utf-8", "replace")).hexdigest()[:16]


# ── rendering ────────────────────────────────────────────────────────────────────────────────
def split_text(text: str, limit: int) -> list[str]:
    """Ordered pieces of at most `limit` characters, broken at line ends where possible. A piece
    that ends inside a ``` fence closes it, and the next piece reopens it with the same opener."""
    pieces: list[str] = []
    cur: list[str] = []
    size = 0
    fence = ""
    reserve = 16

    def flush():
        nonlocal cur, size
        body = "\n".join(cur)
        if fence:
            body += "\n```"
        pieces.append(body)
        cur = [fence] if fence else []
        size = len(fence) + 1 if fence else 0

    for line in text.split("\n"):
        while len(line) > limit - reserve - len(fence) - 1:
            room = max(limit - reserve - len(fence) - 1 - size, 1)
            if size and room < 200:
                flush()
                continue
            cur.append(line[:room])
            size += room + 1
            line = line[room:]
            flush()
        if size + len(line) + 1 > limit - reserve - (4 if fence else 0) and cur:
            flush()
        cur.append(line)
        size += len(line) + 1
        if line.lstrip().startswith("```"):
            fence = "" if fence else line.strip()
    if cur and "\n".join(cur).strip():
        body = "\n".join(cur)
        if fence:
            body += "\n```"
        pieces.append(body)
    return pieces or [""]


def render(kind: str, text: str, redact) -> list[str]:
    head = PROMPT_HEAD if kind == "prompt" else REPLY_HEAD
    body = defang(redact(text))
    parts = split_text(body, CHUNK - len(head) - 16)
    if len(parts) == 1:
        return [f"{head}\n{parts[0]}"]
    return [f"{head} ({i}/{len(parts)})\n{p}" for i, p in enumerate(parts, 1)]


def render_question(tool_input: dict, redact, lead: str, is_answered: bool) -> list[str]:
    qs = (tool_input or {}).get("questions")
    if not isinstance(qs, list) or not qs:
        return []
    head = (f"\u2753 **Claude asked** (session `{KEY}`; answered in the session)" if is_answered
            else f"\u2753 **Claude is asking you** (session `{KEY}`)")
    lines = []
    for q in qs:
        if not isinstance(q, dict):
            continue
        h = (q.get("header") or "").strip()
        lines.append("")
        lines.append((f"**{h}**: " if h else "") + str(q.get("question") or "").strip())
        for o in q.get("options") or []:
            if isinstance(o, dict):
                desc = str(o.get("description") or "").strip()
                lines.append(f"- **{str(o.get('label') or '').strip()}**" + (f" \u2014 {desc}" if desc else ""))
        if q.get("multiSelect"):
            lines.append("_(more than one may be chosen)_")
    body = defang(redact("\n".join(lines)))
    first = ("" if is_answered else lead) + head
    parts = split_text(body, CHUNK - len(first) - 16)
    return [f"{first}\n{p}" if i == 0 else p for i, p in enumerate(parts)]


# ── Mattermost ───────────────────────────────────────────────────────────────────────────────
def env_value(name: str) -> str:
    try:
        with open(os.path.join(ROOT, ".env"), "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if line.startswith(name + "="):
                    return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


class MM:
    def __init__(self, token: str):
        self.token = token
        self.base = API.rsplit("/posts", 1)[0]

    def _req(self, method: str, url: str, body: dict | None = None):
        """-> (status, json-or-None). -1 = nothing was sent (refused, or no time left);
        0 = no answer after sending (outcome unknown)."""
        timeout = min(POST_TIMEOUT, left() - 1)
        if timeout < 1:
            return -1, None
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": f"Bearer {self.token}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                status = r.status
        except urllib.error.HTTPError as e:
            status, raw = e.code, e.read() or b""
        except (ConnectionRefusedError, urllib.error.URLError) as e:
            reason = getattr(e, "reason", e)
            if isinstance(reason, ConnectionRefusedError) or isinstance(e, ConnectionRefusedError):
                return -1, None
            return 0, None
        except Exception:  # noqa: BLE001 - timeouts, resets, anything: outcome unknown
            return 0, None
        try:
            return status, json.loads(raw or b"null")
        except ValueError:
            return status, None

    def post(self, message: str, root: str, marker: str = ""):
        """-> ("ok", id) | ("deadroot", "") | ("fail", "<why>") | ("unknown", "")."""
        body = {"channel_id": CHANNEL, "message": message}
        if root:
            body["root_id"] = root
        if marker:
            body["props"] = {"mm_mirror": marker}
        for attempt in range(3):
            status, js = self._req("POST", API, body)
            if status in (200, 201) and isinstance(js, dict) and js.get("id") and not js.get("status_code"):
                return "ok", js["id"]
            if status == 0:
                return "unknown", ""
            if status == -1:
                return "fail", "not sent"
            if status == 400 and isinstance(js, dict) and "root_id" in str(js.get("id") or ""):
                return "deadroot", ""
            if status in (429, 500, 502, 503, 504) and attempt < 2 and left() > 4:
                time.sleep(1 + attempt * 2)       # the server answered: nothing was created
                continue
            return "fail", str(status)
        return "fail", "retries"

    def find_marker(self, marker: str, since_ms: int):
        """-> ("found", post) | ("absent", None) | ("error", None) - the server ANSWERED with an
        error | ("noanswer", None) - timeout, refused, no time left."""
        status, js = self._req("GET", f"{self.base}/channels/{CHANNEL}/posts?since={since_ms}")
        if status in (0, -1):
            return "noanswer", None
        if status != 200 or not isinstance(js, dict):
            return "error", None
        for p in (js.get("posts") or {}).values():
            if isinstance(p, dict) and (p.get("props") or {}).get("mm_mirror") == marker:
                return "found", p
        return "absent", None


# ── the thread map (same file, format and lock as notify-mattermost.sh) ──────────────────────
def map_root() -> tuple[str, str]:
    root, sid = "", ""
    try:
        with open(THREADS, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                f = line.split()
                if len(f) >= 2 and f[0] == KEY:
                    root = "" if f[1] == "-" else f[1]
                    sid = f[2] if len(f) > 2 else ""
    except OSError:
        pass
    return root, sid


def map_append(*fields: str) -> None:
    try:
        with open(THREADS, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(" ".join(f for f in fields if f) + "\n")
    except OSError as exc:
        log(f"map append failed ({type(exc).__name__})")


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        # NOT os.kill(pid, 0): on Windows that TERMINATES the process.
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, pid)            # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return k32.GetLastError() == 5                 # access denied: it exists
        try:
            code = ctypes.c_ulong()
            if not k32.GetExitCodeProcess(h, ctypes.byref(code)):
                return True
            return code.value == 259                       # STILL_ACTIVE
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def _read(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def drop_dir_lock(path: str) -> None:
    try:
        for n in os.listdir(path):
            os.remove(os.path.join(path, n))
        os.rmdir(path)
    except OSError:
        pass


def _owner_gone(path: str, blind_since: list) -> bool:
    """True when the lock's owner provably cannot still be working: its pid is dead, or it is past
    its own hard deadline (+5 s; a worker never outlives it - see the watchdog). A lock with no
    owner record yet is given 10 s (its creator writes the record microseconds after mkdir)."""
    pid, dl = _read(os.path.join(path, "pid")), _read(os.path.join(path, "deadline"))
    if pid.isdigit():
        if not pid_alive(int(pid)):
            return True
        return dl.isdigit() and time.time() > int(dl) + 5
    blind_since[0] = blind_since[0] or time.time()
    return time.time() - blind_since[0] > 10


def take_session_lock(path: str) -> bool:
    """Wait (within this worker's wall) for the session lock; take over a dead owner's lock under a
    takeover mutex, re-checking under it, so two waiters cannot both 'take over' and one cannot
    remove a lock a live worker has just created."""
    blind = [0.0]
    while left() > 2:
        try:
            os.mkdir(path)
            with open(os.path.join(path, "pid"), "w") as fh:
                fh.write(str(os.getpid()))
            with open(os.path.join(path, "deadline"), "w") as fh:
                fh.write(str(int(DEADLINE)))
            return True
        except FileExistsError:
            pass
        except OSError:
            return False
        if _owner_gone(path, blind):
            mutex = path + ".takeover"
            try:
                os.mkdir(mutex)
            except FileExistsError:
                try:
                    if time.time() - os.path.getmtime(mutex) > 10:
                        os.rmdir(mutex)
                except OSError:
                    pass
            except OSError:
                pass
            else:
                try:
                    if _owner_gone(path, [blind[0]]):
                        log("taking over a session lock whose owner is gone")
                        drop_dir_lock(path)
                finally:
                    try:
                        os.rmdir(mutex)
                    except OSError:
                        pass
                blind = [0.0]
                continue
        time.sleep(0.2)
    return False


def take_announce_lock(path: str, wait: float, stale: float) -> bool:
    """The bash notifier's announce lock (mkdir + `at`), same protocol and expiry as there."""
    end = time.time() + min(wait, max(left() - 2, 0))
    first_blind = None
    while True:
        try:
            os.mkdir(path)
            try:
                with open(os.path.join(path, "at"), "w") as fh:
                    fh.write(str(int(time.time())))
                with open(os.path.join(path, "pid"), "w") as fh:
                    fh.write(str(os.getpid()))
            except OSError:
                pass
            return True
        except FileExistsError:
            pass
        except OSError:
            return False
        at = _read(os.path.join(path, "at"))
        age = time.time() - int(at) if at.isdigit() else None
        holder = _read(os.path.join(path, "pid"))
        # A mirror worker that died holding it (killed at its wall mid root-creation) records its
        # pid: a dead holder is not waited for. (Bash holders write no pid; their `at` expiry stands.)
        if (age is not None and age > stale) or (holder.isdigit() and not pid_alive(int(holder))):
            drop_dir_lock(path)
            continue
        if age is None:
            first_blind = first_blind or time.time()
            if time.time() - first_blind > 3:
                drop_dir_lock(path)
                continue
        if time.time() >= end:
            return False
        time.sleep(0.2)


class Thread:
    """Posts into this session's thread, creating it (first post = root) when there is none."""

    def __init__(self, mm: MM):
        self.mm = mm

    def send(self, message: str, marker: str = ""):
        root, msid = map_root()
        if root:
            res = self.mm.post(message, root, marker)
            if res[0] != "deadroot":
                if res[0] == "ok" and FULL_SID and not msid:
                    map_append(KEY, root, FULL_SID)
                return res
            map_append(KEY, "-")
            log("dead root; opening a new thread")
        lock = f"{THREADS}.lock.{KEY}"
        held = take_announce_lock(lock, ANNOUNCE_WAIT, 30)
        try:
            root, _ = map_root()            # someone may have opened it while we waited
            if root:
                return self.mm.post(message, root, marker)
            res = self.mm.post(message, "", marker)
            if res[0] == "ok":
                map_append(KEY, res[1], FULL_SID)
            return res
        finally:
            if held:
                drop_dir_lock(lock)


# ── state ────────────────────────────────────────────────────────────────────────────────────
def state_path(key: str = "") -> str:
    return os.path.join(STATE_DIR, f"{key or KEY}.json")


def load_state() -> dict:
    try:
        with open(state_path(), "r", encoding="utf-8") as fh:
            st = json.load(fh)
        return st if isinstance(st, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(st: dict, key: str = "") -> None:
    wall_check()
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = state_path(key) + f".tmp{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(st, fh)
    os.replace(tmp, state_path(key))


# ── the work ─────────────────────────────────────────────────────────────────────────────────
def spool_dir(key: str = "") -> str:
    return os.path.join(STATE_DIR, f"{key or KEY}.outbox")


def spool_append(mention: str, text: str, at: int) -> None:
    """A Notification's post, spooled BEFORE any lock (a busy or dead worker cannot lose it), as ONE
    FILE PER NOTIFICATION: written under a temporary name, then renamed into place. A rename is
    atomic, so simultaneous Notifications can neither overwrite nor merge each other (Windows'
    O_APPEND is seek-then-write: attempt 5 lost 2-5 of 30), and a writer killed mid-write leaves
    only a temporary file, which the drain never reads. Names sort in arrival order."""
    d = spool_dir()
    os.makedirs(d, exist_ok=True)
    name = f"{time.time_ns():020d}-{os.getpid():08d}-{os.urandom(4).hex()}"
    tmp = os.path.join(d, f".{name}.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"at": at, "mention": mention, "text": text, "t": int(time.time())}, fh)
    os.replace(tmp, os.path.join(d, f"{name}.json"))


def spool_names(key: str = "") -> list[str]:
    try:
        return sorted(n[:-5] for n in os.listdir(spool_dir(key)) if n.endswith(".json") and not n.startswith("."))
    except OSError:
        return []


def spool_item(name: str) -> dict:
    try:
        with open(os.path.join(spool_dir(), f"{name}.json"), "r", encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {"bad": True}
    except (OSError, ValueError):
        return {"bad": True}


class Run:
    def __init__(self, job: dict, mm: MM, redact):
        self.job = job
        self.thread = Thread(mm)
        self.mm = mm
        self.redact = redact
        self.posted = 0
        self.stopped = ""
        self.path = self.job.get("transcript_path") or ""
        mention = os.environ.get("MM_OPERATOR_MENTION") or env_value("MM_OPERATOR_MENTION")
        self.lead = f"{mention} " if mention else ""

    # ---- one marked, announced send at a time ------------------------------------------------
    def resolve_pending(self, st: dict) -> bool:
        """A send announced by an earlier run that never confirmed (killed, timed out, no answer):
        look its marker up BEFORE anything else is sent. Found -> record it as done (its chunk, its
        spool item; if it was this session's first post, the thread root in the map). Absent ->
        forget the marker: the item is re-derived (transcript) or still in the spool, and is
        re-sent. Cannot tell -> False: this run posts nothing - until the expiry, which counts
        only DEFINITIVE error answers."""
        pend = st.get("pending") or {}
        if not pend.get("marker"):
            return True
        state, post = self.mm.find_marker(pend["marker"], int(pend.get("since") or 0))
        if state in ("error", "noanswer"):
            if state == "error":
                pend["tries"] = int(pend.get("tries") or 0) + 1
                pend.setdefault("first_fail", int(time.time()))
            if (state == "error" and pend["tries"] >= PENDING_TRIES
                    and time.time() - pend["first_fail"] >= PENDING_SECS):
                log(f"pending post unresolved after {pend['tries']} error answers; EXPIRED - treated as posted "
                    "(a possible gap is preferred over a possible duplicate)")
                state = "expired"
            else:
                st["pending"] = pend
                save_state(st)
                self.stopped = f"pending post cannot be checked ({state})"
                return False
        if state in ("found", "expired"):
            if state == "found":
                root, _ = map_root()
                if not post.get("root_id") and not root:
                    map_append(KEY, post["id"], FULL_SID)          # it was this session's thread root
                log("pending post found by its marker; not re-sent")
            uid = str(pend.get("uid") or "")
            if uid.startswith("out-"):
                st["outbox_last"] = max(str(st.get("outbox_last") or ""), uid[4:])
            elif uid:
                st["partial"] = {"uid": uid, "done": int(pend.get("k") or 0) + 1}
        else:
            log("pending post was not created; it is sent again")
        st.pop("pending", None)
        save_state(st)
        return True

    def send_marked(self, st: dict, uid: str, k: int, message: str) -> bool:
        """Exactly once: the marker is written to `pending` BEFORE the send. Only ever called with
        no pending send outstanding (resolve_pending ran first; a send that ends unknown stops
        the run), so a marker is never overwritten."""
        assert not st.get("pending"), "a pending send must be resolved before the next one"
        wall_check()
        marker = f"{KEY}:{uid}:{k}"
        st["pending"] = {"marker": marker, "since": int(time.time() * 1000) - 120000, "uid": uid, "k": k}
        save_state(st)
        res = self.thread.send(message, marker)
        wall_check()                            # past the wall: leave `pending` for the next owner
        if res[0] == "ok":
            st.pop("pending", None)
            self.posted += 1
            return True
        if res[0] != "unknown":
            st.pop("pending", None)             # the server said no, or nothing was sent
        save_state(st)
        self.stopped = f"post {res[0]} {res[1]}".strip()
        return False

    def post_chunks(self, st: dict, uid: str, chunks: list[str]) -> bool:
        part = st.get("partial") or {}
        start = int(part.get("done") or 0) if part.get("uid") == uid else 0
        for k in range(start, len(chunks)):
            if left() < 3:
                self.stopped = "deadline"
                return False
            if not self.send_marked(st, uid, k, chunks[k]):
                return False
            st["partial"] = {"uid": uid, "done": k + 1}
            save_state(st)
        return True

    # ---- the single ordered drain --------------------------------------------------------------
    def flush_spool(self, st: dict, upto, prompts=()) -> bool:
        """Spooled items stamped at or before transcript position `upto` (None: all), in name
        (= arrival) order after the last one posted. An item with an operator prompt AFTER its
        position in the transcript is stale - the session has moved on - and is posted without the
        @-mention, like an answered question."""
        last = str(st.get("outbox_last") or "")
        for name in (n for n in spool_names() if n > last):
            it = spool_item(name)
            if upto is not None and not it.get("bad") and int(it.get("at") or 0) > upto:
                break
            text = str(it.get("text") or "")
            if it.get("bad") or not text:
                log(f"spool item {name} unreadable or empty; skipped")
            else:
                at = int(it.get("at") or 0)
                stale = any(ps >= at for ps in prompts)
                post = ("" if stale else str(it.get("mention") or "")) + text
                if not self.send_marked(st, f"out-{name}", 0, post):
                    return False
            st["outbox_last"] = name
            save_state(st)
        return True

    def drain(self, st: dict) -> bool:
        """Post, in order, every new transcript item and every spooled item at its position."""
        if not self.path or not os.path.isfile(self.path):
            log("no readable transcript_path")
            return self.flush_spool(st, None)
        npath = norm_path(self.path)
        if st.get("path") != npath:
            if st.get("path"):
                log("transcript path changed for this session; starting a new mark")
            for k in ("offset", "partial", "seen", "qseen"):
                st.pop(k, None)
            st["path"], st["offset"] = npath, 0
        size = os.path.getsize(self.path)
        if size < int(st.get("offset") or 0):
            log("transcript shrank below the high-water mark; resetting the mark to its end")
            st.update({"offset": size, "partial": None})
            save_state(st)
            return self.flush_spool(st, None)
        msgs, end = read_new(self.path, int(st.get("offset") or 0))
        prompts = [m[0] for m in msgs if m[3] == "prompt"]
        for start, pos, uid, kind, payload in msgs:
            if not self.flush_spool(st, start, prompts):
                return False
            chunks = self.render_guarded(st, uid, kind, pos, payload)
            if chunks is None:
                return False
            if chunks and not self.post_chunks(st, uid, chunks):
                return False
            if kind == "reply":
                seen = st.setdefault("seen", [])
                seen.append(reply_hash(payload))
                del seen[:-RING]
            elif kind == "question":
                qseen = st.setdefault("qseen", [])
                qseen.append(payload["id"])
                del qseen[:-RING]
            st["offset"], st["partial"] = pos, None
            save_state(st)
        st["offset"] = end
        save_state(st)
        return self.flush_spool(st, None)

    def render_guarded(self, st: dict, uid: str, kind: str, pos: int, payload):
        """Render (and so redact) one message - without ever wedging the session on it. A run starts
        a redaction only with REDACT_MARGIN of its wall left, and records `redacting` first; if a
        run finds `redacting` already set for the same message, the previous run died inside its
        redaction (no redaction legitimately takes that long), so the message is replaced by a
        placeholder and the session moves on. Redaction holds the GIL, so it cannot be timed out
        from inside; the record makes the next run the timeout."""
        if (st.get("redacting") or {}).get("uid") == uid:
            log(f"message {uid}: redaction did not finish in the previous run; posting a placeholder")
            head = REPLY_HEAD if kind != "prompt" else PROMPT_HEAD
            st.pop("redacting", None)
            return [f"{head}\n_[message omitted: redaction timed out]_"]
        if left() < REDACT_MARGIN:
            self.stopped = "deadline"
            return None
        st["redacting"] = {"uid": uid}
        save_state(st)
        if kind == "question":
            done = answered(self.path, pos, payload["id"])
            chunks = render_question(payload["input"], self.redact, self.lead, done)
        else:
            chunks = render(kind, payload, self.redact)
        st.pop("redacting", None)
        save_state(st)                  # at once: a later death must not be blamed on this redaction
        return chunks

    def wait_until(self, st: dict, done, what: str) -> None:
        """Keep draining until `done(st)` or MM_MIRROR_SETTLE_SECS; what is missed is left for the
        next event."""
        end = time.time() + SETTLE_SECS
        while not done(st):
            if time.time() >= end or left() < 5:
                log(f"{what} not in the transcript yet; left for the next event")
                return
            time.sleep(0.25)
            if not self.drain(st):
                return

    def run(self) -> None:
        event = self.job.get("hook_event_name") or ""
        st = load_state()
        ok = self.resolve_pending(st) and self.drain(st)
        if ok and event == "Stop" and _norm(self.job.get("last_assistant_message") or ""):
            h = reply_hash(self.job["last_assistant_message"])
            self.wait_until(st, lambda s: h in (s.get("seen") or []), "final reply")
        elif ok and event == "PreToolUse" and self.job.get("tool_use_id"):
            tid = str(self.job["tool_use_id"])
            self.wait_until(st, lambda s: tid in (s.get("qseen") or []), "question")
        log(f"done event={event} posted={self.posted}" + (f" stopped={self.stopped}" if self.stopped else ""))


def kill_pid(pid: int) -> None:
    if os.name == "nt":
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x0001, False, pid)                # PROCESS_TERMINATE
        if h:
            k32.TerminateProcess(h, 3)
            k32.CloseHandle(h)
    else:
        try:
            os.kill(pid, 9)
        except OSError:
            pass


def process_start_id(pid: int = 0) -> str:
    """An identity for a process that a reused pid cannot share: its creation time (Windows FILETIME;
    elsewhere the start time from /proc). '' when it cannot be read."""
    pid = pid or os.getpid()
    if os.name == "nt":
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, pid)                # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return ""
        try:
            c, e, kt, ut = (ctypes.c_ulonglong() for _ in range(4))
            if not k32.GetProcessTimes(h, ctypes.byref(c), ctypes.byref(e), ctypes.byref(kt), ctypes.byref(ut)):
                return ""
            return str(c.value)
        finally:
            k32.CloseHandle(h)
    try:
        with open(f"/proc/{pid}/stat") as fh:
            return fh.read().rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        return ""


def watchdog(pid: int, deadline: float, start_id: str) -> None:
    """THE HARD WALL, from outside. A thread in the worker cannot be relied on: a regex holds the
    GIL for its whole run (attempt 2: a worker with a 10 s wall lived 51 s).

    IDENTITY FIRST: it acts only on the process whose creation time it was given. On Windows it
    then holds a HANDLE to that process - a handle names one process for its whole life, so a pid
    reused after the worker exits can never be the one it terminates. Elsewhere it re-checks the
    start time immediately before the kill."""
    if not start_id or process_start_id(pid) != start_id:
        log(f"watchdog: pid={pid} is not the worker it was started for; not watching it")
        return
    if os.name == "nt":
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x00100000 | 0x0001 | 0x1000, False, pid)   # SYNCHRONIZE|TERMINATE|QUERY
        if not h:
            return
        try:
            c, e, kt, ut = (ctypes.c_ulonglong() for _ in range(4))
            k32.GetProcessTimes(h, ctypes.byref(c), ctypes.byref(e), ctypes.byref(kt), ctypes.byref(ut))
            if str(c.value) != start_id:
                return
            ms = max(int((deadline + 2 - time.time()) * 1000), 0)
            if k32.WaitForSingleObject(h, ms) == 0x102:               # WAIT_TIMEOUT: still running
                k32.TerminateProcess(h, 3)
                log(f"watchdog: worker pid={pid} killed at its hard wall")
        finally:
            k32.CloseHandle(h)
        return
    while pid_alive(pid):
        if time.time() >= deadline + 2:
            if process_start_id(pid) == start_id:
                kill_pid(pid)
                log(f"watchdog: worker pid={pid} killed at its hard wall")
            return
        time.sleep(min(1.0, max(deadline + 2 - time.time(), 0.05)))


def start_watchdog() -> None:
    kw: dict = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
                "close_fds": True, "cwd": ROOT}
    args = [python_exe(), os.path.abspath(__file__), "watchdog", str(os.getpid()), str(DEADLINE),
            process_start_id()]
    if os.name == "nt":
        kw["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    subprocess.Popen(args, **kw)


def worker() -> None:
    global DEADLINE, WORKER
    DEADLINE = time.time() + MAX_SECS
    WORKER = True
    raw = sys.stdin.buffer.read()
    try:
        job = json.loads(raw.decode("utf-8", "replace"))
        if not isinstance(job, dict):
            raise ValueError("not an object")
    except ValueError:
        log("worker: hook JSON unreadable")
        return
    event = job.get("hook_event_name") or ""
    if not KEY or not API or not CHANNEL:
        log(f"worker: missing key/api/channel (event={event})")
        return
    if event == "PreToolUse" and job.get("tool_name") != "AskUserQuestion":
        return
    token = env_value("CLAUDE_MM_BOT_TOKEN")
    if not token:
        log("worker: no bot token")
        return
    redact = load_redactor()
    if redact is None:
        return
    if event == "Notification":
        ntype = str(job.get("notification_type") or "")
        message = str(job.get("message") or "").strip() or "needs your attention"
        if ntype in NOTIFY_SKIP or (not ntype and "waiting for your input" in message.lower()):
            log(f"notification not posted (type={ntype or '?'})")
        else:
            mention = os.environ.get("MM_OPERATOR_MENTION") or env_value("MM_OPERATOR_MENTION")
            text = defang(redact(f"\U0001f514 Claude Code ({PROJECT}) needs you: {message}"))
            tp = job.get("transcript_path") or ""
            at = os.path.getsize(tp) if tp and os.path.isfile(tp) else 0
            spool_append(f"{mention} " if mention else "", text, at)
            log(f"notification spooled ({ntype or '?'})")
    start_watchdog()             # before the lock: from here on the worker is dead by the wall
    os.makedirs(STATE_DIR, exist_ok=True)
    lock = os.path.join(STATE_DIR, f"{KEY}.lock")
    if not take_session_lock(lock):
        log(f"worker: session lock not acquired (event={event}); the next event drains")
        return
    try:
        Run(job, MM(token), redact).run()
    finally:
        drop_dir_lock(lock)


def seed(args: list[str]) -> None:
    """Start from now: each transcript's mark = its current end (see the module docstring)."""
    force = "--force" in args
    files: list[str] = []
    for a in (x for x in args if x != "--force"):
        if os.path.isdir(a):
            files += [os.path.join(a, n) for n in sorted(os.listdir(a)) if n.endswith(".jsonl")]
        elif os.path.isfile(a):
            files.append(a)
    done = skipped = 0
    for f in files:
        stem = re.sub(r"[^0-9a-z]", "", os.path.basename(f)[:-6].lower())
        key = stem[:8] if len(stem) >= 32 else stem[:32]
        if not key:
            continue
        if os.path.exists(state_path(key)) and not force:
            skipped += 1
            continue
        # "nothing old gets posted" includes the spool: its position moves to its newest item too
        names = spool_names(key)
        save_state({"path": norm_path(f), "offset": os.path.getsize(f),
                    "outbox_last": names[-1] if names else ""}, key)
        done += 1
    print(f"seeded {done} session mark(s) at the current end of their transcripts; "
          f"{skipped} already had a mark (use --force to reset them)")


def spawn() -> None:
    data = sys.stdin.buffer.read()
    if os.environ.get("MM_MIRROR_SYNC") == "1":
        p = subprocess.Popen([sys.executable, os.path.abspath(__file__), "worker"], stdin=subprocess.PIPE,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        p.communicate(data, timeout=MAX_SECS + 120)
        return
    kw: dict = {"stdin": subprocess.PIPE, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
                "close_fds": True, "cwd": ROOT}
    args = [python_exe(), os.path.abspath(__file__), "worker"]
    if os.name == "nt":
        base = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        try:
            p = subprocess.Popen(args, creationflags=base | subprocess.CREATE_BREAKAWAY_FROM_JOB, **kw)
        except OSError:      # the job forbids breakaway: still detached from the hook's console/pipes
            p = subprocess.Popen(args, creationflags=base, **kw)
    else:
        p = subprocess.Popen(args, start_new_session=True, **kw)
    try:
        p.stdin.write(data)
        p.stdin.close()
    except OSError:
        log("spawn: could not hand the hook JSON to the worker")


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        if mode == "spawn":
            spawn()
        elif mode == "worker":
            worker()
        elif mode == "watchdog":
            watchdog(int(sys.argv[2]), float(sys.argv[3]), sys.argv[4] if len(sys.argv) > 4 else "")
        elif mode == "seed":
            seed(sys.argv[2:])
        else:
            print(__doc__)
    except Exception as exc:  # noqa: BLE001 - never fail the hook
        log(f"{mode}: {type(exc).__name__}: {str(exc)[:200]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
