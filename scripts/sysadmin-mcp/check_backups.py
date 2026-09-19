#!/usr/bin/env python3
"""Backup-freshness monitor — a systems-administrator capability.

Closes the observability gap that let four backups sit dead for ~26 days:
`check-backup-coverage.ps1` verifies a *-backup container EXISTS, but nothing
verified it recently PRODUCED anything. A container can be "up (healthy)" while
its scheduler silently never fires (the busybox-crond-on-Windows failure mode).

This scans ./backups/<service>/ for the newest real artifact per expected
service and alerts #sysadmin (throttled) if any RUNNING backup's newest
artifact is older than its cadence threshold. Read-only; never mutates.

Services whose *-backup container isn't running are SKIPPED (not alerted) —
that covers the profile-gated portal (caddy/authelia) and anything the operator
intentionally stopped, so "portal is off" never pages.

Throttled: won't re-post within alert_throttle_hours unless the stale set
changed (so a persistent condition pings once, not every run).

Usage:
  python scripts/sysadmin-mcp/check_backups.py         # evaluate + post if stale
  python scripts/sysadmin-mcp/check_backups.py --dry    # print; never post
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(_HERE))
_BACKUPS = os.path.join(_REPO_ROOT, "backups")
_STATE = os.path.join(_HERE, "state")
_ALERT = os.path.join(_STATE, "last-backup-alert.json")

sys.path.insert(0, _HERE)

# Only these count as real backup artifacts (ignore .sha256 sentinels, _manual
# logs, and anything else that shares the dir).
_ARTIFACT_EXTS = (".tar.gz", ".dump", ".sql.gz")

# (backup subdir, *-backup container, max_age_hours). Daily sidecars get a 36h
# threshold (one missed nightly run + slack); lm-models is weekly, so ~8.5 days.
_EXPECTED = [
    ("openwebui",        "openwebui-backup",        36),
    ("mnemory",          "mnemory-backup",          36),
    ("little-coder",     "little-coder-backup",     36),
    ("openbrain-db",     "openbrain-db-backup",     36),
    ("openbrain-wiki",   "openbrain-wiki-backup",   36),
    ("open-notebook",    "open-notebook-backup",    36),
    ("tailscale",        "tailscale-backup",        36),
    ("caddy",            "caddy-backup",            36),
    ("authelia",         "authelia-backup",         36),
    ("agent-bridge-db",  "agent-bridge-db-backup",  36),
    ("mattermost-db",    "mattermost-db-backup",    36),
    ("ao-worker-1-journals", "ao-worker-1-journals-backup", 36),
    ("ao-worker-2-journals", "ao-worker-2-journals-backup", 36),
    ("llm-gateway",      "llm-gateway-backup",      36),
    ("lm-models",        "lm-models-backup",        204),  # weekly + slack
]


def _running_containers() -> set[str]:
    try:
        out = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}"],
            capture_output=True, text=True, timeout=30,
        )
        return {n.strip() for n in out.stdout.splitlines() if n.strip()}
    except Exception:  # noqa: BLE001
        return set()


def _newest_artifact_age_h(subdir: str) -> float | None:
    """Age in hours of the newest backup artifact in ./backups/<subdir>, or None if none."""
    d = os.path.join(_BACKUPS, subdir)
    newest = 0.0
    try:
        for entry in os.scandir(d):
            if not entry.is_file():
                continue
            name = entry.name
            if name.startswith("_") or not name.endswith(_ARTIFACT_EXTS):
                continue
            newest = max(newest, entry.stat().st_mtime)
    except FileNotFoundError:
        return None
    except Exception:  # noqa: BLE001
        return None
    if newest == 0.0:
        return None
    return (time.time() - newest) / 3600.0


# The OFF-SITE layer. Everything above this line watches ./backups/ — whether the
# sidecars are producing locally. Nothing watched whether those artifacts ever
# reached the NAS, which is the copy that survives this machine dying.
#
# On 2026-09-13 that gap cost two weekly syncs: the NAS backup-user password
# expired, backup-to-nas.ps1 failed at `net use`, its only alert channel (the
# portal-alerter) had been returning 500 since 2026-08-21, and THIS script
# exited 0 every day throughout because local artifacts were perfectly fresh.
# Local freshness says nothing about off-site safety.
_NAS_LOG_GLOB = "nas-sync-*.log"
_NAS_MAX_AGE_H = 204          # weekly cadence + slack, same basis as lm-models
_NAS_SUCCESS_MARKER = "=== NAS sync complete ==="


def _offsite_status() -> dict:
    """Freshness AND outcome of the newest NAS sync log.

    Two distinct failures have to be caught, and only checking mtime catches one:
      • it never ran        -> newest log is old (or absent)
      • it ran and FAILED   -> log is fresh but never reached the success marker
    The 2026-09-06 and 09-13 logs are the fixture for the second: both were written
    at 04:00 on the day, and both end at 'net use failed'.
    """
    logs_dir = os.path.join(_REPO_ROOT, "logs")
    try:
        import glob
        paths = glob.glob(os.path.join(logs_dir, _NAS_LOG_GLOB))
    except Exception:  # noqa: BLE001
        paths = []
    if not paths:
        return {"name": "nas-offsite", "age_h": None, "max_h": _NAS_MAX_AGE_H,
                "detail": f"no {_NAS_LOG_GLOB} in ./logs — has the weekly sync ever run?"}
    newest = max(paths, key=lambda p: os.path.getmtime(p))
    age_h = (time.time() - os.path.getmtime(newest)) / 3600.0
    base = os.path.basename(newest)
    try:
        with open(newest, "r", encoding="utf-8", errors="replace") as fh:
            body = fh.read()
    except Exception as e:  # noqa: BLE001
        return {"name": "nas-offsite", "age_h": round(age_h, 1), "max_h": _NAS_MAX_AGE_H,
                "detail": f"could not read {base}: {e}"}

    if _NAS_SUCCESS_MARKER not in body:
        last_err = ""
        for line in reversed(body.splitlines()):
            if "[ERROR]" in line:
                last_err = line.strip()[-220:]
                break
        return {"name": "nas-offsite", "age_h": round(age_h, 1), "max_h": _NAS_MAX_AGE_H,
                "detail": f"{base} ran but did NOT complete. Last error: {last_err or '(none logged)'}"}
    if age_h > _NAS_MAX_AGE_H:
        return {"name": "nas-offsite", "age_h": round(age_h, 1), "max_h": _NAS_MAX_AGE_H,
                "detail": f"newest successful sync is {base}, {round(age_h / 24, 1)} days old"}
    return {}


def evaluate() -> dict:
    running = _running_containers()
    stale, skipped, ok = [], [], []
    for subdir, container, max_age_h in _EXPECTED:
        if container not in running:
            skipped.append(subdir)
            continue
        age = _newest_artifact_age_h(subdir)
        if age is None:
            stale.append({"name": subdir, "age_h": None, "max_h": max_age_h})
        elif age > max_age_h:
            stale.append({"name": subdir, "age_h": round(age, 1), "max_h": max_age_h})
        else:
            ok.append(subdir)

    # The off-site layer is NOT gated on a container running: the NAS sync is a
    # Windows scheduled task, so "no container" must never make it skippable.
    offsite = _offsite_status()
    if offsite:
        stale.append(offsite)
    else:
        ok.append("nas-offsite")
    return {"stale": stale, "skipped": skipped, "ok": ok}


def build_message(res: dict) -> str:
    lines = [f"### 🗄️ Backup freshness — **STALE ({len(res['stale'])})**",
             "Newest artifact older than its cadence threshold (container is up but not producing):"]
    for s in res["stale"]:
        age = "no artifacts ever" if s["age_h"] is None else f"{s['age_h']}h (> {s['max_h']}h)"
        if s.get("detail"):
            # The off-site row carries WHY, because "nas-offsite is stale" alone
            # sends the reader to the wrong layer (the sidecars are fine).
            lines.append(f"- **{s['name']}** — {age} — {s['detail']}")
        else:
            lines.append(f"- **{s['name']}** — {age}")
    if res["skipped"]:
        lines.append("\n_Skipped (backup container not running — e.g. portal off): "
                     + ", ".join(res["skipped"]) + "._")
    lines.append("\n@sysadmin: a *-backup container can be 'up (healthy)' yet silently "
                 "producing nothing. Investigate the named container's logs.")
    return "\n".join(lines)


def _stale_hash(res: dict) -> str:
    key = sorted(s["name"] for s in res["stale"])
    return hashlib.sha1(json.dumps(key).encode()).hexdigest()[:12]


def _should_post(res: dict, throttle_h: float) -> bool:
    try:
        with open(_ALERT, "r", encoding="utf-8") as fh:
            last = json.load(fh)
    except Exception:  # noqa: BLE001
        return True
    if time.time() - last.get("ts", 0) > throttle_h * 3600:
        return True
    return _stale_hash(res) != last.get("stale_hash")


def _save_alert(res: dict) -> None:
    try:
        os.makedirs(_STATE, exist_ok=True)
        with open(_ALERT, "w", encoding="utf-8") as fh:
            json.dump({"ts": time.time(), "stale_hash": _stale_hash(res)}, fh)
    except Exception:  # noqa: BLE001
        pass


def _throttle_hours() -> float:
    try:
        import sysadmin as sa  # noqa: E402
        return float(sa.load_config().get("alert_throttle_hours", 20))
    except Exception:  # noqa: BLE001
        return 20.0


def main(argv) -> int:
    dry = "--dry" in argv
    res = evaluate()
    if not res["stale"]:
        print(f"all fresh — ok={len(res['ok'])} skipped={len(res['skipped'])} "
              f"({', '.join(res['skipped']) or 'none'})")
        return 0
    msg = build_message(res)
    if dry:
        print("[--dry] would post to #sysadmin:\n")
        print(msg)
        return 0
    if not _should_post(res, _throttle_hours()):
        print(f"stale ({len(res['stale'])}) — throttled (already alerted; unchanged)")
        return 0
    import mm_post
    if mm_post.post(msg):
        _save_alert(res)
        print(f"posted stale alert ({len(res['stale'])}) to #sysadmin")
    else:
        print(f"stale ({len(res['stale'])}) — alert post failed (non-fatal)")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    sys.exit(main(sys.argv[1:]))
