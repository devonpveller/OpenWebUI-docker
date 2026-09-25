"""
title: Copy Sources
author: ai-stack
version: 1.0.0
description: One-click action that gathers every source/citation referenced in the assistant's response and presents it in a copyable modal so the user can paste it elsewhere.
required_open_webui_version: 0.4.0
"""

from pydantic import BaseModel, Field
from typing import Optional, Any
import json


class Action:
    class Valves(BaseModel):
        output_format: str = Field(
            default="urls",
            description=(
                "Output style: "
                "'urls' = bare URLs only, one per line (paste-ready). "
                "'markdown' = numbered list with [title](url). "
                "'plain' = numbered list with title — url. "
                "'json' = structured JSON array."
            ),
        )
        include_excerpts: bool = Field(
            default=False,
            description="When format is 'markdown', 'plain', or 'json', also include the retrieved text excerpts. Ignored for 'urls'.",
        )
        deduplicate: bool = Field(
            default=True,
            description="Drop duplicate sources that share the same URL or file identifier.",
        )
        delivery: str = Field(
            default="auto",
            description=(
                "How to deliver the sources: "
                "'append' = add a copyable code block to the assistant message (works for any size). "
                "'modal' = popup input field (clean UX but truncates beyond ~20 entries). "
                "'auto' = modal for <=20 sources, append otherwise."
            ),
        )

    def __init__(self):
        self.valves = self.Valves()

    async def action(
        self,
        body: dict,
        __user__: Optional[dict] = None,
        __event_emitter__=None,
        __event_call__=None,
    ) -> Optional[dict]:
        sources = self._collect_sources(body)

        if not sources:
            if __event_emitter__:
                await __event_emitter__(
                    {
                        "type": "notification",
                        "data": {
                            "type": "warning",
                            "content": "No sources found in this response.",
                        },
                    }
                )
            return

        if self.valves.deduplicate:
            sources = self._dedupe(sources)

        formatted = self._format(sources, self.valves.output_format)

        mode = (self.valves.delivery or "auto").lower()
        if mode == "auto":
            mode = "modal" if len(sources) <= 20 else "append"

        if mode == "modal" and __event_call__:
            await __event_call__(
                {
                    "type": "input",
                    "data": {
                        "title": f"Sources ({len(sources)})",
                        "message": "Select all (Ctrl/Cmd+A) and copy (Ctrl/Cmd+C).",
                        "placeholder": "Sources will appear here",
                        "value": formatted,
                    },
                }
            )
        elif __event_emitter__:
            block = f"\n\n---\n**Sources ({len(sources)}):**\n\n```\n{formatted}\n```\n"
            await __event_emitter__(
                {
                    "type": "message",
                    "data": {"content": block},
                }
            )

        if __event_emitter__:
            await __event_emitter__(
                {
                    "type": "notification",
                    "data": {
                        "type": "success",
                        "content": f"Prepared {len(sources)} source(s) for copying.",
                    },
                }
            )

    def _collect_sources(self, body: dict) -> list:
        messages = body.get("messages", []) or []
        for msg in reversed(messages):
            if msg.get("role") != "assistant":
                continue
            msg_sources = msg.get("sources") or msg.get("citations") or []
            if msg_sources:
                return self._normalize(msg_sources)
        return []

    def _normalize(self, raw_sources: list) -> list:
        normalized = []
        for entry in raw_sources:
            if not isinstance(entry, dict):
                normalized.append({"title": str(entry), "url": "", "excerpts": []})
                continue

            source_obj = entry.get("source") or {}
            if isinstance(source_obj, str):
                source_obj = {"name": source_obj}

            metadata_list = entry.get("metadata") or []
            documents = entry.get("document") or entry.get("documents") or []

            # Web-search / RAG entries pack multiple hits into one source entry
            # with parallel metadata[] and document[] arrays — emit one row per hit.
            hit_count = max(len(metadata_list), len(documents), 1)

            for i in range(hit_count):
                meta = metadata_list[i] if i < len(metadata_list) else {}
                if not isinstance(meta, dict):
                    meta = {}
                doc = documents[i] if i < len(documents) else ""

                url = (
                    meta.get("source")
                    or meta.get("url")
                    or meta.get("link")
                    or source_obj.get("url")
                    or source_obj.get("id")
                    or ""
                )

                title = (
                    meta.get("title")
                    or meta.get("name")
                    or (source_obj.get("name") if hit_count == 1 else None)
                    or url
                    or "Untitled source"
                )

                excerpts = [doc] if isinstance(doc, str) and doc.strip() else []

                normalized.append(
                    {
                        "title": str(title).strip(),
                        "url": str(url).strip(),
                        "excerpts": excerpts,
                        "metadata": meta,
                    }
                )
        return normalized

    def _dedupe(self, sources: list) -> list:
        seen = set()
        unique = []
        for s in sources:
            key = s.get("url") or s.get("title")
            if key in seen:
                continue
            seen.add(key)
            unique.append(s)
        return unique

    def _format(self, sources: list, fmt: str) -> str:
        fmt = (fmt or "urls").lower()

        if fmt == "urls":
            return "\n".join(s["url"] for s in sources if s.get("url"))

        if fmt == "json":
            payload = (
                sources
                if self.valves.include_excerpts
                else [{"title": s["title"], "url": s["url"]} for s in sources]
            )
            return json.dumps(payload, indent=2, ensure_ascii=False)

        lines = []
        for idx, s in enumerate(sources, start=1):
            title = s.get("title") or "Untitled source"
            url = s.get("url") or ""

            if fmt == "plain":
                lines.append(f"{idx}. {title}" + (f" — {url}" if url else ""))
            else:
                if url:
                    lines.append(f"{idx}. [{title}]({url})")
                else:
                    lines.append(f"{idx}. {title}")

            if self.valves.include_excerpts:
                for excerpt in s.get("excerpts", []):
                    snippet = excerpt.strip().replace("\n", " ")
                    if len(snippet) > 500:
                        snippet = snippet[:497] + "..."
                    prefix = "   > " if fmt == "markdown" else "   "
                    lines.append(f"{prefix}{snippet}")

        return "\n".join(lines)
