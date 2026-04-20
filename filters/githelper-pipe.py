"""
title: GitHelper
author: ai-stack
version: 2.0.0
description: GitHub repo investigation agent with two-phase architecture. Phase 1 (Reason): full conversation context + compact tool catalog, no JSON schemas — model decides what to do. Phase 2 (Act): pipe synthesizes tool_call events from the model's output. OpenWebUI middleware executes tools and loops back. FileShed bridges context across steps.
required_open_webui_version: 0.4.0
"""

from pydantic import BaseModel, Field
from typing import Optional, Union, Callable, Awaitable, AsyncGenerator
import json
import logging
import re
import uuid
import aiohttp
import time

log = logging.getLogger("githelper_pipe")
log.setLevel(logging.DEBUG)
if not log.handlers:
    log.addHandler(logging.StreamHandler())

# ── System prompt (loaded from file if available, else embedded fallback) ──

SYSTEM_PROMPT = """\
You are a Senior GitHub Engineer with two complementary specializations:

**Mode 1 — Git Operations & Troubleshooting**
When a user asks about Git workflows, commands, merge conflicts, branching strategies, LFS, .gitignore, CI/CD, or repository management, provide expert guidance. Give clear explanations with practical commands.

**Mode 2 — Repository Analysis & Feature Validation**
When a user asks you to examine, audit, or validate code in a GitHub repository, use your tools. Never try to answer from memory — always gather real evidence from the repository first.

## Tool Use — CRITICAL

You have GitHub repository analysis tools. **Use them aggressively and without hesitation.** Never stall, deliberate, or narrate what you plan to do — just call the tool immediately.

### Progressive Exploration (Token Budget Strategy)

Your tools are designed for **progressive disclosure** — start small, then drill into what matters.

**Level 1 — Map** (~2-4k tokens): `get_repo_overview` → compact metadata + root tree + README snippet.
**Level 2 — Scan** (~1-2k per file): `bulk_read_files(preview=true)` or `get_repo_file(max_lines=50)` → first 50 lines.
**Level 3 — Read** (full): `get_repo_file` or `bulk_read_files` on confirmed-relevant files. Store in FileShed.

**The pattern:** Map → Scan → Read → Answer. Not: dump everything → run out of context → stall.

**Sliding window:** A context manager automatically compresses old tool results and drops the oldest messages when the conversation grows too large. You may see `[trimmed]` in older messages — this is normal. Your recent messages and FileShed notes are always intact. You can safely do long investigations without stalling.

### Investigation Mode (Deep Exploration)

For questions requiring understanding of how a system works, use an **iterative exploration loop**:

```
Map (overview) → Identify first targets
  └→ Loop:
       Read target file(s)
       Write analysis notes to FileShed (findings, patterns, new targets)
       Read next targets (informed by what you just learned)
  └→ When you have enough understanding:
       Recall your shed notes
       Synthesize your answer
```

**Critical: shed analysis notes, not raw files.** Write what you learned (~500 chars of distilled knowledge) instead of ~5k chars of raw code. You can investigate 10x more files this way.

### FileShed — Context Management

Use FileShed as working memory throughout your investigation:
1. After reading files, write analysis notes to FileShed with `shed_create_file`.
2. Continue exploring with free context — your notes are safe.
3. When ready to answer, recall shed notes with `shed_read_file` and synthesize.

### Rules

1. **Act, don't narrate.** Never say "Let me fetch…" — just call the tool.
2. **Chain calls without pausing.** After every tool result, immediately call the next tool or give your answer.
3. **Start with `get_repo_overview`.** For any new repository.
4. **Preview before full read.** Use preview mode to scan files before full reads.
5. **Use `bulk_read_files` over `get_repo_file`** when you need 2+ files.
6. **Shed notes, not hoards.** Write analysis notes after reading files.
7. **Switch to investigation mode for deep questions** (4+ files needed).
8. **Know when to stop.** If overview + 2-3 previews answer the question, respond.

### Anti-Patterns (NEVER do these)
- Reading 10 full files without previewing first.
- Shedding raw file contents instead of analysis notes.
- Reading 3+ files without shedding between rounds.
- Ending a response with "Let me know if you'd like me to look at…" when you could just look now.
- Summarizing a tool result and stopping without calling the next obvious tool.
"""

