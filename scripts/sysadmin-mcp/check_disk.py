#!/usr/bin/env python3
"""Weekly disk-pressure detector — capability #1 of the systems-administrator agent.

Runs on a schedule (Sunday). Calls the read-only disk_report; if the verdict is not "healthy" it
posts an alert to #sysadmin (falls back to #claude-code until #sysadmin exists) telling the operator
what's big and what the safe/gated options are, so they can engage @sysadmin to reclaim. It never
mutates anything itself — the actual reclaim/compaction stays behind the approval gate.

Throttled: won't re-post within alert_throttle_hours unless the severity worsened or the flag set
changed (so a persistent condition pings once, not every run).

Usage:
  python scripts/sysadmin-mcp/check_disk.py           # evaluate + post if needed
  python scripts/sysadmin-mcp/check_disk.py --dry      # print what it WOULD post; never posts
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, _HERE)
import sysadmin as sa  # noqa: E402

_STATE = os.path.join(_HERE, "state")
_ALERT = os.path.join(_STATE, "last-alert.json")


def _poster():
    """Import the mattermost MCP server for its _api/_token/DEFAULT_CHANNEL helpers (same as bridge.py)."""
    sys.path.insert(0, os.path.join(_REPO_ROOT, "scripts", "mattermost-mcp"))
    import server as mm  # noqa: E402
    return mm


def build_message(rep: dict) -> str:
    v = rep["verdict"]
    sysd = rep["drives"]["system"]
    vh = rep.get("vhdx", {})
    lines = [
        f"### 🖥️ Disk check — **{v['severity'].upper()}**",
        f"{sysd.get('drive', 'C:')} free **{sysd.get('free_gb')} GB** · vhdx trapped **{vh.get('trapped_gb')} GB** · recommended: _{v.get('recommended_action')}_",
    ]
    if v.get("flags"):
        lines.append("\n**Flags:**")
        lines += [f"- {f}" for f in v["flags"][:8]]
    opts = []
    if v.get("safe_reclaim_available"):
        opts.append("safe reclaim (no downtime): `reclaim_plan` → `reclaim_execute`")
    if v.get("compaction_recommended"):
        opts.append("vhdx compaction (brief downtime): `compact_plan` → `compact_execute`")
    if opts:
        lines.append("\n**Options (gated — I'll ask before executing):**")
        lines += [f"- {o}" for o in opts]
    lines.append("\n@sysadmin can investigate and propose a plan — reply to engage, then approve the action.")
    return "\n".join(lines)


def _flags_hash(rep: dict) -> str:
    return hashlib.sha1(json.dumps(rep["verdict"].get("flags", []), sort_keys=True).encode()).hexdigest()[:12]


_SEV = {"healthy": 0, "attention": 1, "critical": 2}


def _should_post(rep: dict, throttle_h: float) -> bool:
    try:
        with open(_ALERT, "r", encoding="utf-8") as fh:
            last = json.load(fh)
    except Exception:  # noqa: BLE001
        return True
    now = time.time()
    if now - last.get("ts", 0) > throttle_h * 3600:
        return True
    if _SEV.get(rep["verdict"]["severity"], 0) > _SEV.get(last.get("severity", "healthy"), 0):
        return True
    if _flags_hash(rep) != last.get("flags_hash"):
        return True
    return False


def _save_alert(rep: dict) -> None:
    try:
        os.makedirs(_STATE, exist_ok=True)
        with open(_ALERT, "w", encoding="utf-8") as fh:
            json.dump({"ts": time.time(), "severity": rep["verdict"]["severity"],
                       "flags_hash": _flags_hash(rep)}, fh)
    except Exception:  # noqa: BLE001
        pass


def main(argv) -> int:
    dry = "--dry" in argv
    cfg = sa.load_config()
    rep = sa.disk_report()
    sev = rep["verdict"]["severity"]
    if sev == "healthy":
        print("healthy — no alert")
        return 0
    msg = build_message(rep)
    if dry:
        print("[--dry] would post to #sysadmin:\n")
        print(msg)
        return 0
    if not _should_post(rep, float(cfg.get("alert_throttle_hours", 20))):
        print(f"{sev} — throttled (already alerted; unchanged)")
        return 0
    # Post as bot-sysadmin (a member of #sysadmin; bot-claude is NOT), resolving a #name or a
    # 26-char id in config to the channel id.
    import resolve_channel
    tok = resolve_channel.sysadmin_token()
    if tok:
        os.environ["MM_TOKEN"] = tok
    mm = _poster()
    try:
        ch = resolve_channel.resolve()
    except Exception as e:  # noqa: BLE001
        print(f"channel resolve failed (non-fatal): {e}")
        return 0
    try:
        mm._api("POST", "/posts", {"channel_id": ch, "message": msg, "props": {"from_claude": True}})
        _save_alert(rep)
        print(f"posted {sev} alert to {ch}")
    except Exception as e:  # noqa: BLE001
        print(f"alert post failed (non-fatal): {e}")
        return 0
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    sys.exit(main(sys.argv[1:]))
