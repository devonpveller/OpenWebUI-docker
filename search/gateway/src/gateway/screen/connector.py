"""THE ONE MODULE BOUNDARY between the gate and intake (security plan step 2, connector-template).

Everything the gate knows about intake is in this file: the envelope it builds (EXACTLY intake's
stage-1 schema, OB1 integrations/intake/intake/envelope.py - REQUIRED source, connector,
external_id, fetched_at, content_type, raw; OPTIONAL raw_encoding, metadata, links; nothing else),
the POST to /v1/intake with the gate's own caller key, and how each answer maps to an ``Outcome``.
The rest of the gate sees only ``Item`` in and ``Outcome`` out.

STATUS: interim. The connector-template kit (envelope builder, bounded client, fake intake) is
being built in parallel; when it lands, ``IntakeConnector`` becomes a thin adapter over the kit's
client and this file's own builder goes. Nothing outside this file changes.

What the answers mean (intake_service/core.py + web.py):
  200  a result. ``label`` clean | suspicious | pending | injected (and ``text`` only for
       clean/suspicious). The gate's read policy keeps only clean.
  422  stage 1 rejected the envelope (``rejected``).
  408 / 503  transient (busy, no worker, store down): ONE retry inside the deadline, then ``error``.
  400 / 401 / 403 / 413  the gate's own fault or configuration: ``error``, never retried.
Every outcome that is not clean is withheld by the caller of this module. A failure is never clean.
"""

from __future__ import annotations

import asyncio
import base64
import datetime as dt
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from gateway.logging import get_logger

log = get_logger("gateway.screen")

CONNECTOR = "search-gate"
SOURCE_SNIPPET = "web-search"
SOURCE_PAGE = "web-page"
MAX_EXTERNAL_ID = 1024  # intake envelope.py: external_id 1..1024 characters
MAX_REPLY_BYTES = 16 * 1024 * 1024
LABELS = ("clean", "suspicious", "pending", "injected", "rejected", "error")


@dataclass(frozen=True)
class Item:
    source: str
    external_id: str
    content_type: str
    raw: bytes
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Outcome:
    label: str  # one of LABELS
    text: str | None = None
    title: str | None = None
    labels: dict[str, Any] = field(default_factory=dict)
    detail: str = ""

    @property
    def readable(self) -> bool:
        return self.label in ("clean", "internal") and self.text is not None


class Connector(Protocol):
    async def submit(self, item: Item) -> Outcome: ...

    async def aclose(self) -> None: ...


def external_id_for(url: str) -> str:
    """A URL is the item's identity; one too long for intake's field becomes its sha256."""
    if 0 < len(url) <= MAX_EXTERNAL_ID and url.isprintable():
        return url
    return "url-sha256:" + hashlib.sha256(url.encode("utf-8", "surrogatepass")).hexdigest()


def build_envelope(item: Item, *, now: dt.datetime | None = None) -> dict[str, Any]:
    """Intake's stage-1 envelope for one item (raw always base64, so no byte is re-encoded)."""
    fetched_at = (now or dt.datetime.now(dt.UTC)).isoformat(timespec="seconds")
    return {
        "source": item.source,
        "connector": CONNECTOR,
        "external_id": item.external_id,
        "fetched_at": fetched_at,
        "content_type": item.content_type,
        "raw": base64.b64encode(item.raw).decode("ascii"),
        "raw_encoding": "base64",
        "metadata": dict(item.metadata),
        "links": [],
    }


class IntakeConnector:
    def __init__(self, url: str, key: str, timeout_s: float) -> None:
        self._url = url
        self._timeout_s = timeout_s
        self._client = httpx.AsyncClient(
            trust_env=False,  # never a proxy: intake is internal
            follow_redirects=False,
            timeout=httpx.Timeout(timeout_s),
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        )

    async def _post_once(self, body: bytes) -> tuple[int, dict[str, Any] | None]:
        async with self._client.stream("POST", self._url, content=body) as r:
            buf = bytearray()
            async for chunk in r.aiter_bytes():
                buf += chunk
                if len(buf) > MAX_REPLY_BYTES:
                    return r.status_code, None
            try:
                parsed = json.loads(bytes(buf))
            except ValueError:
                parsed = None
            return r.status_code, parsed if isinstance(parsed, dict) else None

    async def submit(self, item: Item) -> Outcome:
        body = json.dumps(build_envelope(item), ensure_ascii=True).encode("ascii")
        try:
            async with asyncio.timeout(self._timeout_s):
                status, reply = await self._post_once(body)
                if status in (408, 503):
                    await asyncio.sleep(0.5)
                    status, reply = await self._post_once(body)
        except (httpx.HTTPError, TimeoutError) as e:
            log.warning("intake_unreachable", error=type(e).__name__)
            return Outcome("error", detail="screening unavailable")
        if status == 422:
            return Outcome("rejected", detail="intake rejected the item")
        if status != 200 or reply is None:
            log.warning("intake_status", status=status)
            return Outcome("error", detail=f"screening failed ({status})")
        label = reply.get("label")
        if label not in LABELS:
            return Outcome("error", detail="screening returned an unknown label")
        labels = reply.get("labels") if isinstance(reply.get("labels"), dict) else {}
        text = reply.get("text") if isinstance(reply.get("text"), str) else None
        title = reply.get("title") if isinstance(reply.get("title"), str) else None
        return Outcome(label, text=text if label == "clean" else None,
                       title=title if label == "clean" else None, labels=labels)

    async def aclose(self) -> None:
        await self._client.aclose()


class Unavailable:
    """No intake key configured: every item is ``error`` (screened routes fail closed)."""

    async def submit(self, item: Item) -> Outcome:
        return Outcome("error", detail="screening is not configured")

    async def aclose(self) -> None:
        return None
