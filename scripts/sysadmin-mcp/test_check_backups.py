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


# --- the PRECHECK layer (2026-09-21) --------------------------------------
# lm-models sat at 350 h with `/data is empty` repeating in its own log and
# nothing but the age number anywhere the operator looks. These pin the three
# distinctions that cost that: skip vs no-skip, wrong-mount vs transient probe,
# and current vs superseded.

EMPTY_MOUNT_LOG = """[2026-09-19T18:54:00Z] lm-models-backup started (sleep-loop; interval=604800s, retain=2)
[2026-09-19T18:54:00Z] lm-models PRECHECK SKIP: /data is empty
"""

SUPERSEDED_LOG = """[2026-09-11T23:47:58Z] ao-worker-1-journals PRECHECK SKIP: /data is empty
[2026-09-19T07:29:02Z] ao-worker-1-journals tar -> /backups/ao-worker-1-journals-20260919T072902Z.tar.gz (10587 bytes; retain=7)
[2026-09-20T07:29:21Z] ao-worker-1-journals tar -> /backups/ao-worker-1-journals-20260920T072921Z.tar.gz (10609 bytes; retain=7)
"""

PROBE_LOG = """[2026-09-20T07:29:00Z] mnemory PRECHECK SKIP: mnemory-cloud-gateway:8060 unreachable -- service unhealthy or down
"""

MIN_AGE_LOG = """[2026-09-20T07:29:00Z] lm-models SKIP: newest backup is 300s old (< MIN_AGE_SECS=86400)
"""


def test_precheck() -> None:
    print("\nPRECHECK - the reason the sidecar already wrote down and nobody read")

    s = cb.precheck_skip("lm-models-backup", EMPTY_MOUNT_LOG)
    check("empty DATA_DIR is a skip", bool(s), str(s))
    check("  ... and names the MOUNT, not just the sidecar", s.get("mount") == "/data", str(s))

    s = cb.precheck_skip("mnemory-backup", PROBE_LOG)
    check("unreachable probe is a skip", bool(s), str(s))
    check("  ... but carries NO mount (it is not a path problem)", s.get("mount") is None, str(s))

    check("the min-age SKIP is not a PRECHECK SKIP",
          cb.precheck_skip("lm-models-backup", MIN_AGE_LOG) == {},
          str(cb.precheck_skip("lm-models-backup", MIN_AGE_LOG)))
    check("a clean log has no skip", cb.precheck_skip("x", "") == {})

    # THE FALSE-POSITIVE THIS CHECK WOULD OTHERWISE HAVE SHIPPED WITH: both live
    # ao-worker journal sidecars carry a first-boot `/data is empty` in the same
    # 60-line tail as last night's successful tar. Reading for the marker alone
    # reported two healthy sidecars as producing nothing.
    check("a skip superseded by a later line is NOT current",
          cb.precheck_skip("ao-worker-1-journals-backup", SUPERSEDED_LOG) == {},
          str(cb.precheck_skip("ao-worker-1-journals-backup", SUPERSEDED_LOG)))

    # The artifact clock is the second guard: a skip older than the newest
    # artifact has been overtaken by a successful run.
    skip = cb.precheck_skip("lm-models-backup", EMPTY_MOUNT_LOG)
    check("skip with NO artifact at all is current", cb._skip_is_current(skip, None))
    check("skip OLDER than the newest artifact is not current",
          not cb._skip_is_current(skip, skip["at"] + 3600))
    check("skip NEWER than the newest artifact is current",
          cb._skip_is_current(skip, skip["at"] - 3600))


