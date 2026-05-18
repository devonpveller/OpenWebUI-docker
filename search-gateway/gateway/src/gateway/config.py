"""Env-driven settings (pydantic-settings). All defaults match the integration plan."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process configuration. Every field is overridable via environment."""

    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    # --- Required ---
    gateway_api_key: str = Field(
        ...,
        alias="GATEWAY_API_KEY",
        description="Shared bearer token for native / Tavily / MCP clients.",
    )

    # --- Upstreams ---
    searxng_url: str = Field("http://searxng:8080", alias="SEARXNG_URL")
    redis_url: str = Field("redis://redis:6379/1", alias="REDIS_URL")

    # --- Behaviour ---
    cache_ttl_seconds: int = Field(900, alias="CACHE_TTL_SECONDS", ge=0)
    provider_priority: str = Field("searxng", alias="PROVIDER_PRIORITY")
    request_timeout_seconds: float = Field(20.0, alias="REQUEST_TIMEOUT_SECONDS", gt=0)
    circuit_failure_threshold: int = Field(3, alias="CIRCUIT_FAILURE_THRESHOLD", ge=1)
    circuit_cooldown_seconds: int = Field(120, alias="CIRCUIT_COOLDOWN_SECONDS", ge=1)

    # --- Logging (privacy-sensitive) ---
    log_level: str = Field("INFO", alias="LOG_LEVEL")
    log_queries: bool = Field(
        False,
        alias="LOG_QUERIES",
        description="Must default to false. When true, only a sha256 of the query is logged.",
    )

    @property
    def provider_order(self) -> list[str]:
        """Provider names in priority order (highest privacy first)."""
        return [p.strip() for p in self.provider_priority.split(",") if p.strip()]


@lru_cache
def get_settings() -> Settings:
    """Cached settings singleton. Injected via FastAPI ``Depends`` elsewhere."""
    return Settings()  # type: ignore[call-arg]  # values come from the environment
