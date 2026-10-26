"""Acceptance: the kit's conformance runner passes for both gate connectors (all 9 contract tests:
declaration, static scan, only valid envelopes, no table writes or model calls, network only to
intake through the kit's client, no secret in logs, stable idempotency keys, size caps, the outcome
table)."""

from __future__ import annotations

import pytest

import gate_conformance
from connector_kit.testkit.conformance import TESTS, run


@pytest.mark.parametrize("name,factory,oversize", [
    ("pages", gate_conformance.pages, gate_conformance.oversize_pages),
    ("snippets", gate_conformance.snippets, None),
])
def test_conformant(name: str, factory, oversize) -> None:
    v = run(factory, oversize)
    assert len(TESTS) == 9
    assert v.passed, {n: r for n, r in v.results.items() if r != "pass"}
