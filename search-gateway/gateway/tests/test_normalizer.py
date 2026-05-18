"""SearXNG JSON -> SearchResult mapping, including missing-field handling."""

from __future__ import annotations

from datetime import datetime

from gateway.normalizer import normalize_searxng_payload, normalize_searxng_result


def test_full_result_maps_all_fields() -> None:
    item = {
        "title": "Anthropic",
        "url": "https://www.anthropic.com",
        "content": "Claude is an AI assistant.",
        "engine": "duckduckgo",
        "publishedDate": "2026-01-15T00:00:00Z",
        "score": 1.5,
    }
    r = normalize_searxng_result(item, "searxng")
    assert r is not None
    assert r.title == "Anthropic"
    assert str(r.url) == "https://www.anthropic.com/"
    assert r.snippet == "Claude is an AI assistant."
    assert r.source_engine == "duckduckgo"
    assert r.score == 1.5
    assert r.published_at == datetime.fromisoformat("2026-01-15T00:00:00+00:00")
    assert r.provider == "searxng"


def test_missing_optional_fields() -> None:
    r = normalize_searxng_result({"url": "https://example.com"}, "searxng")
    assert r is not None
    assert r.title == "https://example.com"  # falls back to URL when title absent
    assert r.snippet is None
    assert r.published_at is None
    assert r.score is None
    assert r.source_engine is None


def test_result_without_url_is_dropped() -> None:
    assert normalize_searxng_result({"title": "no url"}, "searxng") is None


def test_invalid_url_scheme_dropped() -> None:
    assert normalize_searxng_result({"url": "ftp://x"}, "searxng") is None


def test_unparseable_published_date_is_none() -> None:
    r = normalize_searxng_result(
        {"url": "https://a.com", "publishedDate": "not-a-date"}, "searxng"
    )
    assert r is not None and r.published_at is None


def test_payload_respects_max_results_and_skips_bad_items() -> None:
    payload = {
        "results": [
            {"url": "https://a.com"},
            "not-a-dict",
            {"title": "no url"},
            {"url": "https://b.com"},
            {"url": "https://c.com"},
        ]
    }
    out = normalize_searxng_payload(payload, "searxng", max_results=2)
    assert [str(r.url) for r in out] == ["https://a.com/", "https://b.com/"]


def test_empty_payload() -> None:
    assert normalize_searxng_payload({}, "searxng", 10) == []
