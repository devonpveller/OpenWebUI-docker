"""
title: Context Window Manager
author: ai-stack
version: 1.0.0
description: Sliding window filter that keeps conversations within token budget. Compresses old tool results first, then drops oldest messages. Protects system prompts and recent messages. Works transparently with any model.
required_open_webui_version: 0.4.0
"""

from pydantic import BaseModel, Field
from typing import Optional, Callable, Awaitable
import json
import time


class Filter:
    class Valves(BaseModel):
        MAX_CONTEXT_TOKENS: int = Field(
            default=0,
            description="Model's context window in tokens. Set this to your model's limit (e.g. 32768, 128000). 0 = use MAX_CONTEXT_CHARS instead.",
        )
        HEADROOM_TOKENS: int = Field(
            default=2048,
            description="Tokens reserved for the model's response and overhead. Budget = MAX_CONTEXT_TOKENS - HEADROOM_TOKENS.",
        )
        CHARS_PER_TOKEN: float = Field(
            default=3.0,
            description="Estimated characters per token. 3.0 is safe for code/JSON-heavy content. 3.5-4.0 for mostly English prose.",
        )
        MAX_CONTEXT_CHARS: int = Field(
            default=80000,
            description="Fallback: max total chars if MAX_CONTEXT_TOKENS is 0. Ignored when MAX_CONTEXT_TOKENS is set.",
        )
        PRESERVE_RECENT: int = Field(
            default=6,
            description="Always keep the N most recent messages completely intact (no truncation).",
        )
        TOOL_RESULT_CAP: int = Field(
            default=3000,
            description="Max chars per tool result in older (non-protected) messages. Larger results are truncated with a note.",
        )
        TOOL_CALL_CAP: int = Field(
            default=200,
            description="Max chars per tool_calls JSON in older assistant messages. Old call details are noise — keep just the function names.",
        )
        ASSISTANT_CAP: int = Field(
            default=6000,
            description="Max chars per assistant message in older (non-protected) messages.",
        )
        ENABLE_STATUS: bool = Field(
            default=True,
            description="Show a brief status message when context is trimmed.",
        )
        priority: int = Field(
            default=1,
            description="Filter priority (lower = runs first). Set low so this runs before other filters.",
        )

    def __init__(self):
        self.valves = self.Valves()

    def _get_char_budget(self) -> int:
        """Calculate the character budget from token settings or fallback."""
        if self.valves.MAX_CONTEXT_TOKENS > 0:
            usable_tokens = self.valves.MAX_CONTEXT_TOKENS - self.valves.HEADROOM_TOKENS
            return max(1000, int(usable_tokens * self.valves.CHARS_PER_TOKEN))
        return self.valves.MAX_CONTEXT_CHARS

    def _char_count(self, messages: list) -> int:
        """Sum character length of all message content."""
        total = 0
        for m in messages:
            content = m.get("content")
            if isinstance(content, str):
                total += len(content)
            elif isinstance(content, list):
                # Multimodal content (list of parts)
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        total += len(part.get("text", ""))
            # Count tool_calls JSON if present (they consume tokens too)
            if m.get("tool_calls"):
                total += len(json.dumps(m["tool_calls"]))
        return total

    def _truncate_content(self, content: str, cap: int, label: str) -> str:
        """Truncate content to cap chars with a clean break and a note."""
        if len(content) <= cap:
            return content
        # Try to cut at a newline for cleaner output
        cut = content[:cap].rfind("\n")
        if cut < cap // 2:
            cut = cap
        trimmed = len(content) - cut
        return (
            content[:cut]
            + f"\n\n... [{label}: {trimmed} chars trimmed by context manager]"
        )

    def _is_tool_result(self, msg: dict) -> bool:
        """Check if a message is a tool result."""
        return msg.get("role") == "tool"

    def _has_tool_calls(self, msg: dict) -> bool:
        """Check if an assistant message contains tool calls."""
        return bool(msg.get("tool_calls"))

    async def inlet(
        self,
        body: dict,
        __user__: Optional[dict] = None,
        __event_emitter__: Optional[Callable[[dict], Awaitable[None]]] = None,
    ) -> dict:
        messages = body.get("messages", [])
        if len(messages) <= self.valves.PRESERVE_RECENT:
            return body

        # Separate system messages (always preserved in full)
        system_msgs = []
        conversation = []
        for m in messages:
            if m.get("role") == "system":
                system_msgs.append(m)
            else:
                conversation.append(m)

        if not conversation:
            return body

        original_chars = self._char_count(messages)

        # If under budget, pass through unchanged
        if original_chars <= self.valves.MAX_CONTEXT_CHARS:
            return body

        # --- Phase 1: Compress older messages ---
        # Protected = last N messages (never touched)
        protect_count = min(self.valves.PRESERVE_RECENT, len(conversation))
        old_msgs = conversation[:-protect_count] if protect_count > 0 else conversation[:]
        recent_msgs = conversation[-protect_count:] if protect_count > 0 else []

        compressed_count = 0
        for i, msg in enumerate(old_msgs):
            role = msg.get("role", "")

            # Compress tool results most aggressively
            content = msg.get("content", "")
            if isinstance(content, str) and self._is_tool_result(msg) and len(content) > self.valves.TOOL_RESULT_CAP:
                old_msgs[i] = {
                    **msg,
                    "content": self._truncate_content(
                        content, self.valves.TOOL_RESULT_CAP, "tool result"
                    ),
                }
                compressed_count += 1

            # Compress assistant messages with tool_calls — shrink the calls JSON
            # and truncate any content
            elif role == "assistant" and self._has_tool_calls(msg):
                changed = False
                tc_json = json.dumps(msg["tool_calls"])
                if len(tc_json) > self.valves.TOOL_CALL_CAP:
                    # Keep just function names for reference
                    summary_calls = []
                    for tc in msg["tool_calls"]:
                        fn = tc.get("function", {})
                        summary_calls.append({
                            "id": tc.get("id", ""),
                            "type": "function",
                            "function": {
                                "name": fn.get("name", "unknown"),
                                "arguments": "{}",
                            },
                        })
                    old_msgs[i] = {**msg, "tool_calls": summary_calls}
                    changed = True
                if isinstance(content, str) and len(content) > self.valves.ASSISTANT_CAP:
                    old_msgs[i] = {
                        **old_msgs[i],
                        "content": self._truncate_content(
                            content, self.valves.ASSISTANT_CAP, "assistant response"
                        ),
                    }
                    changed = True
                if changed:
                    compressed_count += 1

            # Compress long assistant messages without tool_calls
            elif role == "assistant" and isinstance(content, str) and len(content) > self.valves.ASSISTANT_CAP:
                old_msgs[i] = {
                    **msg,
                    "content": self._truncate_content(
                        content, self.valves.ASSISTANT_CAP, "assistant response"
                    ),
                }
                compressed_count += 1

            # Compress long user messages (e.g. pasted code blocks)
            elif role == "user" and isinstance(content, str) and len(content) > self.valves.ASSISTANT_CAP:
                old_msgs[i] = {
                    **msg,
                    "content": self._truncate_content(
                        content, self.valves.ASSISTANT_CAP, "user message"
                    ),
                }
                compressed_count += 1

        conversation = old_msgs + recent_msgs
        current_chars = self._char_count(system_msgs + conversation)

        # --- Phase 2: Drop oldest messages if still over budget ---
        dropped_count = 0
        while (
            len(conversation) > protect_count
            and current_chars > self.valves.MAX_CONTEXT_CHARS
        ):
            # Drop from front, but handle tool call pairs:
            # If dropping an assistant msg with tool_calls, also drop the
            # following tool result messages that belong to it.
            dropped = conversation.pop(0)
            drop_chars = len(dropped.get("content", "") or "")
            if dropped.get("tool_calls"):
                drop_chars += len(json.dumps(dropped["tool_calls"]))

            # Drop orphaned tool results that followed the assistant tool_call
            while conversation and len(conversation) > protect_count:
                if self._is_tool_result(conversation[0]):
                    orphan = conversation.pop(0)
                    drop_chars += len(orphan.get("content", "") or "")
                else:
                    break

            current_chars -= drop_chars
            dropped_count += 1

        body["messages"] = system_msgs + conversation

        # --- Emit status if anything was modified ---
        if (compressed_count > 0 or dropped_count > 0) and self.valves.ENABLE_STATUS and __event_emitter__:
            parts = []
            if compressed_count:
                parts.append(f"{compressed_count} older messages compressed")
            if dropped_count:
                parts.append(f"{dropped_count} oldest exchanges dropped")
            savings = original_chars - self._char_count(body["messages"])
            parts.append(f"~{savings // 1000}k chars freed")

            await __event_emitter__(
                {
                    "type": "status",
                    "data": {
                        "description": f"Context managed: {', '.join(parts)}",
                        "done": True,
                    },
                }
            )

        return body
