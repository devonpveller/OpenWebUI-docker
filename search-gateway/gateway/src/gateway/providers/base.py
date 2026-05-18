"""SearchProvider interface + provider error taxonomy (spec §4.4.4 / §4.4.5)."""

from __future__ import annotations

from abc import ABC, abstractmethod

from gateway.models import SearchRequest, SearchResult


class ProviderError(Exception):
    """Base class for provider failures."""


class ProviderConfigError(ProviderError):
    """Permanent misconfiguration (e.g. SearXNG 4xx except 429).

    The circuit breaker does NOT count these — retrying won't help.
    """


class TransientProviderError(ProviderError):
    """Temporary failure (5xx, 429, timeout, connection error).

    The circuit breaker counts these toward opening the circuit.
    """


class SearchProvider(ABC):
    name: str  # e.g. "searxng", "kagi"
    privacy_rank: int  # lower = more private; used for sorting

    @abstractmethod
    async def search(self, req: SearchRequest) -> list[SearchResult]: ...

    @abstractmethod
    async def health(self) -> bool: ...

    async def remaining_quota(self) -> int | None:
        """Optional quota hook. SearXNG has none; paid providers override."""
        return None

    async def aclose(self) -> None:
        """Optional resource cleanup (HTTP clients). Default no-op."""
        return None
