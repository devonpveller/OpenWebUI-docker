"""structlog configuration. PRIVACY: never log query strings or result URLs.

Spec §8 / §4.4.9:
  - Query strings must not be logged unless LOG_QUERIES=true, and even then
    only as a sha256 hash (never the plaintext).
  - Result URLs must not be logged at INFO level.
This module exposes ``query_fingerprint`` so call sites log a hash, not text.
"""

from __future__ import annotations

import hashlib
import logging
import sys

import structlog

_configured = False
_QUIET_LOGGERS = ("httpx", "httpcore", "hpack", "uvicorn.access", "mcp", "urllib3")


def configure_logging(level: str) -> None:
    global _configured
    if _configured:
        return

    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level.upper())
    # Libraries log through the standard tree, and httpx logs EVERY request line at INFO - the
    # engine URL with ?q=<query>, every fetched URL (search-gate round trip, 2026-10-19: the live
    # gateway's log carried each query that way, beside the uvicorn access log audit A3 named).
    # They never log below WARNING here, whatever LOG_LEVEL says.
    for noisy in _QUIET_LOGGERS:
        logging.getLogger(noisy).setLevel(max(logging.WARNING, logging.getLevelName(level.upper())))
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(level.upper())
        ),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    _configured = True


def get_logger(name: str = "gateway") -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)


def query_fingerprint(query: str) -> str:
    """sha256 of the query — the ONLY representation safe to log, and only
    when LOG_QUERIES=true. Truncated to 16 hex chars for readable logs."""
    return hashlib.sha256(query.encode("utf-8")).hexdigest()[:16]
