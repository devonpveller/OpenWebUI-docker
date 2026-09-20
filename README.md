# ai-stack

A self-hosted AI stack on Docker: **Open WebUI** chat, local **llama.cpp**
inference behind a **LiteLLM** gateway with an admission queue, a memory layer
(**mnemory** + **Open Brain**), a private **search gateway** (SearXNG over
Mullvad), a self-improving coding agent (**little-coder**) with a governed
multi-agent org (**agent-org**), and an internet-facing **portal**
(Caddy + Authelia + Cloudflare Tunnel) that is off by default.

**A fresh clone gives you Open WebUI and nothing else.** Then you read the
product menu below, pick one more thing, and turn it on.

> The previous 1,362-line README described the retired Ollama-era stack; it is
> preserved at
> [`documentation/archive/README-pre-2026-08.md`](documentation/archive/README-pre-2026-08.md).

## Quickstart

You need **Docker** and **Python 3.11 or newer** (the driver is standard-library
only, so a fresh host needs nothing else).

**The commands here are written for PowerShell**, because this stack is
developed on Windows + Docker Desktop. The driver itself is Python
(`scripts/stack/stack.py`, standard library only) and runs anywhere Docker and
Python do; what is PowerShell-only is the **lifecycle, recovery and check
scripts** these READMEs point at - `portal-on.ps1`, `emergency-recovery.ps1`,
`stack-watchdog.ps1` and the rest, which are `.ps1` and assume PowerShell 5.1.
In the block below only the two `Copy-Item` lines are shell-specific - `cp`
does the same job - because the two `python` lines are written with forward
slashes, which both shells accept on Windows.

```powershell
git config core.hooksPath .githooks       # pre-commit checks (.githooks/pre-commit)
Copy-Item .env.example .env               # the anchor's own; nearly empty
Copy-Item frontend/.env.example frontend/.env
#   then set WEBUI_SECRET_KEY in frontend/.env - it is REQUIRED and encrypts
#   values at rest in webui.db, so pin it once and never rotate casually
python scripts/stack/stack.py init        # writes .stack/state.json: frontend, alone
python scripts/stack/stack.py up          # the anchor's networks, then Open WebUI
```

Open WebUI is then on **http://127.0.0.1:3000**. That is two containers:
`openwebui` on the pinned upstream image and its backup sidecar. No GPU, no
local build, no other plane - `frontend/.env.example` ships
`COMPOSE_PROFILES=stock` for exactly this.

Check on it:

```powershell
python scripts/stack/stack.py list        # planes, what is enabled, the products
python scripts/stack/stack.py status      # docker compose ps per plane
python scripts/stack/stack.py doctor      # docker, compose, env files, blank keys
python scripts/stack/stack.py health      # 15 functional probes; exit code = failures
```

`init` refuses if a key a plane needs is missing or blank, and names the key
and the file. Every verb takes `--dry-run` where it would change something, and
prints the exact `docker compose` line it would run.

`.\scripts\stack\stack.ps1 <verb>` is a thin shim over the same driver, kept
because runbooks and muscle memory say it. It forwards `up down status restart
health stats list doctor inventory`; `enable`, `disable` and `init` are
`stack.py` only.

## The product menu

A **product** is a vertical slice: the planes it needs to run, the compose
profiles it turns on, and the surfaces a person reads it through. All of it is
declared in [`stack.manifest.toml`](stack.manifest.toml), which is the file of
record - the table below is derived from it, and
`python scripts/stack/stack.py list` prints the same set.

