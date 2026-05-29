"""SearXNG provider. All outbound goes through SearXNG, which itself egresses
via Tor (enforced by network topology + searxng/settings.yml proxies)."""

from __future__ import annotations

import httpx

from gateway.models import SearchRequest, SearchResult
from gateway.normalizer import normalize_searxng_payload
from gateway.providers.base import (
    ProviderConfigError,
    SearchProvider,
    TransientProviderError,
)

# SearXNG safesearch is 0/1/2; our SearchRequest.safe_search uses the same scale.


class SearxngProvider(SearchProvider):
    name = "searxng"
    privacy_rank = 0  # most private path we have (Tor-routed metasearch)

    def __init__(self, base_url: str, timeout_seconds: float) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=True,
            headers={"Accept": "application/json"},
        )

    def _params(self, req: SearchRequest) -> dict[str, str]:
        params: dict[str, str] = {
            "q": req.query,
            "format": "json",
            "safesearch": str(req.safe_search),
        }
        if req.language:
            params["language"] = req.language
        if req.time_range:
            params["time_range"] = req.time_range
        return params

    async def search(self, req: SearchRequest) -> list[SearchResult]:
        url = f"{self._base_url}/search"
        try:
            resp = await self._client.get(url, params=self._params(req))
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            # Connection refused / Tor slow / DNS — treat as transient.
            raise TransientProviderError(f"searxng transport error: {exc!r}") from exc

        if resp.status_code == 429 or resp.status_code >= 500:
            raise TransientProviderError(
                f"searxng transient status {resp.status_code}"
            )
        if resp.status_code >= 400:
            # 403 here almost always means JSON format not enabled in settings.yml.
            raise ProviderConfigError(
                f"searxng config error {resp.status_code} "
                f"(if 403: enable 'json' in search.formats)"
            )

        try:
            payload = resp.json()
        except ValueError as exc:
            raise TransientProviderError("searxng returned non-JSON body") from exc

        return normalize_searxng_payload(payload, self.name, req.max_results)

    async def health(self) -> bool:
        try:
            resp = await self._client.get(
                f"{self._base_url}/search",
                params={"q": "healthcheck", "format": "json"},
            )
            return resp.status_code == 200
        except Exception:
            return False

    async def aclose(self) -> None:
        await self._client.aclose()
