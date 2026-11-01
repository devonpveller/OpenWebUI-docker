"""Provider-agnostic request/response models (spec §4.4.3)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, HttpUrl, field_validator

TimeRange = Literal["day", "week", "month", "year"]

# rw-p5a-intake: the SearXNG categories a caller may ask for. `science` is where the scholarly
# engines live (pubmed, europepmc, crossref, openalex, arxiv, google scholar - the ones
# named enabled in search/searxng/settings.yml); the research service's `academic` contract class
# asks for general + science. Any other value is DROPPED, never passed through, so a caller cannot
# reach an engine category this gateway does not answer for (OWUI's `categories` keeps today's).
ALLOWED_CATEGORIES: tuple[str, ...] = ("general", "science")


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
    # rw-p5a-intake: allowed categories only, de-duplicated in the order given; empty = SearXNG's
    # default (what every caller got before). Part of the cache key (the model dump below).
    categories: list[str] = Field(default_factory=list)

    @field_validator("categories", mode="before")
    @classmethod
    def _allowed_categories(cls, v: object) -> list[str]:
        if v is None:
            return []
        items: list[object] = list(v.split(",")) if isinstance(v, str) else (
            list(v) if isinstance(v, (list, tuple)) else [v])
        out: list[str] = []
        for item in items:
            c = str(item).strip().lower()
            if c in ALLOWED_CATEGORIES and c not in out:
                out.append(c)
        return out

    def cache_key_payload(self) -> str:
        """Stable JSON used to derive the cache key (field order fixed by model)."""
        return self.model_dump_json()


class SearchResponse(BaseModel):
    query: str
    provider_used: str
    results: list[SearchResult]
    cached: bool = False


# --- the gate (screened callers) -------------------------------------------------------------
class ScreenedResult(BaseModel):
    """One search result that intake labelled clean. ``title``/``snippet`` are intake's text (not
    the engine's bytes); ``fenced`` is the same text inside the per-response nonce fence - the form
    to put in front of a model."""

    title: str
    url: HttpUrl
    snippet: str
    fenced: str
    labels: dict[str, Any]
    published_at: datetime | None = None
    score: float | None = None
    source_engine: str | None = None
    provider: str


class GateSearchResponse(BaseModel):
    query: str
    provider_used: str
    results: list[ScreenedResult]
    withheld: dict[str, int] = Field(default_factory=dict)
    cached: bool = False


class FetchRequest(BaseModel):
    url: str = Field(min_length=1, max_length=4096)


class FetchResponse(BaseModel):
    """status: ok (clean, text returned) | refused (the guard or a cap; nothing was screened) |
    withheld (intake did not label it clean; no text) | error (network or screening failure)."""

    status: Literal["ok", "refused", "withheld", "error"]
    url: str
    final_url: str | None = None
    redirects: list[str] = Field(default_factory=list)
    http_status: int | None = None
    content_type: str | None = None
    title: str | None = None
    text: str | None = None
    fenced: str | None = None
    labels: dict[str, Any] | None = None
    label: str | None = None
    reason: str | None = None


class LoaderRequest(BaseModel):
    """Open WebUI's external web loader body (retrieval/loaders/external_web.py: {'urls': [...]},
    batches of 20)."""

    urls: list[str] = Field(min_length=1, max_length=100)


class OwuiSearchRequest(BaseModel):
    """Open WebUI's external web-search body (retrieval/web/external.py: {'query', 'count'})."""

    query: str = Field(min_length=1, max_length=2000)
    count: int = Field(default=10, ge=1, le=50)
