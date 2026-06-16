"""
title: Add Web Sources to Knowledge
author: @G30
author_url: https://openwebui.com/u/g30
funding_url: https://github.com/open-webui
version: 0.1.1
license: MIT
required_open_webui_version: 0.8.12
icon_url: data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIyNCIgaGVpZ2h0PSIyNCIgdmlld0JveD0iMCAwIDI0IDI0IiBmaWxsPSJub25lIiBzdHJva2U9ImN1cnJlbnRDb2xvciIgc3Ryb2tlLXdpZHRoPSIyIiBzdHJva2UtbGluZWNhcD0icm91bmQiIHN0cm9rZS1saW5lam9pbj0icm91bmQiPjxwYXRoIGQ9Ik00IDIwaDE2YTIgMiAwIDAgMCAyLTJWOGEyIDIgMCAwIDAtMi0yaC03LjkzYTIgMiAwIDAgMS0xLjY2LS45bC0uODItMS4yQTIgMiAwIDAgMCA3LjkzIDNINGEyIDIgMCAwIDAtMiAydjEzYTIgMiAwIDAgMCAyIDJaIi8+PHBhdGggZD0iTTEyIDEwdjYiLz48cGF0aCBkPSJNOSAxM2gzIi8+PC9zdmc+
"""

import asyncio
import uuid
import re
import inspect

from pydantic import BaseModel, Field
from typing import Optional


