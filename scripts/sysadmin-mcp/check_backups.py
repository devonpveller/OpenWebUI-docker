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
  python scripts/sysadmin-mcp/check_backups.py --check  # print; never post; EXIT 1 if stale
"""

from __future__ import annotations

import hashlib
import json
import os
import re
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


def _newest_artifact_mtime(subdir: str) -> float | None:
    """Epoch seconds of the newest artifact, or None. The skip-vs-artifact clock."""
    age = _newest_artifact_age_h(subdir)
    return None if age is None else time.time() - age * 3600.0


# The PRECHECK layer. Above this line the script reads ./backups/ only, so a
# sidecar that runs every night and declines to tar anything is indistinguishable
# from one that never ran -- both leave the artifact age climbing and no reason
# anywhere the operator looks.
#
# backup/generic-tar-backup.sh declines by DESIGN (never capture broken state)
# and it says why, on stdout, with exit 0:
#     [<ts>] <PREFIX> PRECHECK SKIP: <DATA_DIR> does not exist
#     [<ts>] <PREFIX> PRECHECK SKIP: <DATA_DIR> is empty
#     [<ts>] <PREFIX> PRECHECK SKIP: <HEALTH_TCP> unreachable -- service unhealthy or down
# Until 2026-09-21 that sentence reached the sidecar's own `docker logs` and
# nowhere else. lm-models sat at 350 h with "/data is empty" repeating in its log
# because a recreate on 2026-09-19 bound the compose default (an empty directory)
# instead of LM_MODELS_DIR, and the age number alone sent the reader hunting for
# a dead cron.
#
# The min-age guard's "<PREFIX> SKIP: newest backup is <n>s old" is deliberately
# NOT matched: that one means a fresh artifact already exists.
_PRECHECK_TAIL_LINES = 60
_PRECHECK_RE = re.compile(
    r"^\[(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)\]\s+\S+\s+PRECHECK SKIP:\s+(?P<why>.+?)\s*$"
)
# An empty or absent DATA_DIR is the signature of a bind mount pointing at the
# wrong path after a move. It does not heal on the next run, so it is a FAILURE
# on its own and the MOUNT PATH is the thing worth printing. An unreachable
# HEALTH_TCP probe is the opposite: the next run fixes it, so it is a reason
# printed beside a stale age, never a failure by itself.
_MOUNT_RE = re.compile(r"^(?P<mount>\S+) (?:is empty|does not exist)$")
# Every line these sidecars print is stamped the same way. The newest stamp in
# the tail is "when this container last did anything", and a skip that is not
# the newest stamp has already been superseded by a later run - the ao-worker
# journal sidecars carry a months-old `/data is empty` from a first boot in the
# same 60 lines as last night's successful tar, and reading only for the skip
# marker reports both of them broken.
_STAMPED_RE = re.compile(r"^\[(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)\]")


def _container_log_tail(container: str) -> str:
    try:
        out = subprocess.run(
            ["docker", "logs", "--tail", str(_PRECHECK_TAIL_LINES), container],
            capture_output=True, text=True, errors="replace", timeout=30,
        )
        return (out.stdout or "") + (out.stderr or "")
    except Exception:  # noqa: BLE001 - no logs is not an assertion that all is well
        return ""


def _parse_iso_z(stamp: str) -> float | None:
    try:
        return time.mktime(time.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ")) - time.timezone
    except Exception:  # noqa: BLE001
        return None


def precheck_skip(container: str, log_text: str | None = None) -> dict:
    """The NEWEST `PRECHECK SKIP:` in the sidecar's log tail, or {}.

    `log_text` is the seam the tests drive; production reads `docker logs`.
    Returns {"why": <reason>, "at": <epoch or None>, "mount": <path or None>}.
    `mount` is set only for the empty/absent-DATA_DIR forms - the ones that
    name a bind mount and never fix themselves.
    """
    text = _container_log_tail(container) if log_text is None else log_text
    newest: dict = {}
    newest_stamp: float | None = None
    for raw in text.splitlines():
        line = raw.strip()
        stamped = _STAMPED_RE.match(line)
        if stamped:
            at = _parse_iso_z(stamped.group("ts"))
            if at is not None and (newest_stamp is None or at > newest_stamp):
                newest_stamp = at
        match = _PRECHECK_RE.match(line)
        if not match:
            continue
        why = match.group("why")
        mount_match = _MOUNT_RE.match(why)
        newest = {"why": why, "at": _parse_iso_z(match.group("ts")),
                  "mount": mount_match.group("mount") if mount_match else None}
    # Superseded by a later line from the same container -> not the current state.
    if newest and newest.get("at") is not None and newest_stamp is not None:
        if newest["at"] < newest_stamp:
            return {}
    return newest


def _skip_is_current(skip: dict, artifact_mtime: float | None) -> bool:
    """A skip counts only while it is the sidecar's LAST WORD.

    With no artifact at all, any skip in the tail is current. With an artifact,
    a skip older than it has already been superseded by a successful run - which
    is exactly the state lm-models lands in after its mount is repaired, and the
    reason a repaired host goes back to exit 0 without anyone editing this file.
    """
    if artifact_mtime is None:
        return True
    if skip.get("at") is None:
        return False
    return skip["at"] > artifact_mtime


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
        skip = precheck_skip(container)
        current = bool(skip) and _skip_is_current(skip, _newest_artifact_mtime(subdir))
        row = {"name": subdir, "age_h": None if age is None else round(age, 1),
               "max_h": max_age_h}
        if current:
            # The reason travels WITH the age, both ways round: a stale row that
            # says why, and - for the wrong-mount class - a row that fails even
            # while the age still looks fine, because the artifact it is fresh
            # against is the last one from before the mount moved.
            row["detail"] = f"{container} declined its last run - PRECHECK SKIP: {skip['why']}"
            if skip.get("mount"):
                row["mount"] = skip["mount"]
                row["detail"] = (
                    f"{container} is producing NOTHING: its DATA_DIR bind "
                    f"{skip['mount']} is empty or absent inside the container "
                    f"(PRECHECK SKIP: {skip['why']}). That is a mount pointing "
                    f"somewhere the data is not - check the compose bind and the "
                    f"plane .env, not the schedule."
                )
                stale.append(row)
                continue
        if age is None or age > max_age_h:
            stale.append(row)
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
        if s.get("mount"):
            # The wrong-mount class keeps its own line shape: the age is the
            # LEAST informative number on it, and printing it as "(> max)" when
            # the row failed on the mount instead would be a lie.
            lines.append(f"- **{s['name']}** — newest artifact "
                         + ("none" if s["age_h"] is None else f"{s['age_h']}h old")
                         + f" — MOUNT `{s['mount']}` — {s['detail']}")
        elif s.get("detail"):
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
    # --check is --dry with a GATE's exit code. The alerting invocation (the
    # scheduled task, no flags) keeps exiting 0 on a stale set on purpose: it
    # alerts, and a task that "fails" every day until someone fixes a backup is
    # a task the operator disables. A gate wants the opposite, so it asks.
    check = "--check" in argv
    dry = check or "--dry" in argv
    # ./backups/ is HOST state, not repo state: a git worktree has none, and a
    # gate run from one would report every sidecar dead. --repo-root lets the
    # worktree's copy of this file read the deployment's artifacts.
    if "--repo-root" in argv:
        global _REPO_ROOT, _BACKUPS
        _REPO_ROOT = os.path.abspath(argv[argv.index("--repo-root") + 1])
        _BACKUPS = os.path.join(_REPO_ROOT, "backups")
    if not os.path.isdir(_BACKUPS):
        print(f"REFUSED: no backups directory at {_BACKUPS}. This check reads HOST "
              f"state; run it from the deployment checkout, or pass "
              f"--repo-root <deployment root>.", file=sys.stderr)
        return 2
    res = evaluate()
    if not res["stale"]:
        print(f"all fresh — ok={len(res['ok'])} skipped={len(res['skipped'])} "
              f"({', '.join(res['skipped']) or 'none'})")
        return 0
    msg = build_message(res)
    if dry:
        print(("[--check] " if check else "[--dry] ") + "would post to #sysadmin:\n")
        print(msg)
        return 1 if check else 0
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
