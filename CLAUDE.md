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
| **Main** (`ai-stack`) | `docker compose ...` | core (`openwebui`, `tailscale`, `llama-cpp`, `llama-cpp-embed`, `watchtower`), memory (`mnemory`, `mnemory-gateway`, `mnemory-backup`), search (`tor`, `redis`, `searxng`, `gateway`, `mcpo`), coder (`open-terminal`, `little-coder`, `lc-mcpo`, `lc-egress`, `little-coder-backup`), aux (`smolcrawl-pipelines`, `surrealdb`, `open_notebook`, `openwebui-backup`) |
| **Open Brain** (`open-brain`) | `docker compose -f OB1/docker/docker-compose.yml ...` | 10 `openbrain-*` containers — a **separate** project that attaches to the main stack's `ai-stack_llm-net` as an external network |
| **Recovery stack** | `scripts/emergency-recovery.ps1` (or `.bat`) | Ordered restart/repair across **both** compose projects — `recover` / `nuclear` / `gpu-reset` |

A plain `docker compose` command never touches Open Brain — it is its own
project. Bring OB1 up after `llama-cpp` is healthy; tear it down before the
main stack.

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
