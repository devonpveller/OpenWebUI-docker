# Copilot instructions — ai-stack

> Regenerated 2026-08-20 from CLAUDE.md + README.md (the previous version
> described the retired Ollama-era stack). Keep this file short; CLAUDE.md is
> the authoritative agent guidance.

## What this repo is

A self-hosted AI stack on Windows + Docker Desktop, organized as **multiple
Docker Compose projects** (Part K, 2026-08-21, is dissolving the old main
project into per-plane projects; root `ai-stack` = the network anchor):

- **Main** (`ai-stack`, `docker-compose.yml`): Open WebUI, tailscale,
  mnemory memory layer, private search gateway (Mullvad + SearXNG),
  little-coder, aux services, backup sidecars — shrinking as planes split
  out. The portal is its OWN compose project since 2026-08-21
  (`portal/docker-compose.yml`), driven only by `scripts/portal/portal-on.ps1`.
- **Inference** (`inference/docker-compose.yml`, own project since K.1):
  `llm-gateway` = LiteLLM front door → `llm-queue` →
  `llama-cpp-upstream`/`llama-cpp-embed-upstream`, + gateway db/ui and the
  llm-gateway/lm-models backups. Drive with `--env-file .env` from the root.
- **Open Brain** (`OB1/docker/docker-compose.yml`): a separate project of
  ~26 `openbrain-*` containers (incl. its own db/wiki backup sidecars),
  attaching to `ai-stack_llm-net` externally.
- **agent-org** (`agent-org/docker/docker-compose.yml`): Mattermost + the
  governed agent-bridge org.

## Hard rules

1. **Never route inference around LiteLLM.** Callers use the `llama-cpp` /
   `llama-cpp-embed` aliases (they live on `llm-gateway`); only
   health/GPU/recovery probes touch `*-upstream`.
   `scripts/checks/check-llm-gateway-routing.ps1` enforces this pre-commit.
2. **Container rule:** adding/removing/moving a container = change the compose
   file + `scripts/recovery/emergency-recovery.ps1`/`.bat` + the stack-map reference
   doc together.
3. **Git:** never commit or push on the operator's behalf unless explicitly
   asked. Pre-commit hooks live in `.githooks/` (`core.hooksPath`).
4. **Secrets** live only in `.env` / `secrets/` (gitignored). Never stage an
   env file; never hardcode keys (the staged-secrets guard blocks known
   formats, including this stack's `gw-` gateway keys).
5. **Windows notes:** PowerShell 5.1 — `.ps1` files carrying non-ASCII MUST be UTF-8 **with BOM** (BOM-less reads as ANSI and garbles);
   never restart `openwebui` alone — tailscale shares its network namespace
   (restart order: openwebui → tailscale).

## Where things are

`owui/` = canonical OWUI plugin/skill exports (paste-deployed; `manifest.csv`
maps file → OWUI id). `scripts/` = ops plane (recovery, checks, portal,
backups, bridges). `documentation/runbooks/` = operational procedures.
`documentation/implementation-guide/README.md` = per-feature status index.
