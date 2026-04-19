"""
title: SmolCrawl Knowledge Builder
author: smolcrawl
date: 2026-04-11
version: 2.0
license: MIT
description: Crawl a website, augment markdown for RAG, and upload to an OWUI knowledge collection. Streams progress in chat.
requirements: httpx, markdownify, readabilipy, beautifulsoup4, lxml
"""

import logging
import queue
import re
import threading
import time
from typing import Generator, Iterator, List, Optional, Union
from urllib.parse import urlparse

from pydantic import BaseModel


class Pipeline:
    """OWUI Pipeline that crawls a website and uploads to a knowledge base."""

    class Valves(BaseModel):
        """User-configurable settings shown in OWUI admin panel."""
        owui_base_url: str = "http://openwebui:8080"
        owui_api_key: str = ""
        server_intensity: float = 0.3
        max_pages: int = 1000
        upload_concurrency: int = 1
        augment_for_rag: bool = True

    def __init__(self):
        self.name = "SmolCrawl Knowledge Builder"
        self.valves = self.Valves()

    async def on_startup(self):
        """Verify smolcrawl is importable."""
        try:
            import smolcrawl  # noqa: F401
        except ImportError:
            print("[SmolCrawl Pipeline] WARNING: smolcrawl package not found. "
                  "Install with: pip install smolcrawl")

    async def on_shutdown(self):
        pass

    def pipe(
        self,
        user_message: str,
        model_id: str,
        messages: List[dict],
        body: dict,
    ) -> Union[str, Generator[str, None, None], Iterator[str]]:
        """Process a user message containing a URL to crawl.

        Streams progress as markdown-formatted status updates.
        """
        url = self._extract_url(user_message)
        if not url:
            return ("Please provide a URL to crawl and a knowledge base name.\n\n"
                    "Example: `crawl https://docs.example.com into My KB Name`")

        kb_name = self._extract_kb_name(user_message, url)
        return self._run_pipeline(url, kb_name)

    # Seconds between progress yields (batches events between intervals)
    _PROGRESS_INTERVAL = 15
    # Seconds before emitting a heartbeat when nothing happened at all
    _HEARTBEAT_INTERVAL = 20

    def _run_pipeline(self, url: str, kb_name: str) -> Generator[str, None, None]:
        """Execute the full crawl → augment → upload pipeline with streaming.

        Progress updates are batched: one summary line every _PROGRESS_INTERVAL
        seconds instead of one per page/file, keeping SSE traffic low even for
        crawls with thousands of pages.
        """
        from smolcrawl.crawl import crawl_target_sync
        from smolcrawl.augment import augment_pages
        from smolcrawl.owui_client import OwuiConfig, OwuiKnowledgeClient

        log = logging.getLogger("smolcrawl_pipeline")

        log.info("[pipeline] starting, url=%s kb=%s", url, kb_name)
        yield f"## SmolCrawl Pipeline\n\n"
        yield f"**Target:** {url}\n"
        yield f"**Knowledge Base:** {kb_name}\n"
        yield f"**Max Pages:** {self.valves.max_pages}\n\n"

        # ── Phase 1: Crawl (runs in background thread) ──
        yield "### Phase 1: Crawling\n\n"
        crawl_queue: queue.Queue = queue.Queue()

        def crawl_worker():
            try:
                intensity = self.valves.server_intensity
                max_concurrent = max(1, int(1 + (intensity * 11)))
                delay = (1.0 - intensity) * 2.0

                def on_page(page_count, page_url):
                    crawl_queue.put(("progress", page_count, page_url))

                result = crawl_target_sync(
                    url,
                    max_pages=self.valves.max_pages,
                    max_concurrent=max_concurrent,
                    delay=delay,
                    on_page_crawled=on_page,
                )
                crawl_queue.put(("done", result))
            except Exception as e:
                crawl_queue.put(("error", str(e)))

        thread = threading.Thread(target=crawl_worker, daemon=True)
        thread.start()

        pages = None
        last_count = 0
        last_url = ""
        last_yield = time.monotonic()
        start_time = time.monotonic()

        while True:
            # Drain all available events from the queue (non-blocking after
            # the first blocking wait), then decide whether to yield.
            try:
                item = crawl_queue.get(timeout=1.0)
            except queue.Empty:
                # Nothing arrived — emit heartbeat if overdue
                if time.monotonic() - last_yield >= self._HEARTBEAT_INTERVAL:
                    elapsed = int(time.monotonic() - start_time)
                    msg = f"⏳ Crawling… {last_count} pages ({elapsed}s)\n"
                    log.info("[pipeline] heartbeat yield: %s", msg.strip())
                    yield msg
                    last_yield = time.monotonic()
                continue

            if item[0] == "progress":
                last_count = item[1]
                last_url = item[2]
                # Yield a batched summary at most every _PROGRESS_INTERVAL
                if time.monotonic() - last_yield >= self._PROGRESS_INTERVAL:
                    elapsed = int(time.monotonic() - start_time)
                    msg = f"Crawled **{last_count}** pages so far ({elapsed}s)\n"
                    log.info("[pipeline] progress yield: %s", msg.strip())
                    yield msg
                    last_yield = time.monotonic()
            elif item[0] == "done":
                pages = item[1]
                elapsed = int(time.monotonic() - start_time)
                yield f"\n✅ Crawled **{len(pages)}** pages in {elapsed}s.\n\n"
                break
            elif item[0] == "error":
                yield f"**Error during crawl:** {item[1]}\n"
                return

        thread.join(timeout=5)

        if not pages:
            yield "No pages found. Check the URL and try again.\n"
            return

        # ── Phase 2: Augment ──
        if self.valves.augment_for_rag:
            yield "### Phase 2: Augmenting for RAG\n\n"
            try:
                pages = augment_pages(pages)
                yield f"✅ Augmented **{len(pages)}** pages.\n\n"
            except Exception as e:
                yield f"⚠️ Augmentation failed ({e}), uploading raw content.\n\n"

        # ── Phase 3: Upload (runs in background thread) ──
        yield "### Phase 3: Uploading to Knowledge Base\n\n"
        config = OwuiConfig(
            base_url=self.valves.owui_base_url,
            api_key=self.valves.owui_api_key,
            knowledge_base_name=kb_name,
            upload_concurrency=self.valves.upload_concurrency,
        )

        progress_queue: queue.Queue = queue.Queue()
        result_holder: list = []
        error_holder: list = []

        def upload_worker():
            try:
                with OwuiKnowledgeClient(config) as client:
                    result = client.sync_pages(
                        pages, kb_name,
                        on_progress=lambda cur, tot, name:
                            progress_queue.put(("progress", cur, tot, name)),
                    )
                    result_holder.append(result)
            except Exception as e:
                error_holder.append(str(e))
            finally:
                progress_queue.put(None)  # sentinel

        thread = threading.Thread(target=upload_worker, daemon=True)
        thread.start()

        last_upload_count = 0
        upload_total = len(pages)
        last_yield = time.monotonic()
        upload_start = time.monotonic()

        while True:
            try:
                item = progress_queue.get(timeout=1.0)
            except queue.Empty:
                if time.monotonic() - last_yield >= self._HEARTBEAT_INTERVAL:
                    elapsed = int(time.monotonic() - upload_start)
                    yield f"⏳ Uploading… {last_upload_count}/{upload_total} ({elapsed}s)\n"
                    last_yield = time.monotonic()
                continue

            if item is None:
                break
            _, cur, tot, name = item
            last_upload_count = cur
            # Batched upload progress
            if time.monotonic() - last_yield >= self._PROGRESS_INTERVAL or cur == tot:
                elapsed = int(time.monotonic() - upload_start)
                yield f"Uploaded **{cur}/{tot}** files ({elapsed}s)\n"
                last_yield = time.monotonic()

        thread.join(timeout=60)

        if error_holder:
            yield f"\n**Upload error:** {error_holder[0]}\n"
            return

        # Summary
        total_elapsed = int(time.monotonic() - start_time)
        if result_holder:
            result = result_holder[0]
            yield f"\n### ✅ Complete in {total_elapsed}s\n\n"
            yield f"| Metric | Value |\n|--------|-------|\n"
            yield f"| Pages crawled | {len(pages)} |\n"
            yield f"| Files uploaded | {result.uploaded} |\n"
            yield f"| Skipped (unchanged) | {result.skipped} |\n"
            yield f"| Failures | {result.failed} |\n"
            yield f"| Knowledge Base | {kb_name} |\n"
        else:
            yield f"\n**Upload completed** in {total_elapsed}s (no result details available).\n"

    @staticmethod
    def _extract_kb_name(message: str, url: str) -> str:
        """Extract knowledge base name from the message, falling back to domain."""
        # Look for patterns like: "into <name>", "as <name>", "kb:<name>"
        for pattern in [
            r'(?:into|to|as|kb:|knowledge[- ]?base[: ])\s*["\']?(.+?)["\']?\s*$',
            r'(?:into|to|as|kb:|knowledge[- ]?base[: ])\s*["\']?(.+?)["\']?(?:\s+(?:with|using|from))',
        ]:
            m = re.search(pattern, message, re.IGNORECASE)
            if m:
                name = m.group(1).strip().strip('"\'')
                if name and name != url:
                    return name
        return f"SmolCrawl - {urlparse(url).netloc}"

    @staticmethod
    def _extract_url(message: str) -> Optional[str]:
        """Extract the first URL from a user message."""
        # Try to find an explicit URL
        url_pattern = re.compile(
            r'https?://[^\s<>\'")\]]+',
            re.IGNORECASE,
        )
        match = url_pattern.search(message)
        if match:
            return match.group(0).rstrip('.,;:!?')
        return None
