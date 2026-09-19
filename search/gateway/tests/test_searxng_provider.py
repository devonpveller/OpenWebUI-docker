"""SearXNG provider: outbound URL/params, status-code -> error mapping."""

from __future__ import annotations

import httpx
import pytest
import respx

from gateway.models import SearchRequest
from gateway.providers.base import ProviderConfigError, TransientProviderError
from gateway.providers.searxng import SearxngProvider

BASE = "http://searxng:8080"


@respx.mock
async def test_outbound_url_and_params() -> None:
    route = respx.get(f"{BASE}/search").mock(
        return_value=httpx.Response(
            200,
            json={"results": [{"url": "https://a.com", "title": "A", "content": "c"}]},
        )
    )
    provider = SearxngProvider(BASE, timeout_seconds=5)
    results = await provider.search(
        SearchRequest(query="hello world", max_results=5, time_range="week")
    )
    await provider.aclose()

    assert len(results) == 1
    request = route.calls.last.request
    assert request.url.params["q"] == "hello world"
    assert request.url.params["format"] == "json"
    assert request.url.params["time_range"] == "week"
    assert request.url.params["safesearch"] == "0"


@respx.mock
async def test_403_is_config_error() -> None:
    respx.get(f"{BASE}/search").mock(return_value=httpx.Response(403, text="forbidden"))
    provider = SearxngProvider(BASE, timeout_seconds=5)
    with pytest.raises(ProviderConfigError, match="403"):
        await provider.search(SearchRequest(query="x"))
    await provider.aclose()


@respx.mock
async def test_502_is_transient() -> None:
    respx.get(f"{BASE}/search").mock(return_value=httpx.Response(502))
    provider = SearxngProvider(BASE, timeout_seconds=5)
    with pytest.raises(TransientProviderError):
        await provider.search(SearchRequest(query="x"))
    await provider.aclose()


@respx.mock
async def test_429_is_transient() -> None:
    respx.get(f"{BASE}/search").mock(return_value=httpx.Response(429))
    provider = SearxngProvider(BASE, timeout_seconds=5)
    with pytest.raises(TransientProviderError):
        await provider.search(SearchRequest(query="x"))
    await provider.aclose()


@respx.mock
async def test_timeout_is_transient() -> None:
    respx.get(f"{BASE}/search").mock(side_effect=httpx.ConnectTimeout("slow tor"))
    provider = SearxngProvider(BASE, timeout_seconds=1)
    with pytest.raises(TransientProviderError):
        await provider.search(SearchRequest(query="x"))
    await provider.aclose()
