"""The fake engine's adapter: a second, engine-neutral implementation of ``SearchProvider``.

It exists to PROVE the adapter boundary (harness item search-gate, artifact (7)): the gateway runs
unchanged on an engine that is not SearXNG, selected by configuration alone
(``SEARCH_ENGINE=fake`` + ``SEARCH_ENGINE_URL``). The fake engine (tests/fake_engine.py, also
run as a container in the docker round trip) speaks its own minimal protocol:

    GET {url}/query?q=<query>&n=<max>   ->   {"hits": [{"link", "headline", "summary"}]}
    GET {url}/ping                      ->   200

The same adapter contract tests (tests/adapter_contract.py) run against this adapter and against
the SearXNG adapter, both in-process and against the real images.
"""

from __future__ import annotations

from typing import Any

import httpx
from pydantic import ValidationError

from gateway.models import SearchRequest, SearchResult
from gateway.providers.base import ProviderConfigError, SearchProvider, TransientProviderError


class FakeEngineProvider(SearchProvider):
    name = "fake"
    privacy_rank = 90

    def __init__(self, base_url: str, timeout_seconds: float) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds), trust_env=False, follow_redirects=False,
            headers={"Accept": "application/json"},
        )

    async def search(self, req: SearchRequest) -> list[SearchResult]:
        try:
            resp = await self._client.get(f"{self._base_url}/query",
                                          params={"q": req.query, "n": str(req.max_results)})
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise TransientProviderError(
                f"fake engine transport error: {type(exc).__name__}") from exc
        if resp.status_code == 429 or resp.status_code >= 500:
            raise TransientProviderError(f"fake engine transient status {resp.status_code}")
        if resp.status_code >= 400:
            raise ProviderConfigError(f"fake engine config error {resp.status_code}")
        try:
            payload: Any = resp.json()
        except ValueError as exc:
            raise TransientProviderError("fake engine returned non-JSON body") from exc
        out: list[SearchResult] = []
        hits = payload.get("hits") if isinstance(payload, dict) else None
        for hit in hits or []:
            if not isinstance(hit, dict) or not hit.get("link"):
                continue
            try:
                out.append(SearchResult(
                    title=(hit.get("headline") or "").strip() or hit["link"],
                    url=hit["link"],
                    snippet=hit.get("summary") or None,
                    source_engine="fake",
                    provider=self.name,
                ))
            except ValidationError:
                continue
            if len(out) >= req.max_results:
                break
        return out

    async def health(self) -> bool:
        try:
            return (await self._client.get(f"{self._base_url}/ping")).status_code == 200
        except Exception:
            return False

    async def aclose(self) -> None:
        await self._client.aclose()
