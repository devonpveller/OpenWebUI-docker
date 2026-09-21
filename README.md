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
python scripts/stack/stack.py health      # 16 functional probes; exit code = failures
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

## Posture: local-first, cloud-capable

**No component in this stack sends a prompt, a document or a memory to a model
provider by default.** Every model call goes to llama.cpp on this host through
the LiteLLM gateway, and that gateway is attached to two networks that are both
`internal: true` - `llm-net`, declared that way by the anchor
[`docker-compose.yml`](docker-compose.yml), and `llm-backend-net`, declared that
way by [`inference/docker-compose.yml`](inference/docker-compose.yml). The
cloud-capable parts are present in the tree and inert: each is gated behind a
compose profile, a credential, or a script the operator runs by hand. That is
stack-layers decision **D14** - keep the components, ship them off, and write
down what turns each one on.

That is not the same as "nothing reaches the internet". Three containers do,
from a fresh clone, and they are named at the end of this section.

**How this list was built, so it can be rebuilt and disagreed with.** Two
stages, because either alone gets it wrong:

1. **Render, then classify by network.** `docker compose config --format json`
   for every plane, every profile; a service is a *candidate* if any network it
   joins is non-internal. An `external: true` reference carries no internal
   flag in a plane's own render, so resolve those against the anchor:
   `ai-stack_llm-net` is `internal: true`, `ai-stack_app-net` and
   `ai-stack_default` are ordinary bridges.
2. **Read the candidate's source for an outbound call.** Being on a bridge is a
   *route*, not traffic. `llm-gateway-ui`, the search `gateway`, `openbrain-ext`
   and the portal's watchers are all on bridges and all make no call off this
   host; they are listed under "network-capable, no outbound call" below rather
   than in the table.

A grep for provider names finds stage 2 and misses stage 1 entirely - which is
how the first version of this section missed four services, every one of them
unprofiled. Where a sentence here and a compose file disagree, the compose file
wins.

### The table

