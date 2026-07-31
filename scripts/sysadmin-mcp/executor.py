#!/usr/bin/env python3
"""Mutating executor for sysadmin-mcp — the GATED side of the systems-administrator capability.

Split from the read-only probes (sysadmin.py) on purpose: everything here can change stack state,
so every entry point is built to be safe by construction:

  • PLAN / EXECUTE split — `reclaim_plan()` is read-only (frictionless investigation); `reclaim_
    execute()` is the only thing that mutates and it REQUIRES a plan-bound `confirm_token`. Calling
    execute with a stale/absent token is refused (fail-closed) and just returns the fresh plan.
  • Idle-gated — an ao-worker's /tmp is cleared ONLY when the worker shows no active build/agent
    process AND no lc-*.jsonl was written in the last few minutes. If idleness can't be verified,
    the worker is treated as BUSY and skipped (fail-safe).
  • Scoped deletes — only `/tmp/lc-pi-*.jsonl` and `/tmp/lc-ot-*.jsonl` (the known little-coder
    session-log bloat), never a blanket `rm`. Logs are truncated (data-preserving) not deleted.
  • NEVER touches volumes — there is deliberately no `docker volume` operation in this file; a test
    asserts the source contains no volume-mutating verbs.
  • Elevation stays out — vhdx compaction is not done here; it is triggered as a pre-registered
    RunLevel-Highest Scheduled Task by compaction.py, behind the same gate.

Every mutation writes an audit line to state/sysadmin-audit.jsonl.
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

_STATE = os.path.join(_HERE, "state")
_AUDIT = os.path.join(_STATE, "sysadmin-audit.jsonl")
GB = sa.GB

# active-work markers inside an ao-worker; presence ⇒ treat as BUSY (do not clear its /tmp).
# The idle baseline is a single process: `python3.12 lc-daemon`. An active effort spawns claude and
# build/vcs tools as additional processes — those are what we watch for.
_BUSY_MARKERS = ("claude", "git ", "/git", "npm", "pnpm", "yarn", "cargo", "gcc", "cc1",
                 "make", "tsc", "webpack", "vite", "pytest", "jest", "rustc", "node ",
                 "go build", "gradle", "mvn")
# lines from the /proc scan that are NOT real work (idle daemon baseline + our own probe)
_PROC_IGNORE = ("lc-daemon", "cmdline", "/proc/")
_RECENT_MIN = 10  # a lc-*.jsonl written in the last N minutes ⇒ worker is actively logging ⇒ skip


def _audit(event: str, detail: dict) -> None:
    try:
        os.makedirs(_STATE, exist_ok=True)
        rec = {"ts": int(time.time()), "event": event, "detail": detail}
        with open(_AUDIT, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")
    except Exception:  # noqa: BLE001 - auditing must never break the action
        pass


# ── idleness ───────────────────────────────────────────────────────────────────
def _running(name: str) -> bool:
    r = sa._docker(["ps", "--format", "{{.Names}}"])
    return r["rc"] == 0 and name in {ln.strip() for ln in r["out"].splitlines()}


def worker_state(w: str) -> dict:
    """Return {worker, running, busy, reason, clearable, tmp_gb, files}. Fail-safe → busy."""
    if not _running(w):
        return {"worker": w, "running": False, "busy": True, "reason": "not running",
                "clearable": False}
    # process scan via /proc (busybox ps is absent in these containers); idle baseline = lc-daemon.
    proc = sa._docker(["exec", w, "sh", "-c",
                       'for p in /proc/[0-9]*/cmdline; do tr "\\0" " " < "$p" 2>/dev/null; echo; done'],
                      timeout=30)
    proc_ok = proc["rc"] == 0 and bool(proc["out"].strip())
    if proc_ok:
        for ln in proc["out"].lower().splitlines():
            s = ln.strip()
            if not s or any(ig in s for ig in _PROC_IGNORE):
                continue
            if any(m in s for m in _BUSY_MARKERS):
                return {"worker": w, "running": True, "busy": True,
                        "reason": f"active process: {s[:80]}", "clearable": False}
    # recency guard (primary signal for ao-workers; window widened if /proc was unreadable)
    window = _RECENT_MIN if proc_ok else _RECENT_MIN * 3
    rec = sa._docker(["exec", w, "sh", "-c",
                      f"find /tmp -maxdepth 1 -name 'lc-*.jsonl' -mmin -{window} 2>/dev/null | head -1"],
                     timeout=30)
    if rec["rc"] == 0 and rec["out"].strip():
        return {"worker": w, "running": True, "busy": True,
                "reason": f"lc-*.jsonl written in last {window} min", "clearable": False}
    # size of the clearable set
    du = sa._docker(["exec", w, "sh", "-c",
                     "du -ck /tmp/lc-pi-*.jsonl /tmp/lc-ot-*.jsonl 2>/dev/null | tail -1"], timeout=60)
    kb = 0.0
    files = 0
    if du["rc"] == 0 and du["out"].strip():
        try:
            kb = float(du["out"].split()[0])
        except (ValueError, IndexError):
            kb = 0.0
    cnt = sa._docker(["exec", w, "sh", "-c",
                      "ls -1 /tmp/lc-pi-*.jsonl /tmp/lc-ot-*.jsonl 2>/dev/null | wc -l"], timeout=60)
    if cnt["rc"] == 0:
        try:
            files = int(cnt["out"].strip())
        except ValueError:
            files = 0
    reason = "idle" if proc_ok else "idle (proc unreadable; recency-only, widened window)"
    return {"worker": w, "running": True, "busy": False, "reason": reason,
            "clearable": files > 0, "tmp_gb": round(kb * 1024 / GB, 2), "files": files}


# ── plan ───────────────────────────────────────────────────────────────────────
def _plan_token(actions: dict) -> str:
    """A short, deterministic token bound to the ACTION SET (not exact byte sizes, so it stays
    valid across small growth between plan and execute). Changes if a worker becomes busy, a log
    crosses the threshold, or the prune flags flip."""
    canon = json.dumps(actions, sort_keys=True)
    return hashlib.sha1(canon.encode()).hexdigest()[:8]  # noqa: S324 - non-crypto gate token


def reclaim_plan() -> dict:
    """Read-only: what a safe reclaim WOULD do + estimated bytes freed + a confirm_token."""
    cfg = sa.load_config()
    th = cfg["thresholds"]
    log_gb = max(0.1, th.get("container_log_warn_gb", 2))

    workers = [worker_state(w) for w in cfg["ao_workers"]]
    clear_workers = sorted(w["worker"] for w in workers if w.get("clearable"))
    tmp_est_gb = round(sum(w.get("tmp_gb", 0) for w in workers if w.get("clearable")), 2)

    logs = [lg for lg in sa._big_logs(log_gb) if "log_gb" in lg]
    logs_est_gb = round(sum(lg["log_gb"] for lg in logs), 2)

    df = sa._system_df()
    img_recl = df.get("Images", {}).get("reclaimable_gb", 0) if isinstance(df, dict) else 0
    cache_recl = df.get("Build Cache", {}).get("reclaimable_gb", 0) if isinstance(df, dict) else 0
    prune_images = True   # `docker image prune -f` — dangling only, always safe
    prune_builder = True  # `docker builder prune -f` — unreferenced cache only, always safe

    actions = {
        "clear_workers": clear_workers,
        "truncate_logs_over_gb": log_gb if logs else 0,
        "prune_images": prune_images,
        "prune_builder": prune_builder,
    }
    token = _plan_token(actions)
    return {
        "workers": workers,
        "clear_workers": clear_workers,
        "logs_to_truncate": [{"container": lg["container"], "log_gb": lg["log_gb"]} for lg in logs],
        "prune_images": prune_images,
        "prune_builder": prune_builder,
        "estimate_gb": {
            "ao_worker_tmp": tmp_est_gb,
            "logs": logs_est_gb,
            "images_dangling": img_recl,   # actual varies; dangling-only is a subset of reclaimable
            "build_cache": cache_recl,
        },
        "confirm_token": token,
        "note": ("Read-only plan. Call reclaim_execute(confirm_token) to run. Busy/not-running "
                 "workers are skipped. No volumes are touched; logs are truncated, not deleted."),
    }


# ── execute (the only mutating entry point) ────────────────────────────────────
def reclaim_execute(confirm_token: str | None = None) -> dict:
    """Perform the safe reclaim. Refuses unless confirm_token matches the CURRENT plan (fail-closed)."""
    plan = reclaim_plan()
    if not confirm_token or confirm_token != plan["confirm_token"]:
        _audit("reclaim_refused", {"given": confirm_token, "expected": plan["confirm_token"]})
        return {"refused": True,
                "reason": "missing or stale confirm_token — the situation changed; review the fresh plan and retry",
                "current_token": plan["confirm_token"], "plan": plan}

    cfg = sa.load_config()
    results = {"cleared_workers": [], "skipped_workers": [], "truncated_logs": [],
               "pruned": {}, "freed_gb": {}}

    # 1) idle ao-worker /tmp session logs (re-verify idleness at execute time)
    freed_tmp = 0.0
    for w in cfg["ao_workers"]:
        st = worker_state(w)
        if not st.get("clearable"):
            results["skipped_workers"].append({"worker": w, "reason": st.get("reason")})
            continue
        before = st.get("tmp_gb", 0)
        rm = sa._docker(["exec", w, "sh", "-c",
                         "rm -f /tmp/lc-pi-*.jsonl /tmp/lc-ot-*.jsonl"], timeout=120)
        if rm["rc"] == 0:
            results["cleared_workers"].append({"worker": w, "freed_gb": before, "files": st.get("files")})
            freed_tmp += before
            _audit("reclaim_cleared_tmp", {"worker": w, "freed_gb": before, "files": st.get("files")})
        else:
            results["skipped_workers"].append({"worker": w, "reason": f"rm failed: {rm['err'].strip()}"})
    results["freed_gb"]["ao_worker_tmp"] = round(freed_tmp, 2)

    # 2) truncate oversized container json logs (data-preserving reset, not delete)
    th = cfg["thresholds"]
    log_bytes = int(max(0.1, th.get("container_log_warn_gb", 2)) * GB)
    mount = cfg["docker_desktop_mount"]
    before_logs = [lg for lg in sa._big_logs(max(0.1, th.get("container_log_warn_gb", 2))) if "log_gb" in lg]
    freed_logs = round(sum(lg["log_gb"] for lg in before_logs), 2)
    tr = sa._wsl_dd(["find", f"{mount}/data/docker/containers", "-name", "*-json.log",
                     "-size", f"+{log_bytes}c", "-exec", "truncate", "-s", "0", "{}", ";"], timeout=120)
    if tr["rc"] == 0:
        results["truncated_logs"] = [{"container": lg["container"], "was_gb": lg["log_gb"]} for lg in before_logs]
        results["freed_gb"]["logs"] = freed_logs
        _audit("reclaim_truncated_logs", {"count": len(before_logs), "freed_gb": freed_logs})
    else:
        results["truncated_logs"] = [{"error": tr["err"].strip() or "truncate failed"}]
        results["freed_gb"]["logs"] = 0

    # 3) dangling images + unreferenced build cache (both safe)
    img = sa._docker(["image", "prune", "-f"], timeout=180)
    bld = sa._docker(["builder", "prune", "-f"], timeout=180)
    results["pruned"]["images"] = "ok" if img["rc"] == 0 else img["err"].strip()
    results["pruned"]["build_cache"] = "ok" if bld["rc"] == 0 else bld["err"].strip()
    _audit("reclaim_pruned", {"images_rc": img["rc"], "builder_rc": bld["rc"]})

    total = round(results["freed_gb"].get("ao_worker_tmp", 0) + results["freed_gb"].get("logs", 0), 2)
    results["freed_gb"]["total_approx"] = total
    results["ok"] = True
    _audit("reclaim_done", {"freed_gb": results["freed_gb"],
                            "cleared": [c["worker"] for c in results["cleared_workers"]]})
    return results


def sweep_old_tmp(days: float = 3) -> dict:
    """Unattended PREVENTION: delete only lc-*.jsonl OLDER than `days` on running ao-workers.

    Distinct from reclaim_execute (which clears ALL lc logs and is operator-gated): a >days-old
    lc-*.jsonl is never the current effort's file (little-coder writes a fresh ULID-named file per
    turn), so removing old ones is safe even while a worker is mid-effort. This keeps /tmp from
    ballooning between weekly checks. No approval needed; conservative by construction.
    """
    cfg = sa.load_config()
    mins = int(float(days) * 24 * 60)
    results = []
    for w in cfg["ao_workers"]:
        if not _running(w):
            results.append({"worker": w, "skipped": "not running"})
            continue
        cnt = sa._docker(["exec", w, "sh", "-c",
                          f"find /tmp -maxdepth 1 -name 'lc-*.jsonl' -mmin +{mins} 2>/dev/null | wc -l"],
                         timeout=120)
        n = int(cnt["out"].strip()) if cnt["rc"] == 0 and cnt["out"].strip().isdigit() else 0
        if n == 0:
            results.append({"worker": w, "deleted": 0})
            continue
        rm = sa._docker(["exec", w, "sh", "-c",
                         f"find /tmp -maxdepth 1 -name 'lc-*.jsonl' -mmin +{mins} -exec rm -f {{}} + 2>/dev/null"],
                        timeout=180)
        results.append({"worker": w, "deleted": n, "rc": rm["rc"]})
        _audit("sweep_old_tmp", {"worker": w, "deleted": n, "days": days})
    return {"days": days, "results": results}
