#!/usr/bin/env python3
"""Out-of-band Telegram COMMAND listener for the ai-stack sysadmin channel.

The break-glass control channel. Runs as a HOST process (Scheduled Task), so it
survives a full Docker-down -- the exact window when Mattermost (a container) is
gone and the operator has no other way to reach the box. It long-polls the
Telegram Bot API (pure HTTPS, no Docker dependency) and executes a STRICT
whitelist of recovery commands, only for messages from the configured operator
chat_id.

Security model (a remote-command path into the host -- non-negotiable):
  * Only messages whose chat.id == SYSADMIN_TELEGRAM_CHAT_ID are honored; every
    other sender is logged and ignored.
  * Command WHITELIST only -- the message text is never exec/eval'd.
  * Destructive commands (nuclear, gpu-reset) require a typed confirmation.
  * Every honored command is appended to the sysadmin audit log.
  * Exactly ONE poller: a single-instance lock (127.0.0.1:48293) prevents two
    getUpdates loops fighting over updates (the dropped-update / WinError-10055
    class we hit on the Mattermost MCP).

Commands:
  status          daemon up? running-container count, C: free, last compaction
  docker up       start the Docker engine (docker desktop start) + wait
  mattermost / mm bring up ONLY Mattermost + its DB, then confirm the #claude-sessions
                  bridge -- fast, safe path to a Claude session (vs a full recover)
  recover         scripts/recovery/emergency-recovery.ps1 recover  (ordered restart)
  compact status  last vhdx-compaction result
  gpu-reset       (confirm) scripts/recovery/emergency-recovery.ps1 gpu-reset
  nuclear         (confirm) scripts/recovery/emergency-recovery.ps1 nuclear
  help            list commands

Stdlib only; long commands run on a worker thread so the poll loop stays live.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(_HERE))
_STATE = os.path.join(_HERE, "telegram-state")
_OFFSET_FILE = os.path.join(_STATE, "offset.txt")
_AUDIT = os.path.join(_STATE, "audit.jsonl")
_COMPACT_RESULT = os.path.join(_HERE, "state", "compact-result.json")
_LOCK_PORT = 48293  # 48291 = claude-sessions bridge, 48292 = sysadmin bridge

sys.path.insert(0, _HERE)
import telegram_notify as tn  # noqa: E402  (shares the .env creds + send())

_DOCKER = shutil.which("docker") or r"C:\Program Files\Docker\Docker\resources\bin\docker.exe"
_PWSH = shutil.which("powershell") or r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
_RECOVERY = os.path.join(_REPO, "scripts", "recovery", "emergency-recovery.ps1")
# agent-org compose holds Mattermost; invoked with -f only (project name resolves from the
# file/.env), matching emergency-recovery.ps1's proven invocation.
_AGENT_ORG_COMPOSE = os.path.join(_REPO, "agent-org", "docker", "docker-compose.yml")
_CLAUDE_BRIDGE_TASK = "claude-sessions-bridge"  # host Scheduled Task serving #claude-sessions
_CLAUDE_BRIDGE_PORT = 48291                     # its single-instance lock port (liveness proxy)

# a pending destructive confirmation: {"action": "nuclear", "expires": <ts>}
_pending: dict = {}
_pending_lock = threading.Lock()
_CONFIRM_WINDOW_SEC = 90


# ------------------------------------------------------------------ infra ----
def _log(event: str, detail: dict) -> None:
    try:
        os.makedirs(_STATE, exist_ok=True)
        with open(_AUDIT, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": int(time.time()), "event": event, "detail": detail}) + "\n")
    except Exception:  # noqa: BLE001
        pass


def _acquire_lock() -> socket.socket | None:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", _LOCK_PORT))
        s.listen(1)
        return s  # keep it alive for the process lifetime
    except OSError:
        s.close()
        return None


def _read_offset() -> int | None:
    try:
        with open(_OFFSET_FILE, "r", encoding="utf-8") as fh:
            return int(fh.read().strip())
    except (OSError, ValueError):
        return None


def _write_offset(update_id: int) -> None:
    try:
        os.makedirs(_STATE, exist_ok=True)
        with open(_OFFSET_FILE, "w", encoding="utf-8") as fh:
            fh.write(str(update_id))
    except OSError:
        pass


def _api(method: str, params: dict | None = None, timeout: int = 60) -> dict | None:
    tok, _ = tn.creds()
    if not tok:
        return None
    url = f"https://api.telegram.org/bot{tok}/{method}"
    data = urllib.parse.urlencode(params or {}).encode()
    try:
        req = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception:  # noqa: BLE001
        return None


def _reply(text: str) -> None:
    tn.send(text)


def _run(cmd: list[str], timeout: int) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        out = ((p.stdout or "") + (p.stderr or "")).strip()
        return p.returncode, out
    except subprocess.TimeoutExpired:
        return 124, f"(timed out after {timeout}s)"
    except Exception as e:  # noqa: BLE001
        return 1, f"({e})"


def _port_in_use(port: int) -> bool:
    """True if 127.0.0.1:port is already bound (a bridge/listener holds it).
    Uses a PASSIVE bind probe (no SO_REUSEADDR), NOT connect(): the bridge lock
    sockets are never accept()ed, so connect-probes pile up in the backlog and
    then time out -> false 'down'. A bind attempt just asks the OS 'is this taken?'
    without touching the socket. bind fails (EADDRINUSE) => held => alive."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", port))
        return False  # bind succeeded -> nothing holds it -> down
    except OSError:
        return True   # in use -> alive
    finally:
        s.close()