| Cloud-capable component | Where it is defined | What it does when off | What turns it on | Where it egresses |
|---|---|---|---|---|
| LiteLLM cloud model group (`cloud-large`, `cloud-small`) | `inference/config/litellm/model_list/cloud.openrouter.yaml` | `config/litellm/assemble-config.py` drops every model entry whose `os.environ/VAR` reference is unset or empty, and logs the drop with its reason. The two models are not registered, so `/v1/models` does not list them and nothing else about the gateway changes. | `OPENROUTER_API_KEY` in `inference/.env`. It is blank in `inference/.env.example`. | **Nowhere.** See "The D14 mechanism" below: the key makes these models LISTED, not reachable. |
| agent-org cloud lane: `llm-gateway-cloud`, `llm-gateway-cloud-db`, `ao-egress` | `agent-org/docker/docker-compose.yml`, `agent-org/config/litellm-cloud.config.yaml` | All three carry `profiles: ["cloud"]`, and `agent-org/docker/.env.example` sets no `COMPOSE_PROFILES` at all, so none of them renders. `AO_CLOUD_ENABLED=false` separately keeps `agent-bridge` on the local lane. | `cloud` in `COMPOSE_PROFILES` (or `--profile cloud`), plus `OPENROUTER_API_KEY`, `AO_CLOUD_DB_PASSWORD`, `AO_CLOUD_MASTER_KEY` and `AO_CLOUD_ENABLED=true` in `agent-org/docker/.env`. | `llm-gateway-cloud` has no internet leg of its own: it sits on `ao-net` and `ao-cloud-egress-net` (`internal: true`) with `HTTP_PROXY`/`HTTPS_PROXY` pointed at `ao-egress`, the one dual-homed container. Read the allowlist note under the table before turning this on. |
| `ao-git-egress`, and the worker pool behind it | `agent-org/docker/docker-compose.yml` | `profiles: ["workers"]`, so with no `COMPOSE_PROFILES` neither the proxy nor the pool renders. | The `workers` profile. | A default-deny tinyproxy on `ao-worker-net` (`internal: true`) + the project bridge. **The mechanism, since it is not uniform:** only `ao-ot-1`/`ao-ot-2` carry `HTTP_PROXY`; `ao-worker-1`/`-2` carry none and are confined by `ao-worker-net` having no route out at all. The filter file lives on the shared `ao-egress-config` volume - `agent-org/docker/egress/egress-reload.sh` seeds it with `github.com` + `githubusercontent.com` and SIGHUPs tinyproxy whenever `agent-bridge` rewrites it as projects and hosts are onboarded from Mattermost. |
| `agent-bridge`'s GitHub App (the capability plane) | `agent-org/agent-bridge/app/config.py`, `agent-org/docker/docker-compose.yml` | `Settings.github_app_enabled` is false unless `github_app_id` is set **and** the private-key file exists, so the plane stays offline and the bridge runs normally without it. | `AO_GITHUB_APP_ID` + `AO_GITHUB_APP_OWNER` in `agent-org/docker/.env` (absent from the example - this plane leaves `${VAR:-}` names out on purpose), and a readable key at `agent-org/agent-bridge/secrets/github-app-key.pem`. | `https://api.github.com`, directly from `ao-net`, which is a plain bridge. This one does **not** go through `ao-egress`. |
| `lc-egress`, and the `open-terminal` proxied through it | `coder/docker-compose.yml`, `little-coder/docker/Dockerfile.egress` | Nothing: it is unprofiled and **starts whenever the coder plane starts**. What is "off" is the destination set - tinyproxy runs `FilterDefaultDeny Yes`, so any host not matching the allowlist is refused, and the proxy initiates nothing on its own. | No variable. The allowlist is baked into the image from `little-coder/docker/egress-allowlist.txt`, which ships `github.com` and `githubusercontent.com`; widening it is an edit plus a rebuild. | `CONNECT` to 443 and 22 on allowlisted hosts, out of the coder project's own `default` bridge. `open-terminal` has no other route: it is on `lc-net` (`internal: true`) and `llm-net` (also internal). |
| `vpn` (Mullvad WireGuard, gluetun) | `search/docker-compose.yml` | **This is an egress that is meant to be on.** It is unprofiled, so it renders and starts with the search plane and dials Mullvad itself. From the examples it cannot connect: `search/.env.example` ships `MULLVAD_WG_PRIVATE_KEY=change-me-real-wg-private-key`, a placeholder rather than a blank because the `${...:?}` guard rejects empty. | Real values for `MULLVAD_WG_PRIVATE_KEY` and `MULLVAD_WG_ADDRESSES` in `search/.env`. | Mullvad, over WireGuard. It exists so search does **not** leak: `searxng` sits on `search-net` (`internal: true`) and `search/searxng/settings.yml` points `outgoing.proxies` at `http://vpn:8888`, so engine queries and page fetches have no other way out and HTTPS `CONNECT` resolves DNS at the far end of the tunnel. |
| `cloudflared` | `portal/docker-compose.yml` | `profiles: [internet]`, and `portal/.env.example` deliberately carries no `COMPOSE_PROFILES` line. The whole plane is `manual` in `stack.manifest.toml`, so the driver never starts it either. | `scripts/portal/portal-on.ps1`, which passes `--profile internet` on the command line, plus `CLOUDFLARE_TUNNEL_TOKEN` in `portal/.env` (blank in the example). | Cloudflare's edge, from `edge-net`. It is the portal's only ingress - `caddy` publishes no host port. |
| `portal-alerter` | `portal/docker-compose.yml` | Unprofiled, but it can only start when the portal does, which is by hand. | The portal being up, plus Google OAuth files at `secrets/google/portal-alerter/credentials.json` and `token.json`. | `oauth2.googleapis.com` and `gmail.googleapis.com`, on `notify-net`. **Do not read `auth-net`'s `internal: true` as "the portal has one way out":** the render has FOUR non-internal networks - `edge-net`, `notify-net`, the external `app-net`, and an implicit `default` that both backup sidecars join - and `caddy` itself is on `app-net` and `edge-net`. `notify-net` is the alerter's egress leg, not the plane's only one. |
| `tailscale` | `frontend/docker-compose.yml` | `profiles: [tailscale]`, and `frontend/.env.example` ships `COMPOSE_PROFILES=stock`, so a fresh clone renders neither it nor its backup. | `tailscale` in `COMPOSE_PROFILES` in `frontend/.env` - necessarily together with `gpu`, because `network_mode: service:openwebui` names the `gpu` definition - plus `TAILSCALE_AUTH_KEY`, which the example leaves as `putyourtskeyhere`. | Tailscale's coordination servers and DERP relays, from inside `openwebui`'s network namespace. |
| `openwebui` (either profile) | `frontend/docker-compose.yml` | Nothing - one of the two definitions is what this plane exists to run. The `stock` one is on the project-local `owui-net`; the `gpu` one additionally joins `ai-stack_default`, `app-net` and `llm-net`. | Whichever profile is active. | Both sit on an internet-capable bridge. Its web SEARCH does not use that - `SEARXNG_QUERY_URL` points at the search plane's gateway - but that setting covers the query only; what Open WebUI's own loaders fetch at runtime is upstream behaviour this repo does not pin (no `HF_HUB_OFFLINE` is set beside `HF_HOME`). Treat it as network-capable and unbounded rather than as bounded by `SEARXNG_QUERY_URL`. |
| `openwebui-backup` | `frontend/docker-compose.yml` | Nothing. It is **unprofiled and renders under `stock`**, i.e. in the quickstart deployment, and its `command:` begins `apk add --no-cache pigz` on every container start. | Nothing - this is on by default. | The Alpine package CDN, over `owui-net`. The compose comment beside the network list says so in as many words. It is the only runtime package install in any compose file here; the other backup sidecars run their script and nothing else. |
| `mnemory-cloud-gateway` | `memory/docker-compose.yml` | Nothing - it starts with the memory plane. The gateway PROCESS dials only `mnemory`; its one upstream is `MNEMORY_URL`. | It is on whenever `memory` is. `MNEMORY_GATEWAY_KEY` is the key a cloud client presents; that client never holds `MCP_API_KEY`, which the gateway injects upstream. | It makes no outbound call of its own, and publishes on `127.0.0.1:8060` only. |
| `openbrain-gateway` (cloud door) and `openbrain-ops-gateway` | `OB1/docker/docker-compose.yml`; the image is built from `openbrain-gateway/` in this repo | Two instances of one image. The cloud door force-filters reads to `share == "cloud"` and blocks the aggregate tools; the OPS door (`GATEWAY_PROFILE: ops`, for host processes such as the claude-sessions bridge) filters on `exposure == "ops"` and allowlists only the agent-memory tools. | `OPENBRAIN_GATEWAY_KEY` / `OPS_GATEWAY_KEY` in `OB1/docker/.env` - and they must differ; the ops door's `${OPS_GATEWAY_KEY:?…never the cloud one}` guard says so. | The gateway process makes no outbound call, and both publish on loopback (`:8061`, `:8062`). **It is not simply "a door in", though:** `openbrain-gateway/app.py`'s default `WRITE_TOOLS` allowlist includes `ingest_url` and `ingest_urls`, so a remote client holding the cloud key can make `openbrain-mcp` fetch a URL of its choosing - see the next row. |
| `openbrain-mcp` | `OB1/docker/docker-compose.yml`, source `OB1/integrations/kubernetes-deployment/index.ts` | **Nothing gates it.** It carries no profile, sits on `obnet` (a bridge) and is the core of the OB1 plane. The `ingest_url` / `ingest_urls` tools call a bare `fetch(url)` with `redirect: "follow"` on a caller-supplied URL; that file configures no proxy client at all, unlike `openbrain-research`. | Nothing. It is live whenever Open Brain is. | Any host the caller names, unproxied and following redirects. This is the broadest egress in the stack and it is reachable both locally and, for those two tools, through the cloud door above. |
| `openbrain-grounding-backfiller` | `OB1/docker/docker-compose.yml`, source `OB1/integrations/grounding-backfiller/index.ts` | **Nothing gates it** - unprofiled, on `obnet` + `llm-net` + `ai-stack_default`. `WIKI_BASE` defaults to `https://en.wikipedia.org`. | Nothing. | Wikipedia, and the source URLs it re-fetches. It tries `FETCH_PROXY_URL` (default `http://vpn:8888`) first, but `REFETCH_ALLOW_DIRECT` **defaults to `true`** and the refetch path falls back to a DIRECT, unproxied fetch when the proxied one comes back thin. This one fails OPEN; set `REFETCH_ALLOW_DIRECT=false` in `OB1/docker/.env` if that is not what you want. |
| `openbrain-wiki`'s `WIKI_GIT_REMOTE` | `OB1/docker/docker-compose.yml` | Set to `""`, which the compiler reads as local-commits-only: vault history is kept, nothing is pulled or pushed. The SSH URL sits beside it, commented out. | Restoring that URL (and a passphrase-less deploy key at `WIKI_GIT_SSH_KEY`). | `github.com` over SSH - the compiled wiki is force-pushed to a private repo. The present-but-inert shape this whole section is about. |
| OB1's scheduled chain: `openbrain-digest`, `openbrain-gmail-pull`, `openbrain-gmail-prune`, `openbrain-podcast` | `OB1/docker/docker-compose.scheduled.yml` | Unprofiled, but each mounts a Google OAuth client secret and token from `OB1/secrets/`, which is gitignored and absent from a fresh clone. (A fresh clone cannot render this plane at all until two empty `OB1/recipes/*/.env` files exist.) | Putting those OAuth files in place - and the `open-brain` product being enabled at all. | Google's APIs (Gmail read and send, Calendar read), plus `wttr.in` for the digest's weather brief, out of `obnet`. |
| `openbrain-research` | `OB1/docker/docker-compose.yml` | `profiles: ["research"]`. | The `research` profile, which `enable research` writes. | Page fetches go through `FETCH_PROXY_URL`, defaulting to `http://vpn:8888` - the search plane's Mullvad tunnel - and searches to `http://gateway:8080`. **This one** connects TO the privacy boundary rather than around it; the backfiller two rows up is the same shape with the fallback left open, and `openbrain-mcp` has no proxy at all. Do not generalise "OB1 fetches through the tunnel" from this row. |

