#!/usr/bin/env python3
"""Resolve the configured sysadmin channel (a #name OR a 26-char id) to a channel id, using the
bot-sysadmin token (which is a member of #sysadmin). Used by check_disk.py (to post) and by
sysadmin-bridge-launch.ps1 (to hand the bridge a real id even when config holds a readable name).

CLI: prints the resolved channel id (or exits 1). `import resolve_channel; resolve_channel.resolve()`.
"""

from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, _HERE)


sys.path.insert(0, os.path.join(_REPO, "scripts", "lib"))
from mm_lib import default_env_files, read_env_key  # noqa: E402


def sysadmin_token() -> str:
    return read_env_key("SYSADMIN_MM_BOT_TOKEN", default_env_files())


def resolve(value: str | None = None) -> str:
    """Return a channel id for `value` (name or id); defaults to config sysadmin_channel_id.
    Posts/reads use the bot-sysadmin identity so a channel it belongs to resolves."""
    import sysadmin as sa
    if value is None:
        value = sa.load_config().get("sysadmin_channel_id")
    tok = sysadmin_token()
    if tok:
        os.environ["MM_TOKEN"] = tok  # mattermost server _token() honours MM_TOKEN
    sys.path.insert(0, os.path.join(_REPO, "scripts", "mattermost-mcp"))
    import server as mm
    if not value:
        return mm.DEFAULT_CHANNEL
    return mm._resolve_channel(value)  # 26-char id passes through; a name is looked up across teams


if __name__ == "__main__":
    try:
        sys.stdout.write(resolve())
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"resolve failed: {e}\n")
        sys.exit(1)