# --------------------------------------------------------------- handlers ----
def _daemon_up() -> bool:
    rc, _ = _run([_DOCKER, "version", "--format", "{{.Server.Version}}"], timeout=15)
    return rc == 0


def _handle_status() -> str:
    up = _daemon_up()
    lines = [f"docker engine: {'UP' if up else 'DOWN'}"]
    if up:
        rc, out = _run([_DOCKER, "ps", "-q"], timeout=20)
        n = len([x for x in out.splitlines() if x.strip()]) if rc == 0 else "?"
        lines.append(f"running containers: {n}")
    try:
        free_gb = round(shutil.disk_usage("C:\\").free / 1_000_000_000, 1)
        lines.append(f"C: free: {free_gb} GB")
    except Exception:  # noqa: BLE001
        pass
    lines.append(_compact_summary())
    return "\n".join(lines)


def _compact_summary() -> str:
    try:
        with open(_COMPACT_RESULT, "r", encoding="utf-8-sig") as fh:
            d = json.load(fh)
    except Exception:  # noqa: BLE001
        return "last compaction: (no record)"
    if d.get("ok"):
        return (f"last compaction: OK, reclaimed {d.get('reclaimed_gb')} GB, "
                f"stack {d.get('post_running')}/{d.get('pre_running')} back "
                f"({d.get('finished')})")
    if d.get("error"):
        return f"last compaction: ERROR - {d.get('error')}"
    return "last compaction: in progress / unknown"


def _handle_docker_up() -> None:
    _reply("starting the Docker engine...")
    _run([_DOCKER, "desktop", "start"], timeout=60)
    for _ in range(24):  # up to ~120s
        if _daemon_up():
            _reply("Docker engine is UP. Send 'status' to see container recovery, "
                   "or 'recover' if containers don't return.")
            return
        time.sleep(5)
    _reply("Docker engine did NOT come up within 120s. Try 'recover', or a manual "
           "Docker Desktop quit/restart or host reboot.")


def _handle_recover() -> None:
    if not os.path.exists(_RECOVERY):
        _reply(f"recovery script not found at {_RECOVERY}")
        return
    _reply("running emergency-recovery.ps1 recover (ordered restart, this takes a few minutes)...")
    rc, out = _run([_PWSH, "-NoProfile", "-ExecutionPolicy", "Bypass",
                    "-File", _RECOVERY, "recover"], timeout=1200)
    tail = "\n".join(out.splitlines()[-15:]) if out else "(no output)"
    _reply(f"recover finished (exit {rc}).\n{tail}\n\nSend 'status' to confirm.")


