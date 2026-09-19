"""Provider-agnostic request/response models (spec §4.4.3)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl

TimeRange = Literal["day", "week", "month", "year"]


class SearchResult(BaseModel):
    title: str
    url: HttpUrl
    snippet: str | None = None
    published_at: datetime | None = None
    score: float | None = None
    source_engine: str | None = None  # upstream engine inside SearXNG
    provider: str  # top-level provider that returned it


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    max_results: int = Field(default=10, ge=1, le=50)
    safe_search: int = Field(default=0, ge=0, le=2)
    language: str | None = None  # e.g. "en"
    time_range: TimeRange | None = None

    def cache_key_payload(self) -> str:
        """Stable JSON used to derive the cache key (field order fixed by model)."""
        return self.model_dump_json()


class SearchResponse(BaseModel):
    query: str
    provider_used: str
    results: list[SearchResult]
    cached: bool = False
