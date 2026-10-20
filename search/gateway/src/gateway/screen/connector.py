"""THE ONE MODULE BOUNDARY between the gate and intake: the connector kit (security plan step 2).

The gate's connectors (screen/gate_connector.py) collect; the kit (connector_kit, vendored from OB1,
see src/connector_kit/VENDORED.md) builds each envelope - intake's stage-1 schema exactly, plus
metadata.provenance (connector, version, fetched_at, origin, redirect_chain, labels) - and its
IntakeClient sends it: the key from a FILE (INTAKE_KEY_FILE), the envelope re-checked right before
sending, one deadline per attempt, no proxy, no redirect, a capped reply. The gate opts in to
`return_clean_text`: a `clean` answer carries intake's text, title and labels; nothing else does.

The kit is synchronous (http.client, a watchdog thread); each submission runs in a worker thread
(asyncio.to_thread) so the event loop is never blocked. The kit's retry policy is narrowed for a
request path: 2 attempts (408/503/no answer retried once, Retry-After capped at 2 s) within
INTAKE_TIMEOUT_SECONDS.

How each kit answer maps to the gate's Outcome:
  record + clean (with text)            clean       -> returned, fenced
  record + clean, no text               unreadable  (intake withheld it: a repeat whose evidence
                                                     differs) -> withheld
  record + suspicious / pending / injected / rejected -> that label -> withheld
  an envelope the kit refuses (stage 1)  rejected   -> withheld (nothing was sent)
  stop (401/403/413/422/400/...) or deferred (retries spent)  error -> withheld; logged with the
                                                     HTTP status, never the key or the URL
A failure is never clean.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from connector_kit import ConnectorConfig, IntakeClient, RequestTooLarge, RetryPolicy, load_key_file
from gateway.logging import get_logger
from gateway.screen.gate_connector import (
    CONNECTOR_ID,
    PageConnector,
    PageInput,
    SnippetConnector,
    SnippetInput,
)

log = get_logger("gateway.screen")

LABELS = ("clean", "suspicious", "pending", "injected", "rejected", "unreadable", "error")


@dataclass(frozen=True)
class Outcome:
    label: str  # one of LABELS
    text: str | None = None
    title: str | None = None
    labels: dict[str, Any] = field(default_factory=dict)
    detail: str = ""

    @property
    def readable(self) -> bool:
        return self.label == "clean" and self.text is not None


class Connector(Protocol):
    async def snippets(self, inputs: Sequence[SnippetInput]) -> list[Outcome]: ...

    async def pages(self, inputs: Sequence[PageInput]) -> list[Outcome]: ...

    async def aclose(self) -> None: ...


def gate_retry(timeout_s: float) -> RetryPolicy:
    return RetryPolicy(max_attempts=2, base_s=0.5, cap_s=1.0, retry_after_cap_s=2.0,
                       budget_s=timeout_s)


class IntakeConnector:
    """The kit's client with the gate's key; one per process (the client holds no connection)."""

    def __init__(self, url: str, key_file: str, timeout_s: float, concurrency: int = 4) -> None:
        self._sem = asyncio.Semaphore(concurrency)
        self.config = ConnectorConfig(intake_url=url, key_file=key_file, timeout_s=timeout_s,
                                      retry=gate_retry(timeout_s))
        self.client = IntakeClient(url, load_key_file(key_file), connector=CONNECTOR_ID,
                                   timeout_s=timeout_s, retry=self.config.retry,
                                   return_clean_text=True)

    async def snippets(self, inputs: Sequence[SnippetInput]) -> list[Outcome]:
        return await self._run(SnippetConnector(self.config, inputs))

    async def pages(self, inputs: Sequence[PageInput]) -> list[Outcome]:
        return await self._run(PageConnector(self.config, inputs))

    async def _run(self, conn: SnippetConnector | PageConnector) -> list[Outcome]:
        prepared = list(conn.envelopes())  # the kit builds and checks; nothing is sent yet
        return list(await asyncio.gather(*(self._one(p) for p in prepared)))

    async def _one(self, p: Any) -> Outcome:
        if p.envelope is None:
            log.warning("intake_envelope_refused", reason=(p.refusal or "")[:40].split(":")[0])
            return Outcome("rejected", detail="the kit refused the envelope")
        try:
            async with self._sem:
                sub = await asyncio.to_thread(self.client.submit, p.envelope)
        except RequestTooLarge:
            return Outcome("rejected", detail="over intake's request cap")
        o = sub.outcome
        if sub.final != "record" or o.label is None:
            log.warning("intake_not_recorded", result=sub.final, http=o.http, attempts=sub.attempts)
            return Outcome("error", detail="screening failed")
        extra = dict(o.extra or {})
        if o.label == "clean":
            text = extra.get("text")
            if not isinstance(text, str):
                return Outcome("unreadable", detail="intake withheld the text")
            title = extra.get("title")
            labels = extra.get("labels")
            return Outcome("clean", text=text, title=title if isinstance(title, str) else None,
                           labels=dict(labels) if isinstance(labels, dict) else {})
        return Outcome(o.label)

    async def aclose(self) -> None:
        return None


class Unavailable:
    """No intake key file: every item is ``error`` (the screened routes answer 503 before this)."""

    async def snippets(self, inputs: Sequence[SnippetInput]) -> list[Outcome]:
        return [Outcome("error", detail="screening is not configured") for _ in inputs]

    async def pages(self, inputs: Sequence[PageInput]) -> list[Outcome]:
        return [Outcome("error", detail="screening is not configured") for _ in inputs]

    async def aclose(self) -> None:
        return None


def make_connector(url: str, key_file: str, timeout_s: float,
                   concurrency: int = 4) -> tuple[Connector, bool]:
    """The intake connector, or Unavailable when no key file is configured or present (the plane
    ships without one: today's posture). A key file that exists but is malformed RAISES - a
    misconfigured gate never starts."""
    if not key_file or not Path(key_file).exists():
        return Unavailable(), False
    return IntakeConnector(url, key_file, timeout_s, concurrency), True