def test_stamp_parsing_is_utc_all_year() -> None:
    """The attempt-1 defect: a UTC stamp read one hour early for 8 months a year.

    `mktime(strptime(...)) - time.timezone` interprets the struct as LOCAL time
    while time.timezone is the STANDARD offset, so inside the host's DST window
    the pair is 3600 s out. _skip_is_current compares that value against an
    artifact mtime, so a real, CURRENT skip less than an hour newer than the last
    artifact was called "superseded" and the row went green.

    The margins below are DISCRIMINATING on purpose. Attempt 1's test planted a
    1 h old artifact and a skip stamped now+60s - it passed with a sixty-second
    margin, i.e. the bug's offset plus a minute, which is why the bug shipped.
    A 30-minute-newer skip against a 2 h artifact fails under the old code and
    passes under calendar.timegm.
    """
    print("\nSTAMPS - one hour is the difference between a red row and a green one")
    import calendar

    for stamp in ("2026-07-04T12:00:00Z", "2026-09-20T12:00:00Z", "2026-01-15T12:00:00Z"):
        want = float(calendar.timegm(time.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ")))
        got = cb._parse_iso_z(stamp)
        check(f"{stamp} parses as UTC (delta 0 s)", got == want,
              f"delta {None if got is None else got - want:+} s")

    # THE DISCRIMINATING SPACING. What matters is the GAP between the artifact
    # and the skip, not how old either is: the old expression shifts the skip one
    # hour EARLIER, so it only changes the verdict when that gap is under an hour.
    # Artifact 2 h ago, skip 30 MINUTES AFTER IT (= 90 min ago): the shift puts
    # the skip at 150 min ago, BEFORE the artifact, and it is wrongly superseded.
    now = time.time()
    artifact = now - 2 * 3600                  # last artifact two hours ago
    skip_at = artifact + 30 * 60               # skip 30 min AFTER it -> 90 min ago
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(skip_at))
    skip = cb.precheck_skip("x", f"[{stamp}] lm-models PRECHECK SKIP: /data is empty")
    check("a skip 30 min NEWER than a 2 h old artifact is CURRENT",
          cb._skip_is_current(skip, artifact), str(skip))

    # The old expression run side by side, so the margin is demonstrated and not
    # asserted. Only meaningful while the host is actually in DST.
    broken_at = time.mktime(time.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ")) - time.timezone
    if time.daylight and time.localtime().tm_isdst:
        check("  ... and the OLD expression called it superseded (this is the bug)",
              not (broken_at > artifact),
              f"broken_at-artifact={broken_at - artifact:+.0f}s")
    else:
        print("  SKIP  the old-expression contrast (host is not in DST right now)")


def test_evaluate_reports_the_mount() -> None:
    """End to end: a fresh-looking age must not hide a mount pointing nowhere."""
    print("\nEVALUATE - the wrong-mount row fails even when the age looks fine")
    root = tempfile.mkdtemp(prefix="cb-mount-")
    real_root, real_backups = cb._REPO_ROOT, cb._BACKUPS
    real_running, real_logs = cb._running_containers, cb._container_log_tail
    real_expected = cb._EXPECTED
    try:
        os.makedirs(os.path.join(root, "logs"))
        d = os.path.join(root, "backups", "lm-models")
        os.makedirs(d)
        # An artifact from BEFORE the mount moved: 2 h old, well inside the 204 h
        # threshold. Age alone says this sidecar is healthy.
        #
        # TWO hours, and the skip below is THIRTY MINUTES ago, because attempt 1
        # planted a 1 h artifact against a skip stamped now+60s and passed with a
        # sixty-second margin - which is the DST offset plus a minute, so the
        # one-hour stamp bug sailed through. This spacing is smaller than that
        # offset in the right direction: it fails under the old expression.
        art = os.path.join(d, "lm-models-20260921T000000Z.tar.gz")
        with open(art, "w", encoding="utf-8") as fh:
            fh.write("x")
        when = time.time() - 2 * 3600
        os.utime(art, (when, when))

        cb._REPO_ROOT = root
        cb._BACKUPS = os.path.join(root, "backups")
        cb._EXPECTED = [("lm-models", "lm-models-backup", 204)]
        cb._running_containers = lambda: {"lm-models-backup"}
        # The skip is NEWER than that artifact -> the sidecar's last word. Only
        # THIRTY MINUTES newer, which is what makes this discriminating: the old
        # `mktime - time.timezone` read every stamp an hour early inside DST, so a
        # gap under an hour put the skip BEFORE the artifact and the row went
        # green. Attempt 1 used a 60-second margin and never noticed.
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(when + 30 * 60))
        cb._container_log_tail = lambda c: f"[{stamp}] lm-models PRECHECK SKIP: /models is empty\n"

        res = cb.evaluate()
        names = [s["name"] for s in res["stale"]]
        check("a fresh artifact does NOT excuse a current empty-mount skip",
              "lm-models" in names, str(res))
        row = [s for s in res["stale"] if s["name"] == "lm-models"][0]
        check("  ... the row carries the mount path", row.get("mount") == "/models", str(row))
        check("  ... the age is still reported beside it", row.get("age_h") is not None, str(row))
        msg = cb.build_message(res)
        check("  ... and the message prints the mount", "/models" in msg, msg[:300])
        check("  ... and the sidecar name", "lm-models-backup" in msg, msg[:300])

        # Now the repair: the sidecar produces an artifact newer than the skip.
        cb._container_log_tail = lambda c: (
            "[2026-09-19T18:54:00Z] lm-models PRECHECK SKIP: /models is empty\n"
            "[2026-09-21T01:00:00Z] lm-models tar -> /backups/lm-models-x.tar.gz (1 bytes)\n"
        )
        res = cb.evaluate()
        # Scoped to lm-models on purpose: this temp root has no ./logs/nas-sync-*.log,
        # so the off-site row is stale here for an unrelated reason.
        check("after the repair the same sidecar is clean",
              "lm-models" not in [s["name"] for s in res["stale"]] and "lm-models" in res["ok"],
              str(res))
    finally:
        cb._REPO_ROOT, cb._BACKUPS = real_root, real_backups
        cb._running_containers, cb._container_log_tail = real_running, real_logs
        cb._EXPECTED = real_expected
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    test_offsite()
    test_precheck()
    test_stamp_parsing_is_utc_all_year()
    test_evaluate_reports_the_mount()
    print(f"\n{_passed} passed, {_failed} failed")
    sys.exit(1 if _failed else 0)
