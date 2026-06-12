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
| **Main** (`ai-stack`) | `docker compose ...` | core (`openwebui`, `tailscale`, `llama-cpp`, `llama-cpp-embed`, `watchtower`), memory (`mnemory`, `mnemory-gateway`), search (`tor`, `redis`, `searxng`, `gateway`, `mcpo`), coder (`open-terminal`, `little-coder`, `lc-mcpo`, `lc-egress`), aux (`smolcrawl-pipelines`, `surrealdb`, `open_notebook`), backups (9 cron sidecars incl. `*-backup` + `openbrain-db/wiki-backup`) |
| **Main — Portal** (`profiles: [internet]`) | `scripts/portal-on.ps1` / `portal-off.ps1` | **profile-gated, NOT in a default `up`:** `caddy`, `authelia`, `cloudflared`, `portal-init`, `portal-alerter`, `authelia-watcher`, `authelia-notif-bridge`, `integrity-tripwire`, `portal-cron`, `tunnel-watcher` (+ `caddy-backup`, `authelia-backup`). Internet-exposed auth front-end. |
| **Open Brain** (`open-brain`) | `docker compose -f OB1/docker/docker-compose.yml ...` | ~23 `openbrain-*` containers (incl. a 5-container scheduled slice: `cron` + 4 HTTP-triggered jobs) — a **separate** project that attaches to the main stack's `ai-stack_llm-net` as an external network |
| **Recovery stack** | `scripts/emergency-recovery.ps1` (or `.bat`) | Ordered restart/repair across **both** compose projects — `recover` / `nuclear` / `gpu-reset`. Does **not** manage the profile-gated Portal. |

A plain `docker compose` command never touches Open Brain (its own project) **or
the Portal** (profile-gated). Bring OB1 up after `llama-cpp` is healthy; tear it
down before the main stack. The Portal has its own lifecycle (`portal-on/off.ps1`).

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
