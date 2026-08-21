"""Env-driven settings (pydantic-settings).

The projected-wait math needs ``slots`` (P) and the in-flight cap ``N``. These
duplicate ``--parallel`` / ``concurrencyLimit`` from llama-swap.config.yaml, so
the three are a TUNING INVARIANT documented here and in the design (§10.2):

    llama-swap --parallel  ==  LLM_QUEUE_SLOTS (P)      # real concurrent lanes
    LLM_QUEUE_MAX_IN_FLIGHT (N)  <=  P + 1              # headroom discipline (§3.1)
    llama-swap concurrencyLimit  ==  0                  # queue is the sole gate

We deliberately do NOT probe the upstream's ``/props`` at startup to read the
slot count: on llama-swap an unloaded model would be force-loaded by the probe
(swap thrash — the exact failure background_health_checks:false avoids). Config
is the source of truth; keep it in sync with llama-swap.config.yaml.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process configuration. Every field is overridable via environment."""

    model_config = SettingsConfigDict(env_prefix="LLM_QUEUE_", extra="ignore")

    # --- Upstreams (the ONLY sanctioned *-upstream references; guard §3.2) ---
    # NOTE: the env vars are named LLM_QUEUE_UPSTREAM_BASE_URL /
    # LLM_QUEUE_EMBED_UPSTREAM_BASE_URL — check-llm-gateway-routing.ps1 sanctions
    # exactly the `LLM_QUEUE_UPSTREAM` token as the queue's forward target.
    upstream_base_url: str = Field(
        "http://llama-cpp-upstream:8080",
        alias="LLM_QUEUE_UPSTREAM_BASE_URL",
        description="Chat (llama-swap) forward target.",
    )
    embed_upstream_base_url: str = Field(
        "http://llama-cpp-embed-upstream:8080",
        alias="LLM_QUEUE_EMBED_UPSTREAM_BASE_URL",
        description="Embedding forward target (P4 — only queued once embed api_base is repointed).",
    )

    # --- Admission sizing (keep in sync with llama-swap.config.yaml) ---
    slots: int = Field(3, alias="LLM_QUEUE_SLOTS", ge=1, description="P — real parallel lanes.")
    max_in_flight: int = Field(
        4, alias="LLM_QUEUE_MAX_IN_FLIGHT", ge=1, description="N — admitted to upstream (<= P+1)."
    )
    embed_slots: int = Field(2, alias="LLM_QUEUE_EMBED_SLOTS", ge=1)
    embed_max_in_flight: int = Field(3, alias="LLM_QUEUE_EMBED_MAX_IN_FLIGHT", ge=1)
    # The embed upstream is PLAIN llama.cpp (no llama-swap cap) with an unbounded
    # internal FIFO — it never 429s today. Front it with a GENEROUS backstop so
    # large embedding bursts (OB1 chunk-worker backfill) behave as before (held +
    # processed 2-at-a-time), not rejected. LiteLLM retry absorbs anything beyond.
    embed_backstop_depth: int = Field(256, alias="LLM_QUEUE_EMBED_BACKSTOP_DEPTH", ge=1)

    # --- Wait-queue ---
    backstop_depth: int = Field(
        24,
        alias="LLM_QUEUE_BACKSTOP_DEPTH",
        ge=1,
        description="Soft coarse ceiling on WAITING requests per model (design §8b).",
    )
    max_total_connections: int = Field(
        128,
        alias="LLM_QUEUE_MAX_TOTAL_CONNECTIONS",
        ge=1,
        description="Hard absolute cap on concurrent held requests across all models "
        "(FD/socket safety valve, independent of per-service budget — §10.3.3).",
    )

    # --- Rolling completion metric (T) ---
    t_window: int = Field(5, alias="LLM_QUEUE_T_WINDOW", ge=1, description="Completions averaged.")
    t_initial_s: float = Field(
        30.0, alias="LLM_QUEUE_T_INITIAL_S", gt=0, description="T before any sample lands."
    )
    t_trim_outlier: bool = Field(
        True,
        alias="LLM_QUEUE_T_TRIM_OUTLIER",
        description="Drop the single worst sample before averaging (a 9-min deep-research "
        "request shouldn't skew the next arrival's estimate — §7.2).",
    )

    # --- Policy (P2) ---
    # Whether to enforce the per-service acceptable-wait budget at admission.
    # P1 ships with this OFF (admission gated by depth backstop only); P2 flips it
    # ON once budgets/priority classes are tuned. Keeps each phase revertible.
    enforce_budget: bool = Field(False, alias="LLM_QUEUE_ENFORCE_BUDGET")
    default_acceptable_wait_s: float = Field(120.0, alias="LLM_QUEUE_DEFAULT_ACCEPTABLE_WAIT_S")
    # JSON map: key-string -> {"class": str, "rank": int, "acceptable_wait_s": float,
    #                          "max_concurrency": int|null}. Empty => all default.
    policy_json: str = Field("", alias="LLM_QUEUE_POLICY_JSON")
    # Header llm-queue reads to attribute a request to a caller key (priority is
    # derived SERVER-SIDE from this, never from a client-supplied X-Priority — §10.3.2).
    # LiteLLM only forwards the caller's Authorization when
    # forward_client_headers_to_llm_api is enabled (wired in P2); until then every
    # request attributes to the default class, which is the safe, correct P1 behaviour.
    key_header: str = Field("authorization", alias="LLM_QUEUE_KEY_HEADER")

    # --- Wait keep-alive (SSE heartbeat while queued — §10.4) ---
    sse_heartbeat_s: float = Field(10.0, alias="LLM_QUEUE_SSE_HEARTBEAT_S", gt=0)

    # --- Timeouts ---
    upstream_timeout_s: float = Field(
        600.0,
        alias="LLM_QUEUE_UPSTREAM_TIMEOUT_S",
        gt=0,
        description="Per-request upstream read timeout (matches LiteLLM request_timeout).",
    )
    drain_grace_s: float = Field(
        25.0, alias="LLM_QUEUE_DRAIN_GRACE_S", ge=0, description="SIGTERM in-flight grace (§10.4)."
    )

    # --- Connection-leak self-healing (§10.3.3 safety net) ---
    # A held connection is released in the response generator's `finally`. But Starlette does NOT
    # aclose an abandoned StreamingResponse body_iterator on client disconnect
    # (verified 1.3.1) — so a
    # mid-stream disconnect leaves the release to async-generator GC, which under load is delayed or
    # effectively never. Accumulated leaks wedge the hard `max_total_connections`
    # cap and shed ALL load
    # while the GPU sits idle (the observed failure). This reaper reconciles the held set: any
    # connection held longer than a legitimate request could possibly last
    # (max queue wait + the ~600s
    # upstream timeout) is a leak and is reclaimed, so the cap can never permanently wedge.
    conn_ttl_s: float = Field(
        1200.0, alias="LLM_QUEUE_CONN_TTL_S", gt=0,
        description="Reclaim a held connection older than this — MUST exceed "
        "the longest legitimate "
        "hold (> upstream_timeout_s + worst-case queue wait). Safety net for "
        "disconnect-leaked slots.",
    )
    reap_interval_s: float = Field(
        30.0, alias="LLM_QUEUE_REAP_INTERVAL_S", gt=0,
        description="How often the connection-leak reaper sweeps the held set.",
    )

    # --- Analytics (P3) ---
    events_db_path: str = Field(
        "",
        alias="LLM_QUEUE_EVENTS_DB_PATH",
        description="SQLite path for admit/start/finish/reject events. Empty => events "
        "logged only (no durable store). NEVER LiteLLM's Postgres schema (§4.4/§10).",
    )

    # --- Logging ---
    log_level: str = Field("INFO", alias="LLM_QUEUE_LOG_LEVEL")


@lru_cache
def get_settings() -> Settings:
    """Cached settings singleton."""
    return Settings()  # type: ignore[call-arg]  # values come from the environment
