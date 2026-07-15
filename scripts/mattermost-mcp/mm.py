#!/usr/bin/env python3
"""Tiny CLI over the Mattermost MCP tools — a clean host-side path for a shell (and for a Claude
Code session BEFORE the `mattermost` MCP server is loaded, since MCP servers register at session
start). Same token/config as server.py (reads AO_MATTERMOST_BOT_TOKEN from agent-org/docker/.env).

  python scripts/mattermost-mcp/mm.py read [--limit N] [--since MS] [--exclude-self] [--channel X]
  python scripts/mattermost-mcp/mm.py post "message" [--channel X] [--thread ROOT_ID]
  python scripts/mattermost-mcp/mm.py channels [--query X]
"""
from __future__ import annotations

import argparse
import os
import sys

# Windows consoles default to cp1252, which can't encode emoji/Unicode in Mattermost messages
# (a raw `print()` then crashes with UnicodeEncodeError). Force UTF-8 output, replacing anything
# the terminal genuinely can't render — the CLI must never die on a message's characters.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
except (AttributeError, ValueError):  # pragma: no cover - very old Python / non-reconfigurable stream
    pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import server  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(prog="mm")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("read")
    r.add_argument("--limit", type=int, default=15)
    r.add_argument("--since", type=int)
    r.add_argument("--exclude-self", action="store_true")
    r.add_argument("--channel")
    p = sub.add_parser("post")
    p.add_argument("message")
    p.add_argument("--channel")
    p.add_argument("--thread")
    c = sub.add_parser("channels")
    c.add_argument("--query")
    # `wait` = the LISTENER: block until a NEW operator (non-bot) message appears after `--since`,
    # then print it and exit 0 (timeout → exit 2). Run as a BACKGROUND task so its completion
    # re-engages the agent — i.e. an operator Mattermost message becomes a wake-up (the push/trigger
    # the plain read/post MCP lacks). Poll-based, but it turns a message into an event for the
    # duration of a running session/loop.
    w = sub.add_parser("wait")
    w.add_argument("--since", type=int, required=True, help="ms-epoch; trigger on a message after this")
    w.add_argument("--timeout", type=int, default=2700, help="give up after N seconds (exit 2)")
    w.add_argument("--interval", type=int, default=15, help="poll every N seconds")
    w.add_argument("--channel")
    a = ap.parse_args()
    if a.cmd == "read":
        print(server.tool_read({"limit": a.limit, "since": a.since,
                                "exclude_self": a.exclude_self, "channel": a.channel}))
    elif a.cmd == "post":
        print(server.tool_post({"message": a.message, "channel": a.channel, "thread_id": a.thread}))
    elif a.cmd == "channels":
        print(server.tool_channels({"query": a.query}))
    elif a.cmd == "wait":
        import time
        end = time.time() + a.timeout
        consecutive_errors = 0
        while time.time() < end:
            try:
                out = server.tool_read({"exclude_self": True, "since": a.since,
                                        "channel": a.channel, "limit": 20})
                consecutive_errors = 0
                if not out.startswith("(no messages"):
                    print(out)
                    return  # exit 0 — a new operator message arrived; caller handles it
            except Exception as e:  # noqa: BLE001
                # A transient network/socket error (e.g. WinError 10055 under load) must NEVER kill
                # the listener — skip this poll and retry. Back off a little if it persists.
                consecutive_errors += 1
                sys.stderr.write(f"(listener poll error #{consecutive_errors}, retrying: {e})\n")
                sys.stderr.flush()
            # steady interval, gently backing off while errors persist (cap ~60s)
            time.sleep(min(60, max(3, a.interval) + min(consecutive_errors, 5) * 5))
        print("(timeout: no new operator message)")
        sys.exit(2)


if __name__ == "__main__":
    main()
