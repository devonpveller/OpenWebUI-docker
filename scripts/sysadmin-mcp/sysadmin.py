#!/usr/bin/env python3
"""Core logic for the sysadmin-mcp server — the ai-stack "systems-administrator" capability set.

This module is intentionally SEPARATE from the MCP stdio framing (server.py) so every probe is a
plain function returning a structured dict and can be unit-tested directly (see test_sysadmin.py).

Design goals (mirrors scripts/mattermost-mcp/server.py house style):
  • STDLIB ONLY — subprocess/json/shutil/os. No pip install; survives venv churn.
  • Fail-soft — every probe catches its own errors and returns an {"error": ...} field rather than
    raising, so one broken sub-probe never blanks the whole report.
  • READ-ONLY — nothing in this file mutates Docker/host state. Mutating tools live in a separate
    module (executor.py) behind the approval gate; keeping reads pure means the investigative
    surface is safe to call anytime, by anyone, including on a schedule.

Runs as a HOST Windows process (like the claude-sessions bridge), so it can shell out to the
docker CLI, `wsl -d docker-desktop`, and Windows tools directly.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(_HERE))
GB = 1000 ** 3  # docker CLI reports decimal (SI) sizes; use decimal so our math matches its output

# ── config ────────────────────────────────────────────────────────────────────
_CONFIG_CACHE: dict | None = None


def load_config() -> dict:
    global _CONFIG_CACHE
    if _CONFIG_CACHE is not None:
        return _CONFIG_CACHE
    cfg = {}
    try:
        with open(os.path.join(_HERE, "config.json"), "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
    except Exception:  # noqa: BLE001 - fall back to built-in defaults
        cfg = {}
    if not cfg.get("vhdx_path"):
        local = os.environ.get("LOCALAPPDATA", "")
        cfg["vhdx_path"] = os.path.join(local, "Docker", "wsl", "disk", "docker_data.vhdx")
    cfg.setdefault("system_drive", "C:")
    cfg.setdefault("data_drive", "D:")
    cfg.setdefault("docker_desktop_mount", "/mnt/docker-desktop-disk")
    cfg.setdefault("ao_workers", ["ao-worker-1", "ao-worker-2"])
    cfg.setdefault("thresholds", {})
    cfg.setdefault("protected_volume_substrings", [])
    _CONFIG_CACHE = cfg
    return cfg


# ── subprocess helpers ─────────────────────────────────────────────────────────
def _run(cmd: list[str], timeout: int = 30) -> dict:
    """Run a command (arg list, no shell). Returns {rc, out, err}. Never raises."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return {"rc": p.returncode, "out": p.stdout or "", "err": p.stderr or ""}
    except FileNotFoundError as e:
        return {"rc": 127, "out": "", "err": f"not found: {e}"}
    except subprocess.TimeoutExpired:
        return {"rc": 124, "out": "", "err": f"timeout after {timeout}s"}
    except Exception as e:  # noqa: BLE001
        return {"rc": 1, "out": "", "err": str(e)}


def _docker(args: list[str], timeout: int = 30) -> dict:
    return _run(["docker"] + args, timeout)


def _wsl_dd(args: list[str], timeout: int = 30) -> dict:
    """Run a command inside the docker-desktop WSL distro (busybox)."""
    return _run(["wsl", "-d", "docker-desktop", "-e"] + args, timeout)


# ── pure parse helpers (unit-testable, no side effects) ────────────────────────
def parse_size_to_bytes(s: str) -> float:
    """Parse a docker-style size like '125GB', '45.9MB', '0B', '84.18GB'. Decimal (SI) units.
    Only the leading number+unit is read, so '125GB (virtual 126GB)' → the 125GB writable part."""
    if not s:
        return 0.0
    s = s.strip()
    units = [("TB", 1000 ** 4), ("GB", 1000 ** 3), ("MB", 1000 ** 2),
             ("kB", 1000), ("KB", 1000), ("B", 1)]
    tok = s.split()[0]  # drop the "(virtual ...)" tail
    for suf, mult in units:
        if tok.endswith(suf):
            num = tok[: -len(suf)].strip()
            try:
                return float(num) * mult
            except ValueError:
                return 0.0
    try:
        return float(tok)  # bare number = bytes
    except ValueError:
        return 0.0


