"""Environment-driven config (no secrets in files — PLAN §8 / README G-convention).

Everything is read from the process environment (compose injects it). Defaults are
chosen so the service *and its tests* run without a live stack: DATABASE_URL defaults
to a local SQLite file so the gate FSM can be exercised with zero external deps.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AO_", extra="ignore")

    # ── Service ────────────────────────────────────────────────────────────
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "info"

    # ── State store (fail-safe persistence — governance §3.0 invariant i) ───
    # SQLAlchemy async URL. Postgres in prod (asyncpg); SQLite default for
    # dev/tests. A `frozen` effort MUST survive a restart, so this store is the
    # source of truth for gate state — never in-memory in production.
    database_url: str = "sqlite+aiosqlite:///./agent_bridge.db"

    # ── Chat platform (Mattermost) — OD-3: behind a ChatAdapter interface ───
    chat_adapter: str = Field(
        default="fake",
        description="fake | mattermost — which ChatAdapter to instantiate",
    )
    mattermost_url: str = "http://mattermost:8065"
    mattermost_bot_token: str = ""          # env only (no secrets in files)
    mattermost_ws_url: str = ""             # derived from mattermost_url if empty
    mgmt_channel: str = "mgmt"              # Human Operator <-> PO <-> PM (§7)

    # ── Comms model (COMMS-MODEL — channel = project, effort = thread) ───────
    # One stable channel per project (#proj-<slug>); efforts are THREADS inside it, so
    # the sidebar never grows with task volume. Two permanent function channels carry
    # cross-cutting operational signal (the §2 routing table).
    default_project: str = "sandbox"        # #proj-sandbox for throwaway/unassigned work
    incidents_channel: str = "incidents"    # wake-storm / undeliverable / crash notices
    suggestions_channel: str = "suggestions"  # worker suggestion pool -> learning loop (§6)
    # Notification discipline (CM.6): coalesce this many rapid worker commands into one
    # thread post to keep effort threads readable. Failures/denials always post immediately.
    activity_batch: int = 5

    # ── Local model lane (existing air-gapped llm-gateway — PLAN §3.4) ──────
    # Callers reach inference transparently via the `llama-cpp` alias. We add
    # nothing to this gateway; we only consume it. NEVER probe model health (C5).
    local_api_base: str = "http://llama-cpp:8080/v1"
    local_api_key: str = "agent-org"       # any non-empty string (permissive gateway)
    worker_model: str = "qwen36-27b"
    judge_model: str = "qwen36-27b"        # same model = zero swap thrash (OD-10)

    # ── Cloud model lane (separate llm-gateway-cloud — CONDITIONAL, Pc) ──────
    # Only wired if the P0.5 capability-floor gate mandates a cloud judge.
    cloud_enabled: bool = False
    cloud_api_base: str = "http://llm-gateway-cloud:4000/v1"
    cloud_api_key: str = ""                # a virtual key (env only)

    # ── Worker pool (scheduler — PLAN §3.6, machine B) ──────────────────────
    # Static, conservatively-sized semaphore: there is NO live GPU-occupancy
    # signal (/slots is dead on llama-swap — C6), so the interactive reserve is
    # held by CONFIG, not by probing. Default 1 worker at 3-parallel @ ~83k.
    max_concurrent_workers: int = 1
    # Base URLs of the pooled little-coder daemons (comma-separated). Empty in
    # P0-P4; filled when the worker profile is enabled (P5).
    worker_instance_urls: str = ""
    worker_poll_interval_s: float = 3.0
    worker_poll_timeout_s: float = 1800.0
    # FALLBACK repo only. The org works on ANY project onboarded via `/project add` (a repo per
    # project, resolved from the effort's #proj-<slug> channel — see modules/projects.py). This is
    # just the default for a #mgmt request that names no project; empty = the sandbox pool. If set,
    # it is auto-registered as a project on boot so it appears in the registry + gets a channel.
    default_repo: str = ""
    # Path the bridge writes the tinyproxy egress allowlist to (a volume the ao-git-egress proxy
    # mounts + reloads on change). Empty = don't manage the file (dev/tests).
    egress_allowlist_file: str = ""

    # ── Wake bus reliability (event-gateway — PLAN §3.1.1) ──────────────────
    wake_undeliverable_bound_s: float = 300.0   # past this, an undelivered wake is a §3 trigger

    # ── Organizational-level constraints (governance §5) ────────────────────
    wake_storm_window_s: float = 60.0
    wake_storm_max: int = 12                # bounded auto-hand-offs per effort/window
    disagreement_max_exchanges: int = 6     # two agents disagree > N -> §3 trigger

    # ── Audit mirror to Open Brain (governance §5/§6, P6.2) ─────────────────
    openbrain_mirror_enabled: bool = False
    openbrain_url: str = "http://openbrain-gateway:8061"
    openbrain_key: str = ""                # env only

    # ── Cost-tiered supervision (governance §3, P3.7) ───────────────────────
    monitor_sample_rate: float = 0.25      # expensive-continuous LLM monitor sampling (0..1)

    # ── Ground + dry-run, risk-gated (UX-FLOW Stage 4, P4.0) ────────────────
    # Grounding submits an effort's assumptions to the shared openbrain-research service
    # (reach it BY CONTAINER NAME on ai-stack_llm-net; :8818 host loopback is unreachable from
    # a container). Best-effort + OFF by default (like the OB mirror) — advisory context, never a
    # gate. The risk-gated DRY-RUN is the actual execution gate (§4.0/§4.5).
    grounding_enabled: bool = False
    research_url: str = "http://openbrain-research:8000"
    research_key: str = ""                  # env only (if the service requires one)
    grounding_timeout_s: float = 300.0      # bound the poll; on timeout grounding is skipped
    grounding_poll_interval_s: float = 5.0

    # ── Project-context anchor (UX-FLOW Stage 1, P3.8) ──────────────────────
    # Before the readiness gate, run a ONE-TIME read-only worker survey of the repo (languages,
    # structure, conventions) and cache it per project, so the gate ANCHORS to the real codebase
    # instead of guessing (and stops asking placement/language/pattern questions). Only fires when
    # a real repo is focused (default_repo / per-project repo); the sandbox has nothing to survey.
    project_survey_enabled: bool = True

    # ── Charters / rule store (P3) ──────────────────────────────────────────
    charters_dir: str = "charters"
    floor_dir: str = "floor"
    profiles_dir: str = "profiles"


@lru_cache
def get_settings() -> Settings:
    return Settings()
