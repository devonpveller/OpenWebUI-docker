#!/usr/bin/env python3
"""Tests for the elevated compaction capability. Stdlib only. NON-DESTRUCTIVE by design.

Run:  python scripts/sysadmin-mcp/test_compaction.py

Proves (without ever compacting or triggering the task):
  • compaction.py contains no volume/destructive verbs; the elevated .ps1 never prunes volumes.
  • compact_plan is well-formed with a deterministic confirm_token.
  • compact_execute is fail-closed: bad token, unregistered task, and not-warranted all REFUSE,
    and the refusal path does not invoke schtasks /run (monitored via a patched runner).
  • compact_status is readable with no result file present.
"""

from __future__ import annotations

import io
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import compaction as cp  # noqa: E402
import sysadmin as sa  # noqa: E402

_passed = 0
_failed = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  PASS  {name}")
    else:
        _failed += 1
        print(f"  FAIL  {name}  {detail}")


def test_source_guard() -> None:
    print("SOURCE GUARD - no volume/destructive verbs")
    py = io.open(os.path.join(_HERE, "compaction.py"), encoding="utf-8").read().lower()
    for f in ("volume prune", "volume rm", "docker rm ", "rmi ", "system prune", "rm -rf"):
        check(f"compaction.py absent: {f!r}", f not in py)
    ps = io.open(os.path.join(_HERE, "compact-vhdx.ps1"), encoding="utf-8").read().lower()
    for f in ("volume prune", "volume rm", "docker rm ", "remove-item"):
        check(f"compact-vhdx.ps1 absent: {f!r}", f not in ps)
    # the elevated script SHOULD pause + re-arm the watchdog (safety invariant)
    check("ps re-arms watchdog", "enable-scheduledtask" in ps and "disable-scheduledtask" in ps)
    check("ps verifies stack return", "stack_returned" in ps and "pre_running" in ps)

    # ORDERING, not just presence. fstrim is only useful while the docker-desktop distro is UP and
    # the disk mounted; after `wsl --shutdown` it cannot run at all, and Optimize-VHD would again
    # reclaim only what Docker Desktop happened to have trimmed (9.9 of 54.4 GB on 2026-09-13).
    # A later edit that moves the trim below the shutdown would still "contain fstrim" -- hence
    # comparing positions rather than membership.
    # Match CODE forms, not prose: the .DESCRIPTION header names both `wsl --shutdown` and
    # Optimize-VHD, so a bare .find() reports the comment's position and the ordering check
    # silently measures nothing. These three strings appear only in the executable body.
    i_trim = ps.find("/sbin/fstrim")
    i_down = ps.find("wsl --shutdown 2>&1")
    i_opt = ps.find("optimize-vhd -path")
    check("ps trims before compacting", i_trim != -1 and i_opt != -1 and i_trim < i_opt,
          f"fstrim@{i_trim} optimize@{i_opt}")
    check("ps trims BEFORE wsl --shutdown (else it cannot run)",
          i_trim != -1 and i_down != -1 and i_trim < i_down, f"fstrim@{i_trim} shutdown@{i_down}")
    check("ps records the trim outcome for the operator", "fstrim_ok" in ps)
    # the shortfall judgement must be reachable from the script, not just defined in the lib
    check("ps consults the reclaim verdict", "get-reclaimverdict" in ps)
    check("ps records trapped as a field", "trapped_before_gb" in ps)
    # The lib is dot-sourced into an ELEVATED script, so it must not act. Checked against call
    # forms only -- its reason strings legitimately mention Optimize-VHD, fstrim and Docker.
    lib = io.open(os.path.join(_HERE, "compact-lib.ps1"), encoding="utf-8").read().lower()
    for verb in ("remove-item", "set-content", "start-process", "invoke-expression",
                 "optimize-vhd -", "schtasks", "stop-process"):
        check(f"compact-lib.ps1 does not call {verb!r}", verb not in lib)


def test_plan() -> None:
    print("PLAN - read-only compact_plan")
    p = cp.compact_plan()
    check("has confirm_token", isinstance(p.get("confirm_token"), str) and len(p["confirm_token"]) == 8)
    check("has warranted flag", isinstance(p.get("warranted"), bool))
    check("has task_registered flag", isinstance(p.get("task_registered"), bool))
    check("token deterministic", cp.compact_plan()["confirm_token"] == p["confirm_token"])
    print(f"    (trapped={p.get('trapped_gb')}GB, min={p.get('min_trapped_gb')}GB, "
          f"warranted={p.get('warranted')}, registered={p.get('task_registered')}, token={p['confirm_token']})")


def test_gate_failclosed() -> None:
    print("GATE - compact_execute fail-closed, never triggers schtasks on refusal")
    # monitor: patch the module's runner so any schtasks /run during a refusal is caught
    triggered = {"run": False}
    orig = sa._run

    def spy(cmd, timeout=30):
        if cmd[:2] == ["schtasks", "/run"]:
            triggered["run"] = True
            return {"rc": 0, "out": "", "err": ""}  # pretend success but record it
        return orig(cmd, timeout)

    cp.sa._run = spy  # compaction.py calls sa._run via the imported module
    try:
        r1 = cp.compact_execute(None)
        check("no token -> refused", r1.get("refused") is True, str(r1)[:160])
        r2 = cp.compact_execute("deadbeef")
        check("wrong token -> refused", r2.get("refused") is True, str(r2)[:160])
        check("NO schtasks /run triggered on a BAD token (fail-closed)", triggered["run"] is False,
              "schtasks /run was invoked on a refusal path!")
        # A VALID token's behaviour depends on the machine state (robust to either):
        #  - task registered AND warranted  -> it should trigger (spy intercepts; no real compaction)
        #  - otherwise                      -> it should refuse
        plan = cp.compact_plan()
        r3 = cp.compact_execute(plan["confirm_token"])
        if plan["task_registered"] and plan["warranted"]:
            check("valid token + armed + warranted -> triggers (spy caught it; no real compaction)",
                  triggered["run"] is True and not r3.get("refused"), str(r3)[:200])
        else:
            check("valid token but not-armed/not-warranted -> refused",
                  r3.get("refused") is True and triggered["run"] is False, str(r3)[:200])
    finally:
        cp.sa._run = orig


