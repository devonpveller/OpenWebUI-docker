"""
title: GitHelper
author: ai-stack
version: 1.0.0
description: GitHub repo investigation agent with built-in context window management. Proxies to llama-cpp with sliding window trimming on EVERY call — including tool-call retries. Assign your git-repo-analyzer tool to this model in the OpenWebUI model editor. System prompt is built in.
required_open_webui_version: 0.4.0
"""

from pydantic import BaseModel, Field
from typing import Optional, Union, Callable, Awaitable, AsyncGenerator
import json
import logging
import aiohttp

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
            description="Path to system prompt file inside container. Falls back to built-in prompt if not found.",
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
            description="Chars per token. Measured: Qwen+tools ~1.83.",
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
        MAX_TOOL_SCHEMA_CHARS: int = Field(
            default=16000,
            description="Max total chars for tool schemas sent to backend. Schemas are compacted (descriptions truncated, examples stripped) to fit. 0 = no limit.",
        )
        TOOL_DESC_CAP: int = Field(
            default=80,
            description="Max chars per parameter/function description when compacting tool schemas.",
        )
        ENABLE_STATUS: bool = Field(
            default=True,
            description="Show status message when context is trimmed.",
        )
        REQUEST_TIMEOUT: int = Field(
            default=300,
            description="HTTP request timeout in seconds.",
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

    def _ensure_system_prompt(self, messages: list) -> list:
        """Inject system prompt if not already present."""
        if messages and messages[0].get("role") == "system":
            return messages
        return [{"role": "system", "content": self._get_system_prompt()}] + messages

    # ── Tool schema compaction ──

    def _compact_tools(self, tools: list) -> list:
        """Compact tool schemas to fit within MAX_TOOL_SCHEMA_CHARS.

        Design principle: the model needs function descriptions to reason about
        WHICH tool to call next.  Parameter descriptions and schema noise are
        expendable — the model can infer param usage from names and types.

        Compaction phases (each only runs if still over cap):
          1. Truncate function descriptions to TOOL_DESC_CAP, strip examples/defaults/long enums
          2. Strip parameter-level descriptions
          3. Strip required arrays, flatten nested objects to bare types
          4. Drop fattest tools first (survivors keep their function descriptions)
        Function-level descriptions are NEVER removed."""
        if not tools:
            return tools

        cap = self.valves.MAX_TOOL_SCHEMA_CHARS
        if not cap:
            return tools

        original_size = len(json.dumps(tools))
        if original_size <= cap:
            return tools

        desc_cap = self.valves.TOOL_DESC_CAP

        def compact_desc(d: str) -> str:
            if not d or not isinstance(d, str):
                return d or ""
            if len(d) <= desc_cap:
                return d
            return d[:desc_cap].rstrip() + "…"

        def compact_schema(schema: dict) -> dict:
            """Recursively compact a JSON Schema object."""
            if not isinstance(schema, dict):
                return schema
            out = {}
            for k, v in schema.items():
                if k == "description":
                    out[k] = compact_desc(v)
                elif k in ("examples", "example", "default"):
                    continue
                elif k == "enum" and isinstance(v, list) and len(v) > 5:
                    out[k] = v[:5]
                elif k == "properties" and isinstance(v, dict):
                    out[k] = {pk: compact_schema(pv) for pk, pv in v.items()}
                elif k == "items" and isinstance(v, dict):
                    out[k] = compact_schema(v)
                else:
                    out[k] = v
            return out

        # Phase 1: Truncate descriptions, strip examples/defaults
        compacted = []
        for tool in tools:
            if not isinstance(tool, dict):
                compacted.append(tool)
                continue
            fn = tool.get("function", {})
            compacted.append({
                "type": tool.get("type", "function"),
                "function": {
                    "name": fn.get("name", ""),
                    "description": compact_desc(fn.get("description", "")),
                    **({"parameters": compact_schema(fn["parameters"])} if "parameters" in fn else {}),
                },
            })

        result_size = len(json.dumps(compacted))
        log.info(f"[GH] phase1 compact: {original_size} → {result_size} chars ({len(compacted)} tools)")

        # Phase 2: Strip parameter descriptions (keep function descriptions!)
        if result_size > cap:
            for t in compacted:
                params = t.get("function", {}).get("parameters", {})
                for prop in params.get("properties", {}).values():
                    if isinstance(prop, dict):
                        prop.pop("description", None)
            result_size = len(json.dumps(compacted))
            log.info(f"[GH] phase2 strip param desc → {result_size} chars")

        # Phase 3: Strip required arrays, flatten nested objects to bare types
        if result_size > cap:
            for t in compacted:
                params = t.get("function", {}).get("parameters", {})
                params.pop("required", None)
                for pname, prop in list(params.get("properties", {}).items()):
                    if isinstance(prop, dict):
                        ptype = prop.get("type", "string")
                        if ptype == "object" or "properties" in prop:
                            params["properties"][pname] = {"type": "object"}
                        elif ptype == "array":
                            params["properties"][pname] = {"type": "array"}
                        else:
                            params["properties"][pname] = {"type": ptype}
            result_size = len(json.dumps(compacted))
            log.info(f"[GH] phase3 flatten → {result_size} chars ({len(compacted)} tools)")

        # Phase 4: Drop fattest tools first (survivors keep descriptions)
        if result_size > cap:
            sized = [(len(json.dumps(t)), i, t) for i, t in enumerate(compacted)]
            sized.sort(key=lambda x: x[0], reverse=True)
            while len(sized) > 1 and result_size > cap:
                removed = sized.pop(0)
                result_size -= removed[0] + 2
            sized.sort(key=lambda x: x[1])
            compacted = [t for _, _, t in sized]
            result_size = len(json.dumps(compacted))
            log.info(f"[GH] phase4 dropped to {len(compacted)} tools → {result_size} chars")

        return compacted

    # ── Context management ──

    def _char_budget(self, body: dict) -> int:
        usable = self.valves.MAX_CONTEXT_TOKENS - self.valves.HEADROOM_TOKENS

        tools = body.get("tools", [])
        if tools:
            tools_chars = len(json.dumps(tools))
            tools_tokens = int(tools_chars / self.valves.CHARS_PER_TOKEN)
            usable -= tools_tokens
            log.debug(f"[GH] tool defs: {tools_chars} chars (~{tools_tokens} tok)")

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

        # Phase 2: Drop oldest (but never the last user message)
        dropped = 0
        while len(convo) > protect_n and current > budget:
            # Protect last user message
            if convo[0].get("role") == "user":
                remaining_users = sum(1 for m in convo if m.get("role") == "user")
                if remaining_users <= 1:
                    break
            msg = convo.pop(0)
            drop = len(msg.get("content", "") or "")
            if msg.get("tool_calls"):
                drop += len(json.dumps(msg["tool_calls"]))
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
                if current <= budget: break
                cap = self.valves.EMERGENCY_CAP
                if msg.get("role") == "tool":
                    cap = min(cap, self.valves.TOOL_RESULT_CAP)
                convo[i], s = self._compress_msg(msg, cap, f"{msg.get('role','')} (emergency)")
                if s > 0:
                    current -= s
                    emergency += 1

        # Phase 4: Nuclear — but NEVER drop the last user message
        nuked = 0
        while len(convo) > 2 and current > budget:
            # Don't drop if it's the last user message
            if convo[0].get("role") == "user":
                remaining_users = sum(1 for m in convo if m.get("role") == "user")
                if remaining_users <= 1:
                    break  # protect last user message
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
        # 1. Ensure system prompt is present (skip for tasks like title gen)
        if not __task__:
            body["messages"] = self._ensure_system_prompt(body.get("messages", []))

        # Debug: log message structure for every call
        roles = [m.get("role", "?") for m in body.get("messages", [])]
        has_tools = bool(body.get("tools"))
        has_user = any(r == "user" for r in roles)
        log.info(f"[GH] pipe() called: task={__task__}, roles={roles}, has_tools={has_tools}, has_user={has_user}")
        if not has_user:
            log.warning(f"[GH] NO USER MESSAGE — will strip tools to avoid template error")

        # 2. Compact tool schemas BEFORE budget calculation
        if body.get("tools"):
            body["tools"] = self._compact_tools(body["tools"])

        # 3. Trim context (runs on EVERY call including tool retries)
        original_count = self._measure(body.get("messages", []))
        body = self._trim_messages(body)
        trimmed_count = self._measure(body.get("messages", []))

        if trimmed_count < original_count and self.valves.ENABLE_STATUS and __event_emitter__:
            saved = original_count - trimmed_count
            est = int(trimmed_count / self.valves.CHARS_PER_TOKEN)
            await __event_emitter__({
                "type": "status",
                "data": {
                    "description": f"Context trimmed: ~{saved // 1000}k chars freed, ~{est}/{self.valves.MAX_CONTEXT_TOKENS} tok",
                    "done": True,
                },
            })

        # 4. Build backend payload
        target_model = self.valves.TARGET_MODEL_ID or body.get("model", "")
        # Strip pipe prefix (e.g. "githelper.githelper" -> use configured model)
        if "." in target_model and not self.valves.TARGET_MODEL_ID:
            target_model = ""

        payload = {
            "model": target_model,
            "messages": body["messages"],
            "stream": body.get("stream", True),
        }

        # Forward optional fields
        for key in ("temperature", "top_p", "top_k", "max_tokens", "stop",
                     "frequency_penalty", "presence_penalty", "seed",
                     "tools", "tool_choice", "response_format"):
            if key in body and body[key] is not None:
                payload[key] = body[key]

        # Safety: Qwen's Jinja template raises "No user query found" if tools
        # are present but messages have no user role (e.g. task calls like title
        # generation). Strip tools to fall back to the non-tool template path.
        has_user = any(m.get("role") == "user" for m in payload["messages"])
        if not has_user and "tools" in payload:
            log.info("[GH] stripping tools from payload (no user message)")
            payload.pop("tools", None)
            payload.pop("tool_choice", None)

        url = f"{self.valves.TARGET_BASE_URL.rstrip('/')}/chat/completions"

        # 5. Forward to backend
        if body.get("stream", True):
            return self._stream_response(url, payload)
        else:
            return await self._sync_response(url, payload)

    async def _stream_response(self, url: str, payload: dict) -> AsyncGenerator:
        """Yield raw SSE lines (prefixed with 'data: ') so OpenWebUI's
        process_line passes them through unchanged — preserving tool_calls
        in the delta instead of flattening them into text content."""
        timeout = aiohttp.ClientTimeout(total=self.valves.REQUEST_TIMEOUT)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, json=payload, headers={"Content-Type": "application/json"}) as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        log.error(f"[GH] backend error {resp.status}: {error_text[:500]}")
                        yield f"Error from backend: {resp.status} — {error_text[:200]}"
                        return

                    async for line in resp.content:
                        decoded = line.decode("utf-8", errors="replace").strip()
                        if not decoded:
                            continue
                        if decoded.startswith("data:"):
                            # Pass SSE lines through verbatim so OpenWebUI's
                            # stream_body_handler can parse tool_calls from
                            # choices[0].delta.tool_calls properly.
                            yield decoded
        except aiohttp.ClientError as e:
            log.error(f"[GH] connection error: {e}")
            yield f"Connection error: {e}"
        except Exception as e:
            log.error(f"[GH] unexpected error: {e}")
            yield f"Error: {e}"

    async def _sync_response(self, url: str, payload: dict) -> str:
        timeout = aiohttp.ClientTimeout(total=self.valves.REQUEST_TIMEOUT)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, json=payload, headers={"Content-Type": "application/json"}) as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        return f"Error from backend: {resp.status} — {error_text[:200]}"
                    result = await resp.json()
                    choices = result.get("choices", [])
                    if choices:
                        return choices[0].get("message", {}).get("content", "")
                    return "No response from backend"
        except Exception as e:
            log.error(f"[GH] error: {e}")
            return f"Error: {e}"
