"""
title: SmolCrawl Knowledge Builder
author: smolcrawl
date: 2026-04-11
version: 2.3
license: MIT
description: >
  Crawl a website, augment markdown for RAG, and upload to an OWUI knowledge
  collection. Streams progress in chat. Concurrent jobs via a module-level
  thread pool. Persistent job state per collection enables resume after
  interruption and staleness detection by date.
requirements: httpx, markdownify, readabilipy, beautifulsoup4, lxml
"""

import json
import logging
import os
import queue
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Callable, Dict, Generator, Iterator, List, Optional, Union
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

# ── Persistent job state ──────────────────────────────────────────────────────
# One JSON file per KB collection stored at _JOB_STATE_DIR.
# Enables resume-after-interruption and staleness detection across restarts.
_JOB_STATE_DIR = "/app/smolcrawl-data/job-state"
# A job whose last_active is older than this (seconds) is considered crashed.
_STALE_LOCK_SECONDS = 120


def _kb_slug(kb_name: str) -> str:
    """Convert a KB name to a safe filesystem slug."""
    return re.sub(r"[^\w-]", "_", kb_name).lower()


def _load_job_state(kb_name: str) -> Optional[dict]:
    """Load persisted job state for a collection, or None if absent/corrupt."""
    path = os.path.join(_JOB_STATE_DIR, f"{_kb_slug(kb_name)}.json")
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _save_job_state(kb_name: str, state: dict) -> None:
    """Atomically persist job state for a collection (write-then-rename)."""
    os.makedirs(_JOB_STATE_DIR, exist_ok=True)
    path = os.path.join(_JOB_STATE_DIR, f"{_kb_slug(kb_name)}.json")
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2)
        os.replace(tmp, path)
    except OSError as exc:
        logging.getLogger("smolcrawl_pipeline").warning(
            "Could not save job state for %r: %s", kb_name, exc
        )


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
        job_ttl_days: int = 7  # days before a completed job is considered stale

    def __init__(self):
        self.name = "SmolCrawl Knowledge Builder"
        self.valves = self.Valves()

    async def on_startup(self):
        """Verify smolcrawl is importable."""
        try:
            import smolcrawl  # noqa: F401
        except ImportError:
            print(
                "[SmolCrawl Pipeline] WARNING: smolcrawl package not found. "
                "Install with: pip install smolcrawl"
            )

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
        if re.search(r"\b(jobs?|status|queue)\b", user_message, re.IGNORECASE):
            if not re.search(r"https?://", user_message):
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
    # Seconds between flushing upload cursor to disk
    _STATE_FLUSH_INTERVAL = 30

    # ── Job submission ─────────────────────────────────────────────────────────

    def _submit_job(self, url: str, kb_name: str) -> Generator[str, None, None]:
        """Classify the request against persisted state, then dispatch to the
        thread pool. Yields a streaming response immediately.

        Classification modes
        --------------------
        fresh          No prior state for this collection.
        resume         Prior job was interrupted or errored; manifest has partial
                       progress that sync_pages will skip automatically.
        up_to_date     Prior sync completed recently (< job_ttl_days ago).
        stale          Prior sync is old (>= job_ttl_days); source may have changed.
        url_mismatch   Collection was previously synced from a different URL.
        already_running A live in-memory job for this collection exists.
        """
        existing = _load_job_state(kb_name)
        now = datetime.now(timezone.utc)

        mode = "fresh"
        resume_stats: dict = {}

        if existing:
            prev_url = existing.get("url", "")
            prev_status = existing.get("status", "unknown")
            last_active_str = existing.get("last_active")
            finished_at_str = existing.get("finished_at")

            if prev_url and prev_url != url:
                mode = "url_mismatch"

            elif prev_status in ("running", "queued"):
                stale_lock = True
                if last_active_str:
                    try:
                        last_active = datetime.fromisoformat(last_active_str)
                        stale_lock = (
                            (now - last_active).total_seconds() >= _STALE_LOCK_SECONDS
                        )
                    except ValueError:
                        pass
                if stale_lock:
                    mode = "resume"
                    resume_stats = {
                        "cursor": existing.get("progress_cursor", 0),
                        "total": existing.get("total_pages", 0),
                    }
                else:
                    mode = "already_running"

            elif prev_status in ("interrupted", "error"):
                mode = "resume"
                resume_stats = {
                    "cursor": existing.get("progress_cursor", 0),
                    "total": existing.get("total_pages", 0),
                }

            elif prev_status == "done" and finished_at_str:
                try:
                    finished_at = datetime.fromisoformat(finished_at_str)
                    age_secs = (now - finished_at).total_seconds()
                    age_days = age_secs / 86400
                    if age_days >= self.valves.job_ttl_days:
                        mode = "stale"
                        resume_stats = {"age_days": int(age_days)}
                    else:
                        mode = "up_to_date"
                        resume_stats = {"age_hours": int(age_secs / 3600)}
                except ValueError:
                    pass

        # ── Handle already_running ────────────────────────────────────────────
        if mode == "already_running":
            with _JOBS_LOCK:
                live = next(
                    (
                        j for j in _JOBS.values()
                        if j["kb_name"] == kb_name
                        and j["status"] in ("running", "queued")
                    ),
                    None,
                )
            if live:
                yield "## SmolCrawl Pipeline\n\n"
                yield (
                    f"⚠️ Job **`{live['id']}`** for _{kb_name}_ is already running. "
                    "Attaching to its stream…\n\n"
                )
                yield from self._stream_job(live)
                return
            else:
                # State file claims running but no live job → crashed
                mode = "resume"
                resume_stats = {
                    "cursor": existing.get("progress_cursor", 0) if existing else 0,
                    "total": existing.get("total_pages", 0) if existing else 0,
                }

        # ── Create job record ─────────────────────────────────────────────────
        jid = uuid.uuid4().hex[:8]
        event_queue: queue.Queue = queue.Queue()
        job: dict = {
            "id": jid,
            "url": url,
            "kb_name": kb_name,
            "status": "queued",
            "mode": mode,
            "queue": event_queue,
            "submitted_at": time.time(),
            "finished_at": None,
            "summary": "",
        }
        with _JOBS_LOCK:
            _JOBS[jid] = job

        now_iso = now.isoformat()
        _save_job_state(
            kb_name,
            {
                "version": 1,
                "kb_name": kb_name,
                "url": url,
                "status": "queued",
                "submitted_at": now_iso,
                "started_at": None,
                "finished_at": None,
                "last_active": now_iso,
                "last_job_id": jid,
                # Carry forward progress from a prior interrupted run so the
                # resume message can show meaningful numbers.
                "total_pages": existing.get("total_pages", 0) if existing else 0,
                "progress_cursor": existing.get("progress_cursor", 0) if mode == "resume" else 0,
                "uploaded": existing.get("uploaded", 0) if mode == "resume" else 0,
                "skipped": 0,
                "failed": 0,
            },
        )

        _JOB_EXECUTOR.submit(self._run_job, job)

        # ── Stream header ─────────────────────────────────────────────────────
        yield "## SmolCrawl Pipeline\n\n"
        yield f"**Job ID:** `{jid}`\n"
        yield f"**Target:** {url}\n"
        yield f"**Knowledge Base:** {kb_name}\n"
        yield f"**Max Pages:** {self.valves.max_pages}\n\n"

        if mode == "resume":
            cursor = resume_stats.get("cursor", 0)
            total = resume_stats.get("total", 0)
            detail = f" ({cursor}/{total} pages already processed)" if total else ""
            yield (
                f"▶️ **Resuming** interrupted job{detail}. "
                "Manifest deduplication will skip already-uploaded pages.\n\n"
            )
        elif mode == "up_to_date":
            h = resume_stats.get("age_hours", 0)
            yield f"ℹ️ Last synced **{h}h ago** — checking for changed pages.\n\n"
        elif mode == "stale":
            d = resume_stats.get("age_days", 0)
            yield (
                f"⚠️ Last synced **{d} day(s) ago** "
                f"(threshold: {self.valves.job_ttl_days}d) — source may have changed. "
                "Re-crawling.\n\n"
            )
        elif mode == "url_mismatch":
            prev_url = existing.get("url", "") if existing else ""
            yield (
                f"⚠️ This collection was previously synced from a different URL:\n"
                f"- Previous: `{prev_url}`\n"
                f"- New: `{url}`\n\n"
                "Starting fresh sync.\n\n"
            )

        if self.valves.force_full_sync:
            yield "**Mode:** Force full sync (ignore manifest cache)\n\n"

        yield from self._stream_job(job)

    def _stream_job(self, job: dict) -> Generator[str, None, None]:
        """Consume events from a job's queue, injecting heartbeats to keep the
        SSE connection alive. Dropping the connection does not cancel the job.
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
                    yield f"⏳ Running… ({int(now - stream_start)}s)\n"
                    last_heartbeat = now
                continue

            if item is None:  # sentinel posted by _run_job
                break
            yield item

    def _run_job(self, job: dict) -> None:
        """Thread-pool entry point. Manages the persisted state lifecycle."""
        kb_name = job["kb_name"]
        job["status"] = "running"
        q = job["queue"]

        def emit(text: str) -> None:
            q.put(text)

        # Mark running on disk
        state = _load_job_state(kb_name) or {}
        now_iso = datetime.now(timezone.utc).isoformat()
        state.update({"status": "running", "started_at": now_iso, "last_active": now_iso})
        _save_job_state(kb_name, state)

        def on_state_update(
            total_pages: int = 0,
            progress_cursor: int = 0,
            uploaded: int = 0,
            skipped: int = 0,
            failed: int = 0,
        ) -> None:
            """Write incremental progress to disk so crashes can be resumed."""
            s = _load_job_state(kb_name) or {}
            if total_pages:
                s["total_pages"] = total_pages
            if progress_cursor:
                s["progress_cursor"] = progress_cursor
            if uploaded:
                s["uploaded"] = uploaded
            if skipped:
                s["skipped"] = skipped
            if failed:
                s["failed"] = failed
            s["last_active"] = datetime.now(timezone.utc).isoformat()
            _save_job_state(kb_name, s)

        try:
            summary = self._execute_pipeline(job["url"], kb_name, emit, on_state_update)
            job["summary"] = summary
            job["status"] = "done"
            s = _load_job_state(kb_name) or {}
            s.update(
                {
                    "status": "done",
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                    "last_active": datetime.now(timezone.utc).isoformat(),
                }
            )
            _save_job_state(kb_name, s)

        except Exception as exc:
            job["status"] = "error"
            job["summary"] = f"Error: {exc}"
            emit(f"\n**Fatal error:** {exc}\n")
            # Mark as "interrupted" (not "error") so the next request resumes
            # rather than treating it as a permanent failure.
            s = _load_job_state(kb_name) or {}
            s.update(
                {
                    "status": "interrupted",
                    "last_active": datetime.now(timezone.utc).isoformat(),
                }
            )
            _save_job_state(kb_name, s)

        finally:
            job["finished_at"] = time.time()
            q.put(None)  # sentinel

    # ── Core pipeline logic ────────────────────────────────────────────────────

    def _execute_pipeline(
        self,
        url: str,
        kb_name: str,
        emit: Callable[[str], None],
        on_state_update: Callable[..., None],
    ) -> str:
        """Run crawl → augment → upload, emitting progress via emit().

        Returns a one-line summary string stored in the job registry.
        """
        from smolcrawl.crawl import crawl_target_sync
        from smolcrawl.augment import augment_pages
        from smolcrawl.owui_client import OwuiConfig, OwuiKnowledgeClient

        log = logging.getLogger("smolcrawl_pipeline")
        log.info("[job] starting url=%s kb=%s", url, kb_name)

        # ── Phase 1: Crawl ─────────────────────────────────────────────────────
        emit("### Phase 1: Crawling\n\n")
        crawl_q: queue.Queue = queue.Queue()

        def crawl_worker():
            try:
                intensity = self.valves.server_intensity
                max_concurrent = max(1, int(1 + (intensity * 11)))
                delay = (1.0 - intensity) * 2.0

                def on_page(page_count, page_url):
                    crawl_q.put(("progress", page_count, page_url))

                result = crawl_target_sync(
                    url,
                    max_pages=self.valves.max_pages,
                    max_concurrent=max_concurrent,
                    delay=delay,
                    on_page_crawled=on_page,
                )
                crawl_q.put(("done", result))
            except Exception as exc:
                crawl_q.put(("error", str(exc)))

        crawl_thread = threading.Thread(target=crawl_worker, daemon=True)
        crawl_thread.start()

        pages = None
        last_count = 0
        last_emit = time.monotonic()
        start_time = time.monotonic()

        while True:
            try:
                item = crawl_q.get(timeout=1.0)
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

        # Record total page count as soon as we know it
        on_state_update(total_pages=len(pages))

        # ── Phase 2: Augment ───────────────────────────────────────────────────
        if self.valves.augment_for_rag:
            emit("### Phase 2: Augmenting for RAG\n\n")
            try:
                pages = augment_pages(pages)
                emit(f"✅ Augmented **{len(pages)}** pages.\n\n")
            except Exception as exc:
                emit(f"⚠️ Augmentation failed ({exc}), uploading raw content.\n\n")

        # ── Phase 3: Upload ────────────────────────────────────────────────────
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

        progress_q: queue.Queue = queue.Queue()
        result_holder: list = []
        error_holder: list = []

        def upload_worker():
            try:
                with OwuiKnowledgeClient(config) as client:
                    result = client.sync_pages(
                        pages,
                        kb_name,
                        on_progress=lambda cur, tot, name: progress_q.put(
                            ("progress", cur, tot, name)
                        ),
                        force_full_sync=self.valves.force_full_sync,
                    )
                    result_holder.append(result)
            except Exception as exc:
                error_holder.append(str(exc))
            finally:
                progress_q.put(None)  # sentinel

        upload_thread = threading.Thread(target=upload_worker, daemon=True)
        upload_thread.start()

        last_cursor = 0
        upload_total = len(pages)
        last_emit = time.monotonic()
        last_flush = time.monotonic()
        upload_start = time.monotonic()

        while True:
            try:
                item = progress_q.get(timeout=1.0)
            except queue.Empty:
                if time.monotonic() - last_emit >= self._HEARTBEAT_INTERVAL:
                    elapsed = int(time.monotonic() - upload_start)
                    emit(f"⏳ Uploading… {last_cursor}/{upload_total} ({elapsed}s)\n")
                    last_emit = time.monotonic()
                continue

            if item is None:
                break

            _, cur, tot, _ = item
            last_cursor = cur

            if time.monotonic() - last_emit >= self._PROGRESS_INTERVAL or cur == tot:
                elapsed = int(time.monotonic() - upload_start)
                emit(f"Uploaded **{cur}/{tot}** files ({elapsed}s)\n")
                last_emit = time.monotonic()

            # Flush cursor to disk periodically so crash recovery shows
            # meaningful "X/Y already processed" in the resume message.
            if time.monotonic() - last_flush >= self._STATE_FLUSH_INTERVAL:
                on_state_update(progress_cursor=cur)
                last_flush = time.monotonic()

        upload_thread.join(timeout=60)

        if error_holder:
            raise RuntimeError(error_holder[0])

        total_elapsed = int(time.monotonic() - start_time)
        if result_holder:
            result = result_holder[0]
            if result.errors:
                raise RuntimeError(result.errors[0])

            # Persist final accurate counts
            on_state_update(
                uploaded=result.uploaded,
                skipped=result.skipped,
                failed=result.failed,
                progress_cursor=len(pages),
            )

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
            emit(
                f"\n**Upload completed** in {total_elapsed}s (no result details available).\n"
            )
            return f"completed in {total_elapsed}s kb={kb_name}"

    # ── Job status display ─────────────────────────────────────────────────────

    def _show_jobs(self) -> str:
        """Return a markdown summary merging live in-memory jobs and disk history."""
        with _JOBS_LOCK:
            live = {
                j["kb_name"]: j
                for j in _JOBS.values()
                if j["status"] in ("running", "queued")
            }

        disk_states: Dict[str, dict] = {}
        try:
            if os.path.isdir(_JOB_STATE_DIR):
                for fname in sorted(os.listdir(_JOB_STATE_DIR)):
                    if not fname.endswith(".json"):
                        continue
                    path = os.path.join(_JOB_STATE_DIR, fname)
                    try:
                        with open(path, encoding="utf-8") as fh:
                            s = json.load(fh)
                        disk_states[s.get("kb_name", fname[:-5])] = s
                    except Exception:
                        pass
        except Exception:
            pass

        if not live and not disk_states:
            return (
                "No crawl jobs have been submitted yet.\n\n"
                "Example: `crawl https://docs.example.com into My KB Name`"
            )

        icon = {
            "queued": "🕐",
            "running": "🔄",
            "done": "✅",
            "error": "❌",
            "interrupted": "⏸️",
        }
        now = datetime.now(timezone.utc)
        lines: list = ["## SmolCrawl Job Registry\n\n"]

        # Active in-memory jobs
        if live:
            lines.append("### Active\n\n")
            for job in sorted(live.values(), key=lambda j: j["submitted_at"], reverse=True):
                elapsed = int(time.time() - job["submitted_at"])
                lines.append(
                    f"- {icon.get(job['status'], '❓')} **`{job['id']}`** "
                    f"{job['url']} → _{job['kb_name']}_ (running {elapsed}s)\n"
                )
            lines.append("\n")

        # Completed / interrupted states from disk
        history = {
            k: v
            for k, v in disk_states.items()
            if v.get("status") not in ("running", "queued")
        }
        if history:
            lines.append("### Collection History\n\n")
            lines.append("| Collection | Status | Last Sync | Progress | Source URL |\n")
            lines.append("|---|---|---|---|---|\n")
            for kb_name, s in sorted(history.items()):
                status = s.get("status", "unknown")
                ref_ts = s.get("finished_at") or s.get("last_active")
                age_str = "—"
                if ref_ts:
                    try:
                        dt = datetime.fromisoformat(ref_ts)
                        secs = (now - dt).total_seconds()
                        age_str = (
                            f"{int(secs / 3600)}h ago"
                            if secs < 172800
                            else f"{int(secs / 86400)}d ago"
                        )
                    except Exception:
                        pass
                cursor = s.get("progress_cursor", s.get("uploaded", 0))
                total = s.get("total_pages", "?")
                url_short = s.get("url", "—")
                if len(url_short) > 45:
                    url_short = url_short[:42] + "…"
                lines.append(
                    f"| {kb_name} | {icon.get(status, '❓')} {status} "
                    f"| {age_str} | {cursor}/{total} | `{url_short}` |\n"
                )
            lines.append("\n")

        lines.append("_Submit a crawl: `crawl https://... into KB Name`_\n")
        return "".join(lines)

    @staticmethod
    def _extract_kb_name(message: str, url: str) -> str:
        """Extract knowledge base name from the message, falling back to domain."""
        for pattern in [
            r'(?:into|to|as|kb:|knowledge[- ]?base[: ])\s*["\']?(.+?)["\']?\s*$',
            r'(?:into|to|as|kb:|knowledge[- ]?base[: ])\s*["\']?(.+?)["\']?(?:\s+(?:with|using|from))',
        ]:
            m = re.search(pattern, message, re.IGNORECASE)
            if m:
                name = m.group(1).strip().strip("\"'")
                if name and name != url:
                    return name
        return f"SmolCrawl - {urlparse(url).netloc}"

    @staticmethod
    def _extract_url(message: str) -> Optional[str]:
        """Extract the first URL from a user message."""
        url_pattern = re.compile(r"https?://[^\s<>'\")\]]+", re.IGNORECASE)
        match = url_pattern.search(message)
        if match:
            return match.group(0).rstrip(".,;:!?")
        return None
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
