"""context-manager — hierarchical, bounded, relevance-selected conversation context.

The PO reads conversation history to stay coherent. Two problems that context must solve
(operator's refinement): it must be **layered** — the current THREAD is the immediate context, the
CHANNEL is a higher-level background — and it must be **managed** so it never overwhelms the
model's window: bounded by a character budget AND filtered to what's RELEVANT to the current query
(not merely the most recent turns).

Design (applies to ANY channel):
  - remember(channel, thread, role, text) logs every turn, tagged by thread.
  - build(channel, thread, query):
      · THREAD layer  — the current thread's turns, most-recent kept within a char budget
        (immediate continuity — a reply builds on its thread).
      · CHANNEL layer — turns from OTHER threads in the channel, ranked by term-overlap with the
        query, kept within a smaller budget (higher-level, only what's relevant).
  Both layers are labelled so the model knows which is immediate vs background. No model call,
  no embeddings — deterministic + testable; relevance is lexical overlap (cheap, good enough).
"""

from __future__ import annotations

import re

# Terms too common to signal relevance (kept small + generic).
_STOP = {
    "the", "a", "an", "is", "are", "was", "to", "of", "in", "on", "for", "and", "or", "it",
    "this", "that", "what", "where", "when", "how", "why", "i", "you", "me", "my", "so", "do",
    "did", "can", "could", "with", "at", "be", "as", "we", "he", "she", "they", "there", "here",
    "no", "not", "yes", "please", "thanks", "ok", "okay", "let", "get", "got",
}


def _tokens(text: str) -> set[str]:
    return {w for w in re.split(r"[^a-z0-9]+", (text or "").lower()) if len(w) > 2 and w not in _STOP}


class ContextManager:
    def __init__(
        self, *, thread_chars: int = 2500, channel_chars: int = 1500,
        max_thread_turns: int = 14, max_log_per_channel: int = 300,
    ) -> None:
        self.thread_chars = thread_chars
        self.channel_chars = channel_chars
        self.max_thread_turns = max_thread_turns
        self.max_log = max_log_per_channel
        self._log: dict[str, list[dict]] = {}  # channel_id -> [{thread, role, text, seq}]
        self._seq = 0

    def remember(self, channel_id: str, thread_id: str | None, role: str, text: str) -> None:
        text = (text or "").strip()
        if not channel_id or not text:
            return
        self._seq += 1
        buf = self._log.setdefault(channel_id, [])
        buf.append({"thread": thread_id or "", "role": role, "text": text[:1000], "seq": self._seq})
        del buf[:-self.max_log]  # bound memory per channel

    def build(self, channel_id: str, thread_id: str | None, query: str = "") -> str:
        buf = self._log.get(channel_id, [])
        if not buf:
            return "(this is the start of the conversation)"
        tid = thread_id or ""
        thread_turns = [t for t in buf if t["thread"] == tid]
        other_turns = [t for t in buf if t["thread"] != tid]

        thread_block = self._pack(thread_turns[-self.max_thread_turns:], self.thread_chars, keep_recent=True)
        channel_block = self._pack(self._rank(other_turns, query), self.channel_chars, keep_recent=False)

        parts: list[str] = []
        if thread_block:
            parts.append("THIS THREAD (most recent last):\n" + thread_block)
        if channel_block:
            parts.append("ELSEWHERE IN THIS CHANNEL (relevant background):\n" + channel_block)
        return "\n\n".join(parts) or "(this is the start of the conversation)"

    # ── relevance ranking (lexical overlap with the query) ───────────────────
    def _rank(self, turns: list[dict], query: str) -> list[dict]:
        """Order background turns RELEVANT-first (query term-overlap), then by recency. Nothing is
        dropped here — the char budget in `_pack` does the trimming, so when context is large the
        query-relevant turns survive it and the least-relevant/oldest fall off first."""
        terms = _tokens(query)
        scored = [
            (len(terms & _tokens(t["text"])) if terms else 0, t["seq"], t) for t in turns
        ]
        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)  # overlap desc, then recency desc
        return [t for _, _, t in scored]

    # ── budget packing ───────────────────────────────────────────────────────
    def _pack(self, turns: list[dict], budget: int, *, keep_recent: bool) -> str:
        # keep_recent → take from the newest end (thread continuity); else take in the given
        # (already-ranked) order. Always stop at the character budget.
        seq = list(reversed(turns)) if keep_recent else list(turns)
        picked: list[str] = []
        used = 0
        for t in seq:
            line = f"{'You' if t['role'] == 'po' else 'Operator'}: {t['text']}"
            if picked and used + len(line) + 1 > budget:
                break
            picked.append(line)
            used += len(line) + 1
        if keep_recent:
            picked.reverse()  # restore chronological order
        return "\n".join(picked)
