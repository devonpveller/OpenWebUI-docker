"""
Sub-agent for internal LLM calls within the Deep Research pipeline.

Single Responsibility: Only handles invoking the host LLM for sub-tasks.
Dependency Inversion: Depends on OWUI's generate_chat_completion abstraction.
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger("deep_research.sub_agent")

# Web search is now handled by calling OWUI's search_web() directly in
# research.py and domain_discovery.py — not through generate_chat_completion.


class SubAgent:
    """Executes internal LLM calls using OWUI's generate_chat_completion.

    Uses bypass_filter=True to prevent recursive function invocation.
    Reuses the user's selected model for all sub-agent calls.
    """

    def __init__(self, model_id: str, max_prompt_tokens: int = 6000):
        self._model_id = model_id
        self._max_prompt_chars = max_prompt_tokens * 4  # rough char-to-token ratio

    async def run(
        self,
        system_prompt: str,
        user_prompt: str,
        request: Any,
        user: Dict,
        metadata: Optional[Dict] = None,
        json_mode: bool = False,
    ) -> str:
        """Execute a sub-agent LLM call.

        Args:
            system_prompt: System-level instructions for the sub-agent.
            user_prompt: The user-facing query for this sub-task.
            request: The OWUI __request__ object for auth context.
            user: The OWUI __user__ dict.
            metadata: Optional metadata to forward.
            json_mode: Whether to enforce JSON-only output framing.

        Returns:
            The LLM's response content as a string.
        """
        from open_webui.utils.chat import generate_chat_completion
        from open_webui.models.users import UserModel

        if json_mode:
            sys_msg = ("You are a JSON data extraction API. "
                       "Respond with ONLY valid JSON. "
                       "No explanations, no markdown fences, no commentary.")
        else:
            sys_msg = "Follow the user's instructions precisely."

        combined = (
            f"INSTRUCTIONS (follow these exactly):\n{system_prompt}\n\n"
            f"---\nINPUT:\n{user_prompt}"
        )

        # Truncate if prompt exceeds budget (preserves system instructions,
        # truncates user content from the end).  The budget is derived from
        # max_prompt_tokens which should match the model's context window
        # minus a reserve for the response (~4k tokens).
        total_chars = len(sys_msg) + len(combined)
        if total_chars > self._max_prompt_chars:
            budget = self._max_prompt_chars - len(sys_msg) - 100
            if budget > len(system_prompt) + 200:
                # Keep full instructions, truncate input
                combined = combined[:budget] + (
                    "\n\n[... content truncated to fit context window ...]"
                )
            else:
                combined = combined[:max(budget, 500)] + (
                    "\n\n[... content truncated to fit context window ...]"
                )
            logger.warning(
                "Truncated prompt: %d→%d chars (~%d→%d tokens, budget %d tokens). "
                "Increase max_prompt_tokens or reduce research scope.",
                total_chars, len(sys_msg) + len(combined),
                total_chars // 4, (len(sys_msg) + len(combined)) // 4,
                self._max_prompt_chars // 4,
            )

        form_data = {
            "model": self._model_id,
            "messages": [
                {"role": "system", "content": sys_msg},
                {"role": "user", "content": combined},
            ],
            "stream": False,
            "metadata": {"task": "deep_research_sub_agent"},
        }

        response = await generate_chat_completion(
            request=request,
            form_data=form_data,
            user=UserModel(**user),
            bypass_filter=True,
        )

        content = response["choices"][0]["message"]["content"]
        logger.debug("Sub-agent response length: %d chars", len(content))
        return content

    async def run_json(
        self,
        system_prompt: str,
        user_prompt: str,
        request: Any,
        user: Dict,
        metadata: Optional[Dict] = None,
    ) -> Any:
        """Call LLM and parse response as JSON."""
        raw = await self.run(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            request=request,
            user=user,
            metadata=metadata,
            json_mode=True,
        )
        try:
            return self._parse_json_response(raw)
        except ValueError:
            logger.warning("JSON parse failed. Raw response (first 500 chars): %s",
                           raw[:500] if raw else "<empty>")
            raise

    @staticmethod
    def _parse_json_response(text: str) -> Any:
        """Extract and parse JSON from an LLM response.

        Handles pure JSON, JSON in markdown code blocks, and JSON
        embedded in surrounding commentary text.
        """
        text = text.strip()

        # Attempt 1: Pure JSON
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Attempt 2: Markdown code blocks
        import re

        pattern = r"```(?:json)?\s*\n?(.*?)\n?\s*```"
        match = re.search(pattern, text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1).strip())
            except json.JSONDecodeError:
                pass

        # Attempt 3: Find first JSON array or object in raw text
        for pat in [r'(\[\s*\{.*\}\s*\])', r'(\{.*\})']:
            m = re.search(pat, text, re.DOTALL)
            if m:
                try:
                    return json.loads(m.group(1))
                except json.JSONDecodeError:
                    pass

        raise ValueError(
            f"Could not parse JSON from LLM response: {text[:300]}..."
        )

    @staticmethod
    def resolve_model_id(
        metadata: Optional[Dict],
        model: Optional[Dict],
    ) -> str:
        """Extract the active model ID from OWUI context objects.

        Args:
            metadata: The __metadata__ dict from OWUI.
            model: The __model__ dict from OWUI.

        Returns:
            Model ID string, or empty string if not found.
        """
        model_id = (
            ((metadata or {}).get("model") or {}).get("id", "")
            or (model or {}).get("id", "")
        )
        return model_id


# ---- Anchor extraction ----

_ANCHOR_PROMPT = """\
Extract a structured research anchor from the user's query.
This anchor will guide all subsequent search, analysis, and synthesis steps.

