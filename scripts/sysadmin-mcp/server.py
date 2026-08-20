#!/usr/bin/env python3
"""sysadmin-mcp — the ai-stack "systems-administrator" MCP server.

A dependency-free stdio MCP server (mirrors scripts/mattermost-mcp/server.py) exposing SEMANTIC
administrative tools for the ai-stack, so the sysadmin AI persona operates the stack through gated
tool calls instead of ad-hoc shell. Determinism/safety live IN the tools; the persona reasons.

READ-ONLY investigative tools (safe anytime, no gate):
  • disk_report       — full disk-pressure picture + a verdict/recommendation
  • container_status  — containers + their state; flags exited/restarting/unhealthy
  • stack_health      — one-line stack health summary
  • container_logs    — tail a container's logs (investigation)
  • volume_report     — volumes + dangling set (REPORT ONLY; flags protected data volumes)
  • reclaim_plan      — read-only: what a safe reclaim WOULD free + a confirm_token

GATED mutating tools (fall through the bridge approval relay; also token/idle-guarded in code):
  • reclaim_execute   — perform the safe reclaim; refuses without a plan-bound confirm_token

Elevated vhdx compaction is intentionally NOT here — it is a separate RunLevel-Highest Scheduled
Task triggered behind the same gate (compaction.py, later increment).

STDLIB ONLY. Fail-soft: a tool returns a readable error string, never crashes the server.
Runs as a HOST Windows process (can reach docker / wsl / schtasks directly).
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sysadmin as sa  # noqa: E402
import executor as ex  # noqa: E402
import compaction as cp  # noqa: E402

PROTOCOL_VERSION = "2024-11-05"


# ── renderers (dict → readable markdown for the persona) ───────────────────────
def _line(label: str, val) -> str:
    return f"- **{label}:** {val}"


def render_disk_report(d: dict) -> str:
    out = ["# Disk report"]
    v = d.get("verdict", {})
    out.append(f"**Severity: {v.get('severity', '?').upper()}** — {v.get('recommended_action', '')}")
    if v.get("flags"):
        out.append("\n**Flags:**")
        out += [f"  - {f}" for f in v["flags"]]

    sys_d, dat_d = d["drives"]["system"], d["drives"]["data"]
    out.append("\n## Drives")
    out.append(_line(sys_d.get("drive", "sys"), f"{sys_d.get('free_gb', '?')} GB free / {sys_d.get('total_gb', '?')} GB"))
    out.append(_line(dat_d.get("drive", "data"), f"{dat_d.get('free_gb', '?')} GB free / {dat_d.get('total_gb', '?')} GB"))

    vh = d.get("vhdx", {})
    out.append("\n## Docker data vhdx")
    if "error" in vh:
        out.append(f"  - error: {vh['error']}")
    else:
        out.append(_line("allocated", f"{vh.get('allocated_gb', '?')} GB"))
        out.append(_line("used inside", f"{vh.get('used_inside_gb', '?')} GB"))
        out.append(_line("trapped (compactable)", f"{vh.get('trapped_gb', '?')} GB"))

    dk = d.get("docker", {})
    if isinstance(dk, dict) and "error" not in dk:
        out.append("\n## docker system df")
        for t, row in dk.items():
            out.append(f"  - {t}: {row.get('size_gb')} GB (reclaimable {row.get('reclaimable_gb')} GB, {row.get('active')}/{row.get('count')} active)")
    elif isinstance(dk, dict):
        out.append(f"\n## docker system df\n  - error: {dk.get('error')}")

    out.append("\n## Top containers by writable layer")
    for c in d.get("top_containers", [])[:8]:
        if "error" in c:
            out.append(f"  - error: {c['error']}")
        else:
            out.append(f"  - {c['name']}: {c['rw_gb']} GB  ({c.get('status', '')})")

    out.append("\n## ao-worker /tmp")
    for w in d.get("ao_worker_tmp", []):
        if "tmp_gb" in w:
            out.append(f"  - {w['worker']}: {w['tmp_gb']} GB ({w.get('lc_jsonl_files')} lc-*.jsonl)")
        else:
            out.append(f"  - {w.get('worker')}: {w.get('state') or w.get('error')}")

    logs = d.get("big_logs", [])
    out.append("\n## Oversized container logs")
    if not logs:
        out.append("  - (none over threshold)")
    for lg in logs[:8]:
        if "error" in lg:
            out.append(f"  - error: {lg['error']}")
        else:
            out.append(f"  - {lg['container']}: {lg['log_gb']} GB")
    return "\n".join(out)


def render_container_status(d: dict) -> str:
    if "error" in d:
        return f"error: {d['error']}"
    out = [f"# Containers: {d['running']}/{d['total']} running, {len(d['problems'])} problem(s)"]
    if d["problems"]:
        out.append("\n**Problems:**")
        for c in d["problems"]:
            out.append(f"  - {c['name']}: {c['status']}  ({c['image']})")
    shown = d.get("containers", [])
    if shown and shown is not d["problems"]:
        out.append("\n**All:**")
        for c in shown:
            out.append(f"  - {c['name']}: {c['status']}")
    return "\n".join(out)


def render_stack_health(d: dict) -> str:
    if "error" in d:
        return f"error: {d['error']}"
    head = "HEALTHY" if d["healthy"] else f"{d['problem_count']} PROBLEM(S)"
    out = [f"Stack: {d['running']}/{d['total']} running — {head}"]
    for c in d.get("problems", []):
        out.append(f"  - {c['name']}: {c['status']}")
    return "\n".join(out)


def render_container_logs(d: dict) -> str:
    if "error" in d:
        return f"error: {d['error']}"
    return f"# logs {d['container']} (last {d['tail']})\n```\n{d['logs']}\n```"


def render_volume_report(d: dict) -> str:
    if "error" in d:
        return f"error: {d['error']}"
    out = [f"# Volumes: {d['total']} total, {d['dangling_total']} dangling", d["note"]]
    if d["dangling_protected_DO_NOT_PRUNE"]:
        out.append("\n**Dangling but PROTECTED (live data — never prune):**")
        out += [f"  - {v}" for v in d["dangling_protected_DO_NOT_PRUNE"]]
    if d["dangling_named_other"]:
        out.append("\n**Dangling named (review before any action):**")
        out += [f"  - {v}" for v in d["dangling_named_other"]]
    if d["dangling_anonymous"]:
        out.append(f"\n**Dangling anonymous:** {len(d['dangling_anonymous'])} (hex-id volumes)")
    return "\n".join(out)


def render_reclaim_plan(d: dict) -> str:
    out = ["# Safe-reclaim plan (read-only)"]
    est = d.get("estimate_gb", {})
    out.append(f"**Estimated free:** ao-worker /tmp {est.get('ao_worker_tmp', 0)} GB, "
               f"logs {est.get('logs', 0)} GB, dangling images ≤{est.get('images_dangling', 0)} GB, "
               f"build cache {est.get('build_cache', 0)} GB")
    out.append(f"**confirm_token:** `{d.get('confirm_token')}`  (pass to reclaim_execute)")
    out.append("\n**ao-workers:**")
    for w in d.get("workers", []):
        tag = "CLEAR" if w.get("clearable") else "skip"
        extra = f", {w.get('tmp_gb')} GB / {w.get('files')} files" if w.get("clearable") else ""
        out.append(f"  - {w.get('worker')}: {tag} — {w.get('reason')}{extra}")
    logs = d.get("logs_to_truncate", [])
    out.append("\n**logs to truncate:** " + (", ".join(f"{lg['container']} ({lg['log_gb']}GB)" for lg in logs) or "(none)"))
    out.append(f"**prune:** images={d.get('prune_images')}, build_cache={d.get('prune_builder')}")
    out.append("\n" + d.get("note", ""))
    return "\n".join(out)


def render_reclaim_result(d: dict) -> str:
    if d.get("refused"):
        return (f"REFUSED (fail-closed): {d.get('reason')}\n"
                f"fresh confirm_token: `{d.get('current_token')}` — review the plan and retry.")
    out = ["# Safe reclaim done"]
    fg = d.get("freed_gb", {})
    out.append(f"**Freed ≈ {fg.get('total_approx', 0)} GB** (tmp {fg.get('ao_worker_tmp', 0)} GB, logs {fg.get('logs', 0)} GB) + dangling images/cache")
    for c in d.get("cleared_workers", []):
        out.append(f"  - cleared {c['worker']}: {c.get('freed_gb')} GB ({c.get('files')} files)")
    for s in d.get("skipped_workers", []):
        out.append(f"  - skipped {s['worker']}: {s.get('reason')}")
    if d.get("truncated_logs"):
        out.append(f"  - truncated logs: {len(d['truncated_logs'])}")
    out.append(f"  - prune: images={d.get('pruned', {}).get('images')}, cache={d.get('pruned', {}).get('build_cache')}")
    return "\n".join(out)


# ── tool wrappers ──────────────────────────────────────────────────────────────
def tool_disk_report(args: dict) -> str:
    return render_disk_report(sa.disk_report())


def tool_reclaim_plan(args: dict) -> str:
    return render_reclaim_plan(ex.reclaim_plan())


def tool_reclaim_execute(args: dict) -> str:
    return render_reclaim_result(ex.reclaim_execute(args.get("confirm_token")))


def render_compact_plan(d: dict) -> str:
    out = ["# vhdx compaction plan (read-only)"]
    out.append(f"- C: free: {d.get('c_free_gb')} GB")
    out.append(f"- vhdx trapped: {d.get('trapped_gb')} GB (min to warrant: {d.get('min_trapped_gb')} GB)")
    out.append(f"- warranted: **{d.get('warranted')}**   task registered: **{d.get('task_registered')}**")
    out.append(f"- confirm_token: `{d.get('confirm_token')}`")
    out.append("\n" + d.get("note", ""))
    return "\n".join(out)


def render_compact_result(d: dict) -> str:
    if d.get("refused"):
        return f"REFUSED (fail-closed): {d.get('reason')}" + (
            f"\nfresh confirm_token: `{d['plan']['confirm_token']}`" if d.get("plan") else "")
    if d.get("started"):
        return f"STARTED: {d.get('message')} (trapped ~{d.get('trapped_gb')} GB)"
    return f"NOT started: {d.get('error', 'unknown')}"


def render_compact_status(d: dict) -> str:
    st = d.get("state", "?")
    if st in ("idle", "running") and "reclaimed_gb" not in d:
        return f"compaction: {st} — {d.get('note', '')}"
    lines = [f"# compaction: {st}",
             f"- vhdx: {d.get('vhdx_before_gb')} -> {d.get('vhdx_after_gb')} GB (reclaimed {d.get('reclaimed_gb')})",
             f"- C: free: {d.get('c_free_before_gb')} -> {d.get('c_free_after_gb')} GB",
             f"- stack returned: {d.get('stack_returned')} ({d.get('post_running')}/{d.get('pre_running')} running)"]
    if d.get("error"):
        lines.append(f"- error: {d['error']}")
    for n in (d.get("notes") or [])[-6:]:
        lines.append(f"  · {n}")
    return "\n".join(lines)


def tool_compact_plan(args: dict) -> str:
    return render_compact_plan(cp.compact_plan())


def tool_compact_execute(args: dict) -> str:
    return render_compact_result(cp.compact_execute(args.get("confirm_token")))


def tool_compact_status(args: dict) -> str:
    return render_compact_status(cp.compact_status())


def tool_container_status(args: dict) -> str:
    return render_container_status(sa.container_status(
        name_filter=args.get("name"), only_problems=bool(args.get("only_problems", False))))


def tool_stack_health(args: dict) -> str:
    return render_stack_health(sa.stack_health())


def tool_container_logs(args: dict) -> str:
    return render_container_logs(sa.container_logs(args.get("name"), int(args.get("tail", 200))))


def tool_volume_report(args: dict) -> str:
    return render_volume_report(sa.volume_report())


TOOLS = {
    "disk_report": {
        "fn": tool_disk_report,
        "description": ("Full disk-pressure picture of the ai-stack host: C:/D: free space, the "
                        "Docker data vhdx allocated-vs-used (trapped/compactable) space, docker "
                        "system df, top containers by writable layer, ao-worker /tmp session-log "
                        "bloat, and oversized container logs — plus a verdict (healthy/attention/"
                        "critical) and a recommended action. READ-ONLY."),
        "schema": {"type": "object", "properties": {}},
    },
    "container_status": {
        "fn": tool_container_status,
        "description": ("List containers and their state; flags exited/restarting/unhealthy/dead. "
                        "Optional `name` substring filter and `only_problems`. READ-ONLY."),
        "schema": {"type": "object", "properties": {
            "name": {"type": "string", "description": "substring filter on container name"},
            "only_problems": {"type": "boolean", "description": "only exited/restarting/unhealthy"},
        }},
    },
    "stack_health": {
        "fn": tool_stack_health,
        "description": "One-line stack health: running/total counts + any problem containers. READ-ONLY.",
        "schema": {"type": "object", "properties": {}},
    },
    "container_logs": {
        "fn": tool_container_logs,
        "description": "Tail a container's logs for investigation (default 200 lines, max 2000). READ-ONLY.",
        "schema": {"type": "object", "properties": {
            "name": {"type": "string", "description": "container name"},
            "tail": {"type": "integer", "description": "lines to tail (1-2000, default 200)"},
        }, "required": ["name"]},
    },
    "volume_report": {
        "fn": tool_volume_report,
        "description": ("List Docker volumes and the dangling set, flagging protected data volumes "
                        "that must NEVER be pruned. REPORT ONLY — does not and cannot prune."),
        "schema": {"type": "object", "properties": {}},
    },
    "reclaim_plan": {
        "fn": tool_reclaim_plan,
        "description": ("READ-ONLY: what a SAFE disk reclaim would do (clear idle ao-worker /tmp "
                        "session logs, truncate oversized container logs, prune dangling images + "
                        "build cache) and an estimate of bytes freed. Returns a `confirm_token` to "
                        "pass to reclaim_execute. Never touches volumes; skips busy workers."),
        "schema": {"type": "object", "properties": {}},
    },
    "reclaim_execute": {
        "fn": tool_reclaim_execute,
        "description": ("GATED / MUTATING: perform the safe reclaim from reclaim_plan. REQUIRES the "
                        "`confirm_token` from a current reclaim_plan (refuses/fail-closed otherwise). "
                        "Re-verifies each worker is idle at execution time; truncates logs (never "
                        "deletes data); never touches volumes. Does NOT compact the vhdx."),
        "schema": {"type": "object", "properties": {
            "confirm_token": {"type": "string", "description": "token from a current reclaim_plan"},
        }, "required": ["confirm_token"]},
    },
    "compact_plan": {
        "fn": tool_compact_plan,
        "description": ("READ-ONLY: is a Docker-vhdx compaction warranted (trapped/compactable GB vs "
                        "threshold), is the elevated task registered, C: free — plus a confirm_token. "
                        "Compaction returns trapped space to C: but needs a brief full-Docker downtime."),
        "schema": {"type": "object", "properties": {}},
    },
    "compact_execute": {
        "fn": tool_compact_execute,
        "description": ("GATED / DOWNTIME: trigger the elevated vhdx compaction (RunLevel-Highest task). "
                        "REQUIRES a current compact_plan `confirm_token`; refuses if not warranted or the "
                        "task isn't registered. Returns immediately (async) — Docker goes down ~10-15 min "
                        "and auto-recovers; poll compact_status. The health watchdog is paused/re-armed "
                        "automatically and the stack's return is verified."),
        "schema": {"type": "object", "properties": {
            "confirm_token": {"type": "string", "description": "token from a current compact_plan"},
        }, "required": ["confirm_token"]},
    },
    "compact_status": {
        "fn": tool_compact_status,
        "description": "READ-ONLY: progress/outcome of the most recent vhdx compaction (poll after compact_execute).",
        "schema": {"type": "object", "properties": {}},
    },
}


# ── JSON-RPC / MCP loop (identical shape to mattermost-mcp/server.py) ───────────
def _result(rid, result):
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def _error(rid, code, message):
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def handle(msg: dict):
    method = msg.get("method")
    rid = msg.get("id")
    if method == "initialize":
        return _result(rid, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "sysadmin", "version": "1.0.0"},
        })
    if method in ("notifications/initialized", "initialized"):
        return None
    if method == "ping":
        return _result(rid, {})
    if method == "tools/list":
        return _result(rid, {"tools": [
            {"name": n, "description": t["description"], "inputSchema": t["schema"]}
            for n, t in TOOLS.items()]})
    if method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        tool = TOOLS.get(name)
        if not tool:
            return _error(rid, -32602, f"unknown tool: {name}")
        try:
            text = tool["fn"](args)
            return _result(rid, {"content": [{"type": "text", "text": text}]})
        except Exception as e:  # noqa: BLE001
            return _result(rid, {"content": [{"type": "text", "text": f"error: {e}"}],
                                 "isError": True})
    if rid is not None:
        return _error(rid, -32601, f"method not found: {method}")
    return None


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        resp = handle(msg)
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
