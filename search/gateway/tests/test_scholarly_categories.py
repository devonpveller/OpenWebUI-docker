"""rw-p5a-intake: the scholarly engines and the category the academic contract class asks for.

The research service's `academic` contract class sends `categories=general,science` on GET
/search; the gateway hands SearXNG exactly the categories it allows (``general``, ``science``),
so a scholarly question reaches the science-category engines (pubmed, europepmc, semantic
scholar, crossref, openalex, arxiv, google scholar), which are named enabled in the plane's
settings.yml. A category outside the allowed set is dropped (never passed through), so OWUI's
own `categories` value keeps today's behaviour. Two requests that differ only in their
categories never share a cache entry.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx
import yaml
from fastapi.testclient import TestClient

from gateway.cache import cache_key
from gateway.deps import get_engine
from gateway.main import app
from gateway.models import SearchRequest, SearchResponse
from gateway.providers.searxng import SearxngProvider

BASE = "http://searxng:8080"
SETTINGS = Path(__file__).resolve().parents[2] / "searxng" / "settings.yml"
SCHOLARLY = ["pubmed", "europepmc", "crossref", "openalex", "arxiv", "google scholar"]
# measured broken / timing out in the replay lab (2026-11-01): named, and OFF
SCHOLARLY_OFF = ["semantic scholar", "openairepublications", "openairedatasets"]


@respx.mock
async def test_provider_sends_the_allowed_categories() -> None:
    route = respx.get(f"{BASE}/search").mock(
        return_value=httpx.Response(200, json={"results": []}))
    provider = SearxngProvider(BASE, timeout_seconds=5)
    await provider.search(
        SearchRequest(query="100 Hz tone motion sickness", categories=["general", "science"]))
    await provider.aclose()
    assert route.calls.last.request.url.params["categories"] == "general,science"


@respx.mock
async def test_provider_sends_no_categories_when_none_asked() -> None:
    route = respx.get(f"{BASE}/search").mock(
        return_value=httpx.Response(200, json={"results": []}))
    provider = SearxngProvider(BASE, timeout_seconds=5)
    await provider.search(SearchRequest(query="x"))
    await provider.aclose()
    assert "categories" not in route.calls.last.request.url.params


def test_request_drops_categories_outside_the_allowed_set() -> None:
    req = SearchRequest(
        query="x", categories=["science", "files", "general", "science", "nonsense"])
    assert req.categories == ["science", "general"]


def test_cache_key_differs_by_category() -> None:
    k = b"k" * 32
    plain = cache_key(SearchRequest(query="same"), k)
    sci = cache_key(SearchRequest(query="same", categories=["general", "science"]), k)
    assert plain != sci


class _Stub:
    def __init__(self) -> None:
        self.last: SearchRequest | None = None

    async def search(self, req: SearchRequest) -> SearchResponse:
        self.last = req
        return SearchResponse(query=req.query, provider_used="searxng", results=[])


@pytest.fixture
def stub() -> _Stub:
    engine = _Stub()
    app.dependency_overrides[get_engine] = lambda: engine
    yield engine
    app.dependency_overrides.clear()


def test_compat_route_forwards_categories(stub: _Stub) -> None:
    with TestClient(app) as client:
        r = client.get("/search", params={"q": "pubmed question", "format": "json",
                                          "categories": "general,science"})
    assert r.status_code == 200
    assert stub.last is not None and stub.last.categories == ["general", "science"]


def test_compat_route_owui_general_is_today(stub: _Stub) -> None:
    with TestClient(app) as client:
        r = client.get("/search", params={"q": "q", "format": "json", "categories": "images"})
    assert r.status_code == 200
    assert stub.last is not None and stub.last.categories == []


def test_settings_enable_the_scholarly_engines() -> None:
    cfg = yaml.safe_load(SETTINGS.read_text(encoding="utf-8"))
    named = {e["name"]: e for e in cfg["engines"]}
    for name in SCHOLARLY:
        assert name in named, f"{name} is named in settings.yml"
        assert named[name].get("disabled") is False, f"{name} is enabled"
    for name in SCHOLARLY_OFF:
        assert named.get(name, {}).get("disabled") is True, f"{name} is named and disabled"
