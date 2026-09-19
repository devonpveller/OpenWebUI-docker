#!/usr/bin/env python3
"""Tests for the backup-freshness monitor, off-site layer especially. Stdlib only.

Run:  python scripts/sysadmin-mcp/test_check_backups.py

The regression these exist for (2026-09-13): the weekly NAS sync failed on 09-06 and
09-13 because the backup-user password had expired, and this script exited 0 both
days — it only ever looked at local ./backups/ freshness, which was perfect. A fresh
log is not a successful log, and that is the distinction T2 pins down.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import check_backups as cb  # noqa: E402

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


# Verbatim shape of the real logs/nas-sync-2026-09-13.log (password-expired run).
FAILED_LOG = """[2026-09-13T04:00:00-04:00] [INFO] === NAS sync start ===
[2026-09-13T04:00:00-04:00] [INFO] destination   : \\\\PolyshDesignNAS\\backups\\ai-stack\\portal\\slot-B
[2026-09-13T04:00:01-04:00] [INFO] vault loaded (user: backup-user)
[2026-09-13T04:00:06-04:00] [INFO]   net use: The password of this user has expired.
[2026-09-13T04:00:06-04:00] [INFO]   net use: System error 2242 has occurred.
[2026-09-13T04:00:06-04:00] [ERROR] net use failed (exit 2). Check: vault has correct dedicated-user creds
[2026-09-13T04:00:07-04:00] [WARN] alert dispatch FAILED: HTTP/1.1 500 Internal Server Error
"""

GOOD_LOG = """[2026-09-13T04:00:00-04:00] [INFO] === NAS sync start ===
[2026-09-13T05:27:43-04:00] [INFO] integrity check OK (llm-gateway.sql.gz.sha256)
[2026-09-13T05:27:43-04:00] [INFO] === NAS sync complete ===
"""


def _with_logs(files: dict) -> str:
    """Build a temp repo root containing ./logs/<name> with the given bodies+ages."""
    root = tempfile.mkdtemp(prefix="cb-test-")
    logs = os.path.join(root, "logs")
    os.makedirs(logs)
    for name, (body, age_h) in files.items():
        p = os.path.join(logs, name)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(body)
        when = time.time() - age_h * 3600
        os.utime(p, (when, when))
    return root


def test_offsite() -> None:
    print("OFFSITE - the NAS layer, which nothing watched before 2026-09-13")
    real_root = cb._REPO_ROOT
    roots = []
    try:
        # 1. fresh AND completed -> healthy
        r = _with_logs({"nas-sync-2026-09-13.log": (GOOD_LOG, 6)}); roots.append(r)
        cb._REPO_ROOT = r
        check("fresh successful sync is not stale", cb._offsite_status() == {}, str(cb._offsite_status()))

        # 2. THE REGRESSION: fresh but FAILED. mtime alone calls this healthy.
        r = _with_logs({"nas-sync-2026-09-13.log": (FAILED_LOG, 6)}); roots.append(r)
        cb._REPO_ROOT = r
        s = cb._offsite_status()
        check("fresh but FAILED sync is stale", s != {}, str(s))
        check("  ... age alone would NOT have caught it", s.get("age_h", 999) < cb._NAS_MAX_AGE_H,
              f"age_h={s.get('age_h')}")
        check("  ... detail says it did not complete", "did NOT complete" in s.get("detail", ""), str(s))
        check("  ... detail quotes the actual last error",
              "2242" in s.get("detail", "") or "net use failed" in s.get("detail", ""), str(s))

        # 3. completed, but too long ago -> stale on age
        r = _with_logs({"nas-sync-2026-08-30.log": (GOOD_LOG, 24 * 14)}); roots.append(r)
        cb._REPO_ROOT = r
        s = cb._offsite_status()
        check("old successful sync is stale on age", s != {} and s["age_h"] > cb._NAS_MAX_AGE_H, str(s))

        # 4. the NEWEST log decides, even when an older successful one sits beside it.
        #    This is the live shape: 08-30 succeeded, 09-06 and 09-13 failed.
        r = _with_logs({
            "nas-sync-2026-08-30.log": (GOOD_LOG, 24 * 14),
            "nas-sync-2026-09-13.log": (FAILED_LOG, 6),
        }); roots.append(r)
        cb._REPO_ROOT = r
        s = cb._offsite_status()
        check("a newer FAILED log beats an older good one", s != {} and "did NOT complete" in s.get("detail", ""),
              str(s))

        # 5. never ran at all
        r = _with_logs({}); roots.append(r)
        cb._REPO_ROOT = r
        s = cb._offsite_status()
        check("no logs at all is stale", s != {} and s["age_h"] is None, str(s))

        # 6. the off-site row must never be SKIPPED for want of a container: the NAS
        #    sync is a Windows scheduled task, not a docker sidecar.
        r = _with_logs({"nas-sync-2026-09-13.log": (FAILED_LOG, 6)}); roots.append(r)
        cb._REPO_ROOT = r
        real_running = cb._running_containers
        try:
            cb._running_containers = lambda: set()      # nothing running at all
            res = cb.evaluate()
            names = [x["name"] for x in res["stale"]]
            check("offsite reported even with ZERO containers running", "nas-offsite" in names, str(names))
            check("  ... and is not in the skipped list", "nas-offsite" not in res["skipped"], str(res["skipped"]))
            msg = cb.build_message(res)
            check("  ... message carries the reason", "did NOT complete" in msg, msg[:200])
        finally:
            cb._running_containers = real_running
    finally:
        cb._REPO_ROOT = real_root
        for r in roots:
            shutil.rmtree(r, ignore_errors=True)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    test_offsite()
    print(f"\n{_passed} passed, {_failed} failed")
    sys.exit(1 if _failed else 0)
