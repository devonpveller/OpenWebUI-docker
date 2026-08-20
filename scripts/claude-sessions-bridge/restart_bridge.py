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

import os
import subprocess
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(_HERE, "state")
RESTART_LOG = os.path.join(STATE_DIR, "restart.log")
BRIDGE_LOG = os.path.join(STATE_DIR, "bridge.log")
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
    """Kill any pythonw/python still running this bridge's bridge.py (task stop can leave the
    venv-launcher/interpreter pair behind — the README's 'kill pythonw if needed')."""
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe' or Name='python.exe'\" | "
          "Where-Object { $_.CommandLine -match 'claude-sessions-bridge.bridge\\.py' } | "
          "ForEach-Object { Stop-Process -Id $_.ProcessId -Force; \"killed $($_.ProcessId)\" }")
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
        post_to_channel("🔁 **Bridge restarted** — follow/auto-wake support is now live "
                        "(`follow_thread`/`unfollow`/`list_follows` session tools, `follows` / "
                        "`unfollow <id>` operator commands). Post `follows` to check the registry.")
    else:
        post_to_channel(f"⚠️ **Bridge restart attempted but not verified** — no fresh startup "
                        f"line in `state/bridge.log` within 3 min. Check `state/restart.log`, "
                        f"then `Start-ScheduledTask -TaskName \"{BRIDGE_TASK}\"` manually.")

    run(["schtasks", "/Delete", "/TN", WATCHER_TASK, "/F"])
    log("watcher done")


if __name__ == "__main__":
    main()
