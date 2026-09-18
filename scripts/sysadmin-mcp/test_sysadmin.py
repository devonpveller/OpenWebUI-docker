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


def test_volume_age() -> None:
    """A protected NAME is not proof a volume is live.

    After the 2026-08-21 per-plane split both `ai-stack_openwebui-data` (dead) and the live
    `frontend_openwebui-data` matched the "openwebui" substring, so the report stamped
    DO NOT PRUNE on 10 GB nothing had written to since the day before the split. Dangling already
    means no container references it; age is the second signal. Only the wsl/docker boundary is
    stubbed here -- the parsing and the classification under test are the real ones.
    """
    print("VOLUME AGE - protected-and-live vs protected-but-cold")
    import time as _t
    now = int(_t.time())
    root = "/mnt/docker-desktop-disk/data/docker/volumes"
    vols = ["ai-stack_openwebui-data", "ai-stack_mnemory-data", "deadbeef" * 8, "some_other_vol"]

    def fake_wsl(args, timeout=30):
        cold = now - 24 * 86400          # the real Aug-20 orphan: 24 days at time of writing
        warm = now - 1 * 86400
        lines = [
            f"{cold} {root}/ai-stack_openwebui-data",
            f"{cold} {root}/ai-stack_openwebui-data/_data",
            f"{cold} {root}/ai-stack_openwebui-data/_data/webui.db",
            f"{warm} {root}/ai-stack_mnemory-data/_data/state.db",
            f"{now} {root}",                      # the root itself must not become a volume
            "garbage-with-no-space",              # malformed lines must be skipped, not crash
            f"notanumber {root}/some_other_vol/_data",
        ]
        return {"rc": 0, "out": "\n".join(lines), "err": ""}

    def fake_docker(args, timeout=30):
        if "dangling=true" in args:
            return {"rc": 0, "out": "\n".join(vols), "err": ""}
        return {"rc": 0, "out": "\n".join(vols + ["frontend_openwebui-data"]), "err": ""}

    real_wsl, real_docker, real_cfg = sa._wsl_dd, sa._docker, sa.load_config
    try:
        sa._wsl_dd, sa._docker = fake_wsl, fake_docker
        cfg = dict(real_cfg())
        cfg["thresholds"] = dict(cfg["thresholds"], volume_orphan_cold_days=14)
        sa.load_config = lambda: cfg

        ages = sa._volume_last_write()
        check("newest mtime wins per volume", ages["ai-stack_openwebui-data"] == now - 24 * 86400,
              str(ages.get("ai-stack_openwebui-data")))
        check("volumes root is not itself a volume", "" not in ages and root not in ages, str(list(ages)))
        check("malformed lines skipped", "some_other_vol" not in ages, str(list(ages)))

        vr = sa.volume_report()
        cold_names = [e["volume"] for e in vr["dangling_protected_cold"]]
        check("Aug-20 orphan lands in COLD", "ai-stack_openwebui-data" in cold_names, str(cold_names))
        check("  ... and NOT in DO_NOT_PRUNE",
              "ai-stack_openwebui-data" not in vr["dangling_protected_DO_NOT_PRUNE"],
              str(vr["dangling_protected_DO_NOT_PRUNE"]))
        check("recently-written protected volume stays DO_NOT_PRUNE",
              "ai-stack_mnemory-data" in vr["dangling_protected_DO_NOT_PRUNE"],
              str(vr["dangling_protected_DO_NOT_PRUNE"]))
        check("unknown age stays DO_NOT_PRUNE (conservative)",
              "some_other_vol" not in cold_names, str(cold_names))
        check("age is reported, not just the verdict",
              any(e["volume"] == "ai-stack_openwebui-data" and 23 <= e["age_days"] <= 25
                  for e in vr["dangling_protected_cold"]), str(vr["dangling_protected_cold"]))

        # A wsl failure must not silently reclassify every protected volume as cold.
        sa._wsl_dd = lambda args, timeout=30: {"rc": 1, "out": "", "err": "wsl down"}
        vr2 = sa.volume_report()
        check("wsl failure -> nothing is called cold", vr2["dangling_protected_cold"] == [],
              str(vr2["dangling_protected_cold"]))
        check("  ... and the protected set is intact",
              "ai-stack_openwebui-data" in vr2["dangling_protected_DO_NOT_PRUNE"],
              str(vr2["dangling_protected_DO_NOT_PRUNE"]))
    finally:
        sa._wsl_dd, sa._docker, sa.load_config = real_wsl, real_docker, real_cfg


if __name__ == "__main__":
    try:  # console may be cp1252 on Windows; never let a stray non-ASCII char crash the harness
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    only_unit = "--unit" in sys.argv
    test_unit()
    test_volume_age()
    if not only_unit:
        test_live()
        test_stdio()
    print(f"\n{_passed} passed, {_failed} failed")
    sys.exit(1 if _failed else 0)
