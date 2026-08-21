# ai-stack

Self-hosted AI stack on Windows + Docker Desktop: **Open WebUI** chat frontend,
local **llama.cpp** inference behind a **LiteLLM** gateway with an admission
queue, a memory layer (**mnemory** + **Open Brain**), a private **search
gateway** (SearXNG over Mullvad/Tor), a self-improving coding agent
(**little-coder**) with a governed multi-agent org (**agent-org**), and an
internet-facing **portal** (Caddy + Authelia + Cloudflare Tunnel) that is off
by default.

> The previous 1,362-line README described the retired Ollama-era stack; it is
> preserved at `documentation/archive/README-pre-2026-08.md`.

## Topology — two compose projects + a gated portal

| Stack | Driven with | Contents |
|-------|-------------|----------|
| **Main** (`ai-stack`) | `docker compose …` | core (`openwebui`, `tailscale`, `open-terminal`), inference (`llm-gateway` + db/ui, `llm-queue`, `llama-cpp-upstream`, `llama-cpp-embed-upstream`), memory (`mnemory`, `mnemory-gateway`), search (`vpn`, `tor`, `redis`, `searxng`, `gateway`), coder (`little-coder`, `lc-egress`), aux (`smolcrawl-pipelines`, `surrealdb`, `open_notebook`), 12 backup sidecars |
| **Main — Portal** (`profiles: [internet]`) | `scripts/portal/portal-on.ps1` / `portal-off.ps1` | `caddy`, `authelia`, `cloudflared` + watcher/alerter/tripwire/cron sidecars. Internet-exposed auth front-end — **not** part of a default `up`. |
| **Open Brain** (`open-brain`) | `docker compose -f OB1/docker/docker-compose.yml …` | ~24 `openbrain-*` containers (db, MCP servers, gateway, workers, wiki, research, scheduled digest/podcast slice). Separate project; attaches to `ai-stack_llm-net` as an external network. |
| **agent-org** | `docker compose -f agent-org/docker/docker-compose.yml …` | Mattermost + `agent-bridge` (the governed org bus) + profile-gated worker/cloud slices. |
| **Recovery** | `scripts/recovery/emergency-recovery.ps1` (or `.bat`) | Ordered restart/repair across both projects — `recover` / `nuclear` / `gpu-reset`. |

Bring OB1 up **after** `llm-gateway` is healthy; tear it down before the main
stack. A plain `docker compose` command never touches OB1 or the portal.

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
docker compose up -d                  # main stack (31 services; portal stays down)
docker compose -f OB1/docker/docker-compose.yml up -d   # after llm-gateway is healthy
```

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
| `docker-compose.yml` | Main stack (31 default + 12 portal services, split into compose/<plane>.yml) |
| `owui/` | Canonical deploy-by-paste OWUI artifacts: tools/pipes/filters/actions/skills + `manifest.csv` |
| `scripts/` | Ops plane: recovery, checks, portal lifecycle, backups, bridges (`claude-sessions-bridge/`, `sysadmin-mcp/`, `mattermost-mcp/`), `archive/` |
| `llm-queue/`, `search-gateway/`, `mnemory-gateway/`, `openbrain-gateway/`, `smolcrawl/`, `little-coder/` | Service source trees |
| `agent-org/` | Governed multi-agent org (bus, charters, floor, 700+ tests) |
| `OB1/` | Open Brain (vendored independent repo — own git) |
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
