"""
Per-chat research ledger — graceful, resumable cap on research() fan-out.

Single Responsibility: track how many times the chat model has called
research() in a conversation and, when the per-chat budget is reached,
produce a STOP directive plus a coverage/gap summary and a copy-paste
continuation handle.

Why this exists: research() is a single-shot tool the chat model calls via
native function calling. On broad "list everything / comparison matrix"
prompts the model decomposes into one research() call PER ITEM and never
converges, blowing the model's context and OWUI's stream timeout. This
ledger caps calls per conversation and, when the cap is hit, hands the user
an explicit, gap-aware choice to go deeper instead of silently truncating.
"""

import re
from typing import Any, Dict, List, Optional

# Process-scoped: keyed by chat id. Survives across tool calls within the
# same OWUI process (and therefore the same conversation).
RESEARCH_LEDGER: Dict[str, Dict[str, Any]] = {}

CONTINUE_RE = re.compile(r"^\s*research\s+continue\s*:\s*", re.IGNORECASE)


def chat_key(chat_id: str, user: Dict) -> str:
    return chat_id or (user or {}).get("id", "") or "default"


def dedup(seq: List[str], limit: Optional[int] = None) -> List[str]:
    out: List[str] = []
    for s in seq:
        s = (s or "").strip()
        if s and s not in out:
            out.append(s)
        if limit and len(out) >= limit:
            break
    return out


def coverage_block(ledger: Dict[str, Any], topic: str) -> str:
    covered = dedup(ledger.get("covered", []), 12)
    gaps = dedup(ledger.get("gaps", []), 12)
    cov = "\n".join(f"- {c}" for c in covered) or "- (none recorded yet)"
    gp = "\n".join(f"- {g}" for g in gaps) or \
        "- (none recorded — coverage looks complete)"
    gap_hint = "; ".join(gaps[:3]) if gaps else topic
    cont = f"research continue: {topic} — focus: {gap_hint}"
    return (
        "\n\n---\n\n### 🔍 Research Coverage & Next Steps\n\n"
        f"**Topic:** {topic}\n\n"
        f"**Covered so far ({len(covered)}):**\n{cov}\n\n"
        f"**Not yet researched — open gaps ({len(gaps)}):**\n{gp}\n\n"
        "These gaps were left open to keep this response responsive rather "
        "than looping indefinitely. You can accept the findings as-is (with "
        "the gaps noted), or go deeper later *without re-covering the "
        "above*.\n\n"
        "**To continue, copy-paste this as your next message:**\n\n"
        f"```\n{cont}\n```\n"
    )


def stop_payload(ledger: Dict[str, Any], topic: str, used: int, budget: int,
                  ran: bool) -> str:
    """Tool-result text that makes the chat model stop fanning out and
    present a final, gap-aware answer to the user."""
    did = (
        "The findings gathered earlier in this turn are sufficient to answer "
        "now." if not ran else
        "This was the final research pass allowed for this conversation."
    )
    return (
        f"⛔ **RESEARCH BUDGET REACHED** — {used}/{budget} research calls used "
        f"in this conversation.\n\n"
        "**Assistant instructions (do not echo this line to the user):** Do "
        "NOT call the `research` tool again in this turn. " + did + " Using "
        "only the research already returned above, write the user's final "
        "answer now (e.g. the requested list / comparison chart). Then append "
        "the \"Research Coverage & Next Steps\" section below verbatim so the "
        "user can decide whether to dig deeper."
        + coverage_block(ledger, topic)
    )
