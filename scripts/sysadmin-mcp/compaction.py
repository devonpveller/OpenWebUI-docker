#!/usr/bin/env python3
"""Elevated vhdx-compaction capability for sysadmin-mcp — the GATED, downtime-bearing action.

The MCP process is non-elevated; the actual compaction runs as a pre-registered RunLevel=Highest
Scheduled Task (compact-vhdx.ps1, registered once via register-sysadmin-tasks.ps1). This module:

  • compact_plan()    READ-ONLY — is compaction warranted (trapped GB), is the task registered,
                      C: free now, + a confirm_token.
  • compact_execute() GATED     — refuses without a plan-bound confirm_token, refuses if the task
                      isn't registered or compaction isn't warranted, else triggers the task
                      (`schtasks /run`) and returns immediately ("started; poll compact_status").
  • compact_status()  READ-ONLY — reads the task's JSON result file (progress/notes/outcome).

Async by design: a compaction takes 10-15 min + stack re-return, so execute never blocks — the
persona triggers, then narrates progress from compact_status. Belt-and-suspenders gating:
bridge approval relay (human) + token + task-registered + warranted (all deterministic here).
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import sysadmin as sa  # noqa: E402

TASK_NAME = "AI-Stack Sysadmin Compact VHDX"
_STATE = os.path.join(_HERE, "state")
_RESULT = os.path.join(_STATE, "compact-result.json")
_AUDIT = os.path.join(_STATE, "sysadmin-audit.jsonl")


def _audit(event: str, detail: dict) -> None:
    try:
        os.makedirs(_STATE, exist_ok=True)
        with open(_AUDIT, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": int(time.time()), "event": event, "detail": detail}) + "\n")
    except Exception:  # noqa: BLE001
        pass


def task_registered() -> bool:
    r = sa._run(["schtasks", "/query", "/tn", TASK_NAME], timeout=20)
    return r["rc"] == 0


def _min_trapped_gb() -> float:
    return float(sa.load_config()["thresholds"].get("vhdx_trapped_warn_gb", 60))


def _plan_token(warranted: bool, registered: bool) -> str:
    canon = json.dumps({"action": "compact", "warranted": warranted, "registered": registered},
                       sort_keys=True)
    return hashlib.sha1(canon.encode()).hexdigest()[:8]  # noqa: S324 - non-crypto gate token


def compact_plan() -> dict:
    vh = sa._vhdx_trapped()
    trapped = vh.get("trapped_gb")
    min_gb = _min_trapped_gb()
    warranted = isinstance(trapped, (int, float)) and trapped >= min_gb
    reg = task_registered()
    c_free = sa._drive_free(sa.load_config()["system_drive"]).get("free_gb")
    return {
        "vhdx": vh,
        "c_free_gb": c_free,
        "trapped_gb": trapped,
        "min_trapped_gb": min_gb,
        "warranted": warranted,
        "task_registered": reg,
        "confirm_token": _plan_token(bool(warranted), reg),
        "note": (
            ("Compaction WARRANTED. " if warranted else f"NOT warranted (trapped {trapped} < {min_gb} GB). ")
            + ("Task registered — call compact_execute(confirm_token) to run (brief full-Docker downtime). "
               if reg else
               "Task NOT registered — run register-sysadmin-tasks.ps1 elevated once, then retry. ")
            + "Estimated downtime ~10-15 min; stack auto-recovers and is verified."
        ),
    }


def compact_execute(confirm_token: str | None = None) -> dict:
    plan = compact_plan()
    if not confirm_token or confirm_token != plan["confirm_token"]:
        _audit("compact_refused", {"reason": "bad_token", "given": confirm_token, "expected": plan["confirm_token"]})
        return {"refused": True, "reason": "missing or stale confirm_token; review the fresh plan and retry",
                "current_token": plan["confirm_token"], "plan": plan}
    if not plan["task_registered"]:
        _audit("compact_refused", {"reason": "task_not_registered"})
        return {"refused": True, "reason": "elevated task not registered; run register-sysadmin-tasks.ps1 elevated once",
                "plan": plan}
    if not plan["warranted"]:
        _audit("compact_refused", {"reason": "not_warranted", "trapped": plan["trapped_gb"]})
        return {"refused": True, "reason": f"compaction not warranted (trapped {plan['trapped_gb']} < {plan['min_trapped_gb']} GB)",
                "plan": plan}

    # clear the stale result so compact_status reflects THIS run
    try:
        if os.path.exists(_RESULT):
            os.remove(_RESULT)
    except OSError:
        pass
    run = sa._run(["schtasks", "/run", "/tn", TASK_NAME], timeout=30)
    if run["rc"] != 0:
        _audit("compact_trigger_failed", {"err": run["err"].strip()})
        return {"refused": False, "started": False, "error": run["err"].strip() or "schtasks /run failed"}
    _audit("compact_started", {"trapped_gb": plan["trapped_gb"], "c_free_gb": plan["c_free_gb"]})
    return {"refused": False, "started": True,
            "message": ("Compaction started as elevated task. Docker will go down ~10-15 min and "
                        "auto-recover. Poll compact_status for progress/outcome."),
            "trapped_gb": plan["trapped_gb"]}


def compact_status() -> dict:
    if not os.path.exists(_RESULT):
        running = task_registered() and _task_running()
        return {"state": "running" if running else "idle",
                "note": "no result yet" + (" (task running)" if running else " (no run recorded)")}
    try:
        # utf-8-sig: PowerShell 5.1's Set-Content -Encoding utf8 writes a BOM that plain utf-8
        # json.load would choke on (this silently blinded compact_status to a finished run).
        with open(_RESULT, "r", encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except Exception as e:  # noqa: BLE001
        return {"state": "unknown", "error": f"cannot read result: {e}"}
    finished = bool(data.get("finished")) and data.get("ok") is not None and (
        data.get("reclaimed_gb") is not None or data.get("error"))
    data["state"] = "finished" if (data.get("ok") is True) else (
        "error" if data.get("error") else ("finished" if finished else "running"))
    return data


def _task_running() -> bool:
    r = sa._run(["schtasks", "/query", "/tn", TASK_NAME, "/fo", "LIST"], timeout=20)
    return r["rc"] == 0 and "Running" in r["out"]
