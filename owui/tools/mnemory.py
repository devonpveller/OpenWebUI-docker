"""
title: Mnemory Tools
description: Memory tools for storing, searching, and managing persistent memories. The filter handles automatic recall — use these tools for explicit operations.
author: mnemory
version: 0.1.0
license: Apache-2.0
"""

import json
import logging
import time
from typing import Any, Callable, Optional

import aiohttp
from pydantic import BaseModel, Field

_log = logging.getLogger(__name__)


class Tools:
    class Valves(BaseModel):
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
        request_timeout: int = Field(
            default=30,
            description="HTTP request timeout in seconds",
        )
        search_limit: int = Field(
            default=5,
            description=(
                "Max memories to return from search/find. Lower values "
                "reduce context usage for smaller models. Range: 1-20."
            ),
        )
        max_search_calls_per_turn: int = Field(
            default=1,
            description=(
                "Maximum search/find tool calls allowed within a short "
                "time window (30s). Prevents smaller models from looping "
                "on search calls and exhausting their output budget. "
                "Set to 0 to disable the limit."
            ),
        )

        debug: bool = Field(
            default=False,
            description=(
                "Emit detailed debug info as chat status messages. "
                "Shows request URL, payload, response status, result counts, "
                "and error details for every API call."
            ),
        )

    def __init__(self):
        self.valves = self.Valves()
        # Per-user search call tracker: {user_id: [(timestamp, ...), ...]}
        # Used to rate-limit search/find calls within a turn.
        self._search_calls: dict[str, list[float]] = {}

    def _check_search_limit(self, user: dict) -> str | None:
        """Check if the user has exceeded the search call limit.

        Returns a JSON string to return to the model if rate-limited,
        or None if the call should proceed.
        """
        limit = self.valves.max_search_calls_per_turn
        if limit <= 0:
            return None

        user_id = user.get("email", user.get("id", ""))
        if not user_id:
            return None

        now = time.monotonic()
        window = 30.0  # seconds

        # Clean old entries
        calls = self._search_calls.get(user_id, [])
        calls = [t for t in calls if now - t < window]

        if len(calls) >= limit:
            self._search_calls[user_id] = calls
            return json.dumps(
                {
                    "results": [],
                    "message": (
                        "You have already searched memory this turn. "
                        "Use the results you already have and respond "
                        "to the user now. Do NOT search again."
                    ),
                }
            )

        calls.append(now)
        self._search_calls[user_id] = calls

        # Evict stale users periodically
        if len(self._search_calls) > 200:
            self._search_calls = {
                k: v
                for k, v in self._search_calls.items()
                if v and now - v[-1] < window
            }

        return None

    @staticmethod
    def _format_error(result: dict, operation: str) -> str:
        """Format an error result as JSON with a clear error message.

        Returns structured JSON that matches the normal return format
        but with an obvious error field, so the LLM treats it as data
        and continues its execution plan.
        """
        detail = result.get("detail") or result.get("message") or "unknown error"
        status = result.get("status", "")
        return json.dumps(
            {
                "error": True,
                "operation": operation,
                "status": status,
                "error_message": f"{operation} failed: {detail}",
                "instruction": "Do NOT retry this call. Respond to the user with what you already know.",
            }
        )

    @staticmethod
    def _slim_results(result: dict) -> dict:
        """Trim search/find results to only fields the LLM needs.

        Full API responses include all metadata (timestamps, TTL, access
        counts, artifacts, labels, scores, etc.) which floods the context
        window of smaller models, causing them to exhaust their generation
        budget on reasoning and stop before making further tool calls.

        Keeps: id, memory text (truncated to 300 chars), memory_type,
        categories, importance.
        """
        items = result.get("results")
        if not isinstance(items, list):
            return result
        slim = []
        for item in items:
            text = item.get("memory", "")
            if len(text) > 300:
                text = text[:297] + "..."
            entry: dict[str, Any] = {
                "id": item.get("id", ""),
                "memory": text,
            }
            meta = item.get("metadata") or {}
            if meta.get("memory_type"):
                entry["type"] = meta["memory_type"]
            if meta.get("categories"):
                entry["categories"] = meta["categories"]
            if meta.get("importance") and meta["importance"] != "normal":
                entry["importance"] = meta["importance"]
            slim.append(entry)
        return {"results": slim, "count": len(slim)}

    async def _debug(self, emitter: Callable | None, msg: str) -> None:
        """Emit a debug status message if debug mode is on."""
        if not self.valves.debug or not emitter:
            return
        await emitter(
            {
                "type": "status",
                "data": {"description": f"[mnemory debug] {msg}", "done": True},
            }
        )

    def _headers(self, user: dict) -> dict:
        """Build request headers with auth and identity."""
        headers = {
            "Content-Type": "application/json",
            "X-Agent-Id": self.valves.agent_id,
            "X-User-Id": user.get("email", user.get("id", "")),
        }
        if self.valves.api_key:
            headers["Authorization"] = f"Bearer {self.valves.api_key}"
        return headers

    async def _post(
        self,
        path: str,
        payload: dict,
        user: dict,
        emitter: Callable | None = None,
    ) -> dict:
        """POST to mnemory REST API. Returns parsed JSON or error dict."""
        await self._debug(emitter, f"POST {path} payload={json.dumps(payload)[:200]}")
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.valves.mnemory_url}{path}",
                    headers=self._headers(user),
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=self.valves.request_timeout),
                ) as resp:
                    if resp.status == 200:
                        body = await resp.json()
                        await self._debug(emitter, f"POST {path} -> 200 OK")
                        return body
                    # Try to parse error detail from JSON, fall back to text
                    try:
                        body = await resp.json()
                        detail = body.get("detail", resp.reason)
                    except Exception:
                        detail = (await resp.text())[:200] or resp.reason
                    await self._debug(
                        emitter, f"POST {path} -> {resp.status}: {detail}"
                    )
                    return {"error": True, "status": resp.status, "detail": str(detail)}
        except Exception as exc:
            _log.exception("mnemory API error: %s %s", path, exc)
            await self._debug(emitter, f"POST {path} EXCEPTION: {exc}")
            return {"error": True, "message": f"Connection error: {exc}"}

    async def _put(
        self,
        path: str,
        payload: dict,
        user: dict,
        emitter: Callable | None = None,
    ) -> dict:
        """PUT to mnemory REST API."""
        await self._debug(emitter, f"PUT {path} payload={json.dumps(payload)[:200]}")
        try:
            async with aiohttp.ClientSession() as session:
                async with session.put(
                    f"{self.valves.mnemory_url}{path}",
                    headers=self._headers(user),
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=self.valves.request_timeout),
                ) as resp:
                    if resp.status == 200:
                        body = await resp.json()
                        await self._debug(emitter, f"PUT {path} -> 200 OK")
                        return body
                    try:
                        body = await resp.json()
                        detail = body.get("detail", resp.reason)
                    except Exception:
                        detail = (await resp.text())[:200] or resp.reason
                    await self._debug(emitter, f"PUT {path} -> {resp.status}: {detail}")
                    return {"error": True, "status": resp.status, "detail": str(detail)}
        except Exception as exc:
            _log.exception("mnemory API error: %s %s", path, exc)
            await self._debug(emitter, f"PUT {path} EXCEPTION: {exc}")
            return {"error": True, "message": f"Connection error: {exc}"}

    async def _delete(
        self,
        path: str,
        user: dict,
        emitter: Callable | None = None,
    ) -> dict:
        """DELETE to mnemory REST API."""
        await self._debug(emitter, f"DELETE {path}")
        try:
            async with aiohttp.ClientSession() as session:
                async with session.delete(
                    f"{self.valves.mnemory_url}{path}",
                    headers=self._headers(user),
                    timeout=aiohttp.ClientTimeout(total=self.valves.request_timeout),
                ) as resp:
                    if resp.status == 200:
                        body = await resp.json()
                        await self._debug(emitter, f"DELETE {path} -> 200 OK")
                        return body
                    try:
                        body = await resp.json()
                        detail = body.get("detail", resp.reason)
                    except Exception:
                        detail = (await resp.text())[:200] or resp.reason
                    await self._debug(
                        emitter, f"DELETE {path} -> {resp.status}: {detail}"
                    )
                    return {"error": True, "status": resp.status, "detail": str(detail)}
        except Exception as exc:
            _log.exception("mnemory API error: %s %s", path, exc)
            await self._debug(emitter, f"DELETE {path} EXCEPTION: {exc}")
            return {"error": True, "message": f"Connection error: {exc}"}

    # ── Tool: remember ────────────────────────────────────────────────

    async def remember(
        self,
        content: str,
        __user__: dict = {},
        __event_emitter__: Optional[Callable] = None,
    ) -> str:
        """Store a memory. Just pass the content — the server handles classification automatically.

        Call this when the user shares something worth remembering: personal info,
        preferences, decisions, tasks, project context, or corrections.

        Do NOT pass categories, memory_type, or importance — the server auto-classifies.

        Args:
            content: The fact, preference, decision, or detail to remember (max 1000 chars).

        Returns:
            Confirmation with memory ID, or an error message.
        """
        if not content or not content.strip():
            return json.dumps({"error": True, "message": "Content cannot be empty"})

        if len(content) > 1000:
            return json.dumps(
                {
                    "error": True,
                    "message": f"Content too long ({len(content)} chars, max 1000). Summarize first.",
                }
            )

        if __event_emitter__:
            await __event_emitter__(
                {
                    "type": "status",
                    "data": {"description": "Storing memory...", "done": False},
                }
            )

        await self._debug(__event_emitter__, f"remember: content={content[:100]}...")
        result = await self._post(
            "/api/memories", {"content": content}, __user__, __event_emitter__
        )

        if __event_emitter__:
            if result.get("error"):
                desc = f"Memory error: {result.get('detail') or result.get('message', 'unknown')}"
            else:
                count = len(result.get("results", []))
                desc = (
                    f"Stored {count} memory item(s)"
                    if count
                    else "Memory processed (deduplicated)"
                )
            await __event_emitter__(
                {"type": "status", "data": {"description": desc, "done": True}}
            )

        await self._debug(
            __event_emitter__,
            f"remember result: {json.dumps(result, default=str)[:300]}",
        )
        if result.get("error"):
            return self._format_error(result, "Storing memory")
        # Slim down: LLM only needs IDs and actions, not full metadata
        slim = []
        for r in result.get("results", []):
            slim.append({"id": r.get("id", ""), "action": r.get("action", "ADD")})
        return json.dumps({"results": slim, "count": len(slim)})

    # ── Tool: search_memory ───────────────────────────────────────────

    async def search_memory(
        self,
        query: str,
        __user__: dict = {},
        __event_emitter__: Optional[Callable] = None,
    ) -> str:
        """Search memories by keyword. ONLY call this when the user explicitly asks you to look something up AND the answer is not already in the recalled memories injected into this conversation. Do NOT call proactively. Do NOT call more than once per turn. If this returns an error, do NOT retry — respond with what you know.

        Args:
            query: What to search for, in natural language.

        Returns:
            List of matching memories.
        """
        if not query or not query.strip():
            return json.dumps({"error": True, "message": "Query cannot be empty"})

        limited = self._check_search_limit(__user__)
        if limited:
            return limited

        if __event_emitter__:
            await __event_emitter__(
                {
                    "type": "status",
                    "data": {"description": "Searching memories...", "done": False},
                }
            )

        await self._debug(__event_emitter__, f"search_memory: query={query[:100]}")
        result = await self._post(
            "/api/memories/search",
            {"query": query, "limit": min(self.valves.search_limit, 20)},
            __user__,
            __event_emitter__,
        )

        if __event_emitter__:
            if result.get("error"):
                desc = f"Search error: {result.get('detail') or result.get('message', 'unknown')}"
            else:
                count = len(result.get("results", []))
                desc = (
                    f"Found {count} memories" if count else "No matching memories found"
                )
            await __event_emitter__(
                {"type": "status", "data": {"description": desc, "done": True}}
            )

        await self._debug(
            __event_emitter__,
            f"search_memory result count: {len(result.get('results', []))}",
        )
        if result.get("error"):
            return self._format_error(result, "Searching memories")
        return json.dumps(self._slim_results(result), default=str)

    # ── Tool: find_memory ─────────────────────────────────────────────

    async def find_memory(
        self,
        question: str,
        __user__: dict = {},
        __event_emitter__: Optional[Callable] = None,
    ) -> str:
        """Deep search for a specific question ONLY when search_memory was not enough. Do NOT call this proactively. Do NOT call this if you already have the answer from recalled memories. Do NOT call both search_memory and find_memory for the same question. If this returns an error, do NOT retry — respond with what you know.

        Args:
            question: The question to answer, in natural language.

        Returns:
            List of matching memories ranked by relevance.
        """
        if not question or not question.strip():
            return json.dumps({"error": True, "message": "Question cannot be empty"})

        limited = self._check_search_limit(__user__)
        if limited:
            return limited

        if __event_emitter__:
            await __event_emitter__(
                {
                    "type": "status",
                    "data": {
                        "description": "Deep searching memories...",
                        "done": False,
                    },
                }
            )

        await self._debug(__event_emitter__, f"find_memory: question={question[:100]}")
        result = await self._post(
            "/api/memories/find",
            {"question": question, "limit": min(self.valves.search_limit, 20)},
            __user__,
            __event_emitter__,
        )

        # On error, silently fall back to basic search (no LLM needed)
        # so the model gets results instead of an error that triggers
        # a retry loop.
        if result.get("error"):
            await self._debug(
                __event_emitter__,
                f"find_memory failed ({result.get('status', '?')}), "
                f"falling back to search_memory",
            )
            if __event_emitter__:
                await __event_emitter__(
                    {
                        "type": "status",
                        "data": {"description": "Searching memories...", "done": False},
                    }
                )
            result = await self._post(
                "/api/memories/search",
                {"query": question, "limit": min(self.valves.search_limit, 20)},
                __user__,
                __event_emitter__,
            )

        if __event_emitter__:
            if result.get("error"):
                desc = f"Search error: {result.get('detail') or result.get('message', 'unknown')}"
            else:
                count = len(result.get("results", []))
                desc = (
                    f"Found {count} memories" if count else "No matching memories found"
                )
            await __event_emitter__(
                {"type": "status", "data": {"description": desc, "done": True}}
            )

        await self._debug(
            __event_emitter__,
            f"find_memory result count: {len(result.get('results', []))}",
        )
        if result.get("error"):
            return self._format_error(result, "Deep searching memories")
        return json.dumps(self._slim_results(result), default=str)

    # ── Tool: update_memory ───────────────────────────────────────────

    async def update_memory(
        self,
        memory_id: str,
        content: str,
        __user__: dict = {},
        __event_emitter__: Optional[Callable] = None,
    ) -> str:
        """Update an existing memory's content. Use search_memory first to find the memory ID.

        Args:
            memory_id: The ID of the memory to update (from search results).
            content: The new content for this memory (max 1000 chars).

        Returns:
            Confirmation or error message.
        """
        if not memory_id or not memory_id.strip():
            return json.dumps({"error": True, "message": "memory_id is required"})
        if not content or not content.strip():
            return json.dumps({"error": True, "message": "content is required"})
        if len(content) > 1000:
            return json.dumps(
                {
                    "error": True,
                    "message": f"Content too long ({len(content)} chars, max 1000)",
                }
            )

        if __event_emitter__:
            await __event_emitter__(
                {
                    "type": "status",
                    "data": {"description": "Updating memory...", "done": False},
                }
            )

        await self._debug(__event_emitter__, f"update_memory: id={memory_id}")
        result = await self._put(
            f"/api/memories/{memory_id}",
            {"content": content},
            __user__,
            __event_emitter__,
        )

        if __event_emitter__:
            if result.get("error"):
                desc = f"Update error: {result.get('detail') or result.get('message', 'unknown')}"
            else:
                desc = "Memory updated"
            await __event_emitter__(
                {"type": "status", "data": {"description": desc, "done": True}}
            )

        if result.get("error"):
            return self._format_error(result, "Updating memory")
        return json.dumps({"updated": True, "id": memory_id})

    # ── Tool: delete_memory ───────────────────────────────────────────

    async def delete_memory(
        self,
        memory_id: str,
        __user__: dict = {},
        __event_emitter__: Optional[Callable] = None,
    ) -> str:
        """Delete a memory permanently. Use search_memory first to find the memory ID.

        Args:
            memory_id: The ID of the memory to delete (from search results).

        Returns:
            Confirmation or error message.
        """
        if not memory_id or not memory_id.strip():
            return json.dumps({"error": True, "message": "memory_id is required"})

        if __event_emitter__:
            await __event_emitter__(
                {
                    "type": "status",
                    "data": {"description": "Deleting memory...", "done": False},
                }
            )

        await self._debug(__event_emitter__, f"delete_memory: id={memory_id}")
        result = await self._delete(
            f"/api/memories/{memory_id}",
            __user__,
            __event_emitter__,
        )

        if __event_emitter__:
            if result.get("error"):
                desc = f"Delete error: {result.get('detail') or result.get('message', 'unknown')}"
            else:
                desc = "Memory deleted"
            await __event_emitter__(
                {"type": "status", "data": {"description": desc, "done": True}}
            )

        if result.get("error"):
            return self._format_error(result, "Deleting memory")
        return json.dumps({"deleted": True, "id": memory_id})
