#!/usr/bin/env python3
"""Tests for sysadmin-mcp. Stdlib only (no pytest), matching the house style.

Run:  python scripts/sysadmin-mcp/test_sysadmin.py
       python scripts/sysadmin-mcp/test_sysadmin.py --unit   # skip live-stack/stdio tests

Sections:
  UNIT  — pure parse helpers, deterministic, no Docker needed.
  LIVE  — run the read-only probes against the actual running stack (safe: nothing mutates).
  STDIO — spawn server.py and drive it over JSON-RPC like a real MCP client.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import sysadmin as sa  # noqa: E402

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


# ── UNIT: pure parsers ─────────────────────────────────────────────────────────
def test_unit() -> None:
    print("UNIT - parse helpers")
    check("125GB", abs(sa.parse_size_to_bytes("125GB") - 125 * 1000 ** 3) < 1)
    check("45.9MB", abs(sa.parse_size_to_bytes("45.9MB") - 45.9 * 1000 ** 2) < 1)
    check("0B", sa.parse_size_to_bytes("0B") == 0.0)
    check("virtual tail ignored",
          abs(sa.parse_size_to_bytes("125GB (virtual 126GB)") - 125 * 1000 ** 3) < 1)
    check("84.18GB", abs(sa.parse_size_to_bytes("84.18GB") - 84.18 * 1000 ** 3) < 1)
    check("reclaimable with pct",
          abs(sa.parse_size_to_bytes("19.57GB (23%)") - 19.57 * 1000 ** 3) < 1)
    check("empty->0", sa.parse_size_to_bytes("") == 0.0)
    check("garbage->0", sa.parse_size_to_bytes("n/a") == 0.0)
    # df -k parsing (busybox layout: Filesystem 1K-blocks Used Available Use% Mounted)
    df = ("Filesystem           1K-blocks      Used Available Use% Mounted on\n"
          "/dev/sdc             400000000 190000000 210000000  48% /mnt/docker-desktop-disk\n")
    used = sa.parse_df_k_used_bytes(df)
    check("df used bytes", abs(used - 190000000 * 1024) < 1, f"got {used}")
    check("df empty->0", sa.parse_df_k_used_bytes("") == 0.0)


# ── LIVE: read-only probes against the running stack ───────────────────────────
def test_live() -> None:
    print("LIVE - read-only probes (nothing mutates)")
    dr = sa.disk_report()
    check("disk_report has drives", "drives" in dr and "system" in dr["drives"])
    check("disk_report has verdict", "verdict" in dr and "severity" in dr["verdict"])
    check("disk_report system free_gb numeric",
          isinstance(dr["drives"]["system"].get("free_gb"), (int, float)),
          str(dr["drives"]["system"]))
    check("verdict severity valid",
          dr["verdict"]["severity"] in ("healthy", "attention", "critical"),
          dr["verdict"].get("severity"))
    print(f"    (severity={dr['verdict']['severity']}, "
          f"C: free={dr['drives']['system'].get('free_gb')}GB, "
          f"vhdx trapped={dr['vhdx'].get('trapped_gb')}GB, "
          f"reclaim={dr['verdict']['safe_reclaim_available']}, "
          f"compaction={dr['verdict']['compaction_recommended']})")

    cs = sa.container_status()
    check("container_status counts", "running" in cs and "total" in cs, str(cs)[:200])
    check("container_status running>0", cs.get("running", 0) > 0, str(cs)[:200])

    sh = sa.stack_health()
    check("stack_health shape", "healthy" in sh and "running" in sh, str(sh)[:200])

    vr = sa.volume_report()
    check("volume_report shape", "dangling_total" in vr, str(vr)[:200])
    check("volume_report protects data vols",
          "dangling_protected_DO_NOT_PRUNE" in vr)

    # container_logs on a known-present container (llm-gateway is core); tolerate absence
    cl = sa.container_logs("llm-gateway", tail=5)
    check("container_logs returns", "logs" in cl or "error" in cl, str(cl)[:200])


# ── STDIO: drive server.py as an MCP client ────────────────────────────────────
def _rpc(proc, msg):
    proc.stdin.write(json.dumps(msg) + "\n")
    proc.stdin.flush()
    line = proc.stdout.readline()
    return json.loads(line) if line.strip() else None


def test_stdio() -> None:
    print("STDIO - JSON-RPC round-trip against server.py")
    proc = subprocess.Popen(
        [sys.executable, os.path.join(_HERE, "server.py")],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1)
    try:
        init = _rpc(proc, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        check("initialize ok",
              init and init.get("result", {}).get("serverInfo", {}).get("name") == "sysadmin",
              str(init))
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        proc.stdin.flush()
        tl = _rpc(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = [t["name"] for t in tl.get("result", {}).get("tools", [])] if tl else []
        check("tools/list has expected surface",
              set(names) == {"disk_report", "container_status", "stack_health",
                             "container_logs", "volume_report", "reclaim_plan",
                             "reclaim_execute", "compact_plan", "compact_execute",
                             "compact_status"}, str(names))
        call = _rpc(proc, {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                           "params": {"name": "stack_health", "arguments": {}}})
        txt = ""
        if call and call.get("result"):
            txt = call["result"]["content"][0]["text"]
        check("tools/call stack_health returns text", "Stack:" in txt, txt[:200])
        # both gated mutating tools must be fail-closed through the MCP boundary
        bad = _rpc(proc, {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                          "params": {"name": "reclaim_execute", "arguments": {"confirm_token": "deadbeef"}}})
        btxt = bad["result"]["content"][0]["text"] if bad and bad.get("result") else ""
        check("reclaim_execute bad token -> REFUSED via MCP", "REFUSED" in btxt, btxt[:200])
        badc = _rpc(proc, {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                           "params": {"name": "compact_execute", "arguments": {"confirm_token": "deadbeef"}}})
        bctxt = badc["result"]["content"][0]["text"] if badc and badc.get("result") else ""
        check("compact_execute bad token -> REFUSED via MCP", "REFUSED" in bctxt, bctxt[:200])
        unk = _rpc(proc, {"jsonrpc": "2.0", "id": 6, "method": "tools/call",
                          "params": {"name": "nope", "arguments": {}}})
        check("unknown tool -> error", unk and "error" in unk, str(unk))
    finally:
        try:
            proc.stdin.close()
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            proc.kill()


if __name__ == "__main__":
    try:  # console may be cp1252 on Windows; never let a stray non-ASCII char crash the harness
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    only_unit = "--unit" in sys.argv
    test_unit()
    if not only_unit:
        test_live()
        test_stdio()
    print(f"\n{_passed} passed, {_failed} failed")
    sys.exit(1 if _failed else 0)