def test_status() -> None:
    print("STATUS - compact_status readable")
    s = cp.compact_status()
    check("status has state", "state" in s, str(s)[:160])


def test_status_bom() -> None:
    print("STATUS BOM - compact_status tolerates a UTF-8 BOM result file (PS 5.1 Set-Content)")
    import json as _json
    import shutil
    rf = cp._RESULT
    bak = rf + ".testbak"
    had = os.path.exists(rf)
    if had:
        shutil.copy2(rf, bak)
    try:
        os.makedirs(os.path.dirname(rf), exist_ok=True)
        payload = {"ok": True, "finished": "2026-01-01T00:00:00", "reclaimed_gb": 12.3,
                   "vhdx_before_gb": 300.0, "vhdx_after_gb": 287.7, "stack_returned": True,
                   "pre_running": 87, "post_running": 87, "notes": ["done"]}
        with open(rf, "w", encoding="utf-8-sig") as fh:  # utf-8-sig == leading BOM, like PS 5.1
            _json.dump(payload, fh)
        s = cp.compact_status()
        check("BOM result parses -> finished", s.get("state") == "finished" and s.get("ok") is True,
              str(s)[:160])
    finally:
        if had:
            shutil.move(bak, rf)
        elif os.path.exists(rf):
            os.remove(rf)


def test_threshold_split() -> None:
    """The ACT threshold must be its own number, not the WARN one.

    Reusing vhdx_trapped_warn_gb as the refusal gate meant that on 2026-09-13, with 47.8 GB
    trapped, disk_report said HEALTHY *and* compact_execute hard-refused -- one number doing two
    jobs, and 47.8 GB unreachable by either.
    """
    print("THRESHOLD SPLIT - warn (mention it) is not act (refuse below it)")
    real = sa.load_config
    try:
        sa.load_config = lambda: {"thresholds": {"vhdx_trapped_warn_gb": 60, "vhdx_compact_min_gb": 20}}
        check("act threshold is read from vhdx_compact_min_gb", cp._min_trapped_gb() == 20.0,
              str(cp._min_trapped_gb()))
        # the live 2026-09-13 figure: warranted under the split, refused before it
        sa.load_config = lambda: {"thresholds": {"vhdx_trapped_warn_gb": 60, "vhdx_compact_min_gb": 20}}
        check("47.8 GB trapped is actionable under the split", 47.8 >= cp._min_trapped_gb())
        sa.load_config = lambda: {"thresholds": {"vhdx_trapped_warn_gb": 60}}
        check("falls back to the warn number when act is absent", cp._min_trapped_gb() == 60.0,
              str(cp._min_trapped_gb()))
        check("  ... and 47.8 GB was indeed refused by that old behaviour", 47.8 < cp._min_trapped_gb())
    finally:
        sa.load_config = real


def test_status_shortfall() -> None:
    """A run that returned a fraction of its measured target must NOT read as success."""
    print("STATUS SHORTFALL - compact_status surfaces an under-reclaim")
    import json as _json
    import shutil
    import server as srv
    rf = cp._RESULT
    bak = rf + ".testbak"
    had = os.path.exists(rf)
    if had:
        shutil.copy2(rf, bak)
    try:
        os.makedirs(os.path.dirname(rf), exist_ok=True)
        # The REAL 2026-09-13T03:15 numbers, plus what the fixed script now records.
        payload = {"ok": False, "finished": "2026-09-13T03:30:40", "reclaimed_gb": 9.9,
                   "vhdx_before_gb": 247.0, "vhdx_after_gb": 237.1,
                   "trapped_before_gb": 54.4, "shortfall_gb": 44.5,
                   "fstrim_ok": False, "fstrim_note": "exit 1 : no such file",
                   "stack_returned": True, "pre_running": 81, "post_running": 81,
                   "error": "compaction UNDER-RECLAIMED: returned 9.9 GB of 54.4 GB trapped",
                   "notes": ["done"]}
        with open(rf, "w", encoding="utf-8") as fh:
            _json.dump(payload, fh)
        s = cp.compact_status()
        check("under-reclaim is state=error, not finished", s.get("state") == "error", str(s.get("state")))
        rendered = srv.render_compact_status(s)
        check("render shows reclaimed AGAINST the target", "of 54.4 GB trapped" in rendered, rendered[:200])
        check("render shows the shortfall", "44.5" in rendered, rendered[:200])
        check("render shows the failed fstrim", "fstrim: False" in rendered, rendered[:200])
    finally:
        if had:
            shutil.move(bak, rf)
        elif os.path.exists(rf):
            os.remove(rf)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    test_source_guard()
    test_plan()
    test_gate_failclosed()
    test_status()
    test_status_bom()
    test_threshold_split()
    test_status_shortfall()
    print(f"\n{_passed} passed, {_failed} failed")
    sys.exit(1 if _failed else 0)