Return JSON:
{"key_concepts": ["specific concepts/terms the user mentioned"],
 "intent": "one sentence: what the user wants to learn or accomplish",
 "scope_in": ["topics that ARE in scope"],
 "scope_out": ["adjacent topics that are NOT being asked about"],
 "must_cover": ["terms/phrases from the query that MUST appear in results"],
 "initial_search_terms": ["3-5 diverse web search queries designed to find \
authoritative sources. Include: (1) the raw query, (2) an official-docs query \
like 'X official documentation' or 'X getting started', (3) a technical \
definition query like 'what is X framework'. Optimize for search engines, \
not conversational phrasing."]}

Be precise — use the user's exact words for key_concepts and must_cover. \
For initial_search_terms, rewrite the query into effective web search phrases.\
"""


async def extract_anchor(sa: SubAgent, query: str, request: Any, user: Dict) -> tuple:
    """Run one LLM call to distil the query into a reusable anchor block.

    Args:
        sa: SubAgent for LLM calls.
        query: Raw user query.
        request: OWUI __request__ object.
        user: OWUI __user__ dict.

    Returns:
        Tuple of (anchor_string, initial_search_terms).
        anchor_string: Multi-line anchor to prepend to every prompt.
        initial_search_terms: List of diverse search queries for iteration 1.
    """
    try:
        r = await sa.run_json(_ANCHOR_PROMPT, query, request, user)
    except Exception:
        return (
            f"RESEARCH ANCHOR\nQuery: {query}\n"
            f"Key concepts: (extraction failed \u2014 use query as-is)",
            [query],
        )
    lines = ["RESEARCH ANCHOR", f"Query: {query}"]
    if r.get("key_concepts"):
        lines.append(f"Key concepts: {', '.join(r['key_concepts'])}")
    if r.get("intent"):
        lines.append(f"Intent: {r['intent']}")
    if r.get("must_cover"):
        lines.append(f"Must cover: {', '.join(r['must_cover'])}")
    if r.get("scope_in"):
        lines.append(f"In scope: {', '.join(r['scope_in'])}")
    if r.get("scope_out"):
        lines.append(f"Out of scope: {', '.join(r['scope_out'])}")

    search_terms = r.get("initial_search_terms", [])
    if not search_terms:
        search_terms = [query]
    # Always include the raw query as a fallback
    if query not in search_terms:
        search_terms.insert(0, query)

    return "\n".join(lines), search_terms
