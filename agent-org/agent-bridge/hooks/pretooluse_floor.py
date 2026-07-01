#!/usr/bin/env python3
"""PreToolUse floor hook (P3.3) — the thin CLI wrapper around FloorGuard.

Runs inside a worker's harness as a PreToolUse hook. Reads the tool call as JSON on
stdin (Claude-Code hook contract: {"tool_name","tool_input":{"command":...},...} plus an
`AO_*` env carrying the effort/subject), asks the bridge whether the action is permitted
by the floor, and BLOCKS (exit 2) if not — a hard, deterministic stop, not a warning.

Fail-CLOSED: if the bridge is unreachable, an irreversible action is blocked (the floor
must not fail open — governance §4.2). Reversible actions pass through even if the bridge
is down, so ordinary work is never wedged by a bridge blip.

Env:
  AO_BRIDGE_URL   e.g. http://agent-bridge:8000
  AO_SUBJECT      the worker/role id (scope-ledger subject)
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.request

# Mirror of app.modules.floor_guard.IRREVERSIBLE_PATTERNS so the hook can classify
# offline (fail-closed) without importing the bridge package.
_IRR = {
    "push": r"\bgit\s+push\b",
    "deploy": r"\b(deploy|docker\s+compose\s+up|kubectl\s+apply|helm\s+install)\b",
    "delete": r"\b(rm\s+-rf|drop\s+(table|database)|truncate)\b",
    "spend": r"\b(purchase|checkout|charge|stripe|billing)\b",
    "send-outside": r"\b(send(mail)?|smtp)\b",
}


def _classify(action: str) -> str | None:
    for cls, pat in _IRR.items():
        if re.search(pat, action, re.I):
            return cls
    return None


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0  # not a tool call we understand -> allow
    action = (payload.get("tool_input") or {}).get("command", "") or payload.get("command", "")
    cls = _classify(action)
    if cls is None:
        return 0  # reversible -> allow

    subject = os.environ.get("AO_SUBJECT", "worker")
    bridge = os.environ.get("AO_BRIDGE_URL", "").rstrip("/")
    if not bridge:
        print(f"[floor] BLOCKED '{cls}': no bridge configured, failing closed", file=sys.stderr)
        return 2

    try:
        req = urllib.request.Request(
            f"{bridge}/hook/floor-check",
            data=json.dumps({"subject": subject, "action": action}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        if data.get("allowed"):
            return 0
        print(f"[floor] {data.get('reason', 'blocked by floor (hard-rule #4)')}", file=sys.stderr)
        return 2
    except Exception as exc:
        # Fail closed on an irreversible action if the floor can't be consulted.
        print(f"[floor] BLOCKED '{cls}': bridge unreachable ({exc}); failing closed", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
