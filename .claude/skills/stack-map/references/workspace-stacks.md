# Workspace Stack Map

Authoritative inventory of the Docker stacks in this `ai-stack` workspace.
Cross-check against the live compose files before relying on it — the files
are the source of truth; this doc is the curated summary.
Per-container purpose & justification: [documentation/CONTAINER-REGISTRY.md](../../../../documentation/CONTAINER-REGISTRY.md).

**Last reconciled against live compose: 2026-09-19** (stack-layers
`sl-readmes`, re-derived against `sl-compose-anchors` at 55ea48b; every profile
cell, service count and driver name below was re-read from the file it
describes). Since the 2026-08-21 reconcile: each plane gained its own `.env`
and its own README, the plane sources moved into the plane directories, the
repeated hardening / sidecar / healthcheck-timing blocks became per-file YAML
extension fields merged in with `<<: *name` (`sl-compose-anchors`; ten compose
files carry an `x-` anchor today - `git grep -l '^x-[a-z-]*: &' -- '*.yml'` -
and **no rendered service definition changed**, which is why every count below
still holds), and **`stack.manifest.toml` became the declaration of record**,
driven by `python scripts/stack/stack.py` (`scripts/stack/stack.ps1` is now a
shim). What that manifest declares - requires, optional, profiles, ports, keys,
host needs, products - is the machine-readable half of this document; this file
is the rendered topology.

