#!/usr/bin/env python3
"""Session inventory for the Claude-Sessions bridge — answers "which session id is which?".

Scans the repo's Claude Code project directory (~/.claude/projects/<encoded-repo>/*.jsonl),
newest first, and labels each session id with its title: the session's auto-summary if one
exists, else its first user message. Bridge-driven sessions are additionally tagged with their
Mattermost thread title from state.json.

Used two ways:
  • In Mattermost: post `sessions` (or `sessions <filter>`) in #claude-sessions — the bridge
    replies with this listing, so you can copy an id straight into `handoff <id>` / `fork <id>`.
  • On the host:  python scripts/claude-sessions-bridge/sessions.py [filter] [--limit N]

Stdlib only.
"""

from __future__ import annotations

import json
import os
import re
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(_HERE))


def project_dir_for(repo: str) -> str:
    """Claude Code encodes a project path into a directory name by replacing every char that
    isn't [A-Za-z0-9-] with '-'  (d:\\Open WebUI\\ai-stack → d--Open-WebUI-ai-stack)."""
    name = re.sub(r"[^A-Za-z0-9-]", "-", os.path.normpath(repo))
    return os.path.join(os.path.expanduser("~"), ".claude", "projects", name)


def _clean(text: str, n: int = 70) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[:n] + "…"


_TAG_BLOCK_RE = re.compile(r"<(\w[\w-]*)[^>]*>[\s\S]*?</\1>|<[\w-]+[^>]*/?>")


def _user_text(rec: dict) -> str:
    content = (rec.get("message") or {}).get("content")
    text = ""
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text = item.get("text", "")
                break
    # strip harness wrappers (<system-reminder>, <ide_opened_file>, <command-…>) and keep
    # whatever human text remains
    return _TAG_BLOCK_RE.sub("", text).strip()


def _scan_file(path: str, head_lines: int = 4000) -> dict:
    """One pass per file: first real user message, summary records ({leafUuid: summary} —
    Claude Code may store a session's summary in a DIFFERENT file of the chain), and the tail
    message uuids (a summary whose leafUuid matches one of them belongs to this session —
    this is how the /resume picker resolves names)."""
    info = {"user_texts": [], "summaries": {}, "tail_uuids": []}

    def take(rec: dict) -> None:
        if rec.get("type") == "summary" and rec.get("summary") and rec.get("leafUuid"):
            info["summaries"][rec["leafUuid"]] = str(rec["summary"])
        elif rec.get("type") == "user" and len(info["user_texts"]) < 5 and not rec.get("isMeta"):
            text = _user_text(rec)
            if text:
                info["user_texts"].append(text)

    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            for i, line in enumerate(fh):
                if i >= head_lines:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(rec, dict):
                    take(rec)
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            fh.seek(max(0, size - 32768))
            for line in fh.read().decode("utf-8", "ignore").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(rec, dict):
                    continue
                take(rec)
                if rec.get("uuid"):
                    info["tail_uuids"].append(rec["uuid"])
    except OSError:
        pass
    return info


def _age(mtime: float) -> str:
    s = int(time.time() - mtime)
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400:
        return f"{s // 3600}h"
    return f"{s // 86400}d"


def list_sessions(repo: str, bridge_threads: dict | None = None,
                  query: str = "", limit: int = 30) -> list[dict]:
    pdir = project_dir_for(repo)
    mm_titles = {}
    for meta in (bridge_threads or {}).values():
        if isinstance(meta, dict) and meta.get("session_id"):
            mm_titles[meta["session_id"]] = meta.get("title", "")
    entries = []
    try:
        files = [os.path.join(pdir, f) for f in os.listdir(pdir) if f.endswith(".jsonl")]
    except OSError:
        return []
    files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    # Pass 1: scan every file once — summaries can live in a different file than the session
    # they name (chains), so the summary map must be global before titles are resolved.
    scans = {path: _scan_file(path) for path in files}
    all_summaries: dict[str, str] = {}
    for info in scans.values():
        all_summaries.update(info["summaries"])
    q = (query or "").lower()
    for path in files:
        sid = os.path.basename(path)[:-6]
        info = scans[path]
        # Title = first SUBSTANTIAL typed message (a bare "running" or "ok" is a rotten label —
        # prefer the first of the opening messages that carries ≥20 chars); summaries are
        # fallbacks. NOTE: the /resume picker derives its display names with its own heuristic,
        # so names here are recognizable but not guaranteed to match the picker verbatim —
        # content search (below) is the reliable way to find a session by remembered text.
        texts = info["user_texts"]
        title = next((t for t in texts if len(t) >= 20), texts[0] if texts else "")
        if not title:
            for uuid in reversed(info["tail_uuids"]):
                if uuid in all_summaries:
                    title = all_summaries[uuid]
                    break
        if not title:
            title = next(iter(info["summaries"].values()), "") or "(untitled)"
        tag = "mm" if sid in mm_titles else ""
        if q and q not in title.lower() and q not in sid.lower() and q != tag:
            # full-text fallback: match anywhere in the transcript, so a session is findable
            # by any phrase the operator remembers, whatever its title says
            if not _content_match(path, q):
                continue
            title = f"{title} ⟨'{query}' found in content⟩"
        entries.append({"id": sid, "title": _clean(title, 110), "age": _age(os.path.getmtime(path)),
                        "mm": tag == "mm"})
        if len(entries) >= limit:
            break
    return entries


def _content_match(path: str, q: str) -> bool:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            while True:
                chunk = fh.read(1 << 20)
                if not chunk:
                    return False
                if q in chunk.lower():
                    return True
    except OSError:
        return False


def listing_text(repo: str, bridge_threads: dict | None = None,
                 query: str = "", limit: int = 30) -> str:
    entries = list_sessions(repo, bridge_threads, query, limit)
    if not entries:
        return (f"(no sessions matched `{query}`)" if query
                else "(no sessions found for this repo)")
    lines = []
    for e in entries:
        tag = " · 🧵mm" if e["mm"] else ""
        lines.append(f"`{e['id']}` · {e['age']}{tag}\n    {e['title']}")
    head = (f"**Sessions for `{repo}`**" + (f" matching `{query}`" if query else "")
            + f" — newest first (top {limit}). Use `handoff <id>` / `fork <id>` "
              f"in a new thread to continue one here.\n\n")
    return head + "\n".join(lines)


if __name__ == "__main__":
    import argparse
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser(description="List Claude Code sessions for this repo with ids + titles")
    ap.add_argument("filter", nargs="?", default="", help="substring filter (title/id, or 'mm')")
    ap.add_argument("--limit", type=int, default=30)
    ap.add_argument("--repo", default=os.environ.get("BRIDGE_REPO", _REPO_ROOT))
    a = ap.parse_args()
    state_path = os.path.join(_HERE, "state", "state.json")
    threads = {}
    try:
        with open(state_path, "r", encoding="utf-8") as fh:
            threads = json.load(fh).get("threads", {})
    except (OSError, json.JSONDecodeError):
        pass
    print(listing_text(a.repo, threads, a.filter, a.limit))
