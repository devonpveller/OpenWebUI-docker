"""Durable inbox for operator messages — the bridge's crash boundary.

WHY THIS EXISTS (the loss window it closes, traced in the code, not assumed):

`Bridge.poll_once` read a post from Mattermost, appended it to an IN-MEMORY deque
(`_Queues.lane1`), and then — at the end of that same poll pass — advanced
`state['last_seen']` past it and persisted `processed`. From that instant the message
existed in exactly one place: RAM.

If the bridge died in the window between those two facts, the message was gone with no
trace. Not delayed: gone. A restart re-reads `?since=last_seen`, which is now past the
post, and the post id sits in `processed` anyway, so nothing would ever re-admit it. The
operator saw a message they sent, an hourglass reaction, and then silence forever.

That window is not narrow. A turn can run for TURN_TIMEOUT (7200s), and the watchdog,
a `restart_bridge`, an unhandled crash and an ordinary machine reboot all land in it.

So: a message is written to an append-only log BEFORE the poll pass is allowed to forget
it, and is marked consumed only once its turn has actually finished. Everything in between
is replayed on the next start.

DESIGN NOTES that are load-bearing rather than stylistic:

- TWO append-only files per thread, never a rewrite-in-place. A crash during an append
  can lose the tail of a line; it cannot corrupt a line that was already durable. A
  truncated final line is dropped on read (see `_read_jsonl`) precisely because a partial
  write is exactly what a crash looks like.
- Consumption is a SET of post ids, not a count-based offset. The queue does not consume
  in FIFO order — lane 1 pre-empts lane 2 and catch-up coalescing drains lane 2 in bulk —
  so "the first N are done" is not a fact this system can state. Anything that assumed an
  ordered offset would mark the wrong messages consumed.
- `record` is idempotent by post id. Replay must not re-record what it is replaying, and
  N restarts with no intervening turn must leave the message pending exactly ONCE.
- Compaction is what bounds an append-only file. It runs after consumption, rewrites via
  a temp file and `os.replace`, and keeps only what is still pending.
"""

from __future__ import annotations

import json
import os
import threading
import time

# Compaction trigger. An append-only file that is never compacted is a disk leak with good
# intentions; compacting on every consume would rewrite the file for every turn. This is the
# number of RECORDED entries that must accumulate before a consume also compacts.
COMPACT_AFTER = 200


def _safe_name(thread_root: str) -> str:
    """Mattermost ids are 26 alphanumeric characters, but this is the one place a hostile or
    malformed id would become a filesystem path, so it is not taken on trust."""
    return "".join(c for c in str(thread_root) if c.isalnum() or c in "-_")[:64] or "_unknown"