**Since `ac-doc-generator` (2026-09-26) the countable half of this file is
GENERATED**: every service list, container name, profile, published host port,
network attachment and count sits between `stack:` markers and is written by
`python scripts/stack/stack.py docs --write` from the compose renders (each
plane's `.env.example`, `COMPOSE_PROFILES` cleared). The pre-commit hook refuses
a stale block. The hand tables that remain carry only what a render cannot say
- a service's role, why it is shaped the way it is.

**Prior reconcile, 2026-08-21** — Part K restructure
COMPLETE: the root `ai-stack` project is a **pure network anchor (0
services)**; each plane is its own compose project — `frontend` (K.5, incl.
the openwebui+tailscale netns pair), `inference` (K.1, owns
`llm-backend-net`), `memory` (K.2), `search` (K.3, owns `search-net`),
`coder` (K.4, owns `lc-net`, adopted open-terminal); the Open Notebook trio
joined OB1 (K.5b). Earlier that day the
openbrain-db/wiki backups moved into OB1 and `smolcrawl-pipelines`/`-backup`
retired. 2026-08-20 CLEANUP-PLAN v3 execution day: the root compose became a
thin include of `compose/<plane>.yml` files (rendered model proven identical);
**retired**: `watchtower`, `search-mcpo`, `lc-mcpo`; the **portal became its
own compose project `portal`** on 2026-08-21
(`portal/docker-compose.yml`, data migrated to `portal_*` volumes, joins
`ai-stack_app-net` externally); the status-pipe
subsystem consolidated to `status-pipe/` and OWUI's whole-repo mount replaced
by three narrow ro mounts; `frontend/entrypoint.sh` rewritten around a
data-driven route table (ollama/LM Studio blocks gone). Prior reconcile (2026-07-01) — added the **`agent-org`** project
(teams-chat agent orchestration: `mattermost` + `mattermost-db` + `agent-bridge` +
`agent-bridge-db`, plus the profile-gated `workers`/`cloud` planes — see §3). Prior
(2026-06-14): added **`llm-queue`** (B2 front-ended inference admission controller between the
`*-upstream` servers and LiteLLM; chat `api_base` now points at it, llama-swap
`concurrencyLimit: 0`). Prior (2026-06-13): the **LiteLLM `llm-gateway` flip**
(`llama-cpp`/`llama-cpp-embed` → `*-upstream`; the gateway now holds those aliases; +
`llm-gateway-db` / `llm-gateway-backup` / `llm-gateway-db-data`). Prior (2026-06-11): the
portal/auth slice (Authelia/Caddy/Cloudflared + watchers/tripwire), the unified-backup sidecars,
and the portal networks (`edge/auth/app/notify-net`).

Source files:
- `stack.manifest.toml` — **the declaration of record**: every plane's compose
  file, requires/optional edges, profiles, ports, keys and host needs, plus the
  products that group them. Read by `scripts/stack/stack.py`
- `docker-compose.yml` — the **main** (anchor) project: a `networks:` block and
  nothing else, no `include:` and no root `compose/` directory (the watchtower-era
  `docker-compose.override.yml` was archived at K.5 — its settings live in
  `frontend/docker-compose.yml` now)
- `<plane>/README.md` — the per-plane detail (what starts, requires, surfaces,
  host needs, first run, live state) for frontend, inference, memory, search,
  coder and portal
- `portal/local-test.override.yml` — portal test mode, no Cloudflare (own project since 2026-08-21)
- `OB1/docker/docker-compose.yml` (+ `docker-compose.scheduled.yml`) — the **open-brain** project (separate)
- `agent-org/docker/docker-compose.yml` — the **agent-org** project (separate; teams-chat orchestration)

---

## 1. Root anchor — compose project `ai-stack` (networks only since K.5b)

Files: `docker-compose.yml` — **networks only, no `include:`, no services**; the
plane projects are `frontend|inference|memory|search|coder|portal/docker-compose.yml`,
each its own project with its own `.env`.
Run with: `python scripts/stack/stack.py up anchor`, which creates any of the
three networks that is missing and leaves an existing one as it is.
`emergency-recovery.ps1` does the same through `Confirm-AnchorNetworks`. A
bare `docker compose up -d` at the root does NOT create them: on a project with
no services it exits `no service selected` (compose v5.3, ac-recovery-gates R6).

> **Profiles:** <!-- stack:profiled-planes -->5 of the 9 planes declare compose profiles in `stack.manifest.toml`: `inference` (`local`); `frontend` (`stock`, `gpu`, `tailscale`); `ob1` (`idea-refinery`, `research`, `wiki`, `notebook`); `agent-org` (`workers`, `cloud`); `portal` (`internet`)<!-- /stack:profiled-planes -->.
> Each plane's set comes from **its own `<plane>/.env`**, and a
> `--profile` flag on the command line REPLACES that value rather than adding to
> it. **There is no `local-test` compose profile** anywhere in
> `portal/docker-compose.yml`, despite several comments naming one: portal test
> mode is the overlay file `portal/local-test.override.yml`, and
> `portal-on.ps1 -Test` passes no profile at all.
> The **Portal** plane is `manual` in the manifest: the driver never starts or
> stops it, `scripts/portal/portal-on.ps1` / `portal-off.ps1` do.

### Every plane at a glance

<!-- stack:plane-table -->

_Generated by `python scripts/stack/stack.py docs --write`; do not edit between the markers. Service counts are `docker compose config` renders with each plane's `.env.example` and `COMPOSE_PROFILES` cleared, under exactly the profiles named - "the driver's default" is what `up` passes before any `enable`._

| Plane (compose project) | Compose file | Services, by the profiles passed | Published host ports | Started by |
|---|---|---|---|---|
| **anchor** (`ai-stack`) | `docker-compose.yml` | 0 - it declares networks only | none | `up`, always (implicit) |
| **inference** (`inference`) | `inference/docker-compose.yml` | 4 with no profile; 8 with `local` | `127.0.0.1:8081`, `127.0.0.1:8082` | `up`, once enabled |
| **frontend** (`frontend`) | `frontend/docker-compose.yml` | 1 with no profile; 2 with `stock`; 2 with `gpu`; 4 with `gpu` + `tailscale`; every profile (`stock`, `gpu`, `tailscale`) does not render (compose refuses the combination) | `127.0.0.1:3000` | `up`, once enabled |
| **memory** (`memory`) | `memory/docker-compose.yml` | 3 with no profile | `127.0.0.1:8060` | `up`, once enabled |
| **search** (`search`) | `search/docker-compose.yml` | 4 with no profile | `127.0.0.1:8085` | `up`, once enabled |
| **coder** (`coder`) | `coder/docker-compose.yml` | 4 with no profile | `127.0.0.1:9091` | `up`, once enabled |
| **ob1** (`open-brain`) | `OB1/docker/docker-compose.yml` | 20 with no profile; 23 with `idea-refinery` + `research` (the driver's default); 22 with `research`; 24 with `wiki`; 23 with `notebook`; 30 with every profile (`idea-refinery`, `research`, `wiki`, `notebook`) | `127.0.0.1:3001`, `127.0.0.1:5055`, `127.0.0.1:8003`, `127.0.0.1:8061`, `127.0.0.1:8062`, `127.0.0.1:8503`, `127.0.0.1:8810`, `127.0.0.1:8811`, `127.0.0.1:8812`, `127.0.0.1:8813`, `127.0.0.1:8814`, `127.0.0.1:8815`, `127.0.0.1:8816`, `127.0.0.1:8817`, `127.0.0.1:8818`, `127.0.0.1:8819` | `up`, once enabled |
| **agent-org** (`agent-org`) | `agent-org/docker/docker-compose.yml` | 6 with no profile; 13 with `workers`; 9 with `cloud`; 16 with every profile (`workers`, `cloud`) | `127.0.0.1:8065`, `127.0.0.1:8830` | `up`, once enabled |
| **portal** (`portal`) | `portal/docker-compose.yml` | 10 with no profile; 12 with `internet` | none | by hand: `scripts/portal/portal-on.ps1 / scripts/portal/portal-off.ps1` |

<!-- /stack:plane-table -->

### Networks

**The ANCHOR owns exactly three** - `llm-net`, `app-net` and `default`
(`docker-compose.yml`'s `networks:` block, and nothing else in that file). They
are rows 1, 7 and 10 below, not the first three: the table is grouped by subject
rather than by owner. Every other row is **native to the plane project named in
its row** and is created and destroyed with that project.

**This is not every network in the workspace.** The agent-org project declares
three more of its own - `ao-net`, `ao-worker-net` and `ao-cloud-egress-net` -
listed in section 3 rather than here, and OB1 owns `obnet` plus a project
`default` (section 2). The rule for reading any row: a network with an owner
named **anchor** exists once for the whole workspace; anything else is
`<project>_<name>` and dies with its project.

| Network      | Type / owner    | Purpose |
|--------------|-----------------|---------|
| `llm-net`    | internal; **anchor** | **caller plane / shared seam**: every inference consumer sits here and reaches inference ONLY via the `llama-cpp` / `llama-cpp-embed` aliases on **`llm-gateway`** (LiteLLM, in the **inference** project — it attaches externally). The `*-upstream` real servers are NOT here (isolated on the inference project's native `llm-backend-net`) so callers cannot route around LiteLLM |
| `search-net` | internal; **search project** | search gateway isolation — only `vpn` (Mullvad; engine queries AND page fetches since tor retired 2026-08-21) bridges out |
| `lc-net`     | internal; **coder project** | little-coder control plane isolation |
| `llm-backend-net` | internal; **inference project** | the real `*-upstream` servers + `llm-queue`, reachable only by `llm-gateway`. This is what makes routing around LiteLLM physically impossible |
| `owui-net`   | bridge; **frontend project** | the one network both `openwebui` definitions and the always-on backup sidecar share; it is also all the `stock` profile needs |
| `auth-net`   | bridge, internal; **portal project** | portal: caddy ↔ authelia ↔ portal-alerter ↔ watchers (no internet) |
| `app-net`    | bridge; **anchor** | caddy ↔ openwebui / open_notebook / llm-gateway-ui (backends reached only via caddy) |
| `edge-net`   | bridge; **portal project** | portal ingress: cloudflared ↔ caddy |
| `notify-net` | bridge; **portal project** | portal egress chokepoint (portal-alerter → Gmail; portal-cron) - the plane's ONLY internet egress |
| `default`    | bridge; **anchor** (`ai-stack_default`) | host-reachable / internet egress; the cross-project DNS seam for search's `vpn` + `gateway` |
| `obnet`      | bridge; **open-brain project** (`open-brain_obnet`) | OB1's internal plane. Listed here because it used to be attached `external: true` by the aux trio in the root project; since K.5b that trio lives IN open-brain and reaches `openbrain-db` on obnet natively, so no anchor-project service attaches to it - the anchor has none |

### Planes & containers

**Backups (unified snapshot sidecars — `backup/` scripts, nightly cron; NAS-synced)**
| Container | Backs up |
|---|---|
| `openbrain-db-backup` | `pg_dump` of OB1 Postgres (**open-brain** project since 2026-08-21; output still `./backups/openbrain-db`) *(native)* |
| `openbrain-wiki-backup` | openbrain-wiki-data + wiki-assets (**open-brain** project since 2026-08-21; output still `./backups/openbrain-wiki`) |
| `agent-bridge-db-backup` | `pg_dump` of `agent-bridge-db` (**agent-org** project; governance/effort/project state) |
| `mattermost-db-backup` | `pg_dump` of `mattermost-db` (**agent-org** project; conversation content) |
| `caddy-backup` | caddy-data (supercronic, `CADDY_BACKUP_CRON`, default `0 3 * * *`) |
| `authelia-backup` | authelia-data (supercronic, `AUTHELIA_BACKUP_CRON`, default `0 3 * * *`) |

**Portal (internet-exposed front-end — its own project; `manual`, so NOT in any driver `up`)**

<!-- stack:plane-services:portal -->

_Generated by `python scripts/stack/stack.py docs --write`; do not edit between the markers. Rendered from `portal/docker-compose.yml` with `portal/.env.example` and `COMPOSE_PROFILES` cleared, so a service is listed under exactly the profiles that select it._

| Service | Container | Profiles | Host ports | Networks |
|---|---|---|---|---|
| `authelia` | `authelia` | *(none)* | - | `portal_auth-net` (internal) |
| `authelia-backup` | `authelia-backup` | *(none)* | - | `portal_auth-net` (internal), `portal_default` |
| `authelia-notif-bridge` | `authelia-notif-bridge` | *(none)* | - | `portal_auth-net` (internal) |
| `authelia-watcher` | `authelia-watcher` | *(none)* | - | `portal_auth-net` (internal) |
| `caddy` | `caddy` | *(none)* | - | `ai-stack_app-net` (external), `portal_auth-net` (internal), `portal_edge-net` |
| `caddy-backup` | `caddy-backup` | *(none)* | - | `portal_default`, `portal_edge-net` |
| `integrity-tripwire` | `integrity-tripwire` | *(none)* | - | `portal_auth-net` (internal) |
| `portal-alerter` | `portal-alerter` | *(none)* | - | `portal_auth-net` (internal), `portal_notify-net` |
| `portal-cron` | `portal-cron` | *(none)* | - | `portal_notify-net` |
| `portal-init` | `portal-init` | *(none)* | - | `network_mode: none` |
| `cloudflared` | `cloudflared` | `internet` | - | `portal_edge-net` |
| `tunnel-watcher` | `tunnel-watcher` | `internet` | - | `portal_auth-net` (internal), `portal_edge-net` |

<!-- /stack:plane-services:portal -->

<!-- stack:profile-counts:portal -->

_Generated by `python scripts/stack/stack.py docs --write`; do not edit between the markers. Rendered from `portal/docker-compose.yml` with `portal/.env.example` and `COMPOSE_PROFILES` cleared, so a service is listed under exactly the profiles that select it._

| Profiles passed | Services | Added over no profile |
|---|---|---|
| no profile | 10 | - |
| `internet` | 12 | `cloudflared`, `tunnel-watcher` |

<!-- /stack:profile-counts:portal -->

A bare `docker compose -f portal/docker-compose.yml up -d` starts only the
services with no profile, and so no tunnel - which is why the documented path is
`portal-on.ps1`, not a bare `up`. **The role table below leaves out the two
backup sidecars**, `caddy-backup` and `authelia-backup`, which are rows in the
Backups table above.

| Container | Role |
|---|---|
| `portal-init` | one-shot: chown the four volumes to the service UIDs, then exits *(`network_mode: none`)* |
| `caddy` | reverse proxy + `forward_auth`; sole ingress (no host port) |
| `authelia` | SSO / 2FA auth gateway (:9091 internal) |
| `cloudflared` | Cloudflare Tunnel — the only internet ingress |
| `portal-alerter` | Deno alert/digest → Gmail (:8080); the plane's only internet egress |
| `authelia-watcher` | tails auth/access logs → alerts (new-IP, etc.) |
| `authelia-notif-bridge` | forwards Authelia OTP/notifications → alerter |
| `integrity-tripwire` | hashes Caddyfile/Authelia configs; alerts on drift (`TRIPWIRE_CRON`, default `0 4 * * *`) |
| `portal-cron` | supercronic → triggers the daily portal digest (`PORTAL_DIGEST_CRON`, default `0 7 * * *`) |
| `tunnel-watcher` | probes `cloudflared:2000/ready`; alerts on tunnel down |

> **`portal-off.ps1` names ten services explicitly and misses two.**
> `tunnel-watcher` and `authelia-notif-bridge` are not in its list, so they keep
> running after a portal-off. Check with
> `docker compose -p portal -f portal/docker-compose.yml ps`.

### Volumes

**The anchor project declares NONE.** Its `volumes:` section was removed on
2026-09-19; the last entry was the orphan crawl-index volume left behind when
`smolcrawl-pipelines` retired. Every volume belongs to a plane project and
carries that project's prefix:

| Project | Volumes |
|---|---|
| `frontend` | `openwebui-data` |
| `inference` | `llm-gateway-db-data`, `llm-queue-data` |
| `memory` | `mnemory-data` |
| `search` | none, deliberately (redis is in-memory) |
| `coder` | `little-coder-journals`, `-skill`, `-cohorts`, `-polyglot`, `-sessions`, `-workspace` |
| `portal` | `caddy-data`, `caddy-config`, `authelia-data`, `tripwire-data` |
| `open-brain` | `openbrain-db-data`, `openbrain-wiki-data`, `wiki-assets`, `wiki-viewer-srv` (the viewer's published `/srv` snapshots - derived and rebuildable, kept so a recreate never shows the "Building..." splash) |
| `agent-org` | **fifteen**: `mattermost-db-data`, `mattermost-data`, `mattermost-config`, `mattermost-logs`, `mattermost-plugins`, `mattermost-client-plugins`, `agent-bridge-db-data`, `ao-worker-{1,2}-workspace`, `-sessions`, `-journals`, `ao-egress-config`, `llm-gateway-cloud-db-data`. Only the two `*-journals` are backed up - see section 3 |

The pre-split `ai-stack_*` copies still exist on the daemon (data was copied,
not moved, at the 2026-08-21 cutover) and deleting them is the operator's call.
**Restoring into the wrong one looks like a successful restore and changes
nothing** - confirm with
`docker inspect <container> --format "{{range .Mounts}}{{.Name}} {{end}}"`.

---


## 1a. Frontend — compose project `frontend` (SEPARATE since 2026-08-21, Part K.5)

> `frontend/docker-compose.yml` — openwebui + its tailscale netns companion +
> their backups. NETNS RULE unchanged (never restart openwebui alone); the
> project's depends_on encodes the openwebui→tailscale order. All three nets
> attached externally (ai-stack_default / llm-net / app-net) so every DNS
> seam holds. openwebui-data migrated to `frontend_openwebui-data` (~10 GB).
> Images pinned (`openwebui:local` / `tailscale:local`) — rebuilds are
> deliberate, per the UPDATE-MANAGEMENT runbook, never an `up` side effect.
> **SELF-CONTAINED since sl-colo-frontend (2026-09-19):** the plane's build
> inputs — `frontend/Dockerfile.openwebui-gpu`, `frontend/dockerfile.tailscale`
> and the tailscale container's `frontend/entrypoint.sh` — live inside the plane
> directory, and both `build.context` values are `.` (the plane) rather than the
> repo root. The status pipe and the system prompts are plane-internal too since
> ac-planes-contained (2026-09-25): `./status-pipe`, `./system-prompts`. The
> `..`-rooted BIND mounts (`../data/tailscale`, `../backup`, `../backups`) remain:
> those trees are not plane-internal (`backup/` is the shared module the manifest
> declares as `[modules.backup]`). The root `.dockerignore` moved with them to
> `frontend/.dockerignore`; no build context is rooted at the repo root now.

**PROFILE-GATED since 2026-09-19 (stack-layers §2.5 / D8).** This plane renders
differently depending on `COMPOSE_PROFILES`, and **the operator's deployment is
not the default**. Since `sl-env-split` (2026-09-19, D17) the variable is
PER PLANE: compose loads `frontend/.env` natively from this project directory,
so **this plane's full value is `COMPOSE_PROFILES=gpu,tailscale`** and the
inference plane's `local` lives in `inference/.env`. (It used to be one global
assignment in the root `.env` reading `local,gpu,tailscale`, back when every
plane was driven with the same `--env-file`.) A duplicate assignment in one env
file is last-wins and silent, which is why there is only one per file. Without
the frontend's two profiles in that value,
`docker compose -f frontend/docker-compose.yml up -d` starts
`openwebui-backup` and nothing else; `... down` leaves `openwebui` and
`tailscale` running (compose only tears down services whose profile is active);
and **every verb that names `tailscale` — `up -d`, `stop`, `start`, `restart`,
`rm`, `ps`, `config`, and `up -d --force-recreate --no-deps tailscale` — exits
1 with `no such service: openwebui`.** Naming a service activates only that
service's own profile, so `network_mode: service:openwebui` and `depends_on:
openwebui` point outside the project and compose refuses to load it; `--no-deps`
does not help, because the reference resolves at project load. `openwebui` is
the one exception (`restart openwebui`, `build --no-cache openwebui` work) —
the gpu definition names nothing outside its own profile. So the value is
required for the watchdog's seven tailscale repairs (plus two advice strings),
for `emergency-recovery.ps1`, for the manual tailscale rebuild in
`documentation/runbooks/PREVENTION-GUIDE.md`, and for the `stop tailscale openwebui` recipe documented in
`backup/openwebui-restore.sh:9`. TWO callers are independent of it because they pass the
profiles themselves: `scripts/backup/restore-from-snapshot.ps1` (its frontend
and tailscale entries, as the portal and agent-org entries there already did)
and the frontend recipe in `documentation/runbooks/restore-from-snapshot.md`,
both given `--profile gpu --profile tailscale` by this item.
`emergency-recovery.ps1` does NOT pass them — it CHECKS
(`Confirm-FrontendProfiles`) and logs an ERROR naming the fix, because a fixed
`gpu,tailscale` in a generic driver would start the CUDA build and reserve a
GPU on a `stock` host.
`scripts/checks/check-watchdog-repair-targets.ps1` is the check that tells you
whether this host's `.env` is right. A `--profile` flag on the command line
REPLACES `COMPOSE_PROFILES` rather than adding to it.

<!-- stack:profile-counts:frontend -->

_Generated by `python scripts/stack/stack.py docs --write`; do not edit between the markers. Rendered from `frontend/docker-compose.yml` with `frontend/.env.example` and `COMPOSE_PROFILES` cleared, so a service is listed under exactly the profiles that select it._

| Profiles passed | Services | Added over no profile |
|---|---|---|
| no profile | 1 | - |
| `stock` | 2 | `openwebui-stock` |
| `gpu` | 2 | `openwebui` |
| `gpu` + `tailscale` | 4 | `openwebui`, `tailscale`, `tailscale-backup` |
| every profile (`stock`, `gpu`, `tailscale`) | does not render - compose refuses the combination | - |

<!-- /stack:profile-counts:frontend -->

| Profile | For |
|---------|-----|
| *(none)* | nothing useful — a misconfigured `.env` looks like this |
| `stock` | a fresh clone: pinned upstream image, no GPU, no local build, no anchor network, no other plane |
| `gpu` | this host: the CUDA local build, the nvidia device reservation, the `llm-net`/`app-net` seams, the status-pipe mount |
| `tailscale` | the tailnet node. Requires `gpu` — `network_mode: service:openwebui` names that service |

`openwebui-stock` and `openwebui` both declare `container_name: openwebui` and
share `frontend_openwebui-data`, so exactly one may be active: turning on both
is refused by `docker compose config` before anything starts.

<!-- stack:plane-services:frontend -->

_Generated by `python scripts/stack/stack.py docs --write`; do not edit between the markers. Rendered from `frontend/docker-compose.yml` with `frontend/.env.example` and `COMPOSE_PROFILES` cleared, so a service is listed under exactly the profiles that select it._

| Service | Container | Profiles | Host ports | Networks |
|---|---|---|---|---|
| `openwebui-backup` | `openwebui-backup` | *(none)* | - | `frontend_owui-net` |
| `openwebui-stock` | `openwebui` | `stock` | `127.0.0.1:3000->8080` | `frontend_owui-net` |
| `openwebui` | `openwebui` | `gpu` | `127.0.0.1:3000->8080` | `ai-stack_app-net` (external), `ai-stack_default` (external), `ai-stack_llm-net` (external), `frontend_owui-net` |
| `tailscale` | `tailscale` | `tailscale` | - | `network_mode: service:openwebui` |
| `tailscale-backup` | `tailscale-backup` | `tailscale` | - | `ai-stack_default` (external) |

<!-- /stack:plane-services:frontend -->

The role table below is the `gpu,tailscale` deployment.

| Container | Role | GPU |
|---|---|---|
| `openwebui` | Open WebUI chat surface (service `openwebui` under `gpu`; service `openwebui-stock` under `stock`) | yes (`gpu` only) *(external; project-local)* |
| `tailscale` | Tailnet VPN; shares openwebui netns; 8 serve routes (OWUI, llama-cpp aliases — probe = `/health/liveliness` since J.1, ON :8443/:5055, wiki :8444 via caddy:8446, LiteLLM UI :8445, Mattermost :8446) | no *(`network_mode: service:openwebui`)* |
| `openwebui-backup` | openwebui-data (mem-capped 1g; output still `./backups/openwebui`). No profile — it runs in every deployment, which is why it sits on `owui-net` (the one net both openwebui definitions share) rather than `ai-stack_default` |  *(project-local)* |
| `tailscale-backup` | tailscale state dir (bind mount). Gets the project's `default` net implicitly (= `ai-stack_default`) — its compose comment saying "no network attachment" describes the mounts, not the render |  *(external)* |

**Observers know about the profiles.** `stack.ps1 health` prints
`[skip] frontend: 8 tailnet serve routes` instead of a FAIL, and
`stack-watchdog.ps1` skips the whole container-tailscale section
(health/recreate, egress, daemon, node state, serve-route repair) when the
profile is absent — both via the same rule: read the RENDERED project, and fail
OPEN if the render is unreadable *or* if a container named `tailscale` is
running while the render says otherwise (that is this host with
`COMPOSE_PROFILES` missing, and it gets a loud line, not silence). The HOST
Tailscale app check is deliberately outside that guard — it is not a container
this project owns.

---

## 1b. Inference — compose project `inference` (SEPARATE since 2026-08-21, Part K.1)

> `inference/docker-compose.yml` — the LLM host is its own service tree. Drive it
> with `scripts/stack/stack.ps1` or `docker compose -f inference/docker-compose.yml
> ...` from the repo root (fail-loud without `inference/.env`).
> **Split by service group 2026-09-19** (stack-layers `sl-inference-split`): the
> spine file holds `name`, the networks and the volumes and `include:`s
> `inference/compose/{upstreams,queue,gateway,backups}.yml`. ONE project still —
> same project name, same container names, same rendered definitions; only the
> file a definition lives in moved. Relative paths inside `compose/` resolve
> against THAT directory, hence `../../`.
> **Profile `local`** gates the host-specific services (the Profiles column below)
> (`llama-cpp-upstream`, `llama-cpp-embed-upstream`, `llm-queue`,
> `lm-models-backup`). Off, the project renders as `llm-gateway` +
> `llm-gateway-db` + `llm-gateway-ui` + `llm-gateway-backup`: a LiteLLM front
> door that can serve CLOUD models on a machine with no GPU. **The operator's
> deployment runs with it ON**, through `COMPOSE_PROFILES` in **`inference/.env`**
> (which `inference/.env.example` ships COMMENTED OUT, so a straight copy of the
> example is the cloud-only set) and NOT through `--profile local` — `llm-gateway` reads `COMPOSE_PROFILES` to decide which
> model groups to register (`inference/config/litellm/assemble-config.py` merges
> `inference/config/litellm.config.yaml` with
> `inference/config/litellm/model_list/*.yaml`, keeping a
> local group only under the profile and a cloud provider only when its API key
> is set). The GGUF store is `${LM_MODELS_DIR}` (`inference/.env`), no longer a
> literal per-user path. Per-plane detail: [`inference/README.md`](../../../../inference/README.md).
> It ATTACHES to the anchor's `ai-stack_llm-net` (external; llm-gateway carries the
> `llama-cpp`/`llama-cpp-embed` aliases there) and OWNS the internal `llm-backend-net`
> plus the `inference_llm-gateway-db-data` / `inference_llm-queue-data` volumes
> (data migrated from the ai-stack_* volumes at the split).

<!-- stack:plane-services:inference -->

_Generated by `python scripts/stack/stack.py docs --write`; do not edit between the markers. Rendered from `inference/docker-compose.yml` with `inference/.env.example` and `COMPOSE_PROFILES` cleared, so a service is listed under exactly the profiles that select it._

| Service | Container | Profiles | Host ports | Networks |
|---|---|---|---|---|
| `llm-gateway` | `llm-gateway` | *(none)* | - | `ai-stack_llm-net` (external), `inference_llm-backend-net` (internal) |
| `llm-gateway-backup` | `llm-gateway-backup` | *(none)* | - | `ai-stack_llm-net` (external) |
| `llm-gateway-db` | `llm-gateway-db` | *(none)* | - | `ai-stack_llm-net` (external) |
| `llm-gateway-ui` | `llm-gateway-ui` | *(none)* | - | `ai-stack_app-net` (external), `ai-stack_llm-net` (external) |
| `llama-cpp-embed-upstream` | `llama-cpp-embed-upstream` | `local` | `127.0.0.1:8082->8080` | `inference_llm-backend-net` (internal) |
| `llama-cpp-upstream` | `llama-cpp-upstream` | `local` | `127.0.0.1:8081->8080` | `inference_llm-backend-net` (internal) |
| `llm-queue` | `llm-queue` | `local` | - | `inference_llm-backend-net` (internal) |
| `lm-models-backup` | `lm-models-backup` | `local` | - | `inference_llm-backend-net` (internal) |

<!-- /stack:plane-services:inference -->

<!-- stack:profile-counts:inference -->

_Generated by `python scripts/stack/stack.py docs --write`; do not edit between the markers. Rendered from `inference/docker-compose.yml` with `inference/.env.example` and `COMPOSE_PROFILES` cleared, so a service is listed under exactly the profiles that select it._

| Profiles passed | Services | Added over no profile |
|---|---|---|
| no profile | 4 | - |
| `local` | 8 | `llama-cpp-embed-upstream`, `llama-cpp-upstream`, `llm-queue`, `lm-models-backup` |

<!-- /stack:profile-counts:inference -->

| Container | Role | GPU |
|---|---|---|
| `llm-gateway` | **LiteLLM analytics front door** (holds the `llama-cpp` + `llama-cpp-embed` network aliases on :8080; all callers reach inference through it). Routes `/v1/*` by model name; **both chat AND embed** forward to **`llm-queue`** (api_base, since B2/P4); `num_retries:3` (a queue 429 → retry → hold-and-dispatch); read-only `/observe/*` pass-through to `llm-queue` for the live board; master_key + per-caller virtual keys since J.1 2026-08-21 (x-ai-stack-caller lane header) — per-caller spend ledger; `background_health_checks:false` (a model health-probe forces a llama-swap load → thrash) | no *(internal-only; admin/ledger via `docker exec`, not host :4000 — `llm-net` is `internal:true` so host publish is inert; sole bridge)* |
| `llm-queue` | **B2 front-ended inference admission controller** (`inference/llm-queue/` — the source tree moved INTO the plane at sl-colo-inference 2026-09-19, design `DESIGN-B2-inference-queue.md`). Sits between LiteLLM and the `*-upstream` servers (chat + embed): holds-and-dispatches (release-on-completion semaphore, priority heap w/ per-key caps, rolling-T wait estimate, per-model depth backstop — chat 24, embed 256) instead of llama-swap dropping overflow with a flat `429`. Replaces the bare `Too many requests` with a structured 429 + `Retry-After`; `enforce_budget:true` (per-service wait budgets §8b). Read-only state reachable from `llm-net` via the gateway's `/observe/*` pass-through; the **mutating** control API (`POST /queue/{id}/priority`/`cancel`, `/keys/{key}/policy`) is operator-only (`docker exec`, never `llm-net`). Analytics events → own SQLite (`llm-queue-data` volume). Tuning invariant: `LLM_QUEUE_SLOTS` == llama-swap `--parallel` (3) and llama-swap `concurrencyLimit: 0` | no *(internal-only)* |
| `llm-gateway-ui` | **LiteLLM Admin-UI sidecar** (analytics dashboard at `/ui`, added 2026-06-14). A SECOND LiteLLM instance run **with** a `master_key` (`inference/config/litellm.ui.config.yaml` + `.env` `LITELLM_UI_*`) — which LiteLLM 1.88.1 requires for the UI to log in. Serves **no inference** (carries NO `llama-cpp` alias, no caller points at it), shares `llm-gateway-db` so the dashboard reads the SAME spend ledger `llm-gateway` writes. The master_key is isolated here so the permissive main gateway + its junk-key callers stay untouched. Reached via the tailnet **:8445** serve route (`frontend/entrypoint.sh`) and by the portal Caddy, which is why it also joins `app-net` | no *(internal-only; tailnet :8445/ui)* |
| `llm-gateway-db` | Postgres for the LiteLLM spend-log ledger (`llm-gateway-db-data` volume) — shared by `llm-gateway` (writes) and `llm-gateway-ui` (reads) | no |
| `llama-cpp-upstream` | llama-swap inference (was `llama-cpp`) — `qwen36-27b` (∥2); 35B is in llama-swap config but **not registered in the gateway**; one model resident at a time; `--no-mmap` (mmap over the C: bind mount hangs) | yes (device 0) *(isolated)* |
| `llama-cpp-embed-upstream` | bge-m3 embeddings server (was `llama-cpp-embed`) | yes (device 1) *(isolated)* |
| `llm-gateway-backup` | nightly `pg_dump` of the LiteLLM spend ledger (output still `./backups/llm-gateway`) | no |
| `lm-models-backup` | weekly tar of the GGUF model store (HEALTH_TCP liveness probe to `llama-cpp-upstream`; output still `./backups/lm-models`) | no |

---

## 1c. Memory — compose project `memory` (SEPARATE since 2026-08-21, Part K.2)

> `memory/docker-compose.yml` — mnemory + its cloud privacy gateway + backup.
> Attaches to `ai-stack_llm-net` (external); owns `memory_mnemory-data` (data
> migrated) and a project-local default bridge (host-publishes :8060, carries
> the backup's HEALTH_TCP probe). Doors rule unchanged: local callers hit
> mnemory on llm-net; cloud clients get ONLY the gateway. TRAP: `mnemory` is
> pinned to image `mnemory:local` — a fresh build pulls an unpinned newer
> `mcp` package that crash-loops (fastmcp moved); rebuild deliberately only.

<!-- stack:plane-services:memory -->

_Generated by `python scripts/stack/stack.py docs --write`; do not edit between the markers. Rendered from `memory/docker-compose.yml` with `memory/.env.example` and `COMPOSE_PROFILES` cleared, so a service is listed under exactly the profiles that select it._

| Service | Container | Profiles | Host ports | Networks |
|---|---|---|---|---|
| `mnemory` | `mnemory` | *(none)* | - | `ai-stack_llm-net` (external) |
| `mnemory-backup` | `mnemory-backup` | *(none)* | - | `memory_default` |
| `mnemory-cloud-gateway` | `mnemory-cloud-gateway` | *(none)* | `127.0.0.1:8060->8060` | `ai-stack_llm-net` (external), `memory_default` |

<!-- /stack:plane-services:memory -->

| Container | Role |
|---|---|
| `mnemory` | Unified memory layer (mgmt :8051) *(internal only)* |
| `mnemory-cloud-gateway` | Privacy-enforcing MCP proxy for cloud clients *(project-local `memory_default`)* |
| `mnemory-backup` | nightly tar of mnemory-data (output still `./backups/mnemory`) *(project-local)* |

---

## 1d. Search — compose project `search` (SEPARATE since 2026-08-21, Part K.3)

> `search/docker-compose.yml` — the Private Search Gateway (all egress over
> Mullvad WireGuard). Owns `search-net` (internal) natively; `vpn` + `gateway`
> attach EXTERNALLY to the anchor's `ai-stack_default` so their DNS names keep
> resolving for OB1 (openbrain-research/podcast FETCH_PROXY `http://vpn:8888`)
> and OWUI. No data volumes (redis is deliberately in-memory).

<!-- stack:plane-services:search -->

_Generated by `python scripts/stack/stack.py docs --write`; do not edit between the markers. Rendered from `search/docker-compose.yml` with `search/.env.example` and `COMPOSE_PROFILES` cleared, so a service is listed under exactly the profiles that select it._

| Service | Container | Profiles | Host ports | Networks |
|---|---|---|---|---|
| `gateway` | `search-gateway` | *(none)* | `127.0.0.1:8085->8080` | `ai-stack_default` (external), `search_search-net` (internal) |
| `redis` | `search-redis` | *(none)* | - | `search_search-net` (internal) |
| `searxng` | `searxng` | *(none)* | - | `search_search-net` (internal) |
| `vpn` | `search-vpn` | *(none)* | - | `ai-stack_default` (external), `search_search-net` (internal) |

<!-- /stack:plane-services:search -->

| Container | Role |
|---|---|
| `search-vpn` | Mullvad WireGuard (gluetun) — engine-query AND page-fetch egress + kill-switch (HTTP proxy :8888) *(external)* |
| `search-redis` | SearXNG cache |
| `searxng` | Metasearch engine |
| `search-gateway` | REST / Tavily-shim API *(external)* |

---

## 1e. Coder — compose project `coder` (SEPARATE since 2026-08-21, Part K.4)

> `coder/docker-compose.yml` — the little-coder control plane. `open-terminal`
> moved in from core (it is this plane's executor; control-plane DECIDES /
> open-terminal EXECUTES), making `lc-net` fully plane-native. Owns the seven
> coder_little-coder-* volumes (expertise ×5 + sessions + workspace; data
> migrated). llm-net external (inference + the OWUI/agent-org callers of the
> daemon); lc-egress gets internet via a project-local bridge.

<!-- stack:plane-services:coder -->

_Generated by `python scripts/stack/stack.py docs --write`; do not edit between the markers. Rendered from `coder/docker-compose.yml` with `coder/.env.example` and `COMPOSE_PROFILES` cleared, so a service is listed under exactly the profiles that select it._

| Service | Container | Profiles | Host ports | Networks |
|---|---|---|---|---|
| `lc-egress` | `lc-egress` | *(none)* | - | `coder_default`, `coder_lc-net` (internal) |
| `little-coder` | `little-coder` | *(none)* | `127.0.0.1:9091->9090` | `ai-stack_llm-net` (external), `coder_lc-net` (internal) |
| `little-coder-backup` | `little-coder-backup` | *(none)* | - | `coder_default` |
| `open-terminal` | `open-terminal` | *(none)* | - | `ai-stack_llm-net` (external), `coder_lc-net` (internal) |

<!-- /stack:plane-services:coder -->

| Container | Role |
|---|---|
| `open-terminal` | Workspace plane — executes agent commands (egress via `lc-egress`) |
| `little-coder` | Control daemon — decides (daemon :8090) *(metrics)* |
| `lc-egress` | Egress allowlist proxy (git host only) *(project-local)* |
| `little-coder-backup` | nightly tar of the expertise volumes (output still `./backups/little-coder`) *(project-local `coder_default`; it declares no `networks:` key, so compose attaches the project default)* |

## 2. Open Brain — compose project `open-brain` (SEPARATE)

File: `OB1/docker/docker-compose.yml`.
Run with: `docker compose -f OB1/docker/docker-compose.yml ...`.
`.env` lives next to the file at `OB1/docker/.env`.

> **Why separate:** OB1 is its own compose project (`name: open-brain`). It
> attaches to the main stack's `ai-stack_llm-net` as an **external** network,
> so it depends on the main stack being up. Bring OB1 up *after* `llm-gateway`
> (the inference front door; its `llama-cpp-upstream` / `llama-cpp-embed-upstream`
> servers must be healthy first) is up; tear it down *before* the main stack so
> `docker compose down` can drop `llm-net`.

> **Profiles (since 2026-09-19, `sl-ob1-profiles`; LIVE IN THE PINNED GITLINK
> since `sl-ob1-gitlink` bumped it `5005197` -> `fe3e045` on 2026-09-20):** four
> profiles are declared for this plane in `stack.manifest.toml`, all four are in
> the pinned compose file, and the generated Profiles column below names each
> gated service. What each profile set renders (generated - every row a render,
> including the driver's two-profile default, because attempt 1 of `sl-ob1-gitlink`
> added up deltas instead and shipped a real number for a different set to eight
> files):

<!-- stack:profile-counts:ob1 -->

_Generated by `python scripts/stack/stack.py docs --write`; do not edit between the markers. Rendered from `OB1/docker/docker-compose.yml` with `OB1/docker/.env.example` and `COMPOSE_PROFILES` cleared, so a service is listed under exactly the profiles that select it._

| Profiles passed | Services | Added over no profile |
|---|---|---|
| no profile | 20 | - |
| `idea-refinery` + `research` (the driver's default) | 23 | `openbrain-curator`, `openbrain-idea-refinery`, `openbrain-research` |
| `research` | 22 | `openbrain-curator`, `openbrain-research` |
| `wiki` | 24 | `openbrain-wiki`, `openbrain-wiki-backup`, `openbrain-wiki-viewer`, `openbrain-workbench` |
| `notebook` | 23 | `open-notebook-backup`, `open_notebook`, `surrealdb` |
| every profile (`idea-refinery`, `research`, `wiki`, `notebook`) | 30 | `open-notebook-backup`, `open_notebook`, `openbrain-curator`, `openbrain-idea-refinery`, `openbrain-research`, `openbrain-wiki`, `openbrain-wiki-backup`, `openbrain-wiki-viewer`, `openbrain-workbench`, `surrealdb` |

<!-- /stack:profile-counts:ob1 -->

> So a bare `up` starts only the unprofiled services, and the `wiki` + `notebook`
> ones are ones no driver default passes. It is not only `up` — **a bare `stop`
> addresses only the unprofiled services, and a bare `down` removes them, leaves the
> gated ones running, and then FAILS to drop the project network**
> (`Resource is still in use`), measured 2026-09-20 in a throwaway two-service
> compose project. Gated OB1 containers hold endpoints on
> `ai-stack_llm-net` / `app-net` / `default` (the Networks column below) — the anchor
> networks `emergency-recovery.ps1` tears OB1 down first in order to free.
>
> **THE LANDING STEP for this host, both halves:**
>
> - **`COMPOSE_PROFILES=research,wiki,notebook,idea-refinery` in `OB1/docker/.env`** —
>   the per-plane env file (D17). Read 2026-09-20: `frontend/.env` carries
>   `gpu,tailscale`, `inference/.env` carries `local`, and `OB1/docker/.env` carries
>   nothing. (`portal` deliberately has none — CLAUDE.md, it is started by hand — and
>   `agent-org`'s `workers`/`cloud` are operator-driven slices. OB1 is the one whose
>   absence now bites, because its bare verbs sit in the recovery path.) Raw
>   `docker compose` honours it (it renders every profile), and it is what repairs a bare
>   `stop`/`down`.
> - **the driver's state** —
>   `python scripts/stack/stack.py enable research` writes
>   `idea-refinery, research, wiki, notebook` into
>   `.stack/state.json`, and `up --dry-run` then prints all four `--profile` flags
>   on the OB1 line. **`enable`, never `init --product research --force`, on a
>   host that already has a state file**: `init` builds a FRESH state and saves
>   it, so it drops whatever the file already named. Measured 2026-09-20 —
>   six planes in, four out (`memory`, `coder`, `agent-org` gone) — while
>   `enable research` merged the same input to seven. The two print the same
>   `enabled product research:` summary, so nothing on screen tells you which
>   happened. Semantics: `scripts/stack/README.md`, the `init` section.
>
> They do not fight — `stack.py` UNIONS the plane env's list into whatever flags it
> passes (`effective_profiles`). **The consequence, stated rather than discovered:**
> because it unions, a host whose `OB1/docker/.env` carries all four makes
> **`--headless` a no-op for this plane** — the driver drops `wiki`/`notebook` and the
> env file puts them back. On this host, which runs every profile, that is the right trade;
> a deployment that genuinely wants a headless OB1 must omit the env line and rely on
> the driver state alone.
>
> `scripts/recovery/emergency-recovery.ps1` does not depend on either: it carries the
> four in one `$Script:OB1Profiles` list used by **every** OB1 compose invocation in
> that file (eight of them, `stop` / `up` / `down` / `ps`), because a CLI `--profile`
> REPLACES `COMPOSE_PROFILES` and a recovery script cannot rely on a host's env file
> being right.
>
> Neither is set on this host yet (read 2026-09-20: `OB1/docker/.env` has no
> `COMPOSE_PROFILES` line, and `.stack/state.json` does not list the `ob1` plane at
> all). Without one of them, `stack.py up ob1` passes
> `--profile idea-refinery --profile research` only (the `default` plus its
> `requires` closure), which renders <!-- stack:count:ob1:default -->**23** services with `idea-refinery` + `research` (the driver's default)<!-- /stack:count:ob1:default -->,
> against <!-- stack:count:ob1:all -->**30** services with every profile (`idea-refinery`, `research`, `wiki`, `notebook`)<!-- /stack:count:ob1:all -->. Render the pair; do not add deltas.
> Note that a bare `docker compose --profile X`
> **REPLACES** `COMPOSE_PROFILES` rather than adding to it - with all four in the env
> file, `--profile research` alone renders <!-- stack:count:ob1:research -->**22** services with `research`<!-- /stack:count:ob1:research -->.
>
> Why each profile is a profile at all
> (what each turns on is the "Added over no profile" column above):
>
> | Profile | Why not core |
> |---------|--------------|
> | `research` | the research ENGINE; the store captures, embeds, chunks and serves without it |
> | `wiki` | a reading/writing SURFACE onto the store |
> | `notebook` | a second SURFACE onto the store (openbrain-db is canonical since IKS) |
> | `idea-refinery` | pre-existing profile, and **running on this host** — both drivers pass it on every invocation. It is gated because it needs a Mattermost bot token to deliver dossiers, not because it is waiting for one. `requires` the `research` profile (its only engine) — see below |
>
> The full set is all four:
> `docker compose -f OB1/docker/docker-compose.yml --profile research --profile wiki --profile notebook --profile idea-refinery up -d`
> — and `python scripts/stack/stack.py up ob1` passes `idea-refinery` (the
> plane's one `default = true` profile) plus `research` (its `requires` closure)
> unless the state file or `OB1/docker/.env` enables more; `stack.ps1` is a shim
> with no profile list of its own. Before the gitlink bumped, two profiles and four
> rendered the same services because compose ignores a profile it does not know;
> at `fe3e045` that is over — the generated counts above show each set.
>
> **Invariant:** no core service may `depends_on` a profiled one. None does.
> But `depends_on` is not the only way one service reaches another: **six**
> references cross a group boundary as environment URLs. None blocks a start;
> each just goes dead at call time, usually inside a `try/catch` — so the stack
> comes up green and a scheduled job quietly stops producing output.
>
> | Caller | Key | Target profile | Dead when that profile is off |
> |---|---|---|---|
> | `openbrain-ext` (core) | `WIKI_RECOMPILE_URL` | `wiki` | `wiki_trigger_recompile`; the `wiki_*` readers go stale |
> | `openbrain-gmail-prune` (**core**) | `WIKI_RECOMPILE_URL` | `wiki` | **the nightly prune completes and never recompiles the vault** |
> | `openbrain-gmail-pull` (core) | `WIKI_RECOMPILE_URL` | `wiki` | nothing — inherited from the shared `env_file`, its code never reads it |
> | `openbrain-podcast` (core) | `RESEARCH_URL` | `research` | link-enrichment research; the episode degrades to email-only |
> | `openbrain-podcast` (core) | `ON_BASE` | `notebook` | **no audio — the chain runs and produces no episode** |
> | `openbrain-idea-refinery` (`idea-refinery`) | `RESEARCH_URL` | `research` | its only engine — the drain can never drain |
>
> The last one is why `stack.manifest.toml` gives the `idea-refinery` profile
> `requires = ["research"]`: it is the plane's only `default = true` profile, so
> without that every invocation started a drain with no engine.
>
> **Find these by rendering, never by grepping** — `config --format json` with all
> four profiles, then match every `environment` value against the profiled service
> names. Two of the six arrive via `env_file: ../recipes/email-history-import/.env`
> and appear nowhere in the compose text; a grep finds four of six and that is
> exactly the error the first version of this section shipped. Per-service reasons
> and the full table with consequences: `OB1/docker/README.md`, "Compose profiles".
>
> **Cross-PROJECT blast radius**, which no per-plane doc covers: turning `wiki` or
> `notebook` off also breaks consumers outside OB1 — `portal/config/caddy/Caddyfile`
> reverse-proxies `openbrain-workbench`, `openbrain-wiki-viewer` and `open_notebook`,
> and `frontend/status-pipe/modules/system-health/` probes `open_notebook` and
> `openbrain-research`. All degrade at request time, none at start.

### Networks
| Network   | Type                         | Purpose |
|-----------|------------------------------|---------|
| `obnet`   | bridge                       | OB1 internal + host-published ports |
| `llm-net` | external (`ai-stack_llm-net`)| reach llama-cpp / llama-cpp-embed |
| `app-net` | external (`ai-stack_app-net`)| wiki-viewer / workbench reachable by the portal Caddy |
| `search-gw-net` | external (`ai-stack_default`) | research/grounding/podcast reach the private SearXNG `gateway` and the Mullvad egress proxy `vpn:8888`. **Not tor** - that was retired 2026-08-21 and every `FETCH_PROXY_URL` in this project defaults to `http://vpn:8888` |
| `default` | bridge (`open-brain_default`) | the project's own default, which `surrealdb`, `open_notebook`, `open-notebook-backup` and `openbrain-wiki-backup` sit on |

### Containers
<!-- stack:plane-services:ob1 -->

_Generated by `python scripts/stack/stack.py docs --write`; do not edit between the markers. Rendered from `OB1/docker/docker-compose.yml` with `OB1/docker/.env.example` and `COMPOSE_PROFILES` cleared, so a service is listed under exactly the profiles that select it._

| Service | Container | Profiles | Host ports | Networks |
|---|---|---|---|---|
| `openbrain-chunk-worker` | `openbrain-chunk-worker` | *(none)* | `127.0.0.1:8817->8000` | `ai-stack_llm-net` (external), `open-brain_obnet` |
| `openbrain-cron` | `openbrain-cron` | *(none)* | - | `open-brain_obnet` |
| `openbrain-db` | `openbrain-db` | *(none)* | - | `open-brain_obnet` |
| `openbrain-db-backup` | `openbrain-db-backup` | *(none)* | - | `open-brain_obnet` |
| `openbrain-digest` | `openbrain-digest` | *(none)* | - | `ai-stack_llm-net` (external), `open-brain_obnet` |
| `openbrain-entity-worker` | `openbrain-entity-worker` | *(none)* | `127.0.0.1:8810->8000` | `ai-stack_llm-net` (external), `open-brain_obnet` |
| `openbrain-ext` | `openbrain-ext` | *(none)* | - | `open-brain_obnet` |
| `openbrain-extract` | `openbrain-extract` | *(none)* | `127.0.0.1:8815->8000` | `open-brain_obnet` |
| `openbrain-gateway` | `openbrain-gateway` | *(none)* | `127.0.0.1:8061->8061` | `open-brain_obnet` |
| `openbrain-gmail-prune` | `openbrain-gmail-prune` | *(none)* | - | `ai-stack_llm-net` (external), `open-brain_obnet` |
| `openbrain-gmail-pull` | `openbrain-gmail-pull` | *(none)* | - | `ai-stack_llm-net` (external), `open-brain_obnet` |
| `openbrain-grounding-backfiller` | `openbrain-grounding-backfiller` | *(none)* | `127.0.0.1:8819->8000` | `ai-stack_default` (external), `ai-stack_llm-net` (external), `open-brain_obnet` |
| `openbrain-mcp` | `openbrain-mcp` | *(none)* | - | `ai-stack_llm-net` (external), `open-brain_obnet` |
| `openbrain-mcpo` | `openbrain-mcpo` | *(none)* | - | `ai-stack_llm-net` (external), `open-brain_obnet` |
| `openbrain-mcpo-ext` | `openbrain-mcpo-ext` | *(none)* | - | `ai-stack_llm-net` (external), `open-brain_obnet` |
| `openbrain-ops-gateway` | `openbrain-ops-gateway` | *(none)* | `127.0.0.1:8062->8061` | `open-brain_obnet` |
| `openbrain-podcast` | `openbrain-podcast` | *(none)* | - | `ai-stack_default` (external), `ai-stack_llm-net` (external), `open-brain_obnet` |
| `openbrain-postgrest` | `openbrain-postgrest` | *(none)* | - | `open-brain_obnet` |
| `openbrain-rest` | `openbrain-rest` | *(none)* | `127.0.0.1:3001->80` | `open-brain_obnet` |
| `openbrain-suggestion-worker` | `openbrain-suggestion-worker` | *(none)* | `127.0.0.1:8813->8000` | `ai-stack_llm-net` (external), `open-brain_obnet` |
| `openbrain-idea-refinery` | `openbrain-idea-refinery` | `idea-refinery` | - | `ai-stack_llm-net` (external), `open-brain_obnet` |
| `openbrain-curator` | `openbrain-curator` | `research` | `127.0.0.1:8816->8000` | `ai-stack_llm-net` (external), `open-brain_obnet` |
| `openbrain-research` | `openbrain-research` | `research` | `127.0.0.1:8818->8000` | `ai-stack_default` (external), `ai-stack_llm-net` (external), `open-brain_obnet` |
| `openbrain-wiki` | `openbrain-wiki` | `wiki` | `127.0.0.1:8811->8000` | `ai-stack_llm-net` (external), `open-brain_obnet` |
| `openbrain-wiki-backup` | `openbrain-wiki-backup` | `wiki` | - | `open-brain_default` |
| `openbrain-wiki-viewer` | `openbrain-wiki-viewer` | `wiki` | `127.0.0.1:8812->8080` | `ai-stack_app-net` (external), `open-brain_obnet` |
| `openbrain-workbench` | `openbrain-workbench` | `wiki` | `127.0.0.1:8814->8000` | `ai-stack_app-net` (external), `ai-stack_llm-net` (external), `open-brain_obnet` |
| `open-notebook-backup` | `open-notebook-backup` | `notebook` | - | `open-brain_default` |
| `open_notebook` | `open_notebook` | `notebook` | `127.0.0.1:5055->5055`, `127.0.0.1:8503->8502` | `ai-stack_app-net` (external), `ai-stack_llm-net` (external), `open-brain_default`, `open-brain_obnet` |
| `surrealdb` | `surrealdb` | `notebook` | `127.0.0.1:8003->8000` | `open-brain_default` |

<!-- /stack:plane-services:ob1 -->

| Container | Role |
|---|---|
| `openbrain-db` | PostgreSQL 16 + pgvector |
| `openbrain-mcp` | Core MCP server *(internal only)* |
| `openbrain-ext` | Extensions MCP server (39 tools) *(internal only)* |
| `openbrain-gateway` | Privacy-enforcing MCP proxy for cloud clients |
| `openbrain-ops-gateway` | Same image, OPS profile: agent-memory tools for HOST processes, own key |
| `openbrain-mcpo` | MCP→OpenAPI bridge (core) |
| `openbrain-mcpo-ext` | MCP→OpenAPI bridge (extensions) |
| `openbrain-postgrest` | PostgREST API over openbrain-db |
| `openbrain-rest` | Caddy `/rest/v1` path-stripping proxy |
| `openbrain-entity-worker` | Entity-extraction worker |
| `openbrain-suggestion-worker` | Cross-thread suggestion worker (Integrated Knowledge System; `POST /suggest`) |
| `openbrain-curator` | Research-package ingestion inlet (`POST /ingest/research-package`); resolves deep-research onto the best existing thread (pgvector shortlist + LLM decision), delegates the write to openbrain-mcp `/research/persist`, writes grounded claim→source edges (Research Engine P2); deno-postgres + llama-cpp + llama-cpp-embed |
| `openbrain-research` | Shared research harness (Research Engine P3/P4; `POST /research` → job_id, `GET /research/jobs/:id[/stream]`); reuses grounded claims → gap analysis → stages gaps (SearXNG + per-page fetch) → synthesizes verbatim with `[Source N]` citations → enforces grounding (honest `[GAP]`s, never fabricates) → delegates placement+claims to openbrain-curator; deno-postgres + llama-cpp + llama-cpp-embed + SearXNG gateway *(=ai-stack_default, to reach the private `gateway`)* |
| `openbrain-chunk-worker` | Writer-agnostic chunk-embedding worker (Integrated Knowledge System); chunks any OB1 source into `source_chunks` (1200/150 + bge-m3) so passage-level vector retrieval works for every frontend, incl. Open Notebook "ask your knowledge base"; periodic scan + `POST /chunks`; deno-postgres + llama-cpp-embed |
| `openbrain-grounding-backfiller` | S2 brain-health worker. (1) Drains `ungrounded_claims` — per claim extracts its entity (local `:nothink` LLM) → fetches the Wikipedia page (through the Mullvad `vpn` proxy) → `find_or_create_source` + `link_claim_to_source 'corroborates'` so confidence recomputes and the claim leaves the view; `POST /backfill?limit=N {thread_ids?}`. (2) Heals thin/failed web **sources** whose ingestion truncated them (~150-char stubs) — `POST /refetch?limit=N` re-fetches (proxy-first, direct fallback), updates content (chunk-worker re-embeds), 3-attempt cap then `refetch_failed`. Cron: backfill 07:00 UTC, refetch 07:30 UTC. deno-postgres *(the `vpn` proxy)* |
| `openbrain-wiki` | Wiki compiler + scheduler |
| `openbrain-wiki-viewer` | Quartz 4 read-only wiki viewer (also tailnet HTTPS `:8444` + Caddy `wiki.${PUBLIC_DOMAIN}`) |
| `openbrain-workbench` | Deno+Hono read/write API behind the viewer (`/workbench/*` via portal Caddy `handle`, X-Brain-Key injected); deno-postgres writes + PostgREST reads *(debug only)* |
| `openbrain-extract` | FastAPI content-extraction sidecar (`POST /extract`: PDF/DOCX/PPTX/image-OCR/audio-STT registry); sandboxed (non-root, cap_drop, read-only FS); reaches host STT via `host.docker.internal` *(debug only)* |
| `openbrain-cron` | supercronic + curl; fires HTTP-trigger chain (no docker.sock) *(internal only)* |
| `openbrain-gmail-pull` | HTTP-triggered Gmail ingest; chains to prune on success *(internal only)* |
| `openbrain-gmail-prune` | HTTP-triggered short-term prune; chains to digest + wiki recompile *(internal only)* |
| `openbrain-digest` | HTTP-triggered daily digest; mechanical formatting, Gmail send; chains to podcast after delivery *(internal only)* |
| `openbrain-podcast` | HTTP-triggered chain tail (digest → podcast); spawns the link-enrich pipeline — follow newsletter links (through the `vpn` proxy) → grounded research via openbrain-research (article mode) → two-host script → Open Notebook audio → loop-close (episode source linked to the day's threads); best-effort, never blocks the email *(internal only; =ai-stack_default; the `vpn` proxy)* |
| `openbrain-idea-refinery` | **Idea Refinery drain** (IR.1–IR.5/IR.7): `POST /run` walks the owed-idea queue (`ideas`/`idea_revisions`, init-ideas.sql), researches each via openbrain-research (bounded submit-on-complete + rollover), posts the gap-centered dossier to Mattermost `#ideas` (via `host.docker.internal:8065`), ages to dormant + resurfaces. Stand-alone cron `03:00 UTC` (before the 05:00-UTC gmail/wiki chain → new claims feed the 1am-local wiki compile). **PROFILE-GATED (`idea-refinery`)** — NOT started by a plain `up`. deno-postgres *(internal only)* |
| `openbrain-db-backup` | Nightly `pg_dump` of `openbrain-db` (moved from ai-stack 2026-08-21 — OB1 owns its backups; output still lands in `ai-stack/backups/openbrain-db` for the NAS mirror + freshness watchers) |
| `openbrain-wiki-backup` | Daily tar of `openbrain-wiki-data` + `wiki-assets` (moved from ai-stack 2026-08-21; output still `ai-stack/backups/openbrain-wiki`) *(project-local `open-brain_default`; it declares no `networks:` key)* |

**Scheduled-job slice:** the always-on services (`openbrain-cron` + the
HTTP-triggered jobs) — plus the **profile-gated `openbrain-idea-refinery`**
drain (Idea Refinery; enable with `--profile idea-refinery`) — live in [`OB1/docker/docker-compose.scheduled.yml`](../../../../OB1/docker/docker-compose.scheduled.yml),
included from the main OB1 compose file. Trigger model is event-chained:
cron fires `openbrain-gmail-pull` at 01:00; pull→prune→digest is wired
via `NEXT_TRIGGER_URL` env vars, not multiple cron entries. Schedules
live in [`OB1/docker/cron/crontab`](../../../../OB1/docker/cron/crontab)
(bind-mounted; edit + `docker compose restart openbrain-cron` to reload).
No docker.sock anywhere — chain hops are HTTP `POST /run` calls on
`obnet` between long-running services.

**Cloud privacy split:** `openbrain-mcp` and `openbrain-ext` no longer publish
host ports — cloud services (Claude Code, ChatGPT) must enter through
`openbrain-gateway` at `127.0.0.1:8061`. Gateway forces
`metadata.share=cloud` on reads and stamps `metadata.origin=cloud,
share=cloud` on writes; the 39 extension tools are blocked entirely.
Mirrors the mnemory-cloud-gateway pattern (`memory/mnemory-gateway/app.py`). Local
trusted clients (OWUI via the mcpo bridges, recipes on obnet, the
entity worker, the wiki compiler) keep talking to `openbrain-mcp` /
`openbrain-ext` directly on internal networks and are unaffected.

### Volumes
**Four**, at the pinned gitlink `fe3e045` (unchanged by the 2026-09-20 bump):
`openbrain-db-data`,
`openbrain-wiki-data`, `wiki-assets` (binary assets — images now, audio later —
written by `openbrain-workbench`, served read-only by `openbrain-wiki-viewer`;
deliberately NOT mounted into `openbrain-wiki` so binaries never enter the vault
git history) and `wiki-viewer-srv` (the viewer's published `/srv` snapshots,
persisted across recreates so a deploy never shows the "Building…" splash;
derived and rebuildable, so it is NOT backed up).

---

### Open Notebook trio (moved from ai-stack 2026-08-21, Part K.5b — NOT retiring; stays until the wiki workbench matures)

| Container | Purpose |
|---|---|
| `surrealdb` | Open Notebook local store (SurrealDB v2, digest-pinned) *(open-brain)* |
| `open_notebook` | Open Notebook UI + API (IKS fork — openbrain-db is the canonical store) *(external)* |
| `open-notebook-backup` | SurrealDB logical export + notebook_data tar (output still `ai-stack/backups/open-notebook`) *(open-brain)* |


## 3. agent-org — compose project `agent-org` (SEPARATE)

File: `agent-org/docker/docker-compose.yml`. Run with:
`docker compose -f agent-org/docker/docker-compose.yml ...`. `.env` lives next to the file at
`agent-org/docker/.env` (template: `.env.example`). Design corpus:
`../documentation-plans-ai-stack/implementation-guide/teams-chat-agent-orchestration/`.

> **Why separate:** like OB1, `agent-org` is its own compose project (`name: agent-org`). It
> attaches to the main stack's `ai-stack_llm-net` as an **external** network for LOCAL
> inference (via the `llama-cpp` alias on `llm-gateway` — never around LiteLLM), and optionally
> reaches OB1's `openbrain-gateway` for the audit mirror. Bring it up **after** OB1 (last); tear
> it down **before** OB1 (first). The recovery scripts manage the **default plane only**; the
> `workers` and `cloud` profiles are gated (like the Portal) and operator-driven.

### Planes / profiles
| Plane | Profile | Brought up by |
|-------|---------|---------------|
| default (mattermost + bridge) | — | `docker compose ... up -d` / recovery scripts |
| worker pool | `--profile workers` | operator (P5 — after the main stack builds the little-coder images) |
| cloud lane | `--profile cloud` | operator (**Pc — CONDITIONAL**, only if the P0.5 capability-floor test mandates a cloud judge) |

### Networks
| Network | Type | Purpose |
|---------|------|---------|
| `ao-net` | bridge | control plane — host-publishable (like OB1's obnet); "no cloud" enforced at the app layer |
| `ao-worker-net` | internal (no internet) | worker pool isolation (mirrors `lc-net`); egress only via `ao-git-egress` |
| `ao-cloud-egress-net` | internal | cloud egress isolation — only `llm-gateway-cloud` + `ao-egress` attach |
| `llm-net` | external (`ai-stack_llm-net`) | reach the existing air-gapped `llm-gateway` (`llama-cpp` alias) for local inference |
| `default` | bridge | the single internet egress point (`ao-git-egress` git host; `ao-egress` → openrouter.ai) |

### Containers
<!-- stack:plane-services:agent-org -->

_Generated by `python scripts/stack/stack.py docs --write`; do not edit between the markers. Rendered from `agent-org/docker/docker-compose.yml` with `agent-org/docker/.env.example` and `COMPOSE_PROFILES` cleared, so a service is listed under exactly the profiles that select it._

| Service | Container | Profiles | Host ports | Networks |
|---|---|---|---|---|
| `agent-bridge` | `agent-bridge` | *(none)* | `127.0.0.1:8830->8000` | `agent-org_ao-net`, `ai-stack_llm-net` (external) |
| `agent-bridge-db` | `agent-bridge-db` | *(none)* | - | `agent-org_ao-net` |
| `agent-bridge-db-backup` | `agent-bridge-db-backup` | *(none)* | - | `agent-org_ao-net` |
| `mattermost` | `mattermost` | *(none)* | `127.0.0.1:8065->8065` | `agent-org_ao-net`, `ai-stack_llm-net` (external) |
| `mattermost-db` | `mattermost-db` | *(none)* | - | `agent-org_ao-net` |
| `mattermost-db-backup` | `mattermost-db-backup` | *(none)* | - | `agent-org_ao-net` |
| `ao-git-egress` | `ao-git-egress` | `workers` | - | `agent-org_ao-worker-net` (internal), `agent-org_default` |
| `ao-ot-1` | `ao-ot-1` | `workers` | - | `agent-org_ao-worker-net` (internal), `ai-stack_llm-net` (external) |
| `ao-ot-2` | `ao-ot-2` | `workers` | - | `agent-org_ao-worker-net` (internal), `ai-stack_llm-net` (external) |
| `ao-worker-1` | `ao-worker-1` | `workers` | - | `agent-org_ao-worker-net` (internal), `ai-stack_llm-net` (external) |
| `ao-worker-1-journals-backup` | `ao-worker-1-journals-backup` | `workers` | - | `agent-org_default` |
| `ao-worker-2` | `ao-worker-2` | `workers` | - | `agent-org_ao-worker-net` (internal), `ai-stack_llm-net` (external) |
| `ao-worker-2-journals-backup` | `ao-worker-2-journals-backup` | `workers` | - | `agent-org_default` |
| `ao-egress` | `ao-egress` | `cloud` | - | `agent-org_ao-cloud-egress-net` (internal), `agent-org_default` |
| `llm-gateway-cloud` | `llm-gateway-cloud` | `cloud` | - | `agent-org_ao-cloud-egress-net` (internal), `agent-org_ao-net` |
| `llm-gateway-cloud-db` | `llm-gateway-cloud-db` | `cloud` | - | `agent-org_ao-net` |

<!-- /stack:plane-services:agent-org -->

<!-- stack:profile-counts:agent-org -->

_Generated by `python scripts/stack/stack.py docs --write`; do not edit between the markers. Rendered from `agent-org/docker/docker-compose.yml` with `agent-org/docker/.env.example` and `COMPOSE_PROFILES` cleared, so a service is listed under exactly the profiles that select it._

| Profiles passed | Services | Added over no profile |
|---|---|---|
| no profile | 6 | - |
| `workers` | 13 | `ao-git-egress`, `ao-ot-1`, `ao-ot-2`, `ao-worker-1`, `ao-worker-1-journals-backup`, `ao-worker-2`, `ao-worker-2-journals-backup` |
| `cloud` | 9 | `ao-egress`, `llm-gateway-cloud`, `llm-gateway-cloud-db` |
| every profile (`workers`, `cloud`) | 16 | `ao-egress`, `ao-git-egress`, `ao-ot-1`, `ao-ot-2`, `ao-worker-1`, `ao-worker-1-journals-backup`, `ao-worker-2`, `ao-worker-2-journals-backup`, `llm-gateway-cloud`, `llm-gateway-cloud-db` |

<!-- /stack:profile-counts:agent-org -->

| Container | Role |
|---|---|
| `mattermost-db` | Postgres for Mattermost |
| `mattermost` | Chat platform + mobile (Team Edition); on llm-net too so the `tailscale` netns can reach it for `tailscale serve` (P7.4) |
| `agent-bridge` | Orchestration + the governance gate (FastAPI); WebSocket consumer + REST poster; floor-hook endpoint |
| `agent-bridge-db` | Postgres — the bridge's fail-safe state store (gate/effort/parked-effort/project/scope/audit) |
| `agent-bridge-db-backup` | Nightly `pg_dump` of `agent-bridge-db` (governance/effort/project state) → repo-root `./backups/agent-bridge-db/` (generic `backup/pg-backup.sh`) |
| `mattermost-db-backup` | Nightly `pg_dump` of `mattermost-db` (conversation content) → `./backups/mattermost-db/` |
| `ao-worker-1-journals-backup` / `ao-worker-2-journals-backup` | Nightly tar of each worker's append-only task journals → `./backups/ao-worker-{1,2}-journals/` (generic `backup/generic-tar-backup.sh`). One sidecar per volume so each archive restores 1:1; profile-gated with the workers *(project-local `agent-org_default`; they declare no `networks:` key, so compose attaches the project default - the volume is their only SOURCE, but the render gives them a network)* |
| `ao-worker-1` / `ao-worker-2` | Pooled `little-coder` control daemons (reuse `little-coder:local`) |
| `ao-ot-1` / `ao-ot-2` | Per-worker `open-terminal` workspace planes (reuse `little-coder-open-terminal:local`) |
| `ao-git-egress` | Shared git-allowlist egress for the worker pool (mirrors `lc-egress`); allowlist is the **bridge-written** `ao-egress-config` file, reloaded on change (custom `docker/egress/tinyproxy.conf` + `egress-reload.sh` command override) so the org can work on any onboarded repo |
| `llm-gateway-cloud` | **CONDITIONAL** separate LiteLLM for OpenRouter (master_key + per-role budgets); the only egress, via `ao-egress` |
| `llm-gateway-cloud-db` | Postgres for the cloud LiteLLM spend ledger |
| `ao-egress` | Allowlist egress proxy pinned to `openrouter.ai` (mirrors `lc-egress`); the ONLY agent-org internet path |

### Volumes
`mattermost-db-data`, `mattermost-data`, `mattermost-config`, `mattermost-logs`,
`mattermost-plugins`, `mattermost-client-plugins`, `agent-bridge-db-data`,
`ao-worker-1-workspace`, `ao-worker-1-sessions`, `ao-worker-1-journals`,
`ao-worker-2-workspace`, `ao-worker-2-sessions`, `ao-worker-2-journals`,
`ao-egress-config` (bridge-written git-egress allowlist, shared with
`ao-git-egress`), `llm-gateway-cloud-db-data`.

The two `*-journals` volumes (added 2026-08-29, memory-plane Phase 0.3) are the
only ao-worker volumes that are BACKED UP: workspaces are re-clonable and
sessions are regenerable per-effort continuity, but the journals are the
append-only evidence corpus and nothing can reproduce them once lost.

---

## 4. Recovery stack

The **recovery stack** keeps every container above runnable after a crash,
update, or network-namespace break.

| File | Role |
|------|------|
| `scripts/recovery/emergency-recovery.ps1` | Primary recovery — `recover` / `nuclear` / `gpu-reset`; 5-phase ordered restart that also drives the OB1 project |
| `scripts/recovery/status_check.py` | Read-only overview: `docker ps`, then `docker exec` / `docker inspect` by container name; no compose project involved |
| `scripts/archive/legacy-recovery/` | ARCHIVED 2026-09-25: `quick-fixes.bat`, `update-stack.bat`, five Python helpers orphaned since 2026-08-20, and `dev-helper.ps1`. Seven of them issued bare `docker compose` commands from the repo root, which since Part K address the zero-service root anchor (`quick-fixes.bat`'s `-f <plane>` calls did work). The eighth, `namespace_reset.py`, issued none: it only printed advice and returned 0, and it was archived as an orphan. The per-script detail is in `scripts/archive/README.md`. Replacements are in `scripts/archive/README.md`. (The `emergency-recovery.bat` twin was archived 2026-08-21.) |
| `scripts/archive/emergency-recovery-module/` | ARCHIVED 2026-08-20 (was OWUI-reachable stale guidance; recovery keywords now route to help-system) |

The recovery script holds a PER-PLANE service inventory - `$Script:InferenceServices`,
`$Script:FrontendServices`, `$Script:MemoryServices`, `$Script:SearchServices`,
`$Script:CoderServices`, `$Script:OB1Services`, `$Script:AgentOrgServices` -
each beside that plane's `$Script:<Plane>Compose` path. `$Script:MainStackServices`
is now the EMPTY array, because the root project owns no services; there is no
`$MainBackups` group left either, since every backup sidecar starts and stops
with its own plane. agent-org is driven as a
separate project (`Start-/Stop-/Reset-AgentOrgStack`, `$AgentOrgCompose`), stopped first and
started last (downstream of OB1). **When you add a container to any of the three compose
files, add it to that inventory and to the shutdown/startup sequences** so the recovery stack
stays complete.

**The Portal plane is deliberately excluded** from recovery: it is profile-gated
(`profiles: [internet]`) and managed by `scripts/portal/portal-on.ps1` / `portal-off.ps1`.
Nuclear runs no root `down` (since ac-recovery-gates) and never stops or starts the
portal; when caddy is running it **warns** that the portal stays up, attached to
`ai-stack_app-net`, which recovery never removes.

---

## Cross-stack dependency order

**The order is DATA, not prose**: it is a topological sort of the `requires`
edges in `stack.manifest.toml`, with ties broken by the order the `[planes.*]`
tables are declared. `python scripts/stack/stack.py up --all --dry-run` prints
the exact `docker compose` line for each plane, in order, and runs nothing -
prefer that over reading this list. `emergency-recovery.ps1` uses the same
relative order.

Plane by plane (start in this order; `down` reverses it):

1. **anchor** (`python scripts/stack/stack.py up anchor`; a bare root
    `docker compose up -d` exits `no service selected`) — creates
    `ai-stack_llm-net` / `app-net` / `default` and starts nothing. A plane that
    USES one of those fails to render without it (`network ai-stack_llm-net
    declared as external, but could not be found`) - which is inference, memory,
    search, coder, portal, ob1, agent-org, and the frontend's `gpu` profile.
    **Two exceptions, both deliberate:** the frontend's `stock` profile declares
    the three externals but uses only its project-local `owui-net`, and compose
    does not require an UNUSED external network to exist - which is what lets a
    fresh clone come up with no anchor at all; and `memory`, `coder`,
    `portal`, `agent-org` and `open-brain` each declare their OWN project-local
    `default` bridge, so "default" in one of their rows is `<project>_default`,
    not the anchor's.
2. **inference** (`docker compose -f inference/docker-compose.yml up -d`) — its
    internal `depends_on` runs the upstreams → `llm-queue` → `llm-gateway-db`
    → `llm-gateway` (+ ui/backups); one command, ordered and health-gated. Every
    caller in every other project needs IT.
3. **frontend** — `openwebui` (which provides the network namespace) →
    `tailscale`; `openwebui-backup` waits on whichever openwebui definition is
    active. **Never restart `openwebui` alone.**
4. **memory** — `mnemory` → `mnemory-cloud-gateway`, with `mnemory-backup` also
    waiting on `mnemory`.
5. **search** — `vpn` and `redis` in parallel → `searxng` → `gateway`.
6. **coder** — `open-terminal` → `little-coder`; `lc-egress` waits on nothing.
7. **Backup sidecars** — and they do NOT all wait. Read from the service
    definitions, the six in-repo sidecars split three and three:

    | Waits on `depends_on: service_healthy` | Starts immediately |
    |---|---|
    | `openwebui-backup` (both openwebui definitions, `required: false`) | `tailscale-backup` |
    | `llm-gateway-backup` (`llm-gateway-db`) | `lm-models-backup` |
    | `mnemory-backup` (`mnemory`) | `little-coder-backup` |

    The three on the right carry **no `depends_on` at all**; what they have
    instead is a RUNTIME precheck inside the loop. `lm-models-backup` probes
    `HEALTH_TCP=llama-cpp-upstream:8080` before each tar; `tailscale-backup`
    sets `HEALTH_TCP=` empty on purpose (a non-empty directory is its only
    precheck); `little-coder-backup` sets no `HEALTH_TCP` key at all. **A
    precheck that fails is a SKIP, and a skip is exit 0** - which is why backup
    freshness is watched by `stack-watchdog.ps1` against the artifact's age and
    never by a sidecar's exit code. The openbrain-db / wiki / open-notebook
    backups belong to the OB1 project and come up with it.
8. **OB1** (`docker compose -f OB1/docker/docker-compose.yml up -d`) — after
    `llm-gateway` is healthy. It also `requires` search: `openbrain-research`
    and the grounding backfiller reach `gateway` and `vpn` by name.
9. **agent-org** (`docker compose -f agent-org/docker/docker-compose.yml up -d`) — after OB1
    (downstream of it: attaches to `ai-stack_llm-net`, optionally mirrors audit to OB1's
    gateway). Default plane only; `workers`/`cloud` profiles are operator-driven. Stop it
    first (before OB1) on the way down.
10. **Portal** (`manual`, **separate lifecycle**): `scripts/portal/portal-on.ps1`
    brings it up in five ordered groups - `portal-alerter`, `authelia`, `caddy`,
    the four watchers, the two backups - plus a sixth (`cloudflared` +
    `tunnel-watcher`) in production mode; `portal-init` is pulled in by
    `depends_on`. Never part of a driver `up`; tear down with `portal-off.ps1`,
    which misses two of the portal's services (see the portal note in section 1).