### Network-capable, no outbound call

These are on ordinary bridges and so pass stage 1, but their source makes no
call off this host. They are listed so the criterion is visible and so a reader
who disagrees has something to argue with:

- **`llm-gateway-ui`** - on `app-net` so the portal's Caddy can front `/ui`. Its
  `litellm.ui.config.yaml` declares no `model_list`, sets `telemetry: false`,
  and the service sets `LITELLM_LOCAL_MODEL_COST_MAP=True`, so there is a route
  and no call. It carries no alias and serves no inference.
- **the search `gateway`** - on `ai-stack_default` for cross-project DNS; its
  only HTTP client targets `searxng`.
- **`openbrain-ext`** - its one outbound `fetch` is `WIKI_RECOMPILE_URL`,
  internal.
- **the portal's `caddy`, watchers, tripwire and `portal-cron`** - internal
  targets only. Caddy attempts no ACME: every site address in the `Caddyfile` is
  `http://` or a bare port, which disables auto-HTTPS (the `https://` strings in
  that file are redirect TARGETS and comments). `authelia`'s notifier is `filesystem`
  and it is on `auth-net` (`internal: true`).
- **`status-pipe/`** - every request target is an internal `host:port`.
- **the backup sidecars other than `openwebui-backup`** - on a project bridge,
  but each only runs its own script; the NAS sync (`scripts/backup/backup-to-nas.ps1`)
  is SMB to a LAN address, not the internet.
