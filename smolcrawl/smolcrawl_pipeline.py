"""
title: SmolCrawl Knowledge Builder
author: smolcrawl
date: 2026-04-11
version: 2.1
license: MIT
description: Crawl a website, augment markdown for RAG, and upload to an OWUI knowledge collection. Streams progress in chat. Supports concurrent jobs via a module-level thread pool — submitting a new crawl never blocks a running one.
requirements: httpx, markdownify, readabilipy, beautifulsoup4, lxml
"""

import logging
import queue
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Generator, Iterator, List, Optional, Union
from urllib.parse import urlparse

from pydantic import BaseModel

# ── Module-level concurrent job executor ─────────────────────────────────────
# Persists across pipe() calls. Multiple crawl jobs run in parallel up to
# _JOB_WORKERS. OWUI's RAG embedding is still sequential per KB (upload_concurrency
# defaults to 1), but crawling and augmentation across different KBs overlaps.
_JOB_WORKERS = 4
_JOB_EXECUTOR: ThreadPoolExecutor = ThreadPoolExecutor(
    max_workers=_JOB_WORKERS, thread_name_prefix="smolcrawl-job"
)
_JOBS: Dict[str, dict] = {}
_JOBS_LOCK = threading.Lock()


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
        force_full_sync: bool = False

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
        """Route to job status display or submit a new crawl job."""
        # "jobs", "status", "queue" with no URL → show job registry
        if re.search(r'\b(jobs?|status|queue)\b', user_message, re.IGNORECASE):
            if not re.search(r'https?://', user_message):
                return self._show_jobs()

        url = self._extract_url(user_message)
        if not url:
            return (
                "Please provide a URL to crawl and a knowledge base name.\n\n"
                "Example: `crawl https://docs.example.com into My KB Name`\n\n"
                "To check running jobs: `jobs`"
            )

        kb_name = self._extract_kb_name(user_message, url)
        return self._submit_job(url, kb_name)

    # Seconds between batched progress emits
    _PROGRESS_INTERVAL = 15
    # Seconds before a heartbeat is emitted when nothing happened
    _HEARTBEAT_INTERVAL = 20

    # ── Job submission & streaming ────────────────────────────────────────────

    def _submit_job(self, url: str, kb_name: str) -> Generator[str, None, None]:
        """Register a job, hand it to the module-level executor, stream its output.

        Returns immediately with a streaming generator. The job runs concurrently
        with any other queued jobs in _JOB_EXECUTOR. Closing the chat window does
        not cancel the job — it keeps running in the background.
        """
        jid = uuid.uuid4().hex[:8]
        event_queue: queue.Queue = queue.Queue()
        job: dict = {
            "id": jid,
            "url": url,
            "kb_name": kb_name,
            "status": "queued",   # queued | running | done | error
            "queue": event_queue,
            "submitted_at": time.time(),
            "finished_at": None,
            "summary": "",
        }
        with _JOBS_LOCK:
            _JOBS[jid] = job

        _JOB_EXECUTOR.submit(self._run_job, job)

        # Immediate header — user sees this before the job even starts
        yield "## SmolCrawl Pipeline\n\n"
        yield f"**Job ID:** `{jid}`\n"
        yield f"**Target:** {url}\n"
        yield f"**Knowledge Base:** {kb_name}\n"
        yield f"**Max Pages:** {self.valves.max_pages}\n\n"
        if self.valves.force_full_sync:
            yield "**Mode:** Force full sync (ignore manifest cache)\n\n"

        # Stream this job's events until it finishes (or the connection drops)
        yield from self._stream_job(job)

    def _stream_job(self, job: dict) -> Generator[str, None, None]:
        """Consume text events from a job's queue and yield them to the caller.

        Heartbeats are injected when the job is slow so the SSE connection stays
        alive. If the caller drops the connection the job continues running.
        """
        q = job["queue"]
        stream_start = time.monotonic()
        last_heartbeat = stream_start

        while True:
            try:
                item = q.get(timeout=1.0)
            except queue.Empty:
                if job["status"] in ("done", "error"):
                    break
                now = time.monotonic()
                if now - last_heartbeat >= self._HEARTBEAT_INTERVAL:
                    elapsed = int(now - stream_start)
                    yield f"⏳ Running… ({elapsed}s)\n"
                    last_heartbeat = now
                continue

            if item is None:  # sentinel — job finished
                break
            yield item        # raw markdown string already formatted by _execute_pipeline

    def _run_job(self, job: dict) -> None:
        """Entry point executed by the thread-pool worker.

        Transitions job status, calls _execute_pipeline, and posts the sentinel.
        """
        job["status"] = "running"
        q = job["queue"]

        def emit(text: str) -> None:
            q.put(text)

        try:
            summary = self._execute_pipeline(job["url"], job["kb_name"], emit)
            job["summary"] = summary
            job["status"] = "done"
        except Exception as e:
            job["status"] = "error"
            job["summary"] = f"Error: {e}"
            emit(f"\n**Fatal error:** {e}\n")
        finally:
            job["finished_at"] = time.time()
            q.put(None)  # sentinel — tells _stream_job to stop

    # ── Core pipeline logic ───────────────────────────────────────────────────

    def _execute_pipeline(self, url: str, kb_name: str, emit) -> str:
        """Run the full crawl → augment → upload pipeline.

        Uses emit(str) instead of yield so it can run inside a thread-pool worker
        rather than being driven by a generator. Progress updates are batched at
        _PROGRESS_INTERVAL cadence; heartbeats fire at _HEARTBEAT_INTERVAL.

        Returns a one-line summary string stored in the job registry.
        """
        from smolcrawl.crawl import crawl_target_sync
        from smolcrawl.augment import augment_pages
        from smolcrawl.owui_client import OwuiConfig, OwuiKnowledgeClient

        log = logging.getLogger("smolcrawl_pipeline")
        log.info("[job] starting url=%s kb=%s", url, kb_name)

        # ── Phase 1: Crawl (inner thread so we can emit heartbeats) ──────────
        emit("### Phase 1: Crawling\n\n")
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

        crawl_thread = threading.Thread(target=crawl_worker, daemon=True)
        crawl_thread.start()

        pages = None
        last_count = 0
        last_emit = time.monotonic()
        start_time = time.monotonic()

        while True:
            try:
                item = crawl_queue.get(timeout=1.0)
            except queue.Empty:
                if time.monotonic() - last_emit >= self._HEARTBEAT_INTERVAL:
                    elapsed = int(time.monotonic() - start_time)
                    emit(f"⏳ Crawling… {last_count} pages ({elapsed}s)\n")
                    last_emit = time.monotonic()
                continue

            if item[0] == "progress":
                last_count = item[1]
                if time.monotonic() - last_emit >= self._PROGRESS_INTERVAL:
                    elapsed = int(time.monotonic() - start_time)
                    emit(f"Crawled **{last_count}** pages so far ({elapsed}s)\n")
                    last_emit = time.monotonic()
            elif item[0] == "done":
                pages = item[1]
                elapsed = int(time.monotonic() - start_time)
                emit(f"\n✅ Crawled **{len(pages)}** pages in {elapsed}s.\n\n")
                break
            elif item[0] == "error":
                raise RuntimeError(f"Crawl failed: {item[1]}")

        crawl_thread.join(timeout=5)

        if not pages:
            emit("No pages found. Check the URL and try again.\n")
            return "No pages found."

        # ── Phase 2: Augment ─────────────────────────────────────────────────
        if self.valves.augment_for_rag:
            emit("### Phase 2: Augmenting for RAG\n\n")
            try:
                pages = augment_pages(pages)
                emit(f"✅ Augmented **{len(pages)}** pages.\n\n")
            except Exception as e:
                emit(f"⚠️ Augmentation failed ({e}), uploading raw content.\n\n")

        # ── Phase 3: Upload (inner thread so we can emit heartbeats) ─────────
        emit("### Phase 3: Uploading to Knowledge Base\n\n")
        config = OwuiConfig(
            base_url=self.valves.owui_base_url,
            api_key=self.valves.owui_api_key,
            knowledge_base_name=kb_name,
            upload_concurrency=self.valves.upload_concurrency,
        )

        if not config.api_key.strip():
            emit(
                "**Upload error:** `owui_api_key` is not configured for this pipeline. "
                "Open WebUI knowledge and file APIs require a bearer token in this stack.\n\n"
                "Set `owui_api_key` in the SmolCrawl pipeline valves, then rerun the crawl.\n"
            )
            raise RuntimeError("owui_api_key not configured")

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
                        force_full_sync=self.valves.force_full_sync,
                    )
                    result_holder.append(result)
            except Exception as e:
                error_holder.append(str(e))
            finally:
                progress_queue.put(None)  # sentinel

        upload_thread = threading.Thread(target=upload_worker, daemon=True)
        upload_thread.start()

        last_upload_count = 0
        upload_total = len(pages)
        last_emit = time.monotonic()
        upload_start = time.monotonic()

        while True:
            try:
                item = progress_queue.get(timeout=1.0)
            except queue.Empty:
                if time.monotonic() - last_emit >= self._HEARTBEAT_INTERVAL:
                    elapsed = int(time.monotonic() - upload_start)
                    emit(f"⏳ Uploading… {last_upload_count}/{upload_total} ({elapsed}s)\n")
                    last_emit = time.monotonic()
                continue

            if item is None:
                break
            _, cur, tot, name = item
            last_upload_count = cur
            if time.monotonic() - last_emit >= self._PROGRESS_INTERVAL or cur == tot:
                elapsed = int(time.monotonic() - upload_start)
                emit(f"Uploaded **{cur}/{tot}** files ({elapsed}s)\n")
                last_emit = time.monotonic()

        upload_thread.join(timeout=60)

        if error_holder:
            raise RuntimeError(error_holder[0])

        total_elapsed = int(time.monotonic() - start_time)
        if result_holder:
            result = result_holder[0]
            if result.errors:
                raise RuntimeError(result.errors[0])
            emit(f"\n### ✅ Complete in {total_elapsed}s\n\n")
            emit("| Metric | Value |\n|--------|-------|\n")
            emit(f"| Pages crawled | {len(pages)} |\n")
            emit(f"| Files uploaded | {result.uploaded} |\n")
            emit(f"| Skipped (unchanged) | {result.skipped} |\n")
            emit(f"| Failures | {result.failed} |\n")
            emit(f"| Knowledge Base | {kb_name} |\n")
            return (
                f"uploaded={result.uploaded} skipped={result.skipped} "
                f"failed={result.failed} kb={kb_name}"
            )
        else:
            emit(f"\n**Upload completed** in {total_elapsed}s (no result details available).\n")
            return f"completed in {total_elapsed}s kb={kb_name}"

    # ── Job status display ────────────────────────────────────────────────────

    def _show_jobs(self) -> str:
        """Return a markdown summary of all submitted jobs."""
        with _JOBS_LOCK:
            jobs = list(_JOBS.values())

        if not jobs:
            return (
                "No crawl jobs have been submitted yet.\n\n"
                "Example: `crawl https://docs.example.com into My KB Name`"
            )

        icon = {"queued": "🕐", "running": "🔄", "done": "✅", "error": "❌"}
        lines = [f"## SmolCrawl Job Queue ({len(jobs)} total)\n\n"]
        for job in sorted(jobs, key=lambda j: j["submitted_at"], reverse=True):
            if job["finished_at"]:
                timing = f"{int(job['finished_at'] - job['submitted_at'])}s"
            else:
                timing = f"running {int(time.time() - job['submitted_at'])}s"
            summary = f" — {job['summary']}" if job["summary"] else ""
            lines.append(
                f"- {icon.get(job['status'], '❓')} **`{job['id']}`** "
                f"{job['url']} → _{job['kb_name']}_ ({timing}){summary}\n"
            )

        lines.append("\n_Submit a crawl: `crawl https://... into KB Name`_\n")
        return "".join(lines)

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
