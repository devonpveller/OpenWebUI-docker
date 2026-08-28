#!/usr/bin/env python3
"""Safe self-restart for the claude-sessions bridge.

The bridge cannot be restarted from inside one of its own turns — stopping the Scheduled Task
kills the in-flight `claude -p` before its result posts. This watcher makes an in-chat
"restart the bridge" request safe: it is launched OUT-OF-TREE (its own one-shot Scheduled
Task, so ending the bridge task can't kill it), waits for the given claude.exe PIDs (the
requesting turn) to exit, then restarts the bridge task and posts the outcome to
#claude-sessions.

    pythonw.exe restart_bridge.py [claude_pid ...]

Steps: wait for PIDs (max 30 min) → 20 s grace (old bridge posts the final result) →
`schtasks /End` → kill lingering bridge pythonw by command line → `schtasks /Run` → verify a
fresh "bridge up" line in state/bridge.log → post confirmation (props.from_bridge, so it is
never re-ingested as operator input) → delete its own task. Everything is appended to
state/restart.log. Stdlib only, like the bridge itself.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
# Same resolution as bridge.py, so pointing this watcher at the other persona
# (BRIDGE_STATE_DIR + BRIDGE_TASK_NAME + BRIDGE_CHANNEL_ID) reads ITS log and beacon, not this one's.
STATE_DIR = os.environ.get("BRIDGE_STATE_DIR") or os.path.join(_HERE, "state")
RESTART_LOG = os.path.join(STATE_DIR, "restart.log")
BRIDGE_LOG = os.path.join(STATE_DIR, "bridge.log")
HEALTH_FILE = os.path.join(STATE_DIR, "health.json")
BRIDGE_TASK = os.environ.get("BRIDGE_TASK_NAME", "claude-sessions-bridge")
WATCHER_TASK = os.environ.get("BRIDGE_WATCHER_TASK_NAME", "claude-bridge-restart-once")
CHANNEL_ID = os.environ.get("BRIDGE_CHANNEL_ID", "6z9khgkdd7df9q454be6fimw1h")  # #claude-sessions
PID_WAIT_MAX = 1800
GRACE_SECONDS = 20
FLAGS = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW — no console flashes


def log(msg: str) -> None:
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(RESTART_LOG, "a", encoding="utf-8") as fh:
            fh.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except OSError:
        pass


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=120,
                          creationflags=FLAGS, check=False)


def pid_alive(pid: int) -> bool:
    out = run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"]).stdout or ""
    return f'"{pid}"' in out


def kill_bridge_processes() -> str:
    """Kill any pythonw/python still running THIS persona's bridge (task stop can leave the
    venv-launcher/interpreter pair behind — the README's 'kill pythonw if needed').

    Target the pid from THIS state dir's beacon, never a command-line match: the @bot-sysadmin
    persona runs the SAME `claude-sessions-bridge/bridge.py` with a different env, so matching
    the command line kills it too. That is exactly what happened on 2026-08-28 — a restart of
    #claude-sessions took the sysadmin bridge (lock 48292) down with it and left it down,
    because only the claude-sessions task gets started again below.
    """
    try:
        with open(HEALTH_FILE, "r", encoding="utf-8") as fh:
            pid = int(json.load(fh).get("pid") or 0)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return "no readable beacon — killed nothing (a stray pair is safer than the wrong persona)"
    if not pid:
        return "beacon carries no pid — killed nothing"
    # The venv launcher shim and its interpreter die as a pair, so the beacon's pid is enough.
    ps = (f"$p = Get-CimInstance Win32_Process -Filter \"ProcessId={pid}\" -ErrorAction "
          "SilentlyContinue; if ($p -and $p.CommandLine -match 'bridge\\.py') "
          f"{{ Stop-Process -Id {pid} -Force; \"killed {pid}\" }}")
    r = run(["powershell", "-NoProfile", "-Command", ps])
    return (r.stdout or "").strip() or "none lingering"


def fresh_bridge_up_since(t0: float) -> bool:
    try:
        size = os.path.getsize(BRIDGE_LOG)
        with open(BRIDGE_LOG, "r", encoding="utf-8", errors="ignore") as fh:
            fh.seek(max(0, size - 8000))
            tail = fh.read()
    except OSError:
        return False
    for line in tail.splitlines():
        if "bridge up" not in line or not line.startswith("["):
            continue
        try:
            ts = time.mktime(time.strptime(line[1:20], "%Y-%m-%d %H:%M:%S"))
        except ValueError:
            continue
        if ts >= t0 - 5:
            return True
    return False


def post_to_channel(message: str) -> None:
    try:
        sys.path.insert(0, os.path.join(_HERE, "..", "mattermost-mcp"))
        import server as mmapi  # noqa: PLC0415 - deferred: MM may be reachable only by now
        mmapi._api("POST", "/posts", {"channel_id": CHANNEL_ID, "message": message,
                                      "props": {"from_bridge": True}})
    except Exception as e:  # noqa: BLE001 - confirmation is best-effort; the log is the record
        log(f"mattermost post failed: {e}")


def main() -> None:
    pids = [int(a) for a in sys.argv[1:] if a.isdigit()]
    log(f"watcher started — waiting for claude pid(s) {pids or 'none'} to exit")
    deadline = time.time() + PID_WAIT_MAX
    while pids and any(pid_alive(p) for p in pids) and time.time() < deadline:
        time.sleep(5)
    if pids and time.time() >= deadline:
        log(f"pid wait hit {PID_WAIT_MAX}s — restarting anyway (in-flight turn will be killed)")
    time.sleep(GRACE_SECONDS)

    log("stopping bridge task")
    r = run(["schtasks", "/End", "/TN", BRIDGE_TASK])
    log(f"schtasks /End rc={r.returncode} {(r.stdout or r.stderr or '').strip()}")
    time.sleep(5)
    log(f"lingering processes: {kill_bridge_processes()}")
    time.sleep(3)

    t0 = time.time()
    r = run(["schtasks", "/Run", "/TN", BRIDGE_TASK])
    log(f"schtasks /Run rc={r.returncode} {(r.stdout or r.stderr or '').strip()}")

    ok = False
    for _ in range(36):  # up to 3 min — the bridge logs 'bridge up' once Mattermost answers
        time.sleep(5)
        if fresh_bridge_up_since(t0):
            ok = True
            break
    log(f"verify: fresh 'bridge up' line = {ok}")

    if ok:
        # The reason travels in BRIDGE_RESTART_NOTE — a hard-coded blurb here describes whatever
        # change prompted the FIRST restart and misreports every one after it.
        note = os.environ.get("BRIDGE_RESTART_NOTE", "").strip()
        post_to_channel("🔁 **Bridge restarted** — running the current `bridge.py`."
                        + (f" {note}" if note else ""))
    else:
        post_to_channel(f"⚠️ **Bridge restart attempted but not verified** — no fresh startup "
                        f"line in `state/bridge.log` within 3 min. Check `state/restart.log`, "
                        f"then `Start-ScheduledTask -TaskName \"{BRIDGE_TASK}\"` manually.")

    run(["schtasks", "/Delete", "/TN", WATCHER_TASK, "/F"])
    log("watcher done")


if __name__ == "__main__":
    main()