- **`mattermost`** - FLAGGED rather than cleared: it is on `ao-net`, a plain
  bridge, and nothing in this repo sets `MM_LOGSETTINGS_ENABLEDIAGNOSTICS`.
  Whether Team Edition phones home on its defaults is an upstream fact this
  repo cannot settle.

### Host-side, not compose

Three things that egress are **not containers**, so a compose render cannot see
them. They run on the host, from this repo:

- **The sysadmin Telegram channel** - `scripts/sysadmin-mcp/telegram_notify.py`
  POSTs to `api.telegram.org`, and `scripts/sysadmin-mcp/telegram_listener.py`
  POLLS it for operator commands. The listener is an **inbound control path**
  that no firewall rule sees, because it is the host reaching out. Off without
  the bot token and chat id.
- **The claude-sessions bridge** (`scripts/claude-sessions-bridge/bridge.py`) -
  runs the `claude` CLI headless with `BRIDGE_MODEL` defaulting to `opus`, so
  every bridge turn is a call to Anthropic from the host, and it also posts to
  Telegram. This is the one place the stack talks to a frontier provider at all.
  It is a scheduled host process, not part of any plane; it is off unless the
  operator runs it.
- **The Open WebUI plugins in `owui/`** - deploy-by-paste, tracked in
  `owui/manifest.csv`. `owui/tools/github_chat_mcp_tools.py` targets
  `https://api.github.com`, and `owui/tools/fileshed.py` permits `curl`, `wget`
  and network `git` subcommands from inside the `openwebui` container, behind
  its own valves. They live in the OWUI database, not in a compose file.

