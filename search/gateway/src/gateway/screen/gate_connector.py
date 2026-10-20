"""The search gate as a CONNECTOR (security plan step 2's contract, connector_kit).

Two connector classes, one intake identity (`search-gate`, the name the gate's intake key carries):

  SnippetConnector  source `web-search`: one item per search result, raw = "<title>\\n<snippet>"
  PageConnector     source `web-page`:   one item per fetched page, raw = the page's bytes,
                    origin = the requested URL, redirect_chain = every hop after it (the gate's
                    guarded fetch reports them; PLAN-connectors / connector-template F2)

Each is built with the items the gate already holds and only COLLECTS them: no socket, no DB, no
model - the gate fetched the pages through its guarded egress (gateway/egress/) BEFORE, outside
this module, which is why this file imports nothing but the kit and the standard library (the
kit's static scan reads it). Envelopes are built by the kit (intake's stage-1 schema + provenance)
and sent by the kit's IntakeClient; `screen/connector.py` runs that per request.

`external_id` is the URL (stable, so a repeat is a repeat at intake); one longer than intake's
1024-character field becomes its sha256. An origin or hop over the kit's 2048-character locator
cap is recorded as its sha256 too.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from connector_kit import Connector, ConnectorConfig, Item

CONNECTOR_ID = "search-gate"
VERSION = "1"
MAX_EXTERNAL_ID = 1024
MAX_LOCATOR = 2048


def _hashed(url: str) -> str:
    return "url-sha256:" + hashlib.sha256(url.encode("utf-8", "surrogatepass")).hexdigest()


def external_id_for(url: str) -> str:
    """A URL is the item's identity; one too long for intake's field becomes its sha256."""
    if 0 < len(url) <= MAX_EXTERNAL_ID and url.isprintable():
        return url
    return _hashed(url)


def locator(url: str) -> str:
    return url if 0 < len(url) <= MAX_LOCATOR and url.isprintable() else _hashed(url)


@dataclass(frozen=True)
class SnippetInput:
    url: str
    title: str
    snippet: str
    engine: str


@dataclass(frozen=True)
class PageInput:
    requested_url: str
    final_url: str
    redirects: tuple[str, ...]
    content_type: str
    body: bytes
    http_status: int
    via: str
    fetched_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.UTC))


class _Inputs:
    """The items the gate already holds, handed to the connector at construction."""

    def __init__(self, config: ConnectorConfig, inputs: Iterable[object] = ()) -> None:
        super().__init__(config)  # type: ignore[call-arg]  # Connector.__init__
        self.inputs = tuple(inputs)


class SnippetConnector(_Inputs, Connector):
    id = CONNECTOR_ID
    source = "web-search"
    version = VERSION

    def collect(self) -> Iterable[Item]:
        for s in self.inputs:
            assert isinstance(s, SnippetInput)
            title = " ".join(s.title.split())
            yield Item(
                external_id=external_id_for(s.url),
                content_type="text/plain; charset=utf-8",
                raw=f"{title}\n{s.snippet}".encode("utf-8", "replace"),
                origin=locator(s.url),
                metadata={"kind": "search-result", "engine": s.engine},
            )


class PageConnector(_Inputs, Connector):
    id = CONNECTOR_ID
    source = "web-page"
    version = VERSION

    def collect(self) -> Iterable[Item]:
        for p in self.inputs:
            assert isinstance(p, PageInput)
            meta: Mapping[str, object] = {"kind": "web-page", "final_url": locator(p.final_url),
                                          "http_status": p.http_status, "via": p.via}
            yield Item(
                external_id=external_id_for(p.final_url),
                content_type=p.content_type,
                raw=p.body,
                fetched_at=p.fetched_at,
                origin=locator(p.requested_url),
                redirect_chain=tuple(locator(u) for u in p.redirects),
                metadata=meta,
            )
