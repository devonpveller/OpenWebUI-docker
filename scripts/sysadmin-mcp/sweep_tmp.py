#!/usr/bin/env python3
"""Daily unattended /tmp prevention sweep — deletes lc-*.jsonl older than N days on running
ao-workers so /tmp can't balloon between the weekly detector runs. Conservative (old files only,
never the active effort's per-turn file); no approval needed. Registered as a daily Scheduled Task.

Usage:
  python scripts/sysadmin-mcp/sweep_tmp.py           # sweep files older than the default (3 days)
  python scripts/sysadmin-mcp/sweep_tmp.py 5         # older than 5 days
  python scripts/sysadmin-mcp/sweep_tmp.py --dry     # report what WOULD be deleted, delete nothing
"""

from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import executor as ex  # noqa: E402
import sysadmin as sa  # noqa: E402


def main(argv) -> int:
    dry = "--dry" in argv
    days = 3.0
    for a in argv:
        if a != "--dry":
            try:
                days = float(a)
            except ValueError:
                pass
    if dry:
        cfg = sa.load_config()
        mins = int(days * 24 * 60)
        for w in cfg["ao_workers"]:
            if not ex._running(w):
                print(f"{w}: not running")
                continue
            cnt = sa._docker(["exec", w, "sh", "-c",
                              f"find /tmp -maxdepth 1 -name 'lc-*.jsonl' -mmin +{mins} 2>/dev/null | wc -l"], timeout=120)
            print(f"{w}: would delete {cnt['out'].strip() or '0'} file(s) older than {days}d")
        return 0
    res = ex.sweep_old_tmp(days)
    for r in res["results"]:
        print(r)
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    sys.exit(main(sys.argv[1:]))
