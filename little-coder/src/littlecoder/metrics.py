"""Prometheus metrics endpoint (design §9.3).

Tool-era subset: queue depth, journal write rate, sanitization rejection
rate, llama-cpp slot occupancy, task counts. Later chapters layer more on the
same endpoint. Served on its own port (config `metrics.port`).
"""

from __future__ import annotations

import httpx
from prometheus_client import Counter, Gauge, Info, start_http_server

LC_INFO = Info("lc_build", "little-coder build info")
LC_QUEUE_DEPTH = Gauge("lc_queue_depth", "Tasks waiting in the FIFO queue")
LC_TASK_IN_FLIGHT = Gauge("lc_task_in_flight", "1 while a task is running")
LC_TASKS_TOTAL = Counter("lc_tasks_total", "Tasks completed", ["outcome"])
LC_JOURNAL_WRITTEN = Gauge(
    "lc_journal_records_written", "Journal records written (cumulative)"
)
LC_JOURNAL_REJECTED = Gauge(
    "lc_journal_records_rejected", "Malformed journal records rejected (cumulative)"
)
LC_SANITIZE_PROCESSED = Gauge(
    "lc_sanitization_processed", "Outbound items the filter scanned (cumulative)"
)
LC_SANITIZE_REDACTED = Gauge(
    "lc_sanitization_redacted",
    "Outbound items with >=1 redaction (cumulative) — feeds the §10.2 drift trigger",
)
LC_LLAMA_SLOTS_BUSY = Gauge("lc_llama_slots_busy", "Busy llama-cpp inference slots")


def start_metrics_server(port: int) -> None:
    """Start the Prometheus exposition server on its own thread."""
    start_http_server(port)


def set_build_info(version: str, chapter: str) -> None:
    LC_INFO.info({"version": version, "chapter": chapter})


def refresh(journals, sanitizer, queue_depth: int, task_in_flight: bool) -> None:
    """Mirror the cumulative counters held on the journal/sanitizer instances
    into the gauges. Called periodically by the daemon."""
    LC_JOURNAL_WRITTEN.set(journals.records_written)
    LC_JOURNAL_REJECTED.set(journals.records_rejected)
    LC_SANITIZE_PROCESSED.set(sanitizer.processed)
    LC_SANITIZE_REDACTED.set(sanitizer.redacted)
    LC_QUEUE_DEPTH.set(queue_depth)
    LC_TASK_IN_FLIGHT.set(1 if task_in_flight else 0)


def record_task(outcome: str) -> None:
    LC_TASKS_TOTAL.labels(outcome=outcome).inc()


def poll_llama_slots(base_url: str) -> None:
    """Best-effort scrape of llama-cpp's /slots — design §9.3 slot occupancy.
    A failure leaves the gauge at its last value rather than alarming."""
    root = base_url.rstrip("/")
    if root.endswith("/v1"):
        root = root[:-3]
    try:
        with httpx.Client(timeout=5.0) as c:
            slots = c.get(f"{root}/slots").json()
        busy = sum(1 for s in slots if isinstance(s, dict) and s.get("state", 0) != 0)
        LC_LLAMA_SLOTS_BUSY.set(busy)
    except (httpx.HTTPError, ValueError, TypeError):
        pass
