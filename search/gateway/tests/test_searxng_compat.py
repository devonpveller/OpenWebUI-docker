"""Approach B: GET /search returns the SearXNG-shaped JSON OWUI v0.8.10 expects,
with no bearer auth, and maps OWUI's params correctly."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from gateway.deps import get_engine
from gateway.main import app
from gateway.models import SearchRequest, SearchResponse, SearchResult
from gateway.rotation import AllProvidersFailed


class StubEngine:
    def __init__(self) -> None:
        self.last_request: SearchRequest | None = None
        self.raise_all_failed = False

    async def search(self, req: SearchRequest) -> SearchResponse:
        self.last_request = req
        if self.raise_all_failed:
            raise AllProvidersFailed
        return SearchResponse(
            query=req.query,
            provider_used="searxng",
            results=[
                SearchResult(
                    title="A",
                    url="https://a.com",
                    snippet="content a",
                    score=1.2,
                    source_engine="duckduckgo",
                    provider="searxng",
                )
            ],
        )


@pytest.fixture
def stub() -> StubEngine:
    engine = StubEngine()
    app.dependency_overrides[get_engine] = lambda: engine
    yield engine
    app.dependency_overrides.clear()


def test_no_auth_required_and_owui_shape(stub: StubEngine) -> None:
    with TestClient(app) as client:
        resp = client.get("/search", params={"q": "anthropic", "format": "json"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["query"] == "anthropic"
    assert body["number_of_results"] == 1
    r = body["results"][0]
    # OWUI v0.8.10 reads exactly these per-result fields.
    assert r["url"] == "https://a.com/"
    assert r["title"] == "A"
    assert r["content"] == "content a"
    assert r["score"] == 1.2


def test_param_mapping(stub: StubEngine) -> None:
    with TestClient(app) as client:
        client.get(
            "/search",
            params={
                "q": "x",
                "language": "all",
                "safesearch": 2,
                "time_range": "garbage",
                "count": 15,
            },
        )
    req = stub.last_request
    assert req is not None
    assert req.language is None  # "all" -> None
    assert req.safe_search == 2
    assert req.time_range is None  # invalid time_range dropped
    assert req.max_results == 15


def test_valid_time_range_passed_through(stub: StubEngine) -> None:
    with TestClient(app) as client:
        client.get("/search", params={"q": "x", "time_range": "month"})
    assert stub.last_request is not None
    assert stub.last_request.time_range == "month"


def test_all_failed_returns_empty_searxng_body(stub: StubEngine) -> None:
    stub.raise_all_failed = True
    with TestClient(app) as client:
        resp = client.get("/search", params={"q": "x"})
    assert resp.status_code == 200
    assert resp.json() == {"query": "x", "number_of_results": 0, "results": []}
