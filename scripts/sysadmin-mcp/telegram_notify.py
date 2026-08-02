#!/usr/bin/env python3
"""Out-of-band Telegram notifier for the ai-stack sysadmin channel.

This is the DOCKER-INDEPENDENT alert path. It reads the bot token + operator
chat_id straight from the repo-root .env at runtime and POSTs to the Telegram
Bot API over plain HTTPS. Because it never touches Docker (unlike
notify-mattermost.sh, which posts to the Mattermost *container* on :8065), it
still reaches the operator's phone when the whole stack -- Mattermost included
-- is down. That is the entire point: a compaction or crash that strands Docker
must not also silence every alert path.

Usage:
    python telegram_notify.py "message text"        # CLI, exit 0 on success
    from telegram_notify import send; send("...")    # importable

Best-effort by design: never raises to its caller (a down channel must not
break a recovery script). Stdlib only (urllib) so it runs under any Python,
including the bare system interpreter a Scheduled Task may launch.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request

# repo root = two levels up from scripts/sysadmin-mcp/
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(_HERE))
_ENV = os.path.join(_REPO, ".env")


def _read_env(path: str = _ENV) -> dict:
    """Minimal .env parser -> dict. Ignores comments/blank lines; strips quotes.
    utf-8-sig so a BOM-prefixed .env (Windows editors) parses cleanly."""
    vals: dict = {}
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                vals[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return vals


def creds() -> tuple[str | None, str | None]:
    """(token, chat_id) preferring the process env, falling back to .env."""
    env = _read_env()
    tok = os.environ.get("SYSADMIN_TELEGRAM_BOT_TOKEN") or env.get("SYSADMIN_TELEGRAM_BOT_TOKEN")
    cid = os.environ.get("SYSADMIN_TELEGRAM_CHAT_ID") or env.get("SYSADMIN_TELEGRAM_CHAT_ID")
    return tok, cid


def send(text: str, chat_id: str | None = None) -> bool:
    tok, cid = creds()
    cid = chat_id or cid
    if not tok or not cid:
        return False
    url = f"https://api.telegram.org/bot{tok}/sendMessage"
    data = urllib.parse.urlencode(
        {"chat_id": cid, "text": text, "disable_web_page_preview": "true"}
    ).encode()
    try:
        req = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(req, timeout=12) as resp:
            body = json.loads(resp.read().decode())
            return bool(body.get("ok"))
    except Exception:  # noqa: BLE001 - best-effort; a down channel must never raise
        return False


if __name__ == "__main__":
    msg = " ".join(sys.argv[1:]) or "(empty message)"
    ok = send(msg)
    print("ok" if ok else "FAILED")
    sys.exit(0 if ok else 1)