class Inbox:
    """Per-thread durable record of admitted operator messages.

    Public surface: record / mark_consumed / pending / threads. Nothing else is used by
    bridge.py, and deleting this module means deleting those four call sites.
    """

    def __init__(self, root: str) -> None:
        self.root = root
        self._lock = threading.Lock()
        os.makedirs(self.root, exist_ok=True)

    # ── paths ────────────────────────────────────────────────────────────────
    def _entries_path(self, thread_root: str) -> str:
        return os.path.join(self.root, _safe_name(thread_root) + ".jsonl")

    def _consumed_path(self, thread_root: str) -> str:
        return os.path.join(self.root, _safe_name(thread_root) + ".consumed")

    # ── io ───────────────────────────────────────────────────────────────────
    @staticmethod
    def _read_jsonl(path: str) -> list[dict]:
        """Read a JSONL file, DROPPING any line that does not parse.

        A half-written final line is what a crash mid-append looks like, and this module
        exists to survive crashes — so a partial tail is an expected input, not an error.
        """
        out: list[dict] = []
        try:
            with open(path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue  # torn tail from a crash, or a stray byte — not fatal
                    if isinstance(rec, dict):
                        out.append(rec)
        except OSError:
            return []
        return out

    @staticmethod
    def _append(path: str, rec: dict) -> None:
        """Append one record and force it to disk before returning.

        The fsync is the whole point: `record()` returning must MEAN the message survives
        the process dying on the next line. Without it the durability boundary is wherever
        the OS happens to flush, which is not a boundary anyone can reason about.
        """
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    # ── public surface ───────────────────────────────────────────────────────
    def record(self, thread_root: str, post_id: str, prompt: str, kind: str = "user") -> bool:
        """Durably record an admitted message. Returns False if it was already recorded.

        MUST be called before the caller allows `last_seen` to move past this post. That
        ordering IS the fix; calling it afterwards reproduces the original bug with extra
        steps.
        """
        if not post_id:
            # Lane-2 wake items carry no post id. They are regenerated from follow state on
            # the next poll, so they are not this module's problem (and silently keying them
            # all to "" would collapse them into one another).
            return False
        with self._lock:
            for rec in self._read_jsonl(self._entries_path(thread_root)):
                if rec.get("post_id") == post_id:
                    return False  # idempotent: replay must not duplicate what it replays
            self._append(self._entries_path(thread_root), {
                "post_id": post_id, "thread_root": thread_root, "kind": kind,
                "prompt": prompt, "ts": int(time.time() * 1000),
            })
        return True

    def mark_consumed(self, thread_root: str, post_id: str) -> None:
        """Record that this message's turn FINISHED — successfully or with a reported error.

        Called from the worker's `finally`, which is deliberate on both counts:

        - It runs for a failed turn too. The window being closed is the process DYING, not
          the turn erroring: an errored turn already told the operator so in-thread, and
          replaying it forever would turn one poison message into an infinite loop.
        - It does NOT run when the process is killed, which is exactly when the entry must
          stay pending. Marking consumed at DEQUEUE time instead would look equivalent and
          would silently re-open the original hole one layer down.
        """
        if not post_id:
            return
        with self._lock:
            self._append(self._consumed_path(thread_root), {
                "post_id": post_id, "ts": int(time.time() * 1000),
            })
            self._maybe_compact_locked(thread_root)

    def pending(self, thread_root: str) -> list[dict]:
        """Admitted-but-unfinished messages for one thread, oldest first."""
        with self._lock:
            return self._pending_locked(thread_root)

    def threads(self) -> list[str]:
        """Every thread that has an inbox file — the replay set on startup."""
        try:
            names = os.listdir(self.root)
        except OSError:
            return []
        out = []
        for n in names:
            if not n.endswith(".jsonl"):
                continue
            # The thread_root is stored IN the record rather than parsed back out of the
            # filename, because _safe_name is not reversible.
            recs = self._read_jsonl(os.path.join(self.root, n))
            if recs:
                out.append(str(recs[0].get("thread_root") or ""))
        return [t for t in out if t]

    # ── internals ────────────────────────────────────────────────────────────
    def _pending_locked(self, thread_root: str) -> list[dict]:
        consumed = {r.get("post_id") for r in self._read_jsonl(self._consumed_path(thread_root))}
        seen: set[str] = set()
        out: list[dict] = []
        for rec in self._read_jsonl(self._entries_path(thread_root)):
            pid = rec.get("post_id")
            if not pid or pid in consumed or pid in seen:
                continue
            seen.add(pid)  # belt-and-braces against a duplicate record from an older build
            out.append(rec)
        return out

    def _maybe_compact_locked(self, thread_root: str) -> None:
        """Bound the append-only files by rewriting them with only what is still pending.

        Atomic via temp-file + os.replace: a crash during compaction leaves the ORIGINAL
        files intact, so the worst case is that compaction simply did not happen.
        """
        epath = self._entries_path(thread_root)
        entries = self._read_jsonl(epath)
        if len(entries) < COMPACT_AFTER:
            return
        keep = self._pending_locked(thread_root)
        tmp = epath + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            for rec in keep:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, epath)
        # The consumed log can only be dropped once the entries it refers to are gone. It is
        # rewritten (not deleted) to keep the two files' lifetimes identical.
        cpath = self._consumed_path(thread_root)
        ctmp = cpath + ".tmp"
        with open(ctmp, "w", encoding="utf-8") as fh:
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(ctmp, cpath)
