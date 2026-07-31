# CLAUDE.md — ai-stack workspace

Self-hosted AI stack: Open WebUI + local LLM inference, a memory layer, a
private search gateway, a self-improving coding agent, and Open Brain.

## Stacks at a glance

This workspace is **two separate Docker Compose projects** plus a recovery
layer. Run the `/stack-map` skill (or read
[.claude/skills/stack-map/references/workspace-stacks.md](.claude/skills/stack-map/references/workspace-stacks.md))
for the full inventory — networks, ports, dependency order.

| Stack | Driven with | Contents |
|-------|-------------|----------|
| **Main** (`ai-stack`) | `docker compose ...` | core (`openwebui`, `tailscale`, `llm-gateway` + `llm-gateway-db` — LiteLLM analytics **front door**, holds the `llama-cpp`/`llama-cpp-embed` aliases since 2026-06-12; `llm-gateway-ui` — master-key'd Admin-UI sidecar / analytics dashboard at tailnet :8445/ui, shares `llm-gateway-db`, serves no inference, since 2026-06-14; `llama-cpp-upstream`, `llama-cpp-embed-upstream` — real inference (llama-swap) behind the gateway; `watchtower`), memory (`mnemory`, `mnemory-gateway`), search (`vpn` — Mullvad WireGuard, SearXNG's engine-query egress since 2026-06-14; `tor` — page-fetch egress; `redis`, `searxng`, `gateway`, `mcpo`), coder (`open-terminal`, `little-coder`, `lc-mcpo`, `lc-egress`), aux (`smolcrawl-pipelines`, `surrealdb`, `open_notebook`), backups (10 cron sidecars incl. `*-backup` + `llm-gateway-backup` + `openbrain-db/wiki-backup`) |
| **Main — Portal** (`profiles: [internet]`) | `scripts/portal-on.ps1` / `portal-off.ps1` | **profile-gated, NOT in a default `up`:** `caddy`, `authelia`, `cloudflared`, `portal-init`, `portal-alerter`, `authelia-watcher`, `authelia-notif-bridge`, `integrity-tripwire`, `portal-cron`, `tunnel-watcher` (+ `caddy-backup`, `authelia-backup`). Internet-exposed auth front-end. |
| **Open Brain** (`open-brain`) | `docker compose -f OB1/docker/docker-compose.yml ...` | ~23 `openbrain-*` containers (incl. a scheduled slice: `cron` + 4 HTTP-triggered jobs + the profile-gated `openbrain-idea-refinery` drain — the Idea Refinery) — a **separate** project that attaches to the main stack's `ai-stack_llm-net` as an external network |
| **Recovery stack** | `scripts/emergency-recovery.ps1` (or `.bat`) | Ordered restart/repair across **both** compose projects — `recover` / `nuclear` / `gpu-reset`. Does **not** manage the profile-gated Portal. |

A plain `docker compose` command never touches Open Brain (its own project) **or
the Portal** (profile-gated). Bring OB1 up after `llm-gateway` (and its
`llama-cpp-upstream` / `llama-cpp-embed-upstream` servers) is healthy; tear it
down before the main stack. The Portal has its own lifecycle (`portal-on/off.ps1`).

**Inference plane (since 2026-06-12):** every service reaches inference through
`http://llama-cpp:8080` / `http://llama-cpp-embed:8080`, which are now **network
aliases on `llm-gateway` (LiteLLM)** — the analytics front door. It forwards by
model name to `llama-cpp-upstream` (llama-swap → llama.cpp) and
`llama-cpp-embed-upstream`. **Never route inference around LiteLLM** (it's the
analytics inlet + the future multi-backend router) — this includes tailnet serve
routes (`/llama-cpp`, `/llama-cpp-embed`), which must proxy to the `llama-cpp`
alias, **not** `*-upstream`. Only health/GPU/recovery probes may target the
**real** servers (`*-upstream`) directly, not the gateway. Enforced at change
time by `scripts/check-llm-gateway-routing.ps1` (fails if any inference/serve
endpoint points at a `*-upstream` server); the durable goal is network isolation
so callers physically cannot reach `*-upstream`. Config gotchas:
`config/litellm.config.yaml` runs **permissive (no master_key)** with
`background_health_checks: false` (a model health-probe forces a llama-swap
load → thrash); `config/llama-swap.config.yaml` uses `--no-mmap` (mmap of the big
GGUF over the Windows `C:` bind mount hangs). See `litellm-proxy-status` memory +
`documentation/implementation-guide/LiteLLM-Proxy/`.

## Conventions

- **Git:** never commit or push on the user's behalf unless explicitly asked.
- **Adding/removing a container** means changing three places together: the
  compose file, the recovery scripts' service inventory + shutdown/startup
  sequences (`emergency-recovery.ps1` / `.bat`), and the stack-map reference
  doc. The `/stack-map` skill checks for this drift.
- **Shell:** Windows + PowerShell. Recovery scripts assume Docker Desktop.
- `modules/emergency-recovery/` is a stale OWUI guidance module (still names
  the disabled `ollama` container) — not part of the live recovery path.

## Pointers

- Stack topology / "what runs here?" → `/stack-map` skill
- Recovery after a crash or netns break → `scripts/emergency-recovery.ps1`
- little-coder design + workflow → `documentation/little-coder/`
- Private search gateway → `documentation/web-search/`
