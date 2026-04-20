"""
title: Context Window Manager
author: ai-stack
version: 2.2.0
description: Sliding window filter that enforces token budget. Accounts for tool schemas and chat template overhead. Compresses old messages, drops oldest, emergency-compresses recent. Logs all actions. Set MAX_CONTEXT_TOKENS to your model's limit.
required_open_webui_version: 0.4.0
"""

from pydantic import BaseModel, Field
from typing import Optional, Callable, Awaitable
import json
import logging

log = logging.getLogger("context_window_filter")
log.setLevel(logging.DEBUG)
if not log.handlers:
    log.addHandler(logging.StreamHandler())


class Filter:
    class Valves(BaseModel):
        MAX_CONTEXT_TOKENS: int = Field(
            default=32768,
            description="Model's context window in tokens (e.g. 32768, 128000).",
        )
        HEADROOM_TOKENS: int = Field(
            default=4096,
            description="Tokens reserved for model output. Budget = MAX_CONTEXT_TOKENS - HEADROOM - tool_overhead - template_overhead.",
        )
        TOKENS_PER_MESSAGE: int = Field(
            default=20,
            description="Estimated tokens per message for chat template overhead (role markers, special tokens, etc).",
        )
        CHARS_PER_TOKEN: float = Field(
            default=1.8,
            description="Chars per token ratio. Measured: Qwen+tools = ~1.83. Use 1.8 for safety. Higher = more permissive. Lower = more aggressive.",
        )
        PRESERVE_RECENT: int = Field(
            default=4,
            description="Always keep the N most recent messages (compressed in emergency only).",
        )
        TOOL_RESULT_CAP: int = Field(
            default=2000,
            description="Max chars per tool result in older messages.",
        )
        TOOL_CALL_CAP: int = Field(
            default=200,
            description="Max chars per tool_calls JSON in older messages. Strips arguments, keeps function names.",
        )
        ASSISTANT_CAP: int = Field(
            default=4000,
            description="Max chars per assistant/user message in older messages.",
        )
        EMERGENCY_CAP: int = Field(
            default=1000,
            description="Max chars per message during emergency compression of recent messages.",
        )
        ENABLE_STATUS: bool = Field(
            default=True,
            description="Show status message in chat when context is trimmed.",
        )
        priority: int = Field(
            default=1,
            description="Filter priority (lower = runs first).",
        )

    def __init__(self):
        self.valves = self.Valves()

    def _char_budget(self, body: dict) -> int:
        # Start with token budget minus output headroom
        usable = self.valves.MAX_CONTEXT_TOKENS - self.valves.HEADROOM_TOKENS

        # Subtract tool definition overhead (schemas sent alongside messages)
        tools = body.get("tools", [])
        if tools:
            tools_chars = len(json.dumps(tools))
            tools_tokens = int(tools_chars / self.valves.CHARS_PER_TOKEN)
            usable -= tools_tokens
            log.info(f"[CWF] tool definitions: {tools_chars} chars (~{tools_tokens} tokens)")

        # Subtract per-message chat template overhead
        n_msgs = len(body.get("messages", []))
        template_tokens = n_msgs * self.valves.TOKENS_PER_MESSAGE
        usable -= template_tokens
        log.info(f"[CWF] template overhead: {n_msgs} msgs * {self.valves.TOKENS_PER_MESSAGE} = ~{template_tokens} tokens")

        return max(2000, int(usable * self.valves.CHARS_PER_TOKEN))

    def _measure(self, messages: list) -> int:
        total = 0
        for m in messages:
            c = m.get("content")
            if isinstance(c, str):
                total += len(c)
            elif isinstance(c, list):
                for part in c:
                    if isinstance(part, dict) and part.get("type") == "text":
                        total += len(part.get("text", ""))
            if m.get("tool_calls"):
                total += len(json.dumps(m["tool_calls"]))
        return total

    def _truncate(self, text: str, cap: int, label: str) -> str:
        if len(text) <= cap:
            return text
        cut = text[:cap].rfind("\n")
        if cut < cap // 2:
            cut = cap
        removed = len(text) - cut
        return text[:cut] + f"\n\n... [{label}: {removed} chars trimmed]"

    def _compress_msg(self, msg: dict, content_cap: int, label: str) -> tuple:
        """Compress a single message. Returns (new_msg, chars_saved)."""
        saved = 0
        new_msg = msg

        content = msg.get("content", "")
        if isinstance(content, str) and len(content) > content_cap:
            new_content = self._truncate(content, content_cap, label)
            saved += len(content) - len(new_content)
            new_msg = {**new_msg, "content": new_content}

        if msg.get("tool_calls"):
            tc_json = json.dumps(msg["tool_calls"])
            if len(tc_json) > self.valves.TOOL_CALL_CAP:
                slim = []
                for tc in msg["tool_calls"]:
                    fn = tc.get("function", {})
                    slim.append({
                        "id": tc.get("id", ""),
                        "type": "function",
                        "function": {"name": fn.get("name", "?"), "arguments": "{}"},
                    })
                saved += len(tc_json) - len(json.dumps(slim))
                new_msg = {**new_msg, "tool_calls": slim}

        return new_msg, saved

    async def inlet(
        self,
        body: dict,
        __user__: Optional[dict] = None,
        __event_emitter__: Optional[Callable[[dict], Awaitable[None]]] = None,
    ) -> dict:
        messages = body.get("messages", [])
        budget = self._char_budget(body)
        original = self._measure(messages)

        log.info(
            f"[CWF] inlet: {len(messages)} msgs, {original} chars, "
            f"budget {budget} chars ({self.valves.MAX_CONTEXT_TOKENS}tok - "
            f"{self.valves.HEADROOM_TOKENS}headroom @ {self.valves.CHARS_PER_TOKEN}c/t)"
        )

        if original <= budget:
            log.info("[CWF] under budget, passing through")
            return body

        log.warning(f"[CWF] OVER BUDGET by {original - budget} chars, trimming...")

        # Split system vs conversation
        system = [m for m in messages if m.get("role") == "system"]
        convo = [m for m in messages if m.get("role") != "system"]

        if not convo:
            return body

        # --- Phase 1: Compress older messages ---
        protect_n = min(self.valves.PRESERVE_RECENT, len(convo))
        old = convo[:-protect_n] if protect_n else convo[:]
        recent = convo[-protect_n:] if protect_n else []

        compressed = 0
        for i, msg in enumerate(old):
            role = msg.get("role", "")
            content = msg.get("content", "")

            if role == "tool" and isinstance(content, str) and len(content) > self.valves.TOOL_RESULT_CAP:
                old[i], s = self._compress_msg(msg, self.valves.TOOL_RESULT_CAP, "tool result")
                if s > 0:
                    compressed += 1
            elif role == "assistant":
                old[i], s = self._compress_msg(msg, self.valves.ASSISTANT_CAP, "assistant")
                if s > 0:
                    compressed += 1
            elif role == "user" and isinstance(content, str) and len(content) > self.valves.ASSISTANT_CAP:
                old[i], s = self._compress_msg(msg, self.valves.ASSISTANT_CAP, "user msg")
                if s > 0:
                    compressed += 1

        convo = old + recent
        current = self._measure(system + convo)
        log.info(f"[CWF] after phase 1 (compress old): {current} chars, compressed {compressed} msgs")

        # --- Phase 2: Drop oldest messages ---
        dropped = 0
        while len(convo) > protect_n and current > budget:
            msg = convo.pop(0)
            drop = len(msg.get("content", "") or "")
            if msg.get("tool_calls"):
                drop += len(json.dumps(msg["tool_calls"]))

            while convo and len(convo) > protect_n and convo[0].get("role") == "tool":
                orphan = convo.pop(0)
                drop += len(orphan.get("content", "") or "")

            current -= drop
            dropped += 1

        log.info(f"[CWF] after phase 2 (drop old): {current} chars, dropped {dropped} exchanges")

        # --- Phase 3: Emergency compress recent messages ---
        emergency = 0
        if current > budget:
            log.warning(f"[CWF] EMERGENCY: recent msgs alone = {current} chars > {budget} budget")
            for i, msg in enumerate(convo):
                if current <= budget:
                    break
                role = msg.get("role", "")
                cap = self.valves.EMERGENCY_CAP
                if role == "tool":
                    cap = min(cap, self.valves.TOOL_RESULT_CAP)
                convo[i], s = self._compress_msg(msg, cap, f"{role} (emergency)")
                if s > 0:
                    current -= s
                    emergency += 1

        log.info(f"[CWF] after phase 3 (emergency): {current} chars, emergency-compressed {emergency} msgs")

        # --- Phase 4: Nuclear — if STILL over, drop conversation msgs until fits ---
        nuked = 0
        while len(convo) > 2 and current > budget:
            msg = convo.pop(0)
            drop = len(msg.get("content", "") or "")
            if msg.get("tool_calls"):
                drop += len(json.dumps(msg["tool_calls"]))
            while convo and len(convo) > 2 and convo[0].get("role") == "tool":
                orphan = convo.pop(0)
                drop += len(orphan.get("content", "") or "")
            current -= drop
            nuked += 1

        if nuked:
            log.warning(f"[CWF] NUCLEAR: dropped {nuked} more exchanges, now {current} chars")

        body["messages"] = system + convo
        final = self._measure(body["messages"])
        est_tokens = int(final / self.valves.CHARS_PER_TOKEN)

        log.info(
            f"[CWF] DONE: {len(body['messages'])} msgs, {final} chars, "
            f"~{est_tokens} tokens (limit {self.valves.MAX_CONTEXT_TOKENS})"
        )

        # --- Emit status ---
        if (compressed + dropped + emergency + nuked > 0) and self.valves.ENABLE_STATUS and __event_emitter__:
            parts = []
            if compressed:
                parts.append(f"{compressed} compressed")
            if dropped:
                parts.append(f"{dropped} dropped")
            if emergency:
                parts.append(f"{emergency} emergency-trimmed")
            if nuked:
                parts.append(f"{nuked} force-dropped")
            savings = original - final
            parts.append(f"~{savings // 1000}k chars freed")
            parts.append(f"~{est_tokens}/{self.valves.MAX_CONTEXT_TOKENS} tokens")

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