def parse_df_k_used_bytes(df_output: str) -> float:
    """Parse busybox `df -k <mount>` output; return Used bytes from the data row."""
    lines = [ln for ln in df_output.splitlines() if ln.strip()]
    for ln in lines[1:]:  # skip header
        cols = ln.split()
        if len(cols) >= 3:
            try:
                return float(cols[2]) * 1024  # Used is column 3, in 1K blocks
            except ValueError:
                continue
    return 0.0


def _gb(n: float) -> float:
    return round(n / GB, 1)


# ── probes (read-only) ─────────────────────────────────────────────────────────
def _drive_free(drive: str) -> dict:
    try:
        root = drive if drive.endswith("\\") else drive + "\\"
        total, used, free = shutil.disk_usage(root)
        return {"drive": drive, "free_gb": _gb(free), "total_gb": _gb(total), "used_gb": _gb(used)}
    except Exception as e:  # noqa: BLE001
        return {"drive": drive, "error": str(e)}


def _system_df() -> dict:
    """docker system df totals, keyed by type."""
    fmt = "{{.Type}}|{{.TotalCount}}|{{.Active}}|{{.Size}}|{{.Reclaimable}}"
    r = _docker(["system", "df", "--format", fmt])
    if r["rc"] != 0:
        return {"error": r["err"].strip() or "docker system df failed"}
    out = {}
    for ln in r["out"].splitlines():
        parts = ln.split("|")
        if len(parts) == 5:
            t, total, active, size, recl = parts
            out[t.strip()] = {
                "count": total.strip(), "active": active.strip(),
                "size_gb": _gb(parse_size_to_bytes(size)),
                "reclaimable_gb": _gb(parse_size_to_bytes(recl)),
            }
    return out


def _container_sizes(top: int = 12) -> list[dict]:
    """Per-container writable-layer sizes, largest first."""
    r = _docker(["ps", "-a", "--size", "--format", "{{.Names}}|{{.Size}}|{{.Status}}"])
    if r["rc"] != 0:
        return [{"error": r["err"].strip() or "docker ps failed"}]
    rows = []
    for ln in r["out"].splitlines():
        parts = ln.split("|")
        if len(parts) >= 2:
            name, size = parts[0], parts[1]
            status = parts[2] if len(parts) > 2 else ""
            rows.append({"name": name, "rw_gb": _gb(parse_size_to_bytes(size)),
                         "raw": size.strip(), "status": status.strip()})
    rows.sort(key=lambda x: x.get("rw_gb", 0), reverse=True)
    return rows[:top]


def _ao_worker_tmp() -> list[dict]:
    cfg = load_config()
    running = set()
    r = _docker(["ps", "--format", "{{.Names}}"])
    if r["rc"] == 0:
        running = {ln.strip() for ln in r["out"].splitlines() if ln.strip()}
    out = []
    for w in cfg["ao_workers"]:
        if w not in running:
            out.append({"worker": w, "state": "not running"})
            continue
        du = _docker(["exec", w, "du", "-sk", "/tmp"], timeout=60)
        if du["rc"] != 0:
            out.append({"worker": w, "error": du["err"].strip() or "du failed"})
            continue
        try:
            kb = float(du["out"].split()[0])
            # count the little-coder session-log offenders specifically
            cnt = _docker(["exec", w, "sh", "-c",
                           "ls -1 /tmp/lc-*.jsonl 2>/dev/null | wc -l"], timeout=60)
            n = cnt["out"].strip() if cnt["rc"] == 0 else "?"
            out.append({"worker": w, "tmp_gb": _gb(kb * 1024), "lc_jsonl_files": n})
        except Exception as e:  # noqa: BLE001
            out.append({"worker": w, "error": str(e)})
    return out


def _big_logs(min_gb: float) -> list[dict]:
    """Container json logs larger than min_gb, mapped to container names best-effort."""
    cfg = load_config()
    mount = cfg["docker_desktop_mount"]
    min_bytes = int(min_gb * GB)
    find = _wsl_dd(["find", f"{mount}/data/docker/containers", "-name", "*-json.log",
                    "-size", f"+{min_bytes}c", "-exec", "du", "-k", "{}", ";"], timeout=60)
    if find["rc"] != 0:
        return [{"error": find["err"].strip() or "log scan failed"}]
    # id → name map
    idmap = {}
    ins = _docker(["ps", "-a", "--format", "{{.ID}}|{{.Names}}"])
    if ins["rc"] == 0:
        for ln in ins["out"].splitlines():
            parts = ln.split("|")
            if len(parts) == 2:
                idmap[parts[0].strip()] = parts[1].strip()  # short id (12 chars)
    rows = []
    for ln in find["out"].splitlines():
        cols = ln.split(None, 1)
        if len(cols) != 2:
            continue
        try:
            size_gb = _gb(float(cols[0]) * 1024)
        except ValueError:
            continue
        path = cols[1].strip()
        cid = ""
        # path .../containers/<64hexid>/<64hexid>-json.log
        seg = [p for p in path.split("/") if len(p) == 64]
        if seg:
            cid = seg[0]
        name = idmap.get(cid[:12], cid[:12] or "?")
        rows.append({"container": name, "log_gb": size_gb, "path": path})
    rows.sort(key=lambda x: x.get("log_gb", 0), reverse=True)
    return rows


