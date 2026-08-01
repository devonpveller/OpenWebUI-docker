#!/usr/bin/env python3
"""Best-effort one-shot poster to the #sysadmin channel as bot-sysadmin. Used by the admin actions
(reclaim, sweep, compaction) to post a completion summary. Never raises to its caller — a down
Mattermost must never break an admin action.

CLI:  python mm_post.py "message text"      (exit 0 posted, 1 not)
API:  import mm_post; mm_post.post("...")    -> bool
"""

from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, _HERE)


def post(message: str) -> bool:
    try:
        import resolve_channel
        tok = resolve_channel.sysadmin_token()
        if not tok:
            return False
        os.environ["MM_TOKEN"] = tok  # post as bot-sysadmin (member of #sysadmin)
        sys.path.insert(0, os.path.join(_REPO, "scripts", "mattermost-mcp"))
        import server as mm
        ch = resolve_channel.resolve()
        mm._api("POST", "/posts", {"channel_id": ch, "message": message, "props": {"from_claude": True}})
        return True
    except Exception:  # noqa: BLE001 - best-effort
        return False


if __name__ == "__main__":
    msg = sys.argv[1] if len(sys.argv) > 1 else ""
    sys.exit(0 if (msg and post(msg)) else 1)
