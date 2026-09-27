#!/usr/bin/env python3
"""The AUTOMATIC docker reclaim - what scripts/maintenance/disk-guard.ps1 (the hourly low-disk
sentinel, Scheduled Task "AI-Stack Disk Guard") runs when C: free drops below its warn line.

It is the same reclaim the MCP tools run, with no human in the loop: executor.reclaim_plan(
scope="docker") lists unused image tags, old build cache and orphaned anonymous volumes under the
operator's 2026-09-27 rules (docker_reclaim.py), and executor.reclaim_execute(token) removes only
that listed set, re-checking every item first. ao-worker /tmp and container logs stay out of it
(disk-guard runs sweep_tmp.py for /tmp separately, as before).

Output: the full result as JSON in state/last-auto-reclaim.json, and ONE summary line on stdout -
the last line, which disk-guard.ps1 copies into its #sysadmin alert:
    RECLAIM images 12.3 GB (40 removed, 88 skipped) | build cache 1.1 GB | anon volumes ...

Usage:
  python scripts/sysadmin-mcp/auto_reclaim.py          # plan + execute
  python scripts/sysadmin-mcp/auto_reclaim.py --plan   # plan only (read-only): print the listed set
"""

from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import executor as ex  # noqa: E402
import docker_reclaim as dr  # noqa: E402


def run(plan_only: bool = False) -> tuple[int, str, dict]:
    plan = ex.reclaim_plan(scope="docker")
    dk = plan["docker"]
    if plan_only:
        line = (f"PLAN images {dr._gb(dk['images']['bytes'])} GB ({dk['images']['count']} tags) | "
                f"build cache ~{dr._gb(dk['build_cache']['bytes'])} GB | "
                f"anon volumes {dr._gb(dk['volumes']['bytes'])} GB ({dk['volumes']['count']}) | "
                f"token {plan['confirm_token']}")
        line += "".join(f" | {k} category OFF: {dk[k]['disabled_reason']}"
                        for k in ("images", "volumes") if dk[k].get("disabled_reason"))
        return 0, line, plan
    res = ex.reclaim_execute(plan["confirm_token"], notify=False)
    if res.get("refused"):
        return 1, f"RECLAIM refused: {res.get('reason')}", res
    off = [f"{k} category OFF: {dk[k]['disabled_reason']}" for k in ("images", "volumes")
           if dk[k].get("disabled_reason")]
    res["plan_disabled"] = off
    return 0, "RECLAIM " + res["summary"] + "".join(f" | {o}" for o in off), res


def main(argv) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    rc, line, data = run(plan_only="--plan" in argv)
    try:
        os.makedirs(ex._STATE, exist_ok=True)
        name = "last-auto-reclaim-plan.json" if "--plan" in argv else "last-auto-reclaim.json"
        with open(os.path.join(ex._STATE, name), "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=1, default=str)
    except OSError:
        pass
    print(line)
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
