"""Engine-health window (research-trust 2026-09-11).

The fixtures below are the SHAPE of two real payloads measured on 2026-09-11:
the live gateway answering every one of 12 audited queries with bing only, and
the same queries on the pinned newer image with four to five engines answering.
See documentation/notes/research-trust-findings.md §1.2 and §2.

Imports only gateway.engine_health, so it runs without redis/fastapi installed.
"""

from __future__ import annotations

from gateway.engine_health import (
    EngineHealth,
    engines_in_payload,
    health_verdict,
    unresponsive_in_payload,
)

LIVE_TODAY = {
    "results": [
        {"engine": "bing", "title": "MOST-Missouri's 529 Education Plan"},
        {"engine": "bing", "title": "MOST Definition & Meaning - Merriam-Webster"},
    ],
    "unresponsive_engines": [["mojeek", "Suspended: access denied"]],
}

AFTER_CHANGE = {
    "results": [
        {"engine": "qwant", "engines": ["qwant", "google"], "title": "Support for OptiPlex 3050"},
        {"engine": "google", "title": "A Reference Guide to the OptiPlex Diagnostics"},
        {"engine": "yandex", "title": "Dell OptiPlex 3050 Performance Results"},
        {"engine": "duckduckgo web", "title": "OptiPlex 3050 Owner's Manual"},
    ],
    "unresponsive_engines": [["brave", "too many requests"]],
}


def test_engines_in_payload_reads_both_fields():
    assert engines_in_payload(LIVE_TODAY) == {"bing"}
    assert engines_in_payload(AFTER_CHANGE) == {
        "qwant", "google", "yandex", "duckduckgo web",
    }


def test_unresponsive_is_name_to_reason():
    assert unresponsive_in_payload(LIVE_TODAY) == {"mojeek": "Suspended: access denied"}
    assert unresponsive_in_payload({}) == {}


def test_one_engine_answering_is_DEGRADED_even_though_nothing_errored():
    """The whole point: bing answered 200 on all 12 queries and raised no error."""
    h = EngineHealth()
    for _ in range(12):
        h.record(LIVE_TODAY)
    snap = h.snapshot()
    assert snap["engines_answering_now"] == 1
    assert snap["engines_answering_recent"] == {"bing": 12}
    assert snap["min_engines_in_a_search"] == 1
    assert health_verdict(snap) == "DEGRADED"


def test_four_engines_answering_is_ok_even_though_one_rate_limited():
    h = EngineHealth()
    for _ in range(12):
        h.record(AFTER_CHANGE)
    snap = h.snapshot()
    assert snap["engines_answering_now"] == 4
    assert snap["unresponsive_recent"]["brave"] == 12
    assert health_verdict(snap) == "ok"


def test_a_gateway_that_has_served_nothing_is_unknown_not_healthy():
    assert health_verdict(EngineHealth().snapshot()) == "unknown"


def test_an_all_empty_payload_is_degraded():
    h = EngineHealth()
    h.record({"results": []})
    h.record({"results": []})
    snap = h.snapshot()
    assert snap["empty_payloads"] == 2
    assert health_verdict(snap) == "DEGRADED"


def test_the_window_forgets():
    h = EngineHealth(window=3)
    for _ in range(3):
        h.record(LIVE_TODAY)
    for _ in range(3):
        h.record(AFTER_CHANGE)
    snap = h.snapshot()
    assert "bing" not in snap["engines_answering_recent"]
    assert snap["samples"] == 3
    assert snap["searches_total"] == 6


def test_record_ignores_garbage():
    h = EngineHealth()
    h.record(None)  # type: ignore[arg-type]
    h.record({"results": "not a list"})
    assert h.snapshot()["samples"] == 1