# ── Tool call format injected when tools are available ──

TOOL_CALL_FORMAT = """
## How to Call Tools

To use a tool, output a tool call block immediately:

<tool_call>
{"name": "function_name", "arguments": {"param1": "value1"}}
</tool_call>

Rules:
- Call the tool IMMEDIATELY — don't narrate what you plan to do
- Arguments must be valid JSON with correct parameter types
- STOP writing after the <tool_call> block — the result will appear in your next turn
- For multiple tools, use multiple <tool_call> blocks
- If you don't need a tool, respond with text as normal
"""


class Pipe:
    class Valves(BaseModel):
        TARGET_BASE_URL: str = Field(
            default="http://llama-cpp:8080/v1",
            description="Backend URL (e.g. http://llama-cpp:8080/v1).",
        )
        TARGET_MODEL_ID: str = Field(
            default="",
            description="Model ID for the backend. Leave empty to auto-detect.",
        )
        SYSTEM_PROMPT_FILE: str = Field(
            default="/host_project/system-prompts/git-helper-system-prompt.md",
            description="Path to system prompt file inside container.",
        )
        MAX_CONTEXT_TOKENS: int = Field(
            default=32768,
            description="Model's context window in tokens.",
        )
        HEADROOM_TOKENS: int = Field(
            default=4096,
            description="Tokens reserved for model output.",
        )
        CHARS_PER_TOKEN: float = Field(
            default=1.8,
            description="Chars per token ratio.",
        )
        TOKENS_PER_MESSAGE: int = Field(
            default=20,
            description="Template overhead per message.",
        )
        PRESERVE_RECENT: int = Field(
            default=4,
            description="Always keep N most recent messages.",
        )
        TOOL_RESULT_CAP: int = Field(
            default=2000,
            description="Max chars per tool result in older messages.",
        )
        TOOL_CALL_CAP: int = Field(
            default=200,
            description="Max chars per tool_calls JSON in older messages.",
        )
        ASSISTANT_CAP: int = Field(
            default=4000,
            description="Max chars per assistant/user message in older messages.",
        )
        EMERGENCY_CAP: int = Field(
            default=1000,
            description="Max chars per message during emergency compression.",
        )
        MAX_CATALOG_CHARS: int = Field(
            default=12000,
            description="Max chars for tool catalog in system prompt. Tools mentioned in the system prompt are prioritized.",
        )
        ENABLE_STATUS: bool = Field(
            default=True,
            description="Show status messages.",
        )
        REQUEST_TIMEOUT: int = Field(
            default=300,
            description="HTTP timeout in seconds.",
        )

    def __init__(self):
        self.valves = self.Valves()
        self._system_prompt_cache: Optional[str] = None

    def pipes(self) -> list[dict]:
        return [{"id": "githelper", "name": "GitHelper"}]

    # ── System prompt loading ──

    def _get_system_prompt(self) -> str:
        if self._system_prompt_cache is not None:
            return self._system_prompt_cache
        try:
            path = self.valves.SYSTEM_PROMPT_FILE
            if path:
                with open(path, "r", encoding="utf-8") as f:
                    self._system_prompt_cache = f.read()
                    log.info(f"[GH] loaded system prompt from {path} ({len(self._system_prompt_cache)} chars)")
                    return self._system_prompt_cache
        except (FileNotFoundError, PermissionError, OSError) as e:
            log.warning(f"[GH] could not load {self.valves.SYSTEM_PROMPT_FILE}: {e}, using built-in")
        self._system_prompt_cache = SYSTEM_PROMPT
        return self._system_prompt_cache

    # ── Tool catalog (replaces JSON schemas — ~100 chars/tool vs ~800) ──

    def _build_tool_catalog(self, tools: list) -> str:
        """Build compact function signatures from tool schemas.
        The model uses these to decide WHICH tool to call.
        Actual tool execution is handled by OpenWebUI's middleware."""
        if not tools:
            return ""

        tool_lines = []
        for tool in tools:
            fn = tool.get("function", {})
            name = fn.get("name", "")
            if not name:
                continue
            desc = fn.get("description", "")
            if len(desc) > 120:
                desc = desc[:117] + "..."

            params = fn.get("parameters", {})
            props = params.get("properties", {})
            required = set(params.get("required", []))

            parts = []
            for pname, pinfo in props.items():
                ptype = pinfo.get("type", "str") if isinstance(pinfo, dict) else "any"
                opt = "" if pname in required else "?"
                parts.append(f"{pname}{opt}: {ptype}")

            sig = ", ".join(parts)
            tool_lines.append((name, f"- `{name}({sig})` — {desc}"))

        # Prioritize tools mentioned in system prompt
        prompt = self._get_system_prompt()
        priority = []
        rest = []
        for name, line in tool_lines:
            if name in prompt:
                priority.append(line)
            else:
                rest.append(line)

        lines = ["## Available Tools\n"] + priority + rest
        catalog = "\n".join(lines)

        cap = self.valves.MAX_CATALOG_CHARS
        if cap and len(catalog) > cap:
            catalog = catalog[:cap].rsplit("\n", 1)[0]
            catalog += f"\n... ({len(tool_lines)} tools total, catalog truncated)"
            log.info(f"[GH] catalog truncated to {len(catalog)} chars")

        log.info(f"[GH] tool catalog: {len(catalog)} chars ({len(priority)} priority + {len(rest)} other)")
        return catalog

    # ── Message normalization ──

    def _normalize_messages(self, messages: list) -> list:
        """Convert tool_calls and tool-result messages to plain text.
        Without the `tools` parameter, the Jinja template won't render
        role:tool or tool_calls properly. This converts them to formats
        the model recognizes from training (<tool_call>, <tool_response>)."""
        out = []
        for msg in messages:
            role = msg.get("role", "")

            if role == "assistant" and msg.get("tool_calls"):
                calls_text = []
                for tc in msg["tool_calls"]:
                    fn = tc.get("function", {})
                    name = fn.get("name", "unknown")
                    args = fn.get("arguments", "{}")
                    if isinstance(args, str):
                        try:
                            args = json.dumps(json.loads(args))
                        except (json.JSONDecodeError, TypeError):
                            pass
                    calls_text.append(
                        f'<tool_call>\n{{"name": "{name}", "arguments": {args}}}\n</tool_call>'
                    )
                content = msg.get("content", "") or ""
                full = content + ("\n\n" if content else "") + "\n".join(calls_text)
                out.append({"role": "assistant", "content": full})

            elif role == "tool":
                content = msg.get("content", "") or ""
                name = msg.get("name", "tool")
                out.append({
                    "role": "user",
                    "content": f'<tool_response name="{name}">\n{content}\n</tool_response>',
                })

            else:
                out.append(msg)

        return out

    # ── Response parsing ──

    _TOOL_CALL_RE = re.compile(r'<tool_call>\s*(\{.*?\})\s*</tool_call>', re.DOTALL)

    def _parse_tool_intent(self, response: str) -> tuple:
        """Parse <tool_call> blocks from model response.
        Returns (text_before_tools, list_of_tool_call_dicts)."""
        calls = []
        for match in self._TOOL_CALL_RE.finditer(response):
            try:
                call = json.loads(match.group(1))
                if isinstance(call, dict) and "name" in call:
                    args = call.get("arguments", {})
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except (json.JSONDecodeError, TypeError):
                            args = {}
                    calls.append({"name": str(call["name"]), "arguments": args})
            except json.JSONDecodeError as e:
                log.warning(f"[GH] malformed tool_call JSON: {e}")

        if calls:
            first = self._TOOL_CALL_RE.search(response)
            text = response[:first.start()].rstrip() if first else response
        else:
            text = response

        return text, calls

    # ── Streaming reason + tool_call interception ──

    async def _streaming_reason_act(
        self,
        url: str,
        payload: dict,
        __event_emitter__: Optional[Callable[[dict], Awaitable[None]]] = None,
        context_info: str = "",
    ) -> AsyncGenerator:
        """Stream backend response to user in real-time (thinking is visible).
        Intercept <tool_call> blocks and emit synthetic SSE tool_call events
        so OpenWebUI's middleware executes them."""
        chat_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
        timeout = aiohttp.ClientTimeout(total=self.valves.REQUEST_TIMEOUT)

        TOOL_TAG = "<tool_call>"
        LOOK_AHEAD = len(TOOL_TAG)  # 11 chars

        async def emit_status(desc: str, done: bool = False):
            if self.valves.ENABLE_STATUS and __event_emitter__:
                await __event_emitter__({
                    "type": "status",
                    "data": {"description": desc, "done": done},
                })

        def make_chunk(delta: dict, finish_reason=None) -> str:
            return "data: " + json.dumps({
                "id": chat_id,
                "object": "chat.completion.chunk",
                "choices": [{
                    "index": 0,
                    "delta": delta,
                    "finish_reason": finish_reason,
                }],
            })

        full_response = ""   # Accumulated for final tool_call parsing
        buffer = ""          # Unsent content (look-ahead for <tool_call>)
        tool_detected = False
        sent_role = False
        first_token = False
        t0 = time.monotonic()

        await emit_status(f"Reasoning... {context_info}")

        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                stream_payload = {**payload, "stream": True}
                async with session.post(
                    url, json=stream_payload,
                    headers={"Content-Type": "application/json"},
                ) as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        log.error(f"[GH] stream error {resp.status}: {error_text[:500]}")
                        await emit_status(f"Backend error: {resp.status}", done=True)
                        yield f"Error from backend: {resp.status} — {error_text[:200]}"
                        return

                    async for line in resp.content:
                        decoded = line.decode("utf-8", errors="replace").strip()
                        if not decoded or not decoded.startswith("data:"):
                            continue
                        if decoded.strip() == "data: [DONE]":
                            break

                        try:
                            chunk_data = json.loads(decoded[5:].strip())
                            delta = chunk_data.get("choices", [{}])[0].get("delta", {})
                            content = delta.get("content", "")
                        except (json.JSONDecodeError, IndexError, KeyError):
                            continue

                        # Handle role assignment from backend
                        if delta.get("role") and not sent_role:
                            yield make_chunk({"role": "assistant"})
                            sent_role = True

                        if not content:
                            continue

                        if not sent_role:
                            yield make_chunk({"role": "assistant"})
                            sent_role = True

                        # First token received — update status
                        if not first_token:
                            first_token = True
                            ttft = time.monotonic() - t0
                            await emit_status(f"Generating... (first token {ttft:.1f}s) {context_info}")

                        full_response += content

                        # Already past <tool_call> — just accumulate
                        if tool_detected:
                            continue

                        buffer += content

                        # Check for <tool_call> in buffer
                        tag_pos = buffer.find(TOOL_TAG)
                        if tag_pos >= 0:
                            tool_detected = True
                            # Send everything before the tag
                            pre = buffer[:tag_pos].rstrip()
                            if pre:
                                yield make_chunk({"content": pre})
                            buffer = ""
                            await emit_status(f"Tool call detected... {context_info}")
                            continue

                        # Look-ahead: hold back last N chars in case
                        # <tool_call> spans chunk boundaries
                        safe_len = max(0, len(buffer) - LOOK_AHEAD)
                        if safe_len > 0:
                            yield make_chunk({"content": buffer[:safe_len]})
                            buffer = buffer[safe_len:]

            elapsed = time.monotonic() - t0

            if not sent_role:
                yield make_chunk({"role": "assistant"})

            if not first_token:
                await emit_status(f"No response from backend ({elapsed:.1f}s)", done=True)
                log.warning(f"[GH] no tokens received after {elapsed:.1f}s")

            # Parse full response for tool calls
            text, tool_calls = self._parse_tool_intent(full_response)

            if tool_calls:
                names = [c["name"] for c in tool_calls]
                log.info(f"[GH] → tool calls: {names} ({elapsed:.1f}s)")
                await emit_status(f"Calling {', '.join(names)}... ({elapsed:.1f}s)", done=True)

                # Emit synthetic tool_call events
                for i, call in enumerate(tool_calls):
                    call_id = f"call_{uuid.uuid4().hex[:12]}"
                    args_str = json.dumps(call.get("arguments", {}))
                    yield make_chunk({
                        "tool_calls": [{
                            "index": i,
                            "id": call_id,
                            "type": "function",
                            "function": {"name": call["name"], "arguments": ""},
                        }]
                    })
                    yield make_chunk({
                        "tool_calls": [{
                            "index": i,
                            "function": {"arguments": args_str},
                        }]
                    })
                yield make_chunk({}, "tool_calls")
            else:
                log.info(f"[GH] → text ({len(full_response)} chars, {elapsed:.1f}s)")
                await emit_status(f"Done ({elapsed:.1f}s)", done=True)
                # Flush remaining buffer (no tool calls found)
                if buffer:
                    yield make_chunk({"content": buffer})
                yield make_chunk({}, "stop")

            yield "data: [DONE]"

        except aiohttp.ClientError as e:
            log.error(f"[GH] connection error: {e}")
            await emit_status(f"Connection error: {e}", done=True)
            yield f"Connection error: {e}"
        except Exception as e:
            log.error(f"[GH] error: {e}")
            await emit_status(f"Error: {e}", done=True)
            yield f"Error: {e}"

    # ── Context management ──

    def _char_budget(self, body: dict) -> int:
        """Calculate char budget. Tool schemas aren't sent — the compact catalog
        is part of the system message and counted by _measure automatically."""
        usable = self.valves.MAX_CONTEXT_TOKENS - self.valves.HEADROOM_TOKENS
        n_msgs = len(body.get("messages", []))
        template_tokens = n_msgs * self.valves.TOKENS_PER_MESSAGE
        usable -= template_tokens
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

    def _trim_messages(self, body: dict) -> dict:
        messages = body.get("messages", [])
        budget = self._char_budget(body)
        original = self._measure(messages)

        log.info(f"[GH] {len(messages)} msgs, {original} chars, budget {budget}")

        if original <= budget:
            return body

        log.warning(f"[GH] OVER by {original - budget} chars, trimming...")

        system = [m for m in messages if m.get("role") == "system"]
        convo = [m for m in messages if m.get("role") != "system"]
        if not convo:
            return body

        # Phase 1: Compress older messages
        protect_n = min(self.valves.PRESERVE_RECENT, len(convo))
        old = convo[:-protect_n] if protect_n else convo[:]
        recent = convo[-protect_n:] if protect_n else []

        compressed = 0
        for i, msg in enumerate(old):
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role == "tool" and isinstance(content, str) and len(content) > self.valves.TOOL_RESULT_CAP:
                old[i], s = self._compress_msg(msg, self.valves.TOOL_RESULT_CAP, "tool result")
                if s > 0: compressed += 1
            elif role == "assistant":
                old[i], s = self._compress_msg(msg, self.valves.ASSISTANT_CAP, "assistant")
                if s > 0: compressed += 1
            elif role == "user" and isinstance(content, str) and len(content) > self.valves.ASSISTANT_CAP:
                old[i], s = self._compress_msg(msg, self.valves.ASSISTANT_CAP, "user msg")
                if s > 0: compressed += 1

        convo = old + recent
        current = self._measure(system + convo)
        log.info(f"[GH] phase1: {current} chars, {compressed} compressed")

        # Phase 2: Drop oldest (protect last user message)
        dropped = 0
        while len(convo) > protect_n and current > budget:
            if convo[0].get("role") == "user":
                remaining_users = sum(1 for m in convo if m.get("role") == "user")
                if remaining_users <= 1:
                    break
            msg = convo.pop(0)
            drop = len(msg.get("content", "") or "")
            if msg.get("tool_calls"):
                drop += len(json.dumps(msg["tool_calls"]))
            # Drop orphaned tool results
            while convo and len(convo) > protect_n and convo[0].get("role") == "tool":
                orphan = convo.pop(0)
                drop += len(orphan.get("content", "") or "")
            current -= drop
            dropped += 1

        log.info(f"[GH] phase2: {current} chars, {dropped} dropped")

        # Phase 3: Emergency compress recent
        emergency = 0
        if current > budget:
            for i, msg in enumerate(convo):
                if current <= budget:
                    break
                cap = self.valves.EMERGENCY_CAP
                if msg.get("role") == "tool":
                    cap = min(cap, self.valves.TOOL_RESULT_CAP)
                convo[i], s = self._compress_msg(msg, cap, f"{msg.get('role', '')} (emergency)")
                if s > 0:
                    current -= s
                    emergency += 1

        # Phase 4: Nuclear (protect last user message)
        nuked = 0
        while len(convo) > 2 and current > budget:
            if convo[0].get("role") == "user":
                remaining_users = sum(1 for m in convo if m.get("role") == "user")
                if remaining_users <= 1:
                    break
            msg = convo.pop(0)
            drop = len(msg.get("content", "") or "")
            if msg.get("tool_calls"):
                drop += len(json.dumps(msg["tool_calls"]))
            while convo and len(convo) > 2 and convo[0].get("role") == "tool":
                orphan = convo.pop(0)
                drop += len(orphan.get("content", "") or "")
            current -= drop
            nuked += 1

        body["messages"] = system + convo
        final = self._measure(body["messages"])
        est = int(final / self.valves.CHARS_PER_TOKEN)
        log.info(f"[GH] DONE: {len(body['messages'])} msgs, {final} chars, ~{est} tok")
        return body

    # ── Pipe entry point ──

    async def pipe(
        self,
        body: dict,
        __user__: Optional[dict] = None,
        __event_emitter__: Optional[Callable[[dict], Awaitable[None]]] = None,
        __task__: Optional[str] = None,
    ) -> Union[str, AsyncGenerator]:
        url = f"{self.valves.TARGET_BASE_URL.rstrip('/')}/chat/completions"
        target_model = self.valves.TARGET_MODEL_ID or body.get("model", "")
        if "." in target_model and not self.valves.TARGET_MODEL_ID:
            target_model = ""

        # ── Tasks (title gen, etc.) — direct passthrough, no tools ──
        if __task__:
            payload = {
                "model": target_model,
                "messages": body["messages"],
                "stream": body.get("stream", True),
            }
            if body.get("stream", True):
                return self._stream_response(url, payload)
            else:
                return await self._reason_call(url, {**payload, "stream": False})

        # ── Regular conversation ──
        tools = body.get("tools", [])
        messages = list(body.get("messages", []))

        # Build system message: base prompt + tool catalog (if tools available)
        sys_content = self._get_system_prompt()
        if tools:
            catalog = self._build_tool_catalog(tools)
            sys_content += "\n\n" + catalog + TOOL_CALL_FORMAT

        # Set/replace system message (avoids catalog duplication on retries)
        if messages and messages[0].get("role") == "system":
            messages[0] = {"role": "system", "content": sys_content}
        else:
            messages = [{"role": "system", "content": sys_content}] + messages

        # Debug logging
        roles = [m.get("role", "?") for m in messages]
        has_user = any(r == "user" for r in roles)
        log.info(f"[GH] pipe(): roles={roles}, tools={len(tools)}, has_user={has_user}")

        # Trim context (generous budget — no tool schema overhead in payload)
        body_trimmed = {**body, "messages": messages}
        body_trimmed.pop("tools", None)
        original = self._measure(messages)
        body_trimmed = self._trim_messages(body_trimmed)
        trimmed = self._measure(body_trimmed["messages"])

        if trimmed < original and self.valves.ENABLE_STATUS and __event_emitter__:
            saved = original - trimmed
            est = int(trimmed / self.valves.CHARS_PER_TOKEN)
            await __event_emitter__({
                "type": "status",
                "data": {
                    "description": f"Context: ~{est}/{self.valves.MAX_CONTEXT_TOKENS} tok (~{saved // 1000}k freed)",
                    "done": True,
                },
            })

        # ── No tools: stream directly ──
        if not tools:
            payload = {
                "model": target_model,
                "messages": body_trimmed["messages"],
                "stream": True,
            }
            for key in ("temperature", "top_p", "top_k", "max_tokens", "stop",
                         "frequency_penalty", "presence_penalty", "seed"):
                if key in body and body[key] is not None:
                    payload[key] = body[key]
            return self._stream_response(url, payload)

        # ══════════════════════════════════════════════════════════════
        # Two-phase: Reason → Act
        #
        # Phase 1 (Reason): Full conversation context + compact tool
        #   catalog in system prompt. No JSON tool schemas in payload.
        #   The model decides: call a tool or give a text answer.
        #
        # Phase 2 (Act): Parse the model's <tool_call> blocks and emit
        #   synthetic OpenAI tool_call SSE events. OpenWebUI's middleware
        #   executes the tool and loops back to pipe() with the result.
        # ══════════════════════════════════════════════════════════════

        # Normalize: convert tool_calls/tool messages to plain text
        # so the backend can render them without the tool Jinja template
        normalized = self._normalize_messages(body_trimmed["messages"])

        # Streaming reason call — model output visible in real-time
        reason_payload = {
            "model": target_model,
            "messages": normalized,
        }
        for key in ("temperature", "top_p", "max_tokens"):
            if key in body and body[key] is not None:
                reason_payload[key] = body[key]

        ctx_chars = self._measure(normalized)
        ctx_est = int(ctx_chars / self.valves.CHARS_PER_TOKEN)
        n_tool_results = sum(1 for m in normalized if m.get("role") == "user" and "<tool_response" in (m.get("content", "") or ""))
        round_label = f"round {n_tool_results + 1}" if n_tool_results else "initial"
        context_info = f"({round_label}, ~{ctx_est}/{self.valves.MAX_CONTEXT_TOKENS} tok)"

        log.info(f"[GH] reason call: {len(normalized)} msgs, {ctx_chars} chars, {round_label}")

        # Stream response, intercept <tool_call> blocks, emit synthetic events
        return self._streaming_reason_act(url, reason_payload, __event_emitter__, context_info)

    # ── Backend communication ──

    async def _reason_call(self, url: str, payload: dict) -> str:
        """Sync call to backend. Returns full text response."""
        timeout = aiohttp.ClientTimeout(total=self.valves.REQUEST_TIMEOUT)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, json=payload,
                                        headers={"Content-Type": "application/json"}) as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        log.error(f"[GH] reason error {resp.status}: {error_text[:500]}")
                        return f"Error from backend: {resp.status} — {error_text[:200]}"
                    result = await resp.json()
                    choices = result.get("choices", [])
                    if choices:
                        return choices[0].get("message", {}).get("content", "") or ""
                    return ""
        except Exception as e:
            log.error(f"[GH] reason error: {e}")
            return f"Error: {e}"

    async def _stream_response(self, url: str, payload: dict) -> AsyncGenerator:
        """Yield raw SSE lines for direct passthrough (tasks, no-tool conversations)."""
        timeout = aiohttp.ClientTimeout(total=self.valves.REQUEST_TIMEOUT)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, json=payload,
                                        headers={"Content-Type": "application/json"}) as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        log.error(f"[GH] stream error {resp.status}: {error_text[:500]}")
                        yield f"Error from backend: {resp.status} — {error_text[:200]}"
                        return

                    async for line in resp.content:
                        decoded = line.decode("utf-8", errors="replace").strip()
                        if not decoded:
                            continue
                        if decoded.startswith("data:"):
                            yield decoded
        except aiohttp.ClientError as e:
            log.error(f"[GH] connection error: {e}")
            yield f"Connection error: {e}"
        except Exception as e:
            log.error(f"[GH] error: {e}")
            yield f"Error: {e}"
