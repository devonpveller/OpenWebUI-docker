# Copilot instructions — ai-stack

> Keep this file short; [CLAUDE.md](../CLAUDE.md) is the authoritative agent guidance
> and wins wherever the two differ.

## What this repo is

A self-hosted AI stack on Docker, organized as **one Docker Compose project per
plane** around a root `docker-compose.yml` that declares only the shared networks
(`ai-stack_llm-net` / `app-net` / `default`):

- **Planes** `frontend/`, `inference/`, `memory/`, `search/`, `coder/`, `portal/` —
  each `<plane>/docker-compose.yml` with its own `<plane>/.env` (compose loads it
  natively; nothing passes `--env-file`) and its own README. The portal is driven only
  by `scripts/portal/portal-on.ps1`.
- **Inference** (`inference/docker-compose.yml`): `llm-gateway` = LiteLLM front door →
  `llm-queue` → `llama-cpp-upstream` / `llama-cpp-embed-upstream`.
- **Open Brain** (`OB1/docker/docker-compose.yml`, a pinned git submodule) and
  **agent-org** (`agent-org/docker/docker-compose.yml`, Mattermost + the governed
  agent-bridge org) are separate projects that attach to the anchor's networks.
- `stack.manifest.toml` is the inventory of record; `python scripts/stack/stack.py`
  is the driver that reads it.

## Hard rules

1. **Never route inference around LiteLLM.** Callers use the `llama-cpp` /
   `llama-cpp-embed` aliases (they live on `llm-gateway`); only
   health/GPU/recovery probes touch `*-upstream`.
   `scripts/checks/check-llm-gateway-routing.ps1` enforces this pre-commit.
2. **Container rule:** adding/removing/moving a container = the plane compose file +
   `stack.manifest.toml` + recovery (`scripts/recovery/emergency-recovery.ps1` +
   `scripts/stack/stack.ps1`) + the stack-map reference doc together; full checklist
   in `documentation/runbooks/SERVICE-LIFECYCLE.md`.
3. **Git:** never commit or push on the operator's behalf unless explicitly
   asked. Pre-commit hooks live in `.githooks/` (`core.hooksPath`).
4. **Secrets** live only in `.env` / `secrets/` (gitignored). Never stage an
   env file; never hardcode keys (the staged-secrets guard blocks known
   formats, including this stack's `gw-` gateway keys).
5. **Windows notes:** PowerShell 5.1 — keep `.ps1` files ASCII; a `.ps1` that must carry
   non-ASCII MUST be UTF-8 **with BOM** (BOM-less reads as ANSI and garbles);
   never restart `openwebui` alone — tailscale shares its network namespace
   (restart order: openwebui → tailscale).
6. **New documentation goes to the private plan store, not this repo**
   (`../documentation-plans-ai-stack`, cloned beside this checkout). Plans:
   `implementation-guide/<feature>/`; a work item's findings and test plan:
   `implementation-guide/<feature>/findings/<id>.md` and `.../test-plans/<id>.md`;
   notes, findings or evidence with no feature: `journal/notes/`,
   `journal/evidence/<id>/`; retired docs: `journal/archive/`. Pre-commit refuses new
   files under `documentation/notes|evidence|archive/` and new root-level `PLAN*`,
   `TEST-PLAN*` or `*-FINDINGS*` files. CLAUDE.md, "Where documentation goes", has
   the full table.

## Where things are

`frontend/owui/` = canonical OWUI plugin/skill exports (paste-deployed; `manifest.csv`
maps file → OWUI id). `scripts/` = ops plane (recovery, checks, portal,
backups, bridges). `documentation/runbooks/` = operational procedures.
`documentation/implementation-guide/README.md` = per-feature status index.
