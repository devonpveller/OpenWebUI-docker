"""Provider-agnostic result normalization.

Currently maps SearXNG JSON result objects to ``SearchResult``. Kept separate
from the provider so future providers can reuse the missing-field handling and
so it is unit-testable without HTTP.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import ValidationError

from gateway.models import SearchResult


def _parse_published(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _coerce_score(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def normalize_searxng_result(item: dict[str, Any], provider: str) -> SearchResult | None:
    """Map one SearXNG JSON result. Returns None if it lacks a usable URL
    (a result with no URL is useless downstream) or fails URL validation."""
    url = item.get("url")
    if not url:
        return None
    try:
        return SearchResult(
            title=(item.get("title") or "").strip() or url,
            url=url,
            snippet=(item.get("content") or None),
            published_at=_parse_published(item.get("publishedDate")),
            score=_coerce_score(item.get("score")),
            source_engine=item.get("engine"),
            provider=provider,
        )
    except ValidationError:
        # e.g. non-HTTP scheme; drop the single result rather than fail the batch.
        return None


def normalize_searxng_payload(
    payload: dict[str, Any], provider: str, max_results: int
) -> list[SearchResult]:
    raw = payload.get("results") or []
    out: list[SearchResult] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        result = normalize_searxng_result(item, provider)
        if result is not None:
            out.append(result)
        if len(out) >= max_results:
            break
    return out
