"""Paid-provider stubs (spec §1: interface only, not implemented in v1).

Each defines name + privacy_rank so the rotation registry and future
PROVIDER_PRIORITY ordering work, but search() raises until implemented.
Roadmap: implement SearchProvider, add Redis monthly quota buckets, place
above 'searxng' in PROVIDER_PRIORITY (README roadmap).
"""

from __future__ import annotations

from gateway.models import SearchRequest, SearchResult
from gateway.providers.base import ProviderConfigError, SearchProvider


class _NotImplementedProvider(SearchProvider):
    privacy_rank = 100  # paid/hosted => less private than Tor-routed SearXNG

    async def search(self, req: SearchRequest) -> list[SearchResult]:
        raise ProviderConfigError(f"{self.name} provider is not implemented in v1")

    async def health(self) -> bool:
        return False


class KagiProvider(_NotImplementedProvider):
    name = "kagi"
    privacy_rank = 30


class MojeekProvider(_NotImplementedProvider):
    name = "mojeek"
    privacy_rank = 40


class BraveProvider(_NotImplementedProvider):
    name = "brave"
    privacy_rank = 50


class TavilyProvider(_NotImplementedProvider):
    name = "tavily"
    privacy_rank = 60
