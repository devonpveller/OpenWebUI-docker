# Copilot instructions — ai-stack

> Regenerated 2026-08-20 from CLAUDE.md + README.md (the previous version
> described the retired Ollama-era stack). Keep this file short; CLAUDE.md is
> the authoritative agent guidance.

## What this repo is

A self-hosted AI stack on Windows + Docker Desktop, organized as **two Docker
Compose projects plus a profile-gated portal**:

- **Main** (`ai-stack`, `docker-compose.yml`): Open WebUI, tailscale,
  inference plane (`llm-gateway` = LiteLLM front door → `llm-queue` →
  `llama-cpp-upstream`/`llama-cpp-embed-upstream`), mnemory memory layer,
  private search gateway (Mullvad/Tor + SearXNG), little-coder, aux services,
  12 backup sidecars. Portal services carry `profiles: [internet]` and only
  run via `scripts/portal-on.ps1`.
- **Open Brain** (`OB1/docker/docker-compose.yml`): a separate project of
  ~24 `openbrain-*` containers, attaching to `ai-stack_llm-net` externally.
- **agent-org** (`agent-org/docker/docker-compose.yml`): Mattermost + the
  governed agent-bridge org.

## Hard rules

1. **Never route inference around LiteLLM.** Callers use the `llama-cpp` /
   `llama-cpp-embed` aliases (they live on `llm-gateway`); only
   health/GPU/recovery probes touch `*-upstream`.
   `scripts/check-llm-gateway-routing.ps1` enforces this pre-commit.
2. **Container rule:** adding/removing/moving a container = change the compose
   file + `scripts/emergency-recovery.ps1`/`.bat` + the stack-map reference
   doc together.
3. **Git:** never commit or push on the operator's behalf unless explicitly
   asked. Pre-commit hooks live in `.githooks/` (`core.hooksPath`).
4. **Secrets** live only in `.env` / `secrets/` (gitignored). Never stage an
   env file; never hardcode keys (the staged-secrets guard blocks known
   formats, including this stack's `gw-` gateway keys).
5. **Windows notes:** PowerShell 5.1 (ASCII, no BOM for scripts it parses);
   never restart `openwebui` alone — tailscale shares its network namespace
   (restart order: openwebui → tailscale).

## Where things are

`owui/` = canonical OWUI plugin/skill exports (paste-deployed; `manifest.csv`
maps file → OWUI id). `scripts/` = ops plane (recovery, checks, portal,
backups, bridges). `documentation/runbooks/` = operational procedures.
`documentation/implementation-guide/README.md` = per-feature status index.
