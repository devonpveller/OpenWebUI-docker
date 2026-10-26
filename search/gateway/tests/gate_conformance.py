"""Conformance factories for the gate's connectors (the kit's runner, acceptance check):

    python -m connector_kit.testkit.conformance gate_conformance:pages gate_conformance:oversize_pages
    python -m connector_kit.testkit.conformance gate_conformance:snippets

(run from search/gateway/tests with src on the path; tests/test_kit_conformance.py does it in-process)
"""

from __future__ import annotations

import datetime as dt

from connector_kit.envelope import RAW_MAX_BYTES
from gateway.screen.gate_connector import PageConnector, PageInput, SnippetConnector, SnippetInput

FIXED = dt.datetime(2026, 10, 19, 12, 0, tzinfo=dt.UTC)

PAGES = (
    PageInput("http://public.example.com/page", "http://public.example.com/page", (),
              "text/html; charset=utf-8", b"<p>An ordinary article about bread.</p>", 200, "proxy",
              FIXED),
    PageInput("http://public.example.com/redir-public", "http://other.example.com/page",
              ("http://other.example.com/page",), "text/plain", b"plain words", 200, "proxy", FIXED),
    PageInput("https://third.example.com/data", "https://third.example.com/data", (),
              "application/json", b'{"k": "v"}', 200, "proxy", FIXED),
)
SNIPPETS = (
    SnippetInput("https://public.example.com/a", "Result A", "A plain snippet.", "searxng"),
    SnippetInput("https://other.example.com/b", "Result  B", "Café snippet.", "fake"),
)


def pages(config):
    return PageConnector(config, PAGES)


def oversize_pages(config):
    big = PageInput("http://big.example.com/", "http://big.example.com/", (), "text/plain",
                    b"a" * (RAW_MAX_BYTES + 1), 200, "proxy", FIXED)
    return PageConnector(config, (big, PAGES[0]))


def snippets(config):
    return SnippetConnector(config, SNIPPETS)
