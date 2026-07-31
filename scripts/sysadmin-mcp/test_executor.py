#!/usr/bin/env python3
"""Tests for the GATED executor. Stdlib only.

Run:  python scripts/sysadmin-mcp/test_executor.py              # safe: plan + gating + source guard
      python scripts/sysadmin-mcp/test_executor.py --live-exec  # ALSO performs a real safe reclaim

The default run performs NO mutation. It proves:
  • the source contains no volume-mutating verbs or blanket rm (safety by construction),
  • reclaim_execute is fail-closed (wrong/absent token → refused AND nothing changed),
  • reclaim_plan is well-formed and idle detection works.
--live-exec additionally runs reclaim_execute with the real token and checks it frees safely.
"""

from __future__ import annotations

import io
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import executor as ex  # noqa: E402
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


# ── SOURCE GUARD: safety by construction ───────────────────────────────────────
def test_source_guard() -> None:
    print("SOURCE GUARD - forbidden operations must not appear in executor.py")
    src = io.open(os.path.join(_HERE, "executor.py"), encoding="utf-8").read().lower()
    forbidden = [
        "volume prune", "volume rm", "volume remove", "volume delete",
        "rmi -a", "image prune -a", "system prune", "-a -f", "--all",
        "rm -rf /tmp", "rm -rf /", "rm -r /tmp", "rm -fr",
    ]
    for f in forbidden:
        check(f"absent: {f!r}", f not in src, "FOUND forbidden op")
    # the only rm we allow is the scoped lc-*.jsonl delete
    check("scoped lc delete present",
          "rm -f /tmp/lc-pi-*.jsonl /tmp/lc-ot-*.jsonl" in
          io.open(os.path.join(_HERE, "executor.py"), encoding="utf-8").read())


# ── PLAN: read-only ────────────────────────────────────────────────────────────
def test_plan() -> None:
    print("PLAN - read-only reclaim_plan")
    p = ex.reclaim_plan()
    check("has confirm_token", isinstance(p.get("confirm_token"), str) and len(p["confirm_token"]) == 8, str(p.get("confirm_token")))
    check("has workers list", isinstance(p.get("workers"), list) and len(p["workers"]) >= 1)
    check("has estimate", "estimate_gb" in p)
    check("token is deterministic", ex.reclaim_plan()["confirm_token"] == p["confirm_token"])
    for w in p["workers"]:
        check(f"worker_state shape: {w.get('worker')}",
              "busy" in w and "clearable" in w and "reason" in w, str(w))
    print(f"    (clear_workers={p['clear_workers']}, token={p['confirm_token']}, "
          f"est={p['estimate_gb']})")


# ── GATE: fail-closed ──────────────────────────────────────────────────────────
def _worker_filecount(w: str) -> int:
    r = sa._docker(["exec", w, "sh", "-c",
                    "ls -1 /tmp/lc-pi-*.jsonl /tmp/lc-ot-*.jsonl 2>/dev/null | wc -l"], timeout=60)
    try:
        return int(r["out"].strip()) if r["rc"] == 0 else -1
    except ValueError:
        return -1


def test_gate_failclosed() -> None:
    print("GATE - reclaim_execute is fail-closed (must NOT mutate on bad token)")
    cfg = sa.load_config()
    before = {w: _worker_filecount(w) for w in cfg["ao_workers"]}
    r1 = ex.reclaim_execute(None)
    check("no token -> refused", r1.get("refused") is True, str(r1)[:160])
    r2 = ex.reclaim_execute("deadbeef")
    check("wrong token -> refused", r2.get("refused") is True, str(r2)[:160])
    check("refusal returns a fresh token", isinstance(r2.get("current_token"), str))
    after = {w: _worker_filecount(w) for w in cfg["ao_workers"]}
    # A refused reclaim performs NO deletion. The only mutation reclaim ever does to these files is
    # `rm` (which shrinks the set toward 0); live workers may ADD files concurrently. So the safety
    # invariant that tolerates concurrent writes is monotonic non-decrease per worker — a wipe would
    # show up as a collapse to ~0, which this catches.
    check("refused calls deleted NOTHING (count did not shrink)",
          all(after[w] >= before[w] for w in cfg["ao_workers"]),
          f"before={before} after={after}")


# ── LIVE EXEC (opt-in) ─────────────────────────────────────────────────────────
def test_live_exec() -> None:
    print("LIVE EXEC - real safe reclaim (opt-in)")
    p = ex.reclaim_plan()
    tok = p["confirm_token"]
    r = ex.reclaim_execute(tok)
    check("not refused with valid token", not r.get("refused"), str(r)[:200])
    check("returns freed_gb", "freed_gb" in r, str(r)[:200])
    check("ok flag", r.get("ok") is True, str(r)[:200])
    print(f"    (cleared={[c['worker'] for c in r.get('cleared_workers', [])]}, "
          f"skipped={[s['worker'] for s in r.get('skipped_workers', [])]}, "
          f"freed={r.get('freed_gb')})")
    # verify idempotency: a second run should find nothing to clear (token changes or no-op)
    p2 = ex.reclaim_plan()
    check("post-reclaim: workers no longer clearable OR already empty",
          all(not w.get("clearable") for w in p2["workers"]) or p2["clear_workers"] == [],
          str(p2["clear_workers"]))


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    test_source_guard()
    test_plan()
    test_gate_failclosed()
    if "--live-exec" in sys.argv:
        test_live_exec()
    print(f"\n{_passed} passed, {_failed} failed")
    sys.exit(1 if _failed else 0)
