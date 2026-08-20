#!/usr/bin/env python3
"""Shared .env credential mechanics for the host-side bridge/bot scripts.

Before 2026-08-20 (CLEANUP-PLAN v3 E.2/J.2) this line-walk was hand-copied in
five places (claude-sessions-bridge/bridge.py, approval_server.py,
mattermost-mcp/server.py, sysadmin-mcp/resolve_channel.py,
sysadmin-mcp/telegram_notify.py), each with slightly different quoting/CR
handling. The mechanics live here once; each consumer keeps its OWN key
names, candidate order, and identity policy (probes, caches, fallbacks).

Stdlib-only on purpose — every consumer runs as a bare host process.
"""
from __future__ import annotations

import os
from typing import Dict, List, Optional

# scripts/lib/mm_lib.py -> repo root is two levels up.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def default_env_files(extra_first: Optional[str] = None) -> List[str]:
    """The canonical search order: an optional caller-specific file first,
    then agent-org's .env (bot tokens live there), then the repo root .env."""
    out: List[str] = []
    if extra_first:
        out.append(extra_first)
    out.append(os.path.join(REPO_ROOT, "agent-org", "docker", ".env"))
    out.append(os.path.join(REPO_ROOT, ".env"))
    return out


def read_env_key(key: str, paths: List[str]) -> str:
    """First non-empty ``key=value`` across ``paths`` (quotes/CR stripped).
    Unreadable files are skipped — callers decide what absence means."""
    for path in paths:
        if not path:
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    if line.startswith(key + "="):
                        val = line.split("=", 1)[1].strip().strip('"').strip("'").replace("\r", "")
                        if val:
                            return val
        except OSError:
            continue
    return ""


def parse_env_file(path: str) -> Dict[str, str]:
    """Whole-file .env parse -> dict. utf-8-sig so a BOM-prefixed .env
    (Windows editors) parses cleanly; comments and blank lines ignored."""
    vals: Dict[str, str] = {}
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
