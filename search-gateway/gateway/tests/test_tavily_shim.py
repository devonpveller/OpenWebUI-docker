"""Tavily shim: request/response shape, search_depth mapping, auth."""

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
                    title="Result",
                    url="https://example.com",
                    snippet="snippet text",
                    score=0.9,
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


def test_bearer_auth_and_tavily_shape(stub: StubEngine) -> None:
    with TestClient(app) as client:
        resp = client.post(
            "/tavily/search",
            json={"query": "anthropic"},
            headers={"Authorization": "Bearer test-key"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["query"] == "anthropic"
    assert body["answer"] is None  # shim never synthesizes an answer
    assert isinstance(body["response_time"], float)
    r = body["results"][0]
    assert set(r) == {"title", "url", "content", "score", "published_date"}
    assert r["content"] == "snippet text"


def test_api_key_in_body_is_accepted(stub: StubEngine) -> None:
    with TestClient(app) as client:
        resp = client.post("/tavily/search", json={"query": "x", "api_key": "test-key"})
    assert resp.status_code == 200


def test_bad_key_is_401(stub: StubEngine) -> None:
    with TestClient(app) as client:
        resp = client.post(
            "/tavily/search",
            json={"query": "x"},
            headers={"Authorization": "Bearer wrong"},
        )
    assert resp.status_code == 401


def test_search_depth_advanced_maps_to_20(stub: StubEngine) -> None:
    with TestClient(app) as client:
        client.post(
            "/tavily/search",
            json={"query": "x", "search_depth": "advanced"},
            headers={"Authorization": "Bearer test-key"},
        )
    assert stub.last_request is not None
    assert stub.last_request.max_results == 20


def test_explicit_max_results_overrides_depth(stub: StubEngine) -> None:
    with TestClient(app) as client:
        client.post(
            "/tavily/search",
            json={"query": "x", "search_depth": "advanced", "max_results": 7},
            headers={"Authorization": "Bearer test-key"},
        )
    assert stub.last_request is not None
    assert stub.last_request.max_results == 7