def _mm_health() -> str:
    rc, out = _run([_DOCKER, "inspect", "-f", "{{.State.Health.Status}}", "mattermost"], timeout=15)
    return out.strip() if rc == 0 and out.strip() else "absent"


def _handle_mattermost() -> None:
    """Targeted fast path to a Claude session: bring up ONLY Mattermost + its DB (not the
    whole stack), then confirm the host claude-sessions bridge so #claude-sessions is live.
    Much lighter and safer than `recover` when the rest of the stack is fine."""
    # 1) engine must be up first
    if not _daemon_up():
        _reply("Docker engine is down -- starting it first...")
        _run([_DOCKER, "desktop", "start"], timeout=60)
        ok = False
        for _ in range(24):  # ~120s
            if _daemon_up():
                ok = True
                break
            time.sleep(5)
        if not ok:
            _reply("Could not start the Docker engine. Try 'docker up' again, or 'recover'.")
            return
    # 2) bring up Mattermost ONLY if it isn't already healthy -- never bounce a
    #    working MM (docker compose up -d would recreate it on any config drift).
    health = _mm_health()
    if health == "healthy":
        _reply("Mattermost is already healthy -- not touching it; confirming the bridge...")
    else:
        if not os.path.exists(_AGENT_ORG_COMPOSE):
            _reply(f"agent-org compose not found at {_AGENT_ORG_COMPOSE}")
            return
        _reply(f"Mattermost health='{health}'; bringing up mattermost-db + mattermost only "
               "(leaving the rest of the stack untouched)...")
        rc, out = _run([_DOCKER, "compose", "-f", _AGENT_ORG_COMPOSE, "up", "-d",
                        "mattermost-db", "mattermost"], timeout=180)
        if rc != 0:
            tail = "\n".join(out.splitlines()[-8:]) if out else "(no output)"
            _reply(f"compose up failed (exit {rc}):\n{tail}\n\nTry 'recover'.")
            return
        # 3) wait for Mattermost to report healthy
        health = "starting"
        for _ in range(24):  # ~120s
            health = _mm_health()
            if health == "healthy":
                break
            time.sleep(5)
    # 4) ensure the #claude-sessions bridge (host task) is alive. Passive bind probe
    #    (see _port_in_use). If genuinely down, END then RUN to clear a wedged
    #    'Running' instance (a bare `schtasks /run` no-ops on an already-Running task).
    bridge = _port_in_use(_CLAUDE_BRIDGE_PORT)
    if not bridge:
        _run(["schtasks", "/end", "/tn", _CLAUDE_BRIDGE_TASK], timeout=15)
        time.sleep(2)
        _run(["schtasks", "/run", "/tn", _CLAUDE_BRIDGE_TASK], timeout=30)
        for _ in range(8):  # ~40s
            time.sleep(5)
            if _port_in_use(_CLAUDE_BRIDGE_PORT):
                bridge = True
                break
    # 5) report
    if health == "healthy" and bridge:
        _reply("Mattermost is HEALTHY and the #claude-sessions bridge is up. Open the app -- your Claude session is ready.")
    elif health == "healthy":
        _reply("Mattermost is HEALTHY, but the #claude-sessions bridge isn't listening yet. The watchdog restarts it within ~10 min; give it a minute then open the app.")
    else:
        _reply(f"Mattermost brought up but health='{health}' (still starting or unhealthy). Wait a minute and send 'status'; if it won't go healthy, try 'recover'.")


def _handle_destructive(action: str) -> None:
    if not os.path.exists(_RECOVERY):
        _reply(f"recovery script not found at {_RECOVERY}")
        return
    _reply(f"running emergency-recovery.ps1 {action} (this is heavy)...")
    rc, out = _run([_PWSH, "-NoProfile", "-ExecutionPolicy", "Bypass",
                    "-File", _RECOVERY, action], timeout=1800)
    tail = "\n".join(out.splitlines()[-15:]) if out else "(no output)"
    _reply(f"{action} finished (exit {rc}).\n{tail}\n\nSend 'status' to confirm.")