def _vhdx_trapped() -> dict:
    cfg = load_config()
    vhdx = cfg["vhdx_path"]
    info = {"path": vhdx}
    try:
        info["allocated_gb"] = _gb(os.path.getsize(vhdx))
    except Exception as e:  # noqa: BLE001
        info["error"] = f"cannot stat vhdx: {e}"
        return info
    df = _wsl_dd(["df", "-k", cfg["docker_desktop_mount"]], timeout=30)
    if df["rc"] == 0:
        used = parse_df_k_used_bytes(df["out"])
        info["used_inside_gb"] = _gb(used)
        info["trapped_gb"] = round(info["allocated_gb"] - info["used_inside_gb"], 1)
    else:
        info["note"] = "could not read used space inside vhdx"
    return info


# ── top-level reports ──────────────────────────────────────────────────────────
def disk_report() -> dict:
    """Full disk-pressure picture + a verdict/recommendation the persona can act on."""
    cfg = load_config()
    th = cfg["thresholds"]
    rep = {
        "drives": {
            "system": _drive_free(cfg["system_drive"]),
            "data": _drive_free(cfg["data_drive"]),
        },
        "docker": _system_df(),
        "top_containers": _container_sizes(),
        "ao_worker_tmp": _ao_worker_tmp(),
        "big_logs": _big_logs(max(0.5, th.get("container_log_warn_gb", 2))),
        "vhdx": _vhdx_trapped(),
    }
    # ── verdict ──
    flags = []
    c_free = rep["drives"]["system"].get("free_gb")
    if c_free is not None:
        if c_free < th.get("c_free_critical_gb", 25):
            flags.append(("critical", f"{cfg['system_drive']} free {c_free} GB < critical {th.get('c_free_critical_gb', 25)} GB"))
        elif c_free < th.get("c_free_warn_gb", 60):
            flags.append(("warn", f"{cfg['system_drive']} free {c_free} GB < warn {th.get('c_free_warn_gb', 60)} GB"))
    for c in rep["top_containers"]:
        if c.get("rw_gb", 0) >= th.get("container_layer_warn_gb", 40):
            flags.append(("warn", f"container {c['name']} writable layer {c['rw_gb']} GB"))
    for w in rep["ao_worker_tmp"]:
        if w.get("tmp_gb", 0) >= th.get("ao_worker_tmp_warn_gb", 20):
            flags.append(("warn", f"{w['worker']} /tmp {w['tmp_gb']} GB ({w.get('lc_jsonl_files')} lc-*.jsonl)"))
    for lg in rep["big_logs"]:
        if lg.get("log_gb", 0) >= th.get("container_log_warn_gb", 2):
            flags.append(("warn", f"{lg['container']} json log {lg['log_gb']} GB"))
    trapped = rep["vhdx"].get("trapped_gb")
    if trapped is not None and trapped >= th.get("vhdx_trapped_warn_gb", 60):
        flags.append(("compaction", f"vhdx trapped ≈ {trapped} GB (compaction would reclaim to {cfg['system_drive']})"))
    containers_total = 0.0
    if isinstance(rep["docker"], dict):
        containers_total = rep["docker"].get("Containers", {}).get("size_gb", 0) or 0

    severity = "healthy"
    if any(f[0] == "critical" for f in flags):
        severity = "critical"
    elif flags:
        severity = "attention"

    reclaim_targets = []
    if any(w.get("tmp_gb", 0) >= th.get("ao_worker_tmp_warn_gb", 20) for w in rep["ao_worker_tmp"] if isinstance(w, dict)):
        reclaim_targets.append("ao-worker /tmp session logs (safe, idle-gated)")
    if any(lg.get("log_gb", 0) >= th.get("container_log_warn_gb", 2) for lg in rep["big_logs"] if isinstance(lg, dict)):
        reclaim_targets.append("truncate oversized container logs (safe)")
    compaction = trapped is not None and trapped >= th.get("vhdx_trapped_warn_gb", 60)

    rep["verdict"] = {
        "severity": severity,
        "flags": [f"[{lvl}] {msg}" for lvl, msg in flags],
        "safe_reclaim_available": bool(reclaim_targets),
        "safe_reclaim_targets": reclaim_targets,
        "compaction_recommended": compaction,
        "recommended_action": _recommend(severity, reclaim_targets, compaction),
    }
    return rep