class Action:
    """
    Action function that adds web search result URLs to a Knowledge base.

    When a message contains sources from web search, this action button allows
    users to save those sources directly to their Knowledge base.
    """

    class Valves(BaseModel):
        """Admin-level configuration options (system-wide defaults)."""

        priority: int = Field(
            default=0,
            description="Priority level for button sorting in the UI (lower numbers appear first).",
        )

        max_urls_per_action: int = Field(
            default=10,
            description="Maximum number of URLs to process in a single action (prevents abuse)",
        )
        enable_duplicate_check: bool = Field(
            default=True,
            description="Check if URL already exists in the knowledge base before adding",
        )
        default_knowledge_base: str = Field(
            default="", description="System-wide default Knowledge Base name or ID"
        )
        skip_confirmation: bool = Field(
            default=False, description="System-wide default: skip confirmation dialog"
        )
        file_name_prefix: str = Field(
            default="",
            description="System-wide default prefix for file names (e.g., 'web_')",
        )

    class UserValves(BaseModel):
        """User-level configuration options."""

        default_knowledge_base: str = Field(
            default="",
            description="Default Knowledge Base name or ID. If set, skips the selection dialog.",
        )
        skip_confirmation: bool = Field(
            default=False,
            description="Skip confirmation dialog and use default KB (requires default_knowledge_base to be set)",
        )
        file_name_prefix: str = Field(
            default="",
            description="Prefix to add to file names (e.g., 'web_' or 'source_')",
        )

    def __init__(self):
        self.valves = self.Valves()

    async def action(
        self,
        body: dict,
        __user__: dict = None,
        __event_emitter__=None,
        __event_call__=None,
        __request__=None,
    ) -> Optional[dict]:
        """
        Main action handler that processes the message sources and adds them to Knowledge.
        """
        # Get user valves (with fallback to admin valve defaults)
        user_valves = __user__.get("valves", None) if __user__ else None
        if user_valves and isinstance(user_valves, self.UserValves):
            uv = user_valves
        elif user_valves and isinstance(user_valves, dict):
            uv = self.UserValves(**user_valves)
        else:
            uv = self.UserValves()

        # Merge settings
        effective_default_kb = (
            uv.default_knowledge_base
            if uv.default_knowledge_base
            else self.valves.default_knowledge_base
        )
        effective_skip_confirm = uv.skip_confirmation or self.valves.skip_confirmation
        effective_prefix = (
            uv.file_name_prefix if uv.file_name_prefix else self.valves.file_name_prefix
        )

        # Extract messages and find sources
        messages = body.get("messages", [])
        urls = []

        for message in messages:
            sources = message.get("sources", [])
            if sources:
                for source in sources:
                    # Structure 1: source has 'source' object with url/name
                    source_obj = source.get("source", {})
                    if source_obj:
                        url = source_obj.get("url") or source_obj.get("name", "")
                        if url and (
                            url.startswith("http://") or url.startswith("https://")
                        ):
                            if not any(u["url"] == url for u in urls):
                                urls.append(
                                    {"url": url, "name": source_obj.get("name", url)}
                                )

                    # Structure 2: metadata array with source URLs
                    metadata_list = source.get("metadata", [])
                    for meta in metadata_list if metadata_list else []:
                        meta_source = meta.get("source", "")
                        if meta_source and (
                            meta_source.startswith("http://")
                            or meta_source.startswith("https://")
                        ):
                            if not any(u["url"] == meta_source for u in urls):
                                urls.append({"url": meta_source, "name": meta_source})

        print(
            f"[Add to Knowledge] Found {len(urls)} URLs from {len(messages)} messages"
        )

        if not urls:
            await __event_emitter__(
                {
                    "type": "notification",
                    "data": {
                        "type": "warning",
                        "content": "No web URLs found in message sources. This action works with web search results.",
                    },
                }
            )
            return None

        # Apply max URLs limit from admin valves
        if len(urls) > self.valves.max_urls_per_action:
            urls = urls[: self.valves.max_urls_per_action]
            await __event_emitter__(
                {
                    "type": "notification",
                    "data": {
                        "type": "info",
                        "content": f"Limited to {self.valves.max_urls_per_action} URLs (admin setting)",
                    },
                }
            )

        # Get available knowledge bases
        await __event_emitter__(
            {"type": "status", "data": {"description": "Loading knowledge bases..."}}
        )

        try:
            from open_webui.models.knowledge import Knowledges

            knowledge_bases = await Knowledges.get_knowledge_bases_by_user_id(
                user_id=__user__["id"], permission="write"
            )

            if not knowledge_bases:
                knowledge_bases = await Knowledges.get_knowledge_bases(
                    user_id=__user__["id"]
                )

            if not knowledge_bases:
                await __event_emitter__(
                    {"type": "status", "data": {"description": "", "done": True}}
                )
                await __event_emitter__(
                    {
                        "type": "notification",
                        "data": {
                            "type": "error",
                            "content": "No knowledge bases found. Please create one first.",
                        },
                    }
                )
                return None

            selected_kb = None

            # Check if user/admin has a default KB set
            if effective_default_kb:
                default_kb_lower = effective_default_kb.lower().strip()
                for kb in knowledge_bases:
                    if (
                        kb.id == effective_default_kb
                        or kb.name.lower() == default_kb_lower
                    ):
                        selected_kb = kb
                        break

                if not selected_kb:
                    await __event_emitter__(
                        {
                            "type": "notification",
                            "data": {
                                "type": "warning",
                                "content": f"Default KB '{effective_default_kb}' not found. Please select manually.",
                            },
                        }
                    )

            # If no default KB or skip_confirmation is False, ask user
            if not selected_kb or not effective_skip_confirm:
                # Step 1: URL Selection Dialog
                url_list_numbered = "\n".join(
                    [
                        f"{i+1}. {u['url'][:65]}{'...' if len(u['url']) > 65 else ''}"
                        for i, u in enumerate(urls)
                    ]
                )

                url_response = await __event_call__(
                    {
                        "type": "input",
                        "data": {
                            "title": "Select Sources to Add",
                            "message": f"Found {len(urls)} source(s):\n{url_list_numbered}\n\nEnter numbers to add (e.g., '1,3,5' or '1-3' or 'all'):",
                            "placeholder": "all",
                        },
                    }
                )

                # FIX: Handle empty responses or the UI returning boolean `True` when submitted blank
                if url_response is True:
                    url_response = "all"
                elif not url_response:
                    await __event_emitter__(
                        {"type": "status", "data": {"description": "", "done": True}}
                    )
                    await __event_emitter__(
                        {
                            "type": "notification",
                            "data": {"type": "info", "content": "Action cancelled"},
                        }
                    )
                    return None

                url_response = str(url_response).strip().lower()
                selected_indices = set()

                if url_response == "all" or url_response == "*":
                    selected_indices = set(range(len(urls)))
                else:
                    parts = url_response.replace(" ", "").split(",")
                    for part in parts:
                        if "-" in part:
                            try:
                                start, end = part.split("-")
                                for i in range(int(start) - 1, int(end)):
                                    if 0 <= i < len(urls):
                                        selected_indices.add(i)
                            except (ValueError, IndexError):
                                pass
                        else:
                            try:
                                idx = int(part) - 1
                                if 0 <= idx < len(urls):
                                    selected_indices.add(idx)
                            except ValueError:
                                pass

                if not selected_indices:
                    await __event_emitter__(
                        {"type": "status", "data": {"description": "", "done": True}}
                    )
                    await __event_emitter__(
                        {
                            "type": "notification",
                            "data": {
                                "type": "warning",
                                "content": "No valid URLs selected",
                            },
                        }
                    )
                    return None

                # Filter URLs to selected ones
                urls = [urls[i] for i in sorted(selected_indices)]

                # Step 2: Knowledge Base Selection Dialog
                kb_list = "\n".join([f"• {kb.name}" for kb in knowledge_bases[:10]])
                if len(knowledge_bases) > 10:
                    kb_list += f"\n• ...and {len(knowledge_bases) - 10} more"

                kb_response = await __event_call__(
                    {
                        "type": "input",
                        "data": {
                            "title": "Select Knowledge Base",
                            "message": f"Adding {len(urls)} source(s).\n\nAvailable Knowledge Bases:\n{kb_list}",
                            "placeholder": (
                                effective_default_kb
                                if effective_default_kb
                                else "Enter knowledge base name..."
                            ),
                        },
                    }
                )

                if not kb_response:
                    await __event_emitter__(
                        {"type": "status", "data": {"description": "", "done": True}}
                    )
                    await __event_emitter__(
                        {
                            "type": "notification",
                            "data": {"type": "info", "content": "Action cancelled"},
                        }
                    )
                    return None

                kb_response_lower = str(kb_response).lower().strip()
                for kb in knowledge_bases:
                    if kb.id == kb_response or kb.name.lower() == kb_response_lower:
                        selected_kb = kb
                        break

                if not selected_kb:
                    await __event_emitter__(
                        {"type": "status", "data": {"description": "", "done": True}}
                    )
                    await __event_emitter__(
                        {
                            "type": "notification",
                            "data": {
                                "type": "error",
                                "content": f"Knowledge base '{kb_response}' not found.",
                            },
                        }
                    )
                    return None

            await __event_emitter__(
                {
                    "type": "status",
                    "data": {
                        "description": f"Adding {len(urls)} source(s) to '{selected_kb.name}'..."
                    },
                }
            )

            success_count = 0
            error_count = 0
            skipped_count = 0

            from open_webui.routers.retrieval import get_content_from_url
            from open_webui.models.files import Files, FileForm

            # Check for duplicates if enabled
            existing_sources = set()
            if self.valves.enable_duplicate_check:
                try:
                    # FIX: Handle 0.9.0 model attribute changes while keeping fallback for older versions
                    if hasattr(Knowledges, "get_file_metadatas_by_id"):
                        kb_files = await Knowledges.get_file_metadatas_by_id(
                            selected_kb.id
                        )
                        for kf in kb_files:
                            meta = (
                                kf.get("meta", {})
                                if isinstance(kf, dict)
                                else (kf.meta if hasattr(kf, "meta") else {})
                            )
                            if meta and meta.get("source"):
                                existing_sources.add(meta.get("source"))
                    elif hasattr(Knowledges, "get_knowledge_files_by_id"):
                        kb_files = await Knowledges.get_knowledge_files_by_id(
                            selected_kb.id
                        )
                        for kf in kb_files:
                            file = await Files.get_file_by_id(
                                kf.file_id
                                if hasattr(kf, "file_id")
                                else kf.get("file_id")
                            )
                            if file and file.meta and file.meta.get("source"):
                                existing_sources.add(file.meta.get("source"))
                except Exception as e:
                    print(f"[Add to Knowledge] Could not check duplicates: {e}")

            for idx, url_info in enumerate(urls):
                url = url_info["url"]

                if url in existing_sources:
                    print(f"[Add to Knowledge] Skipping duplicate: {url}")
                    skipped_count += 1
                    continue

                try:
                    await __event_emitter__(
                        {
                            "type": "status",
                            "data": {
                                "description": f"Processing ({idx+1}/{len(urls)}): {url[:40]}..."
                            },
                        }
                    )

                    # FIX: Safely execute the Langchain Playwright loader in an isolated thread to prevent asyncio crash
                    if inspect.iscoroutinefunction(get_content_from_url):
                        try:
                            content, docs = await get_content_from_url(__request__, url)
                        except Exception as inner_e:
                            if "Playwright" in str(inner_e) or "asyncio" in str(
                                inner_e
                            ):

                                def _sync_wrapper():
                                    import asyncio

                                    loop = asyncio.new_event_loop()
                                    asyncio.set_event_loop(loop)
                                    return loop.run_until_complete(
                                        get_content_from_url(__request__, url)
                                    )

                                content, docs = await asyncio.to_thread(_sync_wrapper)
                            else:
                                raise inner_e
                    else:
                        content, docs = await asyncio.to_thread(
                            get_content_from_url, __request__, url
                        )

                    if not content:
                        print(f"[Add to Knowledge] No content fetched for {url}")
                        error_count += 1
                        continue

                    # Create sanitized filename
                    sanitized_name = re.sub(
                        r"[^\w\-.]",
                        "_",
                        url.replace("http://", "")
                        .replace("https://", "")
                        .replace("www.", ""),
                    )[:100]

                    if effective_prefix:
                        sanitized_name = f"{effective_prefix}{sanitized_name}"

                    file_form = FileForm(
                        id=str(uuid.uuid4()),
                        filename=f"{sanitized_name}.txt",
                        path="",
                        meta={
                            "name": sanitized_name,
                            "content_type": "text/plain",
                            "source": url,
                        },
                        data={"content": content},
                    )

                    file = await Files.insert_new_file(
                        user_id=__user__["id"], form_data=file_form
                    )

                    if file:
                        result = await Knowledges.add_file_to_knowledge_by_id(
                            knowledge_id=selected_kb.id,
                            file_id=file.id,
                            user_id=__user__["id"],
                        )
                        if result:
                            success_count += 1
                            print(f"[Add to Knowledge] Added: {url}")
                        else:
                            error_count += 1
                    else:
                        error_count += 1

                except Exception as e:
                    print(f"[Add to Knowledge] Error processing {url}: {e}")
                    error_count += 1

            await __event_emitter__(
                {"type": "status", "data": {"description": "", "done": True}}
            )

            if success_count > 0:
                result_msg = f"Added {success_count} source(s) to '{selected_kb.name}'"
                if skipped_count > 0:
                    result_msg += f" ({skipped_count} duplicates skipped)"
                if error_count > 0:
                    result_msg += f" ({error_count} failed)"
                await __event_emitter__(
                    {
                        "type": "notification",
                        "data": {"type": "success", "content": result_msg},
                    }
                )
            elif skipped_count > 0 and error_count == 0:
                await __event_emitter__(
                    {
                        "type": "notification",
                        "data": {
                            "type": "info",
                            "content": f"All {skipped_count} source(s) already exist in '{selected_kb.name}'",
                        },
                    }
                )
            else:
                await __event_emitter__(
                    {
                        "type": "notification",
                        "data": {
                            "type": "error",
                            "content": "Failed to add sources. Check server logs for details.",
                        },
                    }
                )

            return None

        except Exception as e:
            print(f"[Add to Knowledge] Error: {e}")
            import traceback

            traceback.print_exc()

            await __event_emitter__(
                {"type": "status", "data": {"description": "", "done": True}}
            )
            await __event_emitter__(
                {
                    "type": "notification",
                    "data": {"type": "error", "content": f"Error: {str(e)}"},
                }
            )
            return None