_HELP = (
    "ai-stack sysadmin - commands:\n"
    "  status          engine + container count + C: free + last compaction\n"
    "  docker up       start the Docker engine\n"
    "  mattermost / mm bring up ONLY Mattermost + its DB (fast path to a Claude session)\n"
    "  recover         ordered stack restart (emergency-recovery recover)\n"
    "  compact status  last vhdx-compaction result\n"
    "  gpu-reset       (asks to confirm) GPU/container reset\n"
    "  nuclear         (asks to confirm) full down + up\n"
    "  help            this list"
)


def _spawn(fn, *a) -> None:
    threading.Thread(target=fn, args=a, daemon=True).start()


def _dispatch(text: str) -> None:
    cmd = " ".join(text.lower().split())  # normalize whitespace

    # confirmation flow for destructive actions
    if cmd.startswith("confirm "):
        want = cmd.split(" ", 1)[1].strip()
        with _pending_lock:
            p = dict(_pending)
            _pending.clear()
        if not p or p.get("action") != want:
            _reply(f"nothing pending to confirm for '{want}'.")
            return
        if time.time() > p.get("expires", 0):
            _reply(f"confirmation for '{want}' expired; re-send the command.")
            return
        _log("cmd_confirmed", {"action": want})
        _spawn(_handle_destructive, want)
        return

    if cmd in ("help", "/help", "/start", "start", "?"):
        _reply(_HELP)
    elif cmd in ("status", "/status"):
        _reply(_handle_status())
    elif cmd in ("docker up", "docker start", "start docker", "engine up", "up"):
        _spawn(_handle_docker_up)
    elif cmd in ("mattermost", "mm", "/mattermost"):
        _spawn(_handle_mattermost)
    elif cmd in ("recover", "/recover"):
        _spawn(_handle_recover)
    elif cmd in ("compact status", "compaction status", "compact", "/compact"):
        _reply(_compact_summary())
    elif cmd in ("nuclear", "gpu-reset", "gpu reset"):
        action = "gpu-reset" if "gpu" in cmd else "nuclear"
        with _pending_lock:
            _pending.clear()
            _pending.update({"action": action, "expires": time.time() + _CONFIRM_WINDOW_SEC})
        _reply(f"'{action}' is destructive. Reply `confirm {action}` within "
               f"{_CONFIRM_WINDOW_SEC}s to proceed, or ignore to cancel.")
    else:
        _reply(f"unknown command: '{text.strip()}'. Send 'help'.")


# ------------------------------------------------------------------- loop ----
def main() -> int:
    lock = _acquire_lock()
    if lock is None:
        print(f"another telegram_listener already holds 127.0.0.1:{_LOCK_PORT}; exiting")
        return 0

    tok, cid = tn.creds()
    if not tok or not cid:
        print("SYSADMIN_TELEGRAM_BOT_TOKEN / SYSADMIN_TELEGRAM_CHAT_ID not set in .env; exiting")
        return 1
    operator_id = str(cid).strip()

    # drop any webhook so getUpdates long-polling won't 409
    _api("deleteWebhook", {"drop_pending_updates": "false"}, timeout=15)
    _log("listener_start", {"lock_port": _LOCK_PORT})
    print(f"telegram_listener up (operator chat_id={operator_id}); long-polling...")

    offset = _read_offset()
    while True:
        params = {"timeout": 50}
        if offset is not None:
            params["offset"] = offset
        resp = _api("getUpdates", params, timeout=70)
        if not resp or not resp.get("ok"):
            time.sleep(5)  # network/API hiccup (e.g. host offline) -> back off
            continue
        for u in resp.get("result", []):
            offset = u["update_id"] + 1
            _write_offset(offset)
            msg = u.get("message") or u.get("edited_message") or {}
            chat = msg.get("chat") or {}
            text = msg.get("text")
            if not text:
                continue
            sender = str(chat.get("id"))
            if sender != operator_id:
                _log("ignored_foreign", {"from": sender, "text": text[:80]})
                continue
            _log("cmd", {"text": text[:200]})
            try:
                _dispatch(text)
            except Exception as e:  # noqa: BLE001 - one bad command must not kill the loop
                _log("dispatch_error", {"err": str(e)})
                _reply(f"command failed: {e}")


if __name__ == "__main__":
    sys.exit(main())