| `enable <product>` | Starts (planes, in order) | Profiles it turns on | Surfaces (`--headless` drops these) | Keys it will ask for | What the host must provide |
|---|---|---|---|---|---|
| **chat** | anchor, frontend | - | - | `WEBUI_SECRET_KEY` | nothing beyond Docker |
| **inference** | anchor, inference | `inference: local` | - | `LITELLM_DB_PASSWORD`, `LITELLM_MASTER_KEY` | an NVIDIA GPU + the NVIDIA Container Toolkit, chat GGUFs under `${LM_MODELS_DIR}`, and `bge-m3-f16.gguf` - **all three for the `local` profile only** |
| **memory** | anchor, inference, memory | - | - | + `MCP_API_KEY` | (inherits inference's) |
| **search** | anchor, search | - | - | `MULLVAD_WG_PRIVATE_KEY`, `MULLVAD_WG_ADDRESSES` | `/dev/net/tun` and `NET_ADMIN` for the WireGuard kill-switch, and a Mullvad WireGuard account |
| **open-brain** | anchor, inference, search, ob1 | - | `ob1: wiki` | + `MCP_ACCESS_KEY`, `POSTGRES_PASSWORD`, `OPS_GATEWAY_KEY`, `OPENBRAIN_GATEWAY_KEY` | the OB1 submodule checked out; Google OAuth under `OB1/secrets/google/` for the scheduled digest chain; optionally a speech-to-text server on the HOST at `:8000` |
| **research** | anchor, inference, frontend, search, ob1 | `ob1: research` | `ob1: wiki`, `ob1: notebook` | open-brain's + `WEBUI_SECRET_KEY` | open-brain's + the frontend's |
| **coding-agent** | anchor, inference, frontend, coder | - | `frontend` (the whole plane) | + `OPEN_TERMINAL_API_KEY` | nothing of its own |
| **agent-org** | anchor, inference, agent-org | `agent-org: workers` | - | + `MM_DB_PASSWORD`, `AO_DB_PASSWORD` | the `little-coder:local` and `little-coder-open-terminal:local` images (the coder plane builds them); an OpenRouter key for the `cloud` profile |
| **digest** | anchor, inference, search, ob1 | `ob1: research`, `ob1: notebook` | - | open-brain's | open-brain's |
| **portal** | anchor, frontend, portal | `portal: internet` | - | `WEBUI_SECRET_KEY`, `CLOUDFLARE_TUNNEL_TOKEN`, `AUTHELIA_JWT_SECRET`, `AUTHELIA_SESSION_SECRET`, `AUTHELIA_STORAGE_ENCRYPTION_KEY`, `PUBLIC_DOMAIN` | a Cloudflare tunnel token and a public domain. **The portal is `manual`: the driver never starts it** - `scripts/portal/portal-on.ps1` does |

**Reading the keys column.** A product's real key set is the union of the
`keys` of every plane in its "Starts" cell - so it is always cumulative, and
`+ X` is shorthand for "everything an earlier row already introduced for the
planes this one shares, plus X". `memory` is `LITELLM_DB_PASSWORD`,
`LITELLM_MASTER_KEY`, `MCP_API_KEY`; `coding-agent` is those first two plus
`WEBUI_SECRET_KEY` plus `OPEN_TERMINAL_API_KEY`. You never have to work it out:
`stack.py enable <product>` refuses with the whole list, each key beside the
file it belongs in.

Two more things that table is saying quietly and are worth saying out loud:

- **A product pulls what its planes REQUIRE**, which is why `memory` starts
  inference and `open-brain` starts search. It does **not** pull their optional
  edges. In particular `research` enables the inference *plane* but not its
  `local` profile, so on a GPU-less host it is a cloud-model gateway feeding the
  research engine.
- **`--headless` drops surfaces, never engines.** `enable open-brain
  --headless` leaves the knowledge core without the wiki viewer; `enable
  coding-agent --headless` leaves little-coder without Open WebUI in front of
  it. A profile marked `default` in the manifest survives `--headless`, which is
  why a surface must never be marked default.

## Add one thing, after the first run

```powershell
python scripts/stack/stack.py enable search       # a plane, or
python scripts/stack/stack.py enable research     # a product
python scripts/stack/stack.py enable open-brain --headless   # engines, no reading surface
python scripts/stack/stack.py up                  # start what is now enabled, in order
python scripts/stack/stack.py disable search      # take it back out
```

Before it writes anything, `enable` refuses in two ways, and both name the
remedy:

- **a required plane is off** - `refused: memory requires inference, which is
  not enabled (python scripts/stack/stack.py enable inference)`;
- **a key is blank or missing** - it names each key, the file it belongs in, and
  which plane reads it.

So the loop is: `enable` it, read the refusal, copy that plane's
`.env.example`, fill in what it named, `enable` again, `up`. A name that is
both a plane and a product resolves to the PLANE, and the driver says so;
`--product` / `--plane` disambiguate.

`enable` writes `.stack/state.json` - gitignored, per-host, and the only place
"what does THIS machine run" lives. The manifest is never written by the
driver.

## The layout: compose projects around a network anchor

Since the 2026-08-21 Part K restructure the workspace is **one compose project
per plane**, around a root project that owns only the shared networks.

| Plane (project) | File | What it holds |
|---|---|---|
| **anchor** (`ai-stack`) | [`docker-compose.yml`](docker-compose.yml) | **0 services.** Owns `ai-stack_llm-net`, `ai-stack_app-net` and `ai-stack_default`, which every other project attaches to externally. `up` here creates networks and starts nothing. |
| **frontend** | [`frontend/`](frontend/README.md) | `openwebui` (host :3000) + the `tailscale` netns companion + both backups. Three profiles: `stock`, `gpu`, `tailscale`. |
| **inference** | [`inference/`](inference/README.md) | `llm-gateway` (LiteLLM, holds the aliases) + its DB and Admin UI + `llm-queue` + the two `*-upstream` llama.cpp servers + 2 backups. Owns `llm-backend-net`. |
| **memory** | [`memory/`](memory/README.md) | `mnemory` + `mnemory-cloud-gateway` (host :8060) + backup. |
| **search** | [`search/`](search/README.md) | `vpn` (Mullvad, all egress) + `redis` + `searxng` + `gateway` (host :8085). Owns `search-net`. |
| **coder** | [`coder/`](coder/README.md) | `open-terminal` + `little-coder` (metrics host :9091) + `lc-egress` + backup. Owns `lc-net`. |
| **portal** | [`portal/`](portal/README.md) | 12 services: `caddy`, `authelia`, `cloudflared`, the watchers, the alerter, the tripwire, the cron and 2 backups. Publishes no host port; `manual`. |
| **ob1** (`open-brain`) | `OB1/docker/docker-compose.yml` | 30 containers: the `openbrain-*` fleet, its backups and the Open Notebook trio. A **pinned git submodule**. |
| **agent-org** | `agent-org/docker/docker-compose.yml` | Mattermost (+db) + `agent-bridge`, the governed org bus, + profile-gated `workers` / `cloud` slices. |

Each plane directory holds its own compose file, its own `.env.example` and its
own README; the per-plane README is the detailed one, and this file is the map.

**Every plane owns its environment.** Compose loads `<plane>/.env` NATIVELY
from the project directory, so **nothing passes `--env-file`** and your working
directory is irrelevant to it. A variable lives in the file of the plane whose
service reads it; a value two planes read is declared in each. The root `.env`
keeps only what the anchor, the driver or a non-plane-scoped script reads.
`COMPOSE_PROFILES` is per-plane too. Every one of the eight planes REFUSES to
render when its own file is absent rather than silently blanking - the six
in-repo planes on a `${VAR:?}` guard, `agent-org/docker/` on a service-level
`env_file: .env`, `OB1/docker/` on its own `${OPS_GATEWAY_KEY:?}` (all measured
2026-09-19). Migrating a host that still has one big root `.env`:
[`documentation/runbooks/env-split-migration.md`](documentation/runbooks/env-split-migration.md).

Drive one plane by hand with `docker compose -f <plane>/docker-compose.yml ...`
from this directory.

### The one inference rule

Every service reaches inference through `http://llama-cpp:8080` /
`http://llama-cpp-embed:8080` - network **aliases on `llm-gateway` (LiteLLM)**,
which forwards through **`llm-queue`** (per-caller admission and priority) to
the real llama.cpp servers. **Never route inference around LiteLLM**; only
health, GPU and recovery probes may target `*-upstream` directly. Enforced at
commit time by `scripts/checks/check-llm-gateway-routing.ps1`, and by the
topology: the upstreams live on a network native to the inference project.

And never GET LiteLLM `/health` through the alias - it loads every model the
gateway advertises. `/health/liveliness` is the one to probe.

## Health and recovery

```powershell
python scripts/stack/stack.py status              # per-plane container states
python scripts/stack/stack.py health              # 15 functional probes across every plane
python scripts/stack/stack.py up|down [plane]     # dependency-ordered; --all for every plane
python scripts/stack/stack.py restart <plane>     # one plane in place
python scripts/stack/stack.py stats               # inference demand + queue statistics (WINDOWS ONLY)
```

`health` is read-only and its exit code is the NUMBER of failed probes. A
failing probe never stops the sweep.

**`stats` is the driver's one Windows-gated verb.** It delegates to
`scripts/stack/stack-stats.ps1`, which reads the llm-queue `/observe` board and
the LiteLLM spend ledger through `docker exec ... psql` and is PowerShell 5.1
only. Off Windows it does not degrade - it REFUSES, and names the two ways out:

> refused: `stats` reads the LiteLLM ledger through scripts/stack/stack-stats.ps1,
> which is PowerShell 5.1 only and is not ported. Run it on the Windows host
> (`powershell -NoProfile -ExecutionPolicy Bypass -File scripts/stack/stack-stats.ps1`),
> or read the queue board directly at llm-queue's `/observe/queue`.

Every other verb is platform-neutral. A verb that silently printed nothing and
exited 0 is the failure class this repo hunts, which is why this one is loud.

Manual recovery, escalating:

```powershell
# One service misbehaving - restart it inside its own plane:
docker compose -f <plane>/docker-compose.yml restart <service>

# NETNS RULE: never restart openwebui alone - tailscale shares its network
# namespace. Order is openwebui, wait healthy, then tailscale. The frontend
# project's depends_on encodes that for whole-project operations.

# Crashed or wedged - the ordered repair paths:
.\scripts\recovery\emergency-recovery.ps1 recover     # health-gated restart sweep
.\scripts\recovery\emergency-recovery.ps1 nuclear     # full teardown + rebuild, all projects
.\scripts\recovery\emergency-recovery.ps1 gpu-reset   # GPU/CUDA path rebuild

# Internet exposure is ALWAYS deliberate, and never the driver's:
.\scripts\portal\portal-on.ps1   /   portal-off.ps1   /   portal-status.ps1

# Restore from backups (documentation/runbooks/restore-from-snapshot.md):
.\scripts\backup\restore-from-snapshot.ps1 -SnapshotRoot .\backups `
  -Date <yyyy-MM-dd> [-Services <name|all>] [-Apply]
# -SnapshotRoot and -Date are MANDATORY; without -Apply it only plans.
```

Watching the watchers: `scripts/checks/stack-watchdog.ps1` runs on a 60-second
loop as the `StackWatchdog` scheduled task (tailnet serve repair, backup
recency, Docker-engine restart recovery); the sysadmin scheduled tasks post to
Mattermost `#sysadmin`.

## Backups and the maintenance rotation

Every stateful store has exactly one backup sidecar **in its own plane
project**, writing verified artifacts (plus sha256 sentinels) to
`./backups/<service>/`, mirrored WEEKLY to the NAS (Sundays at 04:00 -
`scripts/backup/install-nas-backup-task.ps1`, its `New-ScheduledTaskTrigger
-Weekly -DaysOfWeek Sunday -At 4am`). Two scheduler idioms: **sleep-loop** for
interval tars (once at container start, then every `BACKUP_INTERVAL` seconds)
and **supercronic** for cron-timed DB dumps.

**Changing a backup interval**: set the variable in that plane's `.env` and
recreate that one sidecar (`docker compose -f <plane>/docker-compose.yml up -d
<sidecar>`). All intervals are seconds; the defaults live in the compose files:

| Variable | Sidecar (plane) | Default |
|---|---|---|
| `MNEMORY_BACKUP_INTERVAL` | mnemory-backup (memory) | 86400 (daily) |
| `OPENWEBUI_BACKUP_INTERVAL` | openwebui-backup (frontend) | 86400 |
| `TAILSCALE_BACKUP_INTERVAL` | tailscale-backup (frontend) | 86400 |
| `LITTLE_CODER_BACKUP_INTERVAL` | little-coder-backup (coder) | 86400 |
| `LM_MODELS_BACKUP_INTERVAL` | lm-models-backup (inference) | 604800 (weekly; empty = disabled) |
| `OPENBRAIN_WIKI_BACKUP_INTERVAL` | openbrain-wiki-backup (ob1; set in `OB1/docker/.env`) | 86400 |
| *(not an interval)* | llm-gateway-backup (inference) sleeps 86400 s, hard-coded in its entrypoint; `caddy-backup` / `authelia-backup` (portal) are supercronic on `*_BACKUP_CRON`, default `0 3 * * *`; `openbrain-db-backup` and `open-notebook-backup` likewise, defaulting to 02:00 and 02:20 UTC | |

**Disk rotation** is autonomous: the `AI-Stack Weekly Maintenance` scheduled
task (Sundays 03:15) runs `scripts/maintenance/weekly-maintenance.ps1` - a safe
docker reclaim (dangling images and build cache; **never** a volume prune),
then the elevated vhdx compaction task, then a post to Mattermost `#sysadmin`.
Re-register after edits with `weekly-maintenance.ps1 -Register`.

Restore procedures: `documentation/runbooks/restore-from-snapshot.md` (per
store) and `scripts/backup/restore-from-snapshot.ps1` (orchestrated DR).
Adding or changing a service? Work through
[`documentation/runbooks/SERVICE-LIFECYCLE.md`](documentation/runbooks/SERVICE-LIFECYCLE.md)
- it is what keeps backups, recovery, health probes and the sysadmin plane
telling the truth.

## Repo map

| Path | What it is |
|---|---|
| [`stack.manifest.toml`](stack.manifest.toml) | **The inventory of record**: every plane's compose file, requires, optional edges, profiles, host needs, keys and ports, and every product. Committed; the driver never writes it. |
| [`scripts/stack/`](scripts/stack/README.md) | The driver (`stack.py`, standard library only) and its design doc. `stack.ps1` is a shim. |
| `docker-compose.yml` | The platform ANCHOR - shared networks only |
| `frontend/` `inference/` `memory/` `search/` `coder/` `portal/` | The plane projects: compose file, `.env.example`, README, and (since 2026-09-19) their own source, config and build inputs |
| `owui/` | Canonical deploy-by-paste Open WebUI artifacts: tools, pipes, filters, actions, skills + `manifest.csv` |
| `status-pipe/` | The Server Status pipe subsystem - the only code mount into the Open WebUI container |
| `scripts/` | Ops plane: recovery, checks, portal lifecycle, backups, maintenance rotation, the bridges (`claude-sessions-bridge/`, `sysadmin-mcp/`, `mattermost-mcp/`), `issue-ops/`, `agent-harness/`, `archive/` |
| `openbrain-gateway/`, `smolcrawl/`, `little-coder/` | Service source trees that are not plane-internal (the search gateway is `search/gateway/`, mnemory's cloud gateway is `memory/mnemory-gateway/`, and the queue is `inference/llm-queue/`) |
| `agent-org/` | The governed multi-agent org (bus, charters, floor, 700+ tests) |
| `OB1/` | Open Brain - a pinned git submodule since 2026-08-21 (bump via PR), including the Open Notebook trio |
| `backup/` + `backups/` | Sidecar scripts and Dockerfiles, and the artifacts they produce |
| `documentation/runbooks/` | Operational runbooks (incident response, backups, updates, the env-split migration) |
| `documentation/notes/` | Findings and evidence - where a true problem found while working on something else goes |
| `documentation/implementation-guide/` | The per-feature status INDEX (it spans two repos) plus the two plan sets that must stay here: `multi-agent-concurrency/` and `dark-factory-unification/`. Plans themselves live in the private `documentation-plans-ai-stack` repo. |
| `documentation/archive/` | Retired docs, kept for history |
| [`CLEANUP-PLAN.md`](CLEANUP-PLAN.md) | The 2026-08 restructure (v3), **CLOSED 2026-09-19** - history, not a worklist. Its "v3 CLOSED" section says where each open item went; the successor is `stack-layers/` in the plan store. |

## Conventions

- **Git:** never commit or push on the operator's behalf unless asked.
- **Container rule:** adding, removing or moving a container means the plane
  compose file + `stack.manifest.toml` + recovery + the stack-map doc **in one
  change**. The full checklist is
  [`SERVICE-LIFECYCLE.md`](documentation/runbooks/SERVICE-LIFECYCLE.md); the
  `/stack-map` skill checks for drift.
- **Secrets** live only in `.env` files and `secrets/` (both gitignored). The
  pre-commit guard blocks staged env files and known token formats. `docker
  compose config` renders them interpolated in plaintext - grep the section you
  need rather than printing the whole thing.
- Security posture: [SECURITY.md](SECURITY.md). Stack topology on demand: the
  `/stack-map` skill, or
  [its reference](.claude/skills/stack-map/references/workspace-stacks.md).