### The D14 mechanism, exactly

Setting `OPENROUTER_API_KEY` in `inference/.env` makes `assemble-config.py` keep
the two cloud entries, so `llm-gateway` registers them and `/v1/models` lists
them. It does **not** make them work. `llm-gateway` is attached to `llm-net` and
`llm-backend-net` and to nothing else, and both are internal-only, so the
container has no route off this host and a request for `cloud-large` fails where
LiteLLM tries to reach openrouter.ai. Making it real would mean giving that
container an egress path - attaching it to an internet-capable network, or
pointing it at a dual-homed allowlisted proxy the way `llm-gateway-cloud` points
at `ao-egress`. **Neither is done here, and neither is a casual change**:
keeping the gateway off every internet-capable bridge is the supply-chain
posture that also explains `LITELLM_LOCAL_MODEL_COST_MAP=True` (no cost-map
fetch at boot) and the digest-pinned image. agent-org took the other route
deliberately - its cloud lane is a *separate* LiteLLM behind its own egress
proxy, and `agent-org/config/litellm-cloud.config.yaml` says in as many words
not to add OpenRouter to the local gateway.

**Before you enable the agent-org cloud lane, read its allowlist.** `ao-egress`
builds from `little-coder/docker/Dockerfile.egress`, whose `CMD` runs tinyproxy
against the allowlist COPYd into the image at build time. The `EGRESS_ALLOWLIST`
environment variable the compose file sets on that service is read by nothing in
that image, so the effective allowlist is the baked `github.com` /
`githubusercontent.com` pair and `openrouter.ai` would be denied. That fails
closed, which is the safe direction, but it means the cloud lane does not work
as shipped. Recorded in
[`documentation/notes/stack-layers-sl-docs-posture-findings.md`](documentation/notes/stack-layers-sl-docs-posture-findings.md);
fixing it is a compose or image change, not a documentation one.

### What a fresh clone actually does

Copy each plane's `.env.example` and:

- Every **provider** credential is blank (`OPENROUTER_API_KEY`,
  `CLOUDFLARE_TUNNEL_TOKEN`, `AO_CLOUD_API_KEY`, `LC_DEPLOY_TOKEN`,
  `LC_SELF_REMOTE_PAT`) or an unmistakable placeholder
  (`TAILSCALE_AUTH_KEY=putyourtskeyhere`,
  `MULLVAD_WG_PRIVATE_KEY=change-me-real-wg-private-key`). The cloud lane's own
  local secrets are placeholders too (`AO_CLOUD_DB_PASSWORD=change-me-cloud-db`,
  `AO_CLOUD_MASTER_KEY=change-me-cloud-master`) - they are not provider
  credentials, and the lane is profile-gated anyway.
- The only **active** `COMPOSE_PROFILES` assignment in any example is
  `frontend/.env.example`'s `COMPOSE_PROFILES=stock`. `inference`'s is commented
  out, and so is a `gpu,tailscale` line a few lines above the active one; the
  other planes have no such line at all. **No active assignment names `cloud`,
  `internet`, `workers` or `tailscale`,** so no profile-gated component renders.
- **Three containers still reach the internet**, and none of them is a model
  provider. Measured from the renders above, not reasoned about:

| Container | Why | Plane |
|---|---|---|
| `vpn` | dials Mullvad itself at start; the whole point of the plane. Cannot connect on the example's placeholder key. | search |
| `openwebui-backup` | `apk add --no-cache pigz` on every start, against the Alpine CDN. **Renders under `stock`, so it is in the quickstart.** | frontend |
| `lc-egress` | up with the plane and dual-homed; it initiates nothing itself, but it is the route `open-terminal` uses, and the allowlist is the control. | coder |

The quickstart enables `frontend` alone: Open WebUI on `owui-net` plus that
backup sidecar. Bring up `coder` or `search` and the other two join it.

## Health and recovery

```powershell
python scripts/stack/stack.py status              # per-plane container states
python scripts/stack/stack.py health              # 16 functional probes across every plane
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
