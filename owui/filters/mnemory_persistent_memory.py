"""
title: Mnemory - Persistent Memory
author: mnemory
description: Automatic memory recall and storage for conversations
version: 0.3.1
"""

import asyncio
import logging
import re
from typing import Callable, Optional

import aiohttp
from pydantic import BaseModel, Field

_log = logging.getLogger(__name__)


class Filter:
    class Valves(BaseModel):
        priority: int = Field(
            default=0,
            description="Filter priority (lower = runs first)",
        )
        mnemory_url: str = Field(
            default="http://mnemory:8050",
            description="Mnemory server base URL",
        )
        api_key: str = Field(
            default="",
            description="API key for mnemory authentication",
        )
        agent_id: str = Field(
            default="open-webui",
            description="Agent ID sent to mnemory",
        )
        cloud_zone_enabled: bool = Field(
            default=True,
            description=(
                "Privacy-first model gating. When the selected model is "
                "NOT confirmed local (see local_model_pattern), treat it "
                "as a cloud service: never inject pinned/core or personal "
                "memories; only recall memories labelled share=cloud, and "
                "fail closed (inject nothing) if that restricted search "
                "errors. Memories written from a cloud model are stamped "
                "origin=cloud,share=cloud."
            ),
        )
        local_model_pattern: str = Field(
            default=r"(?i)\b(qwen|gemma|llama|phi|deepseek-r1|mistral-?7b|"
            r"mixtral|nomic|bge|granite|smollm|local)\b",
            description=(
                "Regex matching model IDs trusted as LOCAL (full memory "
                "access). Anything that does NOT match is treated as a "
                "cloud service (privacy-first default-deny on unknowns)."
            ),
        )
        recall_mode: str = Field(
            default="always",
            description=(
                "When to recall memories: "
                "'always' = every message (recommended), "
                "'first_only' = first message only (no subsequent recalls)"
            ),
        )
        recall_search_mode: str = Field(
            default="search",
            description=(
                "Search mode for recall: "
                "'find' = AI-powered multi-query search (thorough, slower), "
                "'search' = single vector search (fast, no LLM)"
            ),
        )
        recall_find_first: bool = Field(
            default=False,
            description=(
                "When recall_search_mode is 'search', use 'find' for the "
                "first message in a session (thorough initial context). "
                "Default OFF (hybrid recall): the broad first-turn 'find' "
                "is the main source of irrelevant start-of-chat memories. "
                "Ignored when recall_search_mode is 'find'."
            ),
        )
        recall_score_threshold: float = Field(
            default=0.7,
            description=(
                "Minimum relevance score (0.0-1.0) for recalled memories. "
                "Higher = fewer but more relevant memories injected. "
                "Raised from 0.5 -> 0.7 for hybrid recall: inject only "
                "high-confidence hits; the model uses search tools for the "
                "rest."
            ),
        )
        inject_epistemic_block: bool = Field(
            default=True,
            description=(
                "Inject the cached epistemic-taxonomy instruction (guess / "
                "educated guess / fact / stale-research, EV:research header "
                "rules, active tool use). Teaches the model to actively "
                "search/remember and to grade claim confidence."
            ),
        )
        inject_core_memories: bool = Field(
            default=False,
            description=(
                "Cache mnemory's pinned core memories into start-of-chat "
                "context. Default OFF (hybrid): wholesale core dump was the "
                "other source of start-of-chat noise; the model can "
                "search_memory for identity/prefs when relevant."
            ),
        )
        skip_recall_with_tools: bool = Field(
            default=False,
            description=(
                "Skip dynamic memory recall on follow-up turns when mnemory "
                "search tools (search_memory, find_memory) are available. "
                "The model searches explicitly via tools instead. Reduces "
                "duplicate context for smaller models. Static context "
                "(instructions + core memories) is always injected."
            ),
        )
        show_status: bool = Field(
            default=True,
            description="Show memory status messages in chat (can be overridden per-user)",
        )
        debug: bool = Field(
            default=False,
            description=(
                "Emit detailed debug info as chat status messages. "
                "Shows session resolution, query, API response stats, "
                "tool stripping, and injection details."
            ),
        )
        request_timeout: int = Field(
            default=30,
            description="HTTP request timeout in seconds for mnemory API calls",
        )
        strip_redundant_mcp_tools: bool = Field(
            default=True,
            description=(
                "Remove mnemory MCP tools that the filter handles automatically "
                "(initialize_memory, get_core_memories, get_recent_memories) "
                "from the request to reduce prompt token usage."
            ),
        )
        strip_search_tools: bool = Field(
            default=False,
            description=(
                "Remove search_memory and find_memory tools from the request "
                "when the filter already recalled memories for this turn. "
                "Enable this if your model gets stuck in tool-call loops or "
                "stops responding after calling search tools. The filter's "
                "automatic recall already handles memory search — explicit "
                "search tools are redundant. The model can still use "
                "remember, update_memory, and delete_memory."
            ),
        )

    class UserValves(BaseModel):
        enabled: bool = Field(
            default=True,
            description="Enable memory for this user",
        )
        show_status: bool = Field(
            default=True,
            description="Show memory status messages in chat",
        )

    # Max tracked sessions before evicting oldest entries.
    # Prevents unbounded memory growth in long-running instances.
    _MAX_SESSIONS = 1000
    _MAX_PENDING_SESSIONS = 100

    # Mnemory MCP tools that the filter handles automatically.
    # Stripped from tool_ids and tools[] to save prompt tokens.
    _MANAGED_TOOL_SUFFIXES = {
        "initialize_memory",
        "get_core_memories",
        "get_recent_memories",
    }

    # Mnemory search tools — when present, the filter can skip its own
    # dynamic recall so the model searches explicitly via tool calls.
    _SEARCH_TOOL_SUFFIXES = {
        "search_memory",
        "find_memory",
    }

    # Cached epistemic-taxonomy instruction. Injected at a stable early
    # position every turn so it is part of the cacheable prompt prefix.
    # Teaches confidence grading + active memory-tool use.
    _EPISTEMIC_BLOCK = (
        "## Memory & evidence protocol\n"
        "You have mnemory tools (search_memory / find_memory / remember). "
        "Use them proactively — do not wait to be asked:\n"
        "- Before any substantive answer, decision, or code, call "
        "search_memory (or find_memory for broad questions) with a focused "
        "query. Better to search and find nothing than to miss context.\n"
        "- When the user states a durable preference, decision, fact, or "
        "project detail, call remember. Don't ask permission for routine "
        "saves.\n\n"
        "Grade every substantive claim by provenance and say which it is:\n"
        "- **(guess)** — no supporting context or memory. State the "
        "uncertainty plainly.\n"
        "- **(educated guess)** — inferred from conversation context or a "
        "non-research memory. Reasonable, not verified.\n"
        "- **fact** — backed by a recalled memory whose content starts "
        "with a `⟦EV:research …⟧` header AND is still fresh "
        "(see below). State it directly and cite the header's sources / "
        "the attached research artifact.\n\n"
        "Reading the `⟦EV:research | date:YYYY-MM-DD | vol:<tier> | "
        "revalidate:<N>d | src:<n> | …⟧` header on a recalled "
        "memory: compare `date` to today's date (provided in context). If "
        "the memory is older than `revalidate` days it is STALE — do not "
        "present it as fact; downgrade to "
        "**(educated guess — re-validation due, last researched <date>)** "
        "and, if it matters, suggest re-running research. A memory with no "
        "EV header is an ordinary observation: treat as (educated guess) "
        "at best, never as researched fact.\n"
        "Never upgrade a guess to a fact by restating it confidently. "
        "Provenance, not tone, determines the label."
    )

    def __init__(self):
        self.valves = self.Valves()
        # Track which chats have been initialized.
        # Maps chat_id -> {"session_id": str, "user_id": str, "static_ctx": str | None}
        # static_ctx holds cached instructions + core memories from the
        # first turn, re-injected on every subsequent turn so the LLM
        # always has memory context and managed-mode guidance.
        self._sessions: dict[str, dict] = {}
        # Pending sessions from first messages when chat_id is not yet
        # available.  Open WebUI may not provide chat_id on the first
        # message of a new chat (chat not yet saved to DB).  Keyed by
        # user_id (email) to prevent cross-user session leakage when
        # multiple users start chats concurrently.
        self._pending_sessions: dict[str, dict] = {}

    # ── Helpers ───────────────────────────────────────────────────────

    async def _debug(self, emitter: Callable | None, msg: str) -> None:
        """Emit a debug status message into the chat if debug mode is on."""
        if not self.valves.debug or not emitter:
            return
        await emitter(
            {
                "type": "status",
                "data": {"description": f"[mnemory debug] {msg}", "done": True},
            }
        )

    async def _post(
        self,
        path: str,
        payload: dict,
        user: dict,
        emitter: Callable | None = None,
    ) -> dict | None:
        """Make a POST request to mnemory REST API."""
        headers = {
            "Content-Type": "application/json",
            "X-Agent-Id": self.valves.agent_id,
            "X-User-Id": user.get("email", user.get("id", "")),
        }
        if self.valves.api_key:
            headers["Authorization"] = f"Bearer {self.valves.api_key}"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.valves.mnemory_url}{path}",
                    headers=headers,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=self.valves.request_timeout),
                ) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    await self._debug(
                        emitter,
                        f"API {path} returned {resp.status}",
                    )
                    return None
        except Exception as exc:
            await self._debug(emitter, f"API {path} error: {exc}")
            return None  # Graceful degradation

    def _has_search_tools(self, body: dict) -> bool:
        """Check if mnemory search tools are in the request."""
        for t in body.get("tool_ids", []):
            if isinstance(t, str) and any(
                t.endswith(s) for s in self._SEARCH_TOOL_SUFFIXES
            ):
                return True
        for t in body.get("tools", []):
            if isinstance(t, dict):
                name = self._tool_name(t)
                if name and any(name.endswith(s) for s in self._SEARCH_TOOL_SUFFIXES):
                    return True
        return False

    def _strip_managed_tools(
        self, body: dict, emitter: Callable | None = None
    ) -> list[str]:
        """Remove mnemory MCP tools the filter handles automatically.

        Strips from both ``tool_ids`` (list of string identifiers) and
        ``tools`` (list of tool-definition dicts) so the LLM never sees
        the managed tools regardless of how Open WebUI passes them.

        Returns list of stripped tool names for debug logging.
        """
        stripped: list[str] = []

        # Strip from tool_ids (list[str])
        tool_ids = body.get("tool_ids")
        if tool_ids:
            kept = []
            for t in tool_ids:
                if isinstance(t, str) and any(
                    t.endswith(s) for s in self._MANAGED_TOOL_SUFFIXES
                ):
                    stripped.append(t)
                else:
                    kept.append(t)
            body["tool_ids"] = kept

        # Strip from tools (list[dict]) — Open WebUI may pass full tool
        # definitions here instead of (or in addition to) tool_ids.
        tools = body.get("tools")
        if tools and isinstance(tools, list):
            kept_tools = []
            for t in tools:
                if isinstance(t, dict) and self._is_managed_tool(t):
                    name = self._tool_name(t)
                    stripped.append(name or "unknown_tool")
                else:
                    kept_tools.append(t)
            body["tools"] = kept_tools

        return stripped

    def _is_managed_tool(self, tool: dict) -> bool:
        """Check if a tool definition dict matches a managed tool suffix."""
        name = self._tool_name(tool)
        if name and any(name.endswith(s) for s in self._MANAGED_TOOL_SUFFIXES):
            return True
        return False

    def _strip_tools_by_suffixes(self, body: dict, suffixes: set[str]) -> list[str]:
        """Remove tools matching any of the given name suffixes.

        Works on both ``tool_ids`` (list[str]) and ``tools`` (list[dict]).
        Returns list of stripped tool names for debug logging.
        """
        stripped: list[str] = []

        tool_ids = body.get("tool_ids")
        if tool_ids:
            kept = []
            for t in tool_ids:
                if isinstance(t, str) and any(t.endswith(s) for s in suffixes):
                    stripped.append(t)
                else:
                    kept.append(t)
            body["tool_ids"] = kept

        tools = body.get("tools")
        if tools and isinstance(tools, list):
            kept_tools = []
            for t in tools:
                if isinstance(t, dict):
                    name = self._tool_name(t)
                    if name and any(name.endswith(s) for s in suffixes):
                        stripped.append(name)
                    else:
                        kept_tools.append(t)
                else:
                    kept_tools.append(t)
            body["tools"] = kept_tools

        return stripped

    @staticmethod
    def _tool_name(tool: dict) -> str:
        """Extract the tool name from a tool definition dict."""
        for key in ("id", "name", "tool_id"):
            val = tool.get(key, "")
            if val and isinstance(val, str):
                return val
        func = tool.get("function")
        if isinstance(func, dict):
            name = func.get("name", "")
            if name and isinstance(name, str):
                return name
        return ""

    def _get_session(self, chat_id: str, user_id: str = "") -> dict | None:
        """Look up or adopt a session for the given chat_id.

        Handles the first-to-second-message transition where chat_id
        appears after the pending session was already created.

        Args:
            chat_id: Open WebUI chat ID (may be empty on first message).
            user_id: User identifier (email) for pending session lookup.
                Required for correct multi-user isolation.

        When adopting, the pending session is NOT cleared — the inlet
        may still need it on subsequent turns when chat_id is
        unavailable (Open WebUI provides chat_id in the outlet but not
        always in the inlet).  Pending sessions are only cleared when a
        new first turn starts (is_first=True in inlet).

        Note: if the same user opens two browser tabs and both send
        their first message before either receives a chat_id, the
        second pending session overwrites the first.  This is a known
        limitation scoped to a single user (not cross-user).
        """
        if not user_id:
            return None
        if chat_id:
            sess = self._sessions.get(chat_id)
            if sess is None:
                pending = self._pending_sessions.get(user_id)
                if pending:
                    sess = pending
                    self._sessions[chat_id] = sess
                    # Don't clear pending — inlet may still need it
                    # when chat_id is not available.
            # Defense-in-depth: verify session belongs to this user.
            # Prevents cross-user access if chat_ids ever collide.
            # Note: uses != (not truthiness check) so sessions stored
            # with user_id="" are never returned to an authenticated user.
            if sess and sess.get("user_id") != user_id:
                _log.warning(
                    "Session user_id mismatch: stored=%r requesting=%r "
                    "chat_id=%r — refusing access",
                    sess["user_id"],
                    user_id,
                    chat_id,
                )
                return None
            return sess
        return self._pending_sessions.get(user_id)

    def _save_session(
        self,
        chat_id: str,
        session_id: str,
        static_ctx: str | None = None,
        *,
        update_ctx: bool = False,
        user_id: str = "",
    ) -> None:
        """Store or update session data for a chat.

        Args:
            chat_id: Open WebUI chat ID (may be empty on first message).
            session_id: Server-side session ID from recall response.
            static_ctx: Cached instructions + core memories text.
            update_ctx: If True, overwrite static_ctx even when the new
                value is None (used on first turn to set the cache).
                If False, preserve the existing static_ctx.
            user_id: User identifier (email) for pending session scoping.
                Required for correct multi-user isolation.
        """
        if chat_id:
            existing = self._sessions.get(chat_id)
            sess = {
                "session_id": session_id,
                "user_id": user_id,
                "static_ctx": (
                    static_ctx
                    if update_ctx
                    else (static_ctx or (existing["static_ctx"] if existing else None))
                ),
            }
            self._sessions[chat_id] = sess
            # Clear this user's pending session now that we have a chat_id
            if user_id:
                self._pending_sessions.pop(user_id, None)
            # Evict oldest entries if over limit
            if len(self._sessions) > self._MAX_SESSIONS:
                excess = len(self._sessions) - self._MAX_SESSIONS
                for key in list(self._sessions)[:excess]:
                    del self._sessions[key]
                _log.warning(
                    "mnemory: evicted %d oldest sessions (limit=%d)",
                    excess,
                    self._MAX_SESSIONS,
                )
        elif user_id:
            existing = self._pending_sessions.get(user_id)
            self._pending_sessions[user_id] = {
                "session_id": session_id,
                "user_id": user_id,
                "static_ctx": (
                    static_ctx
                    if update_ctx
                    else (static_ctx or (existing["static_ctx"] if existing else None))
                ),
            }
            # Evict oldest pending entries if over limit
            if len(self._pending_sessions) > self._MAX_PENDING_SESSIONS:
                excess = len(self._pending_sessions) - self._MAX_PENDING_SESSIONS
                for key in list(self._pending_sessions)[:excess]:
                    del self._pending_sessions[key]
                _log.warning(
                    "mnemory: evicted %d oldest pending sessions (limit=%d)",
                    excess,
                    self._MAX_PENDING_SESSIONS,
                )
        else:
            _log.warning(
                "mnemory: _save_session called with empty chat_id and "
                "user_id — session data dropped. Check __user__ "
                "population in Open WebUI."
            )

    def _is_local_model(self, body: dict) -> bool:
        """True only if the selected model id matches local_model_pattern.
        Privacy-first: anything we cannot positively confirm as local is
        treated as a cloud service."""
        model = body.get("model")
        if not isinstance(model, str) or not model:
            return False  # unknown => cloud-restricted
        try:
            return re.search(self.valves.local_model_pattern, model) is not None
        except re.error:
            return False  # bad pattern => fail safe (cloud-restricted)

    # ── Inlet (before LLM) ───────────────────────────────────────────

    async def inlet(
        self,
        body: dict,
        __user__: Optional[dict] = None,
        __event_emitter__: Optional[Callable] = None,
    ) -> dict:
        """Before LLM: recall memories and inject into context."""
        if not __user__:
            return body

        # Check user valves
        user_valves = __user__.get("valves")
        if user_valves and hasattr(user_valves, "enabled") and not user_valves.enabled:
            return body

        # Inject the epistemic-taxonomy instruction on EVERY path (incl.
        # the early-return ones below) so confidence grading + active tool
        # use are always in force, independent of recall results.
        self._inject_epistemic_block(body)

        # Strip redundant mnemory MCP tools to save prompt tokens.
        # The filter handles recall automatically — these tools would
        # only waste tokens in the tools[] array on every LLM request.
        if self.valves.strip_redundant_mcp_tools:
            stripped = self._strip_managed_tools(body, __event_emitter__)
            if stripped:
                await self._debug(
                    __event_emitter__,
                    f"Stripped tools: {', '.join(stripped)}",
                )
            else:
                await self._debug(
                    __event_emitter__,
                    "No managed tools found to strip"
                    f" (tool_ids={body.get('tool_ids', 'absent')!r})",
                )

        # Strip search/find tools so the model cannot make redundant
        # search calls after the filter already recalled memories.
        if self.valves.strip_search_tools:
            stripped_search = self._strip_tools_by_suffixes(
                body, self._SEARCH_TOOL_SUFFIXES
            )
            if stripped_search:
                await self._debug(
                    __event_emitter__,
                    f"Stripped search tools: {', '.join(stripped_search)}",
                )

        chat_id = body.get("chat_id") or ""
        user_id = __user__.get("email", __user__.get("id", ""))
        sess = self._get_session(chat_id, user_id)
        session_id = sess["session_id"] if sess else None

        # Determine first turn from conversation history, not session
        # tracking.  Session-based detection is unreliable because
        # chat_id may be absent on the first message, causing the
        # empty-key entry in _sessions to collide across different chats.
        messages = body.get("messages", [])
        user_msg_count = sum(1 for m in messages if m.get("role") == "user")
        is_first = user_msg_count <= 1

        await self._debug(
            __event_emitter__,
            f"chat_id={chat_id!r} session_id={session_id!r} "
            f"user_id={user_id!r} is_first={is_first} user_msgs={user_msg_count}",
        )

        # On the first turn, always send session_id=None so the recall
        # endpoint treats it as a fresh session and loads core memories.
        # A stale pending session from a previous chat could otherwise
        # cause the server to skip core memory loading.
        if is_first:
            session_id = None
            # Clear stale pending session for THIS user only
            if user_id:
                self._pending_sessions.pop(user_id, None)

        # Resolve effective status visibility (admin AND user valve).
        # Must be computed BEFORE the early-return branches below that
        # reference show_status (e.g. skip_recall_with_tools).
        show_status = self.valves.show_status
        if show_status and user_valves and hasattr(user_valves, "show_status"):
            show_status = user_valves.show_status

        # In first_only mode, skip recall on subsequent messages.
        # Still inject cached static context so the LLM keeps its
        # memory instructions and core memories.
        if not is_first and self.valves.recall_mode == "first_only":
            self._inject_static_context(body, sess)
            return body

        # When search tools handle dynamic recall, skip the filter's
        # own search on follow-up turns.  The first turn always runs
        # recall to populate the static context cache.
        if (
            not is_first
            and self.valves.skip_recall_with_tools
            and self._has_search_tools(body)
        ):
            await self._debug(
                __event_emitter__,
                "Skipping dynamic recall (search tools present)",
            )
            self._inject_static_context(body, sess)
            if __event_emitter__ and show_status:
                await __event_emitter__(
                    {
                        "type": "status",
                        "data": {
                            "description": "Memory ready (tools active)",
                            "done": True,
                        },
                    }
                )
            return body

        # Extract query from last user message
        query = ""
        for msg in reversed(body.get("messages", [])):
            if msg.get("role") == "user":
                content = msg.get("content", "")
                query = content if isinstance(content, str) else ""
                break

        await self._debug(
            __event_emitter__,
            f"Query ({len(query)} chars): {query[:120]}{'...' if len(query) > 120 else ''}",
        )

        if not query and not is_first:
            self._inject_static_context(body, sess)
            return body  # No query on subsequent turn — skip search

        # show_status was resolved earlier (before the early-return
        # branches). Announce the recall pass.
        if __event_emitter__ and show_status:
            await __event_emitter__(
                {
                    "type": "status",
                    "data": {
                        "description": "Recalling memories...",
                        "done": False,
                    },
                }
            )

        # Determine search mode for this call
        if is_first and self.valves.recall_find_first:
            search_mode = "find"
        else:
            search_mode = self.valves.recall_search_mode

        # --- Cloud-zone gating (privacy-first) ---
        # If the model is not confirmed local, never call /api/recall
        # (it loads pinned/core incl. personal). Instead do a restricted
        # search limited to share=cloud, and fail CLOSED on any error.
        if self.valves.cloud_zone_enabled and not self._is_local_model(body):
            await self._debug(
                __event_emitter__,
                f"CLOUD ZONE: model={body.get('model')!r} -> restricted "
                f"recall (labels.share=cloud, no core/personal)",
            )
            result = None
            try:
                cloud = await self._post(
                    "/api/memories/search",
                    {"query": query, "limit": 8, "labels": {"share": "cloud"}},
                    __user__,
                    __event_emitter__,
                )
                if cloud is not None:
                    hits = (
                        cloud.get("results", cloud)
                        if isinstance(cloud, dict)
                        else cloud
                    )
                    result = {
                        "search_results": [
                            {"memory": h.get("memory") or h.get("content")}
                            for h in (hits or [])
                            if h.get("memory") or h.get("content")
                        ]
                    }
            except Exception as exc:  # fail closed
                await self._debug(
                    __event_emitter__,
                    f"CLOUD ZONE restricted search failed, injecting "
                    f"nothing: {exc!r}",
                )
                result = None
        else:
            # Call recall endpoint (trusted/local model: full access)
            payload: dict = {
                "session_id": session_id,
                "query": query,
                "search_mode": search_mode,
                "score_threshold": self.valves.recall_score_threshold,
            }
            if is_first:
                payload["include_instructions"] = True
                payload["managed"] = True

            await self._debug(
                __event_emitter__,
                f"Calling /api/recall: mode={search_mode} "
                f"session_id={session_id!r} is_first={is_first}",
            )

            result = await self._post(
                "/api/recall", payload, __user__, __event_emitter__
            )

        if result:
            stats = result.get("stats", {})
            await self._debug(
                __event_emitter__,
                f"Recall response: user_id={user_id!r} "
                f"session={result.get('session_id', '?')!r} "
                f"core={stats.get('core_count', 0)} "
                f"search={stats.get('search_count', 0)} "
                f"new={stats.get('new_count', 0)} "
                f"skipped={stats.get('known_skipped', 0)} "
                f"has_instructions={bool(result.get('instructions'))} "
                f"has_core={bool(result.get('core_memories'))} "
                f"latency={stats.get('latency_ms', 0)}ms",
            )
        else:
            await self._debug(__event_emitter__, "Recall returned None (API error)")

        # Update session tracking
        if result and result.get("session_id"):
            # On first turn, cache instructions + core memories as
            # static context.  This text is re-injected at a fixed
            # early position on every subsequent turn so it becomes
            # part of the stable prompt prefix (good for caching).
            static_ctx = None
            if is_first:
                static_parts = []
                # Hybrid recall: by default DON'T cache mnemory's own
                # instructions or wholesale pinned core memories — that
                # was the start-of-chat noise. The epistemic block (always
                # injected separately) replaces the instruction role; the
                # model can search_memory for identity/prefs on demand.
                if self.valves.inject_core_memories:
                    if result.get("instructions"):
                        static_parts.append(result["instructions"])
                    if result.get("core_memories"):
                        static_parts.append(result["core_memories"])
                static_ctx = "\n\n".join(static_parts) if static_parts else None
                await self._debug(
                    __event_emitter__,
                    f"Cached static_ctx: {len(static_ctx) if static_ctx else 0} chars "
                    f"(instructions={bool(result.get('instructions'))}, "
                    f"core_memories={bool(result.get('core_memories'))})",
                )

            self._save_session(
                chat_id,
                result["session_id"],
                static_ctx,
                update_ctx=is_first,
                user_id=user_id,
            )
            # Re-read session after save so we have the latest data
            sess = self._get_session(chat_id, user_id)

        # --- Inject context into messages ---
        #
        # Two-position injection for optimal prompt caching:
        #
        # 1. STATIC CONTEXT (instructions + core memories):
        #    Inserted at a fixed early position — after the initial
        #    system message(s), before the first user message.  This
        #    becomes part of the stable, cacheable prompt prefix:
        #      [sys_prompt] [STATIC_CTX] [user_1] [asst_1] [user_2] ...
        #    Cached from the first turn onward; never re-processed.
        #
        # 2. DYNAMIC CONTEXT (new search results):
        #    Appended after the last user message.  Changes every turn
        #    so it sits outside the cached prefix.
        #
        # This is strictly better for caching than the previous approach
        # of appending everything after the last user message, because
        # the static context (often 1-2k tokens) is cached instead of
        # being re-processed on every turn.

        # 1. Static context — always inject from cache
        has_static = sess and bool(sess.get("static_ctx"))
        self._inject_static_context(body, sess)
        await self._debug(
            __event_emitter__,
            f"Static context injected: {has_static}",
        )

        # 2. Dynamic context — new search results from this turn
        if result and result.get("search_results"):
            memories_text = "\n".join(
                f"- {m['memory']}" for m in result["search_results"] if m.get("memory")
            )
            if memories_text:
                body["messages"].append(
                    {
                        "role": "system",
                        "content": f"## Recalled Memories\n{memories_text}",
                    },
                )

        # Show detailed status with stats
        if __event_emitter__ and show_status:
            desc = self._build_status(result, is_first)
            await __event_emitter__(
                {
                    "type": "status",
                    "data": {"description": desc, "done": True},
                }
            )

        return body

    def _inject_epistemic_block(self, body: dict) -> None:
        """Insert the epistemic-taxonomy instruction once, at a stable
        early position (after leading system messages). Idempotent."""
        if not self.valves.inject_epistemic_block:
            return
        messages = body.get("messages")
        if not isinstance(messages, list):
            return
        marker = "## Memory & evidence protocol"
        for m in messages:
            c = m.get("content")
            if m.get("role") == "system" and isinstance(c, str) and marker in c:
                return  # already present this turn
        insert_idx = 0
        for i, msg in enumerate(messages):
            if msg.get("role") == "system":
                insert_idx = i + 1
            else:
                break
        messages.insert(
            insert_idx, {"role": "system", "content": self._EPISTEMIC_BLOCK}
        )

    @staticmethod
    def _inject_static_context(body: dict, sess: dict | None) -> None:
        """Inject cached static context at a fixed early position.

        Inserts after the initial system message(s), before the first
        user message.  This keeps the static context as part of the
        stable prompt prefix for LLM prompt caching.
        """
        if not sess or not sess.get("static_ctx"):
            return

        messages = body.get("messages", [])
        # Find insertion point: after consecutive system messages at
        # the start of the conversation.
        insert_idx = 0
        for i, msg in enumerate(messages):
            if msg.get("role") == "system":
                insert_idx = i + 1
            else:
                break

        messages.insert(
            insert_idx,
            {
                "role": "system",
                "content": sess["static_ctx"],
            },
        )

    @staticmethod
    def _build_status(result: dict | None, is_first: bool) -> str:
        """Build a detailed status message from recall stats."""
        if not result:
            return "Memory unavailable"

        stats = result.get("stats", {})
        ms = stats.get("latency_ms", 0)
        core = stats.get("core_count", 0)
        new = stats.get("new_count", 0)

        if is_first:
            if core and new:
                return f"Recalled {core} core + {new} relevant memories ({ms}ms)"
            if core:
                return f"Recalled {core} core memories ({ms}ms)"
            if new:
                return f"Found {new} relevant memories ({ms}ms)"
            return f"Memory ready ({ms}ms)"

        # Subsequent call
        if new:
            return f"Found {new} new memories ({ms}ms)"
        return f"No new memories ({ms}ms)"

    # ── Outlet (after LLM) ───────────────────────────────────────────

    async def outlet(
        self,
        body: dict,
        __user__: Optional[dict] = None,
        __event_emitter__: Optional[Callable] = None,
    ) -> dict:
        """After LLM: store memories from the exchange (fire-and-forget)."""
        if not __user__:
            return body

        user_valves = __user__.get("valves")
        if user_valves and hasattr(user_valves, "enabled") and not user_valves.enabled:
            return body

        chat_id = body.get("chat_id") or ""
        user_id = __user__.get("email", __user__.get("id", ""))
        sess = self._get_session(chat_id, user_id)
        session_id = sess["session_id"] if sess else None
        messages = body.get("messages", [])

        # Only user/assistant messages — exclude system prompts and tool results
        conversation = [m for m in messages if m.get("role") in ("user", "assistant")]

        if len(conversation) < 2:
            await self._debug(
                __event_emitter__,
                f"Outlet: skipping, only {len(conversation)} messages",
            )
            return body

        # Last 2 user/assistant messages (current exchange)
        last_two = conversation[-2:]

        # Build context from the first user message to give the extraction
        # LLM topic awareness. Without this, memories extracted from the
        # last exchange can be vague (e.g., "User wants to search the web")
        # because the LLM doesn't know what the conversation is about.
        context = None
        first_user_msg = next(
            (
                m.get("content", "")
                for m in conversation
                if m.get("role") == "user" and m.get("content")
            ),
            None,
        )
        if first_user_msg and isinstance(first_user_msg, str):
            # Cap context to avoid sending huge first messages
            context = f"Conversation topic: {first_user_msg[:500]}"

        await self._debug(
            __event_emitter__,
            f"Outlet: session={session_id!r} msgs={len(last_two)} chat_id={chat_id!r}",
        )

        # Fire-and-forget
        payload: dict = {"session_id": session_id, "messages": last_two}
        if context:
            payload["context"] = context
        # Attach labels for provenance tracking (chat_id links memories
        # to a specific conversation, source identifies the client).
        # origin/share follow the privacy policy: a memory produced while
        # talking to a cloud model is cloud-originated => shareable to all
        # services. Local-model memories are origin=local with no share
        # flag (cloud-invisible by default until the user elevates them).
        labels: dict[str, str] = {"source": "open-webui"}
        if chat_id:
            labels["chat_id"] = chat_id
        if self.valves.cloud_zone_enabled and not self._is_local_model(body):
            labels["origin"] = "cloud"
            labels["share"] = "cloud"
        else:
            labels["origin"] = "local"
        payload["labels"] = labels
        asyncio.create_task(
            self._post("/api/remember", payload, __user__, __event_emitter__)
        )

        return body
