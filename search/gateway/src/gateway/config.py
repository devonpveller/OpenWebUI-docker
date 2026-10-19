"""Env-driven settings (pydantic-settings). All defaults match the integration plan.

NAMING (operator 2026-10-19): the plane and its work are "search"; SearXNG is only today's engine.
Engine-neutral names are the primary ones (SEARCH_ENGINE, SEARCH_ENGINE_URL); the engine's own name
appears only in its adapter (providers/searxng.py) and in the legacy aliases PROVIDER_PRIORITY /
SEARXNG_URL, still read so today's compose and callers keep working.

SECRETS are files, never values in the environment (the intake-i5b rule: every `docker exec`
process inherits the container's environment). GATEWAY_API_KEY is the one exception, kept as it
was for today's callers (the legacy lane) until step 5 moves them.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# The gate's fetch content-type allow-list: exactly what intake's stage 1 accepts for a web page
# (intake/envelope.py ALLOWED_TYPES, minus message/rfc822, which is not a web page).
DEFAULT_FETCH_TYPES = "text/html,application/xhtml+xml,text/plain,text/markdown,application/json"


class Settings(BaseSettings):
    """Process configuration. Every field is overridable via environment."""

    model_config = SettingsConfigDict(env_prefix="", extra="ignore", populate_by_name=True)

    # --- Required (the legacy lane) ---
    gateway_api_key: str = Field(
        ...,
        alias="GATEWAY_API_KEY",
        description="The legacy shared bearer token: today's callers, unscreened, until step 5.",
    )

    # --- The engine (one adapter interface, a config switch) ---
    search_engine: str = Field(
        "searxng",
        validation_alias=AliasChoices("SEARCH_ENGINE", "PROVIDER_PRIORITY"),
        description="Adapter name(s) in priority order: searxng | fake (comma-separated).",
    )
    search_engine_url: str = Field(
        "http://searxng:8080",
        validation_alias=AliasChoices("SEARCH_ENGINE_URL", "SEARXNG_URL"),
    )
    redis_url: str = Field("redis://redis:6379/1", alias="REDIS_URL")
    redis_password_file: str = Field(
        "",
        alias="REDIS_PASSWORD_FILE",
        description="File with redis's requirepass (a bare password, or a redis.conf line "
        "'requirepass <pw>'). Empty: no AUTH (tests, and the plane until the deploy).",
    )

    # --- Behaviour ---
    cache_ttl_seconds: int = Field(900, alias="CACHE_TTL_SECONDS", ge=0)
    cache_key_file: str = Field(
        "",
        alias="GATE_CACHE_KEY_FILE",
        description="HMAC key for cache keys and cached values. Empty: a random key per process.",
    )
    request_timeout_seconds: float = Field(20.0, alias="REQUEST_TIMEOUT_SECONDS", gt=0)
    circuit_failure_threshold: int = Field(3, alias="CIRCUIT_FAILURE_THRESHOLD", ge=1)
    circuit_cooldown_seconds: int = Field(120, alias="CIRCUIT_COOLDOWN_SECONDS", ge=1)

    # --- Callers ---
    caller_keys_file: str = Field(
        "",
        alias="GATE_CALLER_KEYS_FILE",
        description="Per-caller keys, one 'name=key' per line or comma. These callers are "
        "SCREENED: everything they get passed intake.",
    )
    search_anonymous_legacy: bool = Field(
        False,
        alias="SEARCH_ANONYMOUS_LEGACY",
        description="Serve GET /search without a key (today's OWUI and research). Off by default; "
        "the plane's compose turns it on until step 5 moves those callers.",
    )

    # --- The fetch leg (the target + redirect guard, caps) ---
    fetch_proxy_url: str = Field(
        "http://vpn:8888",
        alias="GATE_FETCH_PROXY_URL",
        description="Egress proxy. Empty string = DIRECT (explicit only; never a fallback).",
    )
    doh_url: str = Field(
        "https://dns.mullvad.net/dns-query",
        alias="GATE_DOH_URL",
        description="DNS-over-HTTPS resolver used in proxy mode, reached THROUGH the proxy, so the "
        "gate resolves (and pins) without a local DNS leak.",
    )
    fetch_max_bytes: int = Field(2 * 1024 * 1024, alias="GATE_FETCH_MAX_BYTES", ge=1024)
    fetch_timeout_seconds: float = Field(20.0, alias="GATE_FETCH_TIMEOUT_SECONDS", gt=0)
    fetch_max_redirects: int = Field(5, alias="GATE_FETCH_MAX_REDIRECTS", ge=0, le=20)
    fetch_ports: str = Field("80,443", alias="GATE_FETCH_PORTS")
    fetch_content_types: str = Field(DEFAULT_FETCH_TYPES, alias="GATE_FETCH_CONTENT_TYPES")
    fetch_concurrency: int = Field(4, alias="GATE_FETCH_CONCURRENCY", ge=1, le=32)
    loader_max_urls: int = Field(20, alias="GATE_LOADER_MAX_URLS", ge=1, le=100)

    # --- The intake hand-off ---
    intake_url: str = Field("http://openbrain-intake:8000/v1/intake", alias="INTAKE_URL")
    intake_key_file: str = Field(
        "",
        alias="INTAKE_KEY_FILE",
        description="The gate's intake caller key (connector search-gate). Empty: screening is "
        "unavailable and every screened route answers 503 (fail closed).",
    )
    intake_timeout_seconds: float = Field(60.0, alias="INTAKE_TIMEOUT_SECONDS", gt=0)
    intake_concurrency: int = Field(4, alias="INTAKE_CONCURRENCY", ge=1, le=32)

    # --- Logging (privacy-sensitive) ---
    log_level: str = Field("INFO", alias="LOG_LEVEL")
    log_queries: bool = Field(
        False,
        alias="LOG_QUERIES",
        description="Must default to false. When true, only a sha256 of the query is logged.",
    )

    @property
    def engine_order(self) -> list[str]:
        """Engine adapter names in priority order."""
        return [p.strip() for p in self.search_engine.split(",") if p.strip()]

    # legacy spellings, kept for callers inside this package that predate the rename
    @property
    def provider_order(self) -> list[str]:
        return self.engine_order

    @property
    def fetch_port_set(self) -> frozenset[int]:
        return frozenset(int(p) for p in self.fetch_ports.split(",") if p.strip())

    @property
    def fetch_type_set(self) -> frozenset[str]:
        types = self.fetch_content_types.split(",")
        return frozenset(t.strip().lower() for t in types if t.strip())


def read_secret_file(path: str) -> str:
    """The file's content without surrounding whitespace. An empty path, or a file that does not
    exist, is '' - the plane ships with these files absent (the pre-gate posture: no per-caller
    keys, no intake key, no redis password), and the start-up log says which are absent. An
    unreadable file that DOES exist raises (a misconfigured gate never serves)."""
    if not path:
        return ""
    p = Path(path)
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8").strip()


@lru_cache
def get_settings() -> Settings:
    """Cached settings singleton. Injected via FastAPI ``Depends`` elsewhere."""
    return Settings()  # type: ignore[call-arg]  # values come from the environment
