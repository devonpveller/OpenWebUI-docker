"""
Context budget management for LLM prompt construction.

Prevents context window overflow by condensing old iteration history
into compact summaries, capping source lists by authority, and building
prompts that fit within the model's context window.

Full research details are always persisted to the Fileshed journal —
only the LLM prompt is kept within budget.  This lets the research
process run many iterations without hitting the model's context limit.
"""

import logging
from typing import Dict, List, Tuple

logger = logging.getLogger("deep_research.context_budget")

# Conservative chars-per-token ratio for mixed markdown content.
CHARS_PER_TOKEN = 4

# Tokens reserved so the model can generate a response.
RESPONSE_RESERVE_TOKENS = 4000

# --- Single source of truth for the target model's context lane -------------
# The deep_research SubAgents run on the ai-stack `qwen36-27b` model. Its
# per-request context LANE = llama-swap ctx-size / n_parallel. As of
# 2026-07-09 that is 180000 / 3 = 60000 tokens (speculative decoding forced the
# ctx down from 262144; see ai-stack/.env "qwen36-27b LANE CONSUMERS").
# Keep this in sync with MNEMORY_LLM_CONTEXT_SIZE and little-coder's
# contextWindow when the lane changes.
MODEL_CONTEXT_LANE_TOKENS = 60000

# Default prompt-token budget: the lane minus room for the generated answer and
# chat-template/tool overhead. Consumed as the `max_prompt_tokens` valve default
# (models.py) and the synthesis fallback. ~10k reserve on a 60k lane.
DEFAULT_MAX_PROMPT_TOKENS = MODEL_CONTEXT_LANE_TOKENS - 10000


def estimate_tokens(text: str) -> int:
    """Estimate token count from character length."""
    return len(text) // CHARS_PER_TOKEN + 1


def usable_budget_chars(max_prompt_tokens: int) -> int:
    """Character budget available for prompt content after reserving
    space for the model's response."""
    usable_tokens = max(max_prompt_tokens - RESPONSE_RESERVE_TOKENS, 2000)
    return usable_tokens * CHARS_PER_TOKEN


def condense_iterations(
    iterations: list,
    recent_full: int = 2,
) -> str:
    """Compress iteration history for use in LLM prompts.

    Keeps the last *recent_full* iterations in full detail while
    condensing earlier iterations to a brief aggregate block.
    Full per-iteration details remain available in the journal.

    Args:
        iterations: ``IterationResult`` objects from the session.
        recent_full: How many recent iterations to keep verbatim.

    Returns:
        Compact iteration history string.
    """
    if not iterations:
        return "No iterations completed yet."

    parts: List[str] = []
    cutoff = max(0, len(iterations) - recent_full)

    # --- Condensed block for older iterations ---
    if cutoff > 0:
        total_new = sum(it.new_chunks for it in iterations[:cutoff])
        total_found = sum(it.chunks_found for it in iterations[:cutoff])
        all_concepts: List[str] = []
        for it in iterations[:cutoff]:
            all_concepts.extend(it.new_concepts[:3])
        unique_concepts = list(dict.fromkeys(all_concepts))[:10]

        parts.append(
            f"**Prior iterations (1\u2013{cutoff}):** "
            f"{total_found} chunks retrieved, {total_new} new"
        )
        if unique_concepts:
            parts.append(f"  Concepts: {', '.join(unique_concepts)}")
        last_old = iterations[cutoff - 1]
        if last_old.summary:
            parts.append(f"  Last finding: {last_old.summary[:200]}")
        parts.append("")

    # --- Recent iterations in full detail ---
    for it in iterations[cutoff:]:
        parts.append(
            f"**Iteration {it.iteration_number}** "
            f"(terms: {', '.join(it.search_terms)}): {it.summary}\n"
            f"New chunks: {it.new_chunks}, "
            f"Concepts: {', '.join(it.new_concepts)}"
        )

    return "\n\n".join(parts)


def cap_sources_to_budget(
    sources: List[Dict],
    budget_chars: int,
) -> Tuple[List[Dict], int]:
    """Select highest-authority sources that fit within *budget_chars*.

    Sources are sorted by their ``authority`` field (highest first).
    Once the budget is exhausted, remaining sources are omitted.

    Args:
        sources: Source dicts (title, url, domain, summary, authority).
        budget_chars: Character budget for the entire sources section.

    Returns:
        ``(selected_sources, omitted_count)``
    """
    sorted_src = sorted(
        sources,
        key=lambda s: s.get("authority", 0.5),
        reverse=True,
    )

    selected: List[Dict] = []
    used = 0
    for s in sorted_src:
        entry_len = (
            len(s.get("title", ""))
            + len(s.get("url", ""))
            + len(s.get("domain", ""))
            + len(s.get("summary", ""))
            + 80  # per-entry formatting overhead
        )
        if used + entry_len > budget_chars and selected:
            break
        selected.append(s)
        used += entry_len

    return selected, len(sources) - len(selected)


def build_iteration_text(
    iteration_summaries: List[str],
    budget_chars: int,
) -> str:
    """Assemble iteration findings for the synthesis prompt.

    Includes the most recent iterations in full (they carry the most
    value for synthesis).  Older iterations are noted as available in
    the journal when the budget runs out.

    Args:
        iteration_summaries: Content strings read from journal files.
        budget_chars: Character budget for this section.

    Returns:
        Assembled iteration text.
    """
    if not iteration_summaries:
        return ""

    parts: List[str] = []
    used = 0

    # Include most-recent first (most valuable for synthesis)
    for i in range(len(iteration_summaries) - 1, -1, -1):
        label = f"# Iteration {i + 1} Findings\n\n"
        entry = label + iteration_summaries[i] + "\n"
        if used + len(entry) > budget_chars and parts:
            remaining = i + 1
            parts.append(
                f"*[{remaining} earlier iteration(s) available in journal]*\n"
            )
            break
        parts.append(entry)
        used += len(entry)

    # Reverse back to chronological order
    parts.reverse()
    return "\n\n".join(parts)