def _recommend(severity: str, reclaim_targets: list, compaction: bool) -> str:
    if severity == "healthy":
        return "none — disk pressure within thresholds."
    parts = []
    if reclaim_targets:
        parts.append("run safe reclaim (no downtime)")
    if compaction:
        parts.append("propose vhdx compaction (brief Docker downtime, operator approval)")
    if not parts:
        parts.append("investigate — thresholds tripped without an obvious safe reclaim target")
    return "; ".join(parts)


def container_status(name_filter: str | None = None, only_problems: bool = False) -> dict:
    r = _docker(["ps", "-a", "--format", "{{.Names}}|{{.Status}}|{{.Image}}"])
    if r["rc"] != 0:
        return {"error": r["err"].strip() or "docker ps failed"}
    all_rows, problems = [], []
    for ln in r["out"].splitlines():
        parts = ln.split("|")
        if len(parts) < 2:
            continue
        name, status = parts[0].strip(), parts[1].strip()
        image = parts[2].strip() if len(parts) > 2 else ""
        if name_filter and name_filter.lower() not in name.lower():
            continue
        row = {"name": name, "status": status, "image": image}
        all_rows.append(row)
        low = status.lower()
        if low.startswith("exited") or "restarting" in low or "unhealthy" in low or "dead" in low:
            problems.append(row)
    running = sum(1 for r in all_rows if r["status"].lower().startswith("up"))
    return {
        "total": len(all_rows), "running": running,
        "problems": problems,
        "containers": problems if only_problems else all_rows,
    }


def stack_health() -> dict:
    cs = container_status()
    if "error" in cs:
        return cs
    return {
        "running": cs["running"],
        "total": cs["total"],
        "problem_count": len(cs["problems"]),
        "problems": cs["problems"],
        "healthy": len(cs["problems"]) == 0,
    }


def container_logs(name: str, tail: int = 200) -> dict:
    if not name:
        return {"error": "name is required"}
    tail = max(1, min(int(tail), 2000))
    r = _docker(["logs", "--tail", str(tail), name], timeout=30)
    if r["rc"] != 0:
        return {"error": r["err"].strip() or f"could not read logs for {name}"}
    text = (r["out"] + r["err"])[-12000:]  # cap payload
    return {"container": name, "tail": tail, "logs": text}


def volume_report() -> dict:
    """List volumes + dangling set. REPORT ONLY — flags protected data volumes; never prunes."""
    cfg = load_config()
    protected = cfg["protected_volume_substrings"]
    ls = _docker(["volume", "ls", "--format", "{{.Name}}"])
    if ls["rc"] != 0:
        return {"error": ls["err"].strip() or "docker volume ls failed"}
    dangling = set()
    dl = _docker(["volume", "ls", "-f", "dangling=true", "--format", "{{.Name}}"])
    if dl["rc"] == 0:
        dangling = {ln.strip() for ln in dl["out"].splitlines() if ln.strip()}
    all_vols = [ln.strip() for ln in ls["out"].splitlines() if ln.strip()]

    def is_protected(v: str) -> bool:
        return any(s in v for s in protected)

    dangling_protected = sorted(v for v in dangling if is_protected(v))
    dangling_anon = sorted(v for v in dangling if not is_protected(v) and len(v) == 64)
    dangling_other = sorted(v for v in dangling if not is_protected(v) and len(v) != 64)
    return {
        "total": len(all_vols),
        "dangling_total": len(dangling),
        "dangling_protected_DO_NOT_PRUNE": dangling_protected,
        "dangling_anonymous": dangling_anon,
        "dangling_named_other": dangling_other,
        "note": "REPORT ONLY. Never `docker volume prune`; named data volumes above hold live state.",
    }
