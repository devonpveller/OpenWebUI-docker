#!/usr/bin/env python3
"""Mutating executor for sysadmin-mcp — the GATED side of the systems-administrator capability.

Split from the read-only probes (sysadmin.py) on purpose: everything here can change stack state,
so every entry point is built to be safe by construction:

  • PLAN / EXECUTE split — `reclaim_plan()` is read-only (frictionless investigation); `reclaim_
    execute()` is the only thing that mutates and it REQUIRES a plan-bound `confirm_token`. The
    token is a hash of the plan's LISTED SET (which workers, which logs, which image tags, which
    anonymous volumes, the build-cache filter); the plan is stored under it in state/reclaim-plans/.
    Execute removes only that set, re-validating every item first: anything that became in use or
    changed since the plan is SKIPPED with the current reason, never removed. An unknown or expired
    token is refused (fail-closed) and just returns the fresh plan.
  • Idle-gated — an ao-worker's /tmp is cleared ONLY when the worker shows no active build/agent
    process AND no lc-*.jsonl was written in the last few minutes. If idleness can't be verified,
    the worker is treated as BUSY and skipped (fail-safe).
  • Scoped deletes — only `/tmp/lc-pi-*.jsonl` and `/tmp/lc-ot-*.jsonl` (the known little-coder
    session-log bloat), never a blanket `rm`. Logs are truncated (data-preserving) not deleted.
  • Docker images / build cache / ANONYMOUS volumes are docker_reclaim.py's job (the operator's
    2026-09-27 rules are stated there in full). NAMED volumes are never removed by anything here;
    there is no `docker volume` operation in THIS file, and every docker mutation docker_reclaim
    makes passes one chokepoint (docker_reclaim._mutate) that admits only three removal shapes
    and refuses every prune verb except the build-cache one.
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
import docker_reclaim as dr  # noqa: E402

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
    """A short, deterministic token bound to the LISTED SET (not exact byte sizes, so it stays
    valid across small growth between plan and execute)."""
    canon = json.dumps(actions, sort_keys=True)
    return hashlib.sha256(canon.encode()).hexdigest()[:8]


def _plans_dir() -> str:
    return os.path.join(_STATE, "reclaim-plans")


def _ttl_s() -> float:
    return float(sa.load_config()["thresholds"].get("reclaim_plan_ttl_hours", 24)) * 3600


def _save_plan(token: str, plan: dict) -> None:
    """Store the plan under its token. Best-effort: a plan that cannot be stored cannot be
    executed (execute will refuse its token), which is the fail-closed side."""
    try:
        d = _plans_dir()
        os.makedirs(d, exist_ok=True)
        now = time.time()
        for fn in os.listdir(d):  # drop expired plans
            fp = os.path.join(d, fn)
            try:
                if now - os.path.getmtime(fp) > _ttl_s():
                    os.remove(fp)
            except OSError:
                pass
        with open(os.path.join(d, f"{token}.json"), "w", encoding="utf-8") as fh:
            json.dump({"issued": now, "plan": plan}, fh)
    except OSError:
        pass


def _load_plan(token: str | None) -> dict | None:
    """The stored plan for `token`, or None when unknown, expired, or not matching its token."""
    if not token or len(token) != 8 or not all(c in "0123456789abcdef" for c in token):
        return None
    try:
        with open(os.path.join(_plans_dir(), f"{token}.json"), "r", encoding="utf-8") as fh:
            rec = json.load(fh)
    except (OSError, ValueError):
        return None
    if time.time() - float(rec.get("issued", 0)) > _ttl_s():
        return None
    plan = rec.get("plan") or {}
    if _plan_token(plan.get("actions") or {}) != token:
        return None  # edited on disk: the token no longer covers what the file lists
    return plan


def reclaim_plan(scope: str = "all", **overrides) -> dict:
    """Read-only: what a safe reclaim WOULD do + estimated bytes freed + a confirm_token.

    scope="all" (the MCP tool): idle ao-worker /tmp, oversized logs, and the docker categories.
    scope="docker" (auto_reclaim.py, the low-disk path): the docker categories only.
    overrides: image_min_age_days / anon_volume_min_age_days / builder_keep_hours (tests and
    drills; the MCP tool passes none, so config.json decides)."""
    cfg = sa.load_config()
    th = cfg["thresholds"]
    log_gb = max(0.1, th.get("container_log_warn_gb", 2))

    if scope == "all":
        workers = [worker_state(w) for w in cfg["ao_workers"]]
        logs = [lg for lg in sa._big_logs(log_gb) if "log_gb" in lg]
    else:
        workers, logs = [], []
    clear_workers = sorted(w["worker"] for w in workers if w.get("clearable"))
    tmp_est_gb = round(sum(w.get("tmp_gb", 0) for w in workers if w.get("clearable")), 2)
    logs_est_gb = round(sum(lg["log_gb"] for lg in logs), 2)

    dk = dr.plan(**overrides)
    actions = {
        "scope": scope,
        "clear_workers": clear_workers,
        "truncate_logs": sorted(lg["path"] for lg in logs),
        "docker": dk["listed"],
        "settings": {k: dk["settings"][k] for k in
                     ("image_min_age_days", "anon_volume_min_age_days", "builder_keep_hours")},
    }
    token = _plan_token(actions)
    plan = {
        "scope": scope,
        "workers": workers,
        "clear_workers": clear_workers,
        "logs_to_truncate": [{"container": lg["container"], "log_gb": lg["log_gb"],
                              "path": lg["path"]} for lg in logs],
        "docker": dk,
        "estimate_gb": {
            "ao_worker_tmp": tmp_est_gb,
            "logs": logs_est_gb,
            "images": dr._gb(dk["images"]["bytes"]),
            "build_cache": dr._gb(dk["build_cache"]["bytes"]),
            "anon_volumes": dr._gb(dk["volumes"]["bytes"]),
        },
        "actions": actions,
        "confirm_token": token,
        "note": ("Read-only plan. reclaim_execute(confirm_token) removes ONLY the listed set, "
                 "re-checking each item first (anything now in use or changed is skipped). "
                 "Busy/not-running workers are skipped. Named volumes are never removed; logs are "
                 "truncated, not deleted. Space freed inside Docker returns to C: only at the next "
                 "vhdx compaction."),
    }
    _save_plan(token, plan)
    return plan


# ── execute (the only mutating entry point) ────────────────────────────────────
def reclaim_execute(confirm_token: str | None = None, notify: bool = True) -> dict:
    """Perform the reclaim a plan listed. Refuses unless confirm_token names a current stored plan
    (fail-closed); then removes only that plan's listed set, each item re-validated now."""
    plan = _load_plan(confirm_token)
    if plan is None:
        fresh = reclaim_plan()
        _audit("reclaim_refused", {"given": confirm_token, "fresh": fresh["confirm_token"]})
        return {"refused": True,
                "reason": ("missing, unknown or expired confirm_token - review the fresh plan and "
                           "retry with its token"),
                "current_token": fresh["confirm_token"], "plan": fresh}

    act = plan["actions"]
    results = {"cleared_workers": [], "skipped_workers": [], "truncated_logs": [],
               "freed_gb": {}, "token": confirm_token}

    # 1) idle ao-worker /tmp session logs - only the listed workers, idleness re-verified now
    freed_tmp = 0.0
    for w in act.get("clear_workers", []):
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

    # 2) truncate the LISTED oversized container json logs (data-preserving reset, not delete)
    paths = act.get("truncate_logs", [])
    results["freed_gb"]["logs"] = 0
    if paths:
        was = {lg["path"]: lg for lg in plan.get("logs_to_truncate", [])}
        tr = sa._wsl_dd(["truncate", "-s", "0"] + paths, timeout=120)
        if tr["rc"] == 0:
            results["truncated_logs"] = [{"container": was.get(p, {}).get("container", p),
                                          "was_gb": was.get(p, {}).get("log_gb")} for p in paths]
            results["freed_gb"]["logs"] = round(sum(was.get(p, {}).get("log_gb") or 0 for p in paths), 2)
            _audit("reclaim_truncated_logs", {"count": len(paths), "freed_gb": results["freed_gb"]["logs"]})
        else:
            results["truncated_logs"] = [{"error": tr["err"].strip() or "truncate failed"}]

    # 3) docker: listed image tags, listed anonymous volumes, build cache older than the filter
    s = dr.settings(**act.get("settings", {}))
    dk = dr.execute(act.get("docker", {}), s, audit=_audit)
    results["docker"] = dk
    fb = dk["freed_bytes"]
    for key, out_key in (("images", "images"), ("build_cache", "build_cache"), ("volumes", "anon_volumes")):
        results["freed_gb"][out_key] = None if fb.get(key) is None else dr._gb(fb[key])
    results["summary"] = dr.summary_line(fb, dk)

    total = round(results["freed_gb"].get("ao_worker_tmp", 0) + results["freed_gb"].get("logs", 0)
                  + sum(max(0.0, results["freed_gb"].get(k) or 0.0)
                        for k in ("images", "build_cache", "anon_volumes")), 2)
    results["freed_gb"]["total_approx"] = total
    results["ok"] = True
    _audit("reclaim_done", {"freed_gb": results["freed_gb"],
                            "cleared": [c["worker"] for c in results["cleared_workers"]]})
    if notify:
        try:  # completion summary to #sysadmin (best-effort)
            import mm_post
            cleared = ", ".join(c["worker"] for c in results["cleared_workers"]) or "none"
            mm_post.post(f"\U0001f9f9 **Safe reclaim** - freed ~{total} GB inside Docker "
                         f"(cleared: {cleared}; {len(results.get('truncated_logs', []))} log(s) "
                         f"truncated; {results['summary']}). Space freed inside the Docker vhdx "
                         f"returns to C: only at the next compaction.")
        except Exception:  # noqa: BLE001
            pass
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
    total = sum(r.get("deleted", 0) for r in results if isinstance(r, dict))
    if total > 0:
        try:  # only pings #sysadmin when it actually deleted something (stays quiet otherwise)
            import mm_post
            mm_post.post(f"\U0001f9f9 **Daily /tmp sweep** removed {total} old lc-*.jsonl file(s) "
                         f"(>{days}d) from running workers.")
        except Exception:  # noqa: BLE001
            pass
    return {"days": days, "results": results}
