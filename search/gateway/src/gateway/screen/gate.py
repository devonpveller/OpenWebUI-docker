"""The gate's read policy over intake's labels (DESIGN-intake.md section 6; PLAN-connectors step 3).

Every search result (title + snippet) and every fetched page becomes one intake item (through the
connector kit: screen/connector.py, screen/gate_connector.py). What goes back to a caller:
  * ONLY items intake labelled ``clean`` (``internal`` is never an external item's label, so in
    practice clean). The gate's callers are model-facing and tool-using (OWUI, the research engine,
    openbrain-mcp): ``suspicious`` is withheld for them (design section 6 and operator D41), and
    ``pending`` (awaiting the second opinion), ``injected``, ``rejected`` and any failure are
    withheld always.
  * the text INTAKE returned (CDR-extracted, invisible characters stripped), never the raw bytes,
    wrapped in a per-response nonce fence ``<<UNTRUSTED-{nonce} src=... verdict=clean>>`` with
    intake's labels beside it.
  * withheld items are COUNTED per label, never described: no title, URL or reason for an item the
    caller may not read.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any

from gateway.egress.fetch import Fetched
from gateway.models import SearchResult
from gateway.screen import fence
from gateway.screen.connector import Connector, Outcome
from gateway.screen.gate_connector import PageInput, SnippetInput


@dataclass(frozen=True)
class ScreenedText:
    title: str
    text: str  # the clean text
    fenced: str  # the same text inside the nonce fence
    labels: dict[str, Any]


def _src(outcome: Outcome) -> str:
    lab = outcome.labels
    conn = lab.get("connector", "search-gate")
    src, item = lab.get("source", ""), lab.get("intake_id", "")
    return f"{conn}/{src}/{item}"


def _fenced(text: str, outcome: Outcome) -> str:
    return fence.wrap(text, src=_src(outcome), verdict="clean").text


def snippet_input(r: SearchResult) -> SnippetInput:
    return SnippetInput(url=str(r.url), title=r.title, snippet=r.snippet or "",
                        engine=r.source_engine or r.provider)


def page_input(f: Fetched) -> PageInput:
    return PageInput(requested_url=f.requested_url, final_url=f.final_url,
                     redirects=tuple(f.redirects), content_type=f.content_type, body=f.body,
                     http_status=f.status, via=f.via)


class Screen:
    def __init__(self, connector: Connector) -> None:
        self.connector = connector

    async def results(self, results: list[SearchResult]
                      ) -> tuple[list[tuple[SearchResult, ScreenedText]], dict[str, int]]:
        outs = await self.connector.snippets([snippet_input(r) for r in results])
        kept: list[tuple[SearchResult, ScreenedText]] = []
        withheld: Counter[str] = Counter()
        for r, o in zip(results, outs, strict=True):
            if not o.readable or o.text is None:
                withheld[o.label] += 1
                continue
            title, _, snippet = o.text.partition("\n")
            kept.append((r, ScreenedText(title=title.strip(), text=snippet.strip(),
                                         fenced=_fenced(o.text, o), labels=dict(o.labels))))
        return kept, dict(withheld)

    async def page(self, f: Fetched) -> tuple[Outcome, ScreenedText | None]:
        o = (await self.connector.pages([page_input(f)]))[0]
        if not o.readable or o.text is None:
            return o, None
        return o, ScreenedText(title=(o.title or "").strip(), text=o.text,
                               fenced=_fenced(o.text, o), labels=dict(o.labels))
