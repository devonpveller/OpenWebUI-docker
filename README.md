# ai-stack

Self-hosted AI stack on Windows + Docker Desktop: **Open WebUI** chat frontend,
local **llama.cpp** inference behind a **LiteLLM** gateway with an admission
queue, a memory layer (**mnemory** + **Open Brain**), a private **search
gateway** (SearXNG over Mullvad), a self-improving coding agent
(**little-coder**) with a governed multi-agent org (**agent-org**), and an
internet-facing **portal** (Caddy + Authelia + Cloudflare Tunnel) that is off
by default.

> The previous 1,362-line README described the retired Ollama-era stack; it is
> preserved at `documentation/archive/README-pre-2026-08.md`.

## Topology — compose projects around a network anchor (Part K, 2026-08-21)

| Project | Driven with | Contents |
|---------|-------------|----------|
| **Anchor** (`ai-stack`, root `docker-compose.yml`) | `docker compose up -d` (or `stack.ps1 up anchor`) | **0 services** — owns the shared `ai-stack_llm-net` / `app-net` / `default` networks every project attaches to externally |
| **Frontend** (`frontend/`) | `stack.ps1` or `docker compose -f frontend/docker-compose.yml --env-file .env …` | `openwebui` + `tailscale` (netns pair) + their backups |
| **Inference** (`inference/`) | same pattern | `llm-gateway` + db/ui (LiteLLM front door, holds the aliases), `llm-queue`, `llama-cpp-upstream`, `llama-cpp-embed-upstream`, 2 backups — owns `llm-backend-net` |
| **Memory** (`memory/`) | same pattern | `mnemory`, `mnemory-cloud-gateway` (host :8060), `mnemory-backup` |
| **Search** (`search/`) | same pattern | `vpn` (Mullvad — all egress), `redis`, `searxng`, `gateway` (host :8085) — owns `search-net` |
| **Coder** (`coder/`) | same pattern | `open-terminal`, `little-coder`, `lc-egress`, backup — owns `lc-net` |
| **Portal** (`portal/`) | `scripts/portal/portal-on.ps1` / `portal-off.ps1` | `caddy`, `authelia`, `cloudflared` + watcher/alerter/tripwire/cron sidecars. Internet-exposed auth front-end |
| **Open Brain** (`OB1/docker/`) | `docker compose -f OB1/docker/docker-compose.yml …` | ~29 containers: the `openbrain-*` fleet + its backups + the Open Notebook trio (`surrealdb`, `open_notebook`, backup) |
| **agent-org** (`agent-org/docker/`) | `docker compose -f agent-org/docker/docker-compose.yml …` | Mattermost + `agent-bridge` (the governed org bus) + profile-gated worker/cloud slices |
| **Recovery** | `scripts/recovery/emergency-recovery.ps1` (or `.bat`) | Ordered restart/repair across ALL projects — `recover` / `nuclear` / `gpu-reset` |

Start order: anchor → inference → the caller planes → OB1 → agent-org
(`scripts/stack/stack.ps1 up` runs exactly that). Bring OB1 up **after**
`llm-gateway` is healthy; tear it down before the planes it consumes.

## Inference plane (the one rule)

Every service reaches inference through `http://llama-cpp:8080` /
`http://llama-cpp-embed:8080` — network **aliases on `llm-gateway` (LiteLLM)**,
which forwards through **`llm-queue`** (per-caller admission/priority) to the
real llama.cpp servers (`*-upstream`). **Never route inference around
LiteLLM**; only health/GPU/recovery probes may target `*-upstream` directly.
Enforced at commit time by `scripts/checks/check-llm-gateway-routing.ps1`.

## Quickstart (fresh clone)

```powershell
git config core.hooksPath .githooks   # pre-commit: secret guard + line endings + routing check
Copy-Item .env.example .env           # then fill in values — WEBUI_SECRET_KEY is REQUIRED
.\scripts\stack\stack.ps1 up          # every project, dependency order (anchor networks first)
.\scripts\stack\stack.ps1 status      # per-project container states
```

Since the 2026-08-21 Part K restructure the workspace is **compose projects
around a network anchor**: the root `docker-compose.yml` owns only the shared
`ai-stack_*` networks (0 services), and each service tree is its own project
(`frontend/`, `inference/`, `memory/`, `search/`, `coder/`, `portal/`,
`OB1/docker/`, `agent-org/docker/`). Drive one plane manually with
`docker compose -f <plane>/docker-compose.yml --env-file .env ...` from this
directory — the `--env-file` is required (plane files fail loud without it).

`WEBUI_SECRET_KEY` encrypts values at rest in `webui.db` — pin it once and
never rotate casually. All published ports bind to `127.0.0.1`; external
access is Tailscale serve or the portal only.

## Health & recovery

- `scripts/checks/stack-watchdog.ps1` — the 60 s watchdog (runs as a Windows
  service; also covers Docker-engine restart, backup recency, bridge health).
- `scripts/checks/check-openbrain-health.ps1`, `scripts/checks/check-agent-org-health.ps1`.
- `scripts/recovery/emergency-recovery.ps1 recover|nuclear|gpu-reset` — ordered
  cross-project repair. `scripts/recovery/quick-fixes.bat` — interactive menu.
- Never restart `openwebui` alone (tailscale shares its netns): order is
  openwebui → tailscale; the watchdog restores the tailnet serve routes.

## Repo map

| Path | What it is |
|---|---|
| `docker-compose.yml` | The platform ANCHOR — shared networks only (Part K, 2026-08-21) |
| `frontend/` `inference/` `memory/` `search/` `coder/` | The plane compose projects (one service tree each) |
| `owui/` | Canonical deploy-by-paste OWUI artifacts: tools/pipes/filters/actions/skills + `manifest.csv` |
| `scripts/` | Ops plane: recovery, checks, portal lifecycle, backups, bridges (`claude-sessions-bridge/`, `sysadmin-mcp/`, `mattermost-mcp/`), `archive/` |
| `llm-queue/`, `search-gateway/`, `mnemory-cloud-gateway/`, `openbrain-gateway/`, `smolcrawl/`, `little-coder/` | Service source trees |
| `agent-org/` | Governed multi-agent org (bus, charters, floor, 700+ tests) |
| `OB1/` | Open Brain — pinned git submodule since 2026-08-21 (bump via PR; incl. the Open Notebook trio since K.5b) |
| `backup/` + `backups/` | Sidecar scripts/Dockerfiles + produced artifacts |
| `documentation/runbooks/` | Operational runbooks (incident response, backups, updates…) |
| `documentation/implementation-guide/` | Per-feature plans — see its README index for shipped/draft status |
| `documentation/archive/` | Retired docs, kept for history |
| `CLEANUP-PLAN.md` | The living restructure/cleanup plan (v3) |

## Conventions

- **Git:** never commit or push on the operator's behalf unless asked.
- **Container rule:** add/remove/move a container ⇒ compose file + recovery
  scripts + stack-map doc change **together** (`/stack-map` skill checks).
- **Secrets:** live only in `.env` / `secrets/` (both gitignored). The
  pre-commit guard blocks staged env files and known token formats.
- Security posture: [SECURITY.md](SECURITY.md). Stack topology on demand:
  the `/stack-map` skill.
