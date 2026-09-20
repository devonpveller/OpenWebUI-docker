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
own compose project `portal`** on 2026-08-21 (12 services,
`portal/docker-compose.yml`, data migrated to `portal_*` volumes, joins
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

## 1. Root anchor — compose project `ai-stack` (0 services since K.5b)

Files: `docker-compose.yml` — **networks only, no `include:`, no services**; the
plane projects are `frontend|inference|memory|search|coder|portal/docker-compose.yml`,
each its own project with its own `.env`.
Run with: `docker compose up -d` from the workspace root (it creates the three
networks and starts nothing), or `python scripts/stack/stack.py up anchor`.

> **Profiles: FIVE planes are profile-gated, not one.** `frontend`
> (`stock` | `gpu` | `tailscale`), `inference` (`local`), `portal` (`internet`),
> `agent-org` (`workers`, `cloud`) and `ob1` (`idea-refinery`, plus
> `research`/`wiki`/`notebook` declared in the manifest and not yet in the pinned
> gitlink). Each plane's set comes from **its own `<plane>/.env`**, and a
> `--profile` flag on the command line REPLACES that value rather than adding to
> it. **There is no `local-test` compose profile** anywhere in
> `portal/docker-compose.yml`, despite several comments naming one: portal test
> mode is the overlay file `portal/local-test.override.yml`, and
> `portal-on.ps1 -Test` passes no profile at all.
> The **Portal** plane is `manual` in the manifest: the driver never starts or
> stops it, `scripts/portal/portal-on.ps1` / `portal-off.ps1` do.

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
| Container | Backs up | Networks | Profile |
|-----------|----------|----------|---------|
| `openbrain-db-backup` | `pg_dump` of OB1 Postgres (**open-brain** project since 2026-08-21; output still `./backups/openbrain-db`) | obnet (native) | default (open-brain) |
| `openbrain-wiki-backup` **[profile `wiki`]** | openbrain-wiki-data + wiki-assets (**open-brain** project since 2026-08-21; output still `./backups/openbrain-wiki`) | — | `wiki` (open-brain) — declared in the manifest and the curated inventory; the PINNED gitlink does not carry it yet, so today it renders unprofiled |
| `agent-bridge-db-backup` | `pg_dump` of `agent-bridge-db` (**agent-org** project; governance/effort/project state) | ao-net | default (agent-org) |
| `mattermost-db-backup` | `pg_dump` of `mattermost-db` (**agent-org** project; conversation content) | ao-net | default (agent-org) |
| `caddy-backup` | caddy-data (supercronic, `CADDY_BACKUP_CRON`, default `0 3 * * *`) | default, edge-net | **none** — it carries no `profiles:` key, so a bare portal `up` starts it |
| `authelia-backup` | authelia-data (supercronic, `AUTHELIA_BACKUP_CRON`, default `0 3 * * *`) | default, auth-net | **none** — same |

**Portal (internet-exposed front-end — its own project; `manual`, so NOT in any driver `up`)**

Re-read from `portal/docker-compose.yml` 2026-09-19: **only two of the twelve
carry a `profiles:` key.** A bare `docker compose -f portal/docker-compose.yml
up -d` starts ten of them with no tunnel - which is why the documented path is
`portal-on.ps1`, not a bare `up`. (Render: 10 services without a profile, 12
with `--profile internet`.) **The table below is TEN of the twelve** - the two
backup sidecars, `caddy-backup` and `authelia-backup`, are rows in the Backups
table above, not here.

| Container | Role | Networks | Profile |
|-----------|------|----------|---------|
| `portal-init` | one-shot: chown the four volumes to the service UIDs, then exits | none (`network_mode: none`) | none |
| `caddy` | reverse proxy + `forward_auth`; sole ingress (no host port) | edge-net, auth-net, app-net | none |
| `authelia` | SSO / 2FA auth gateway (:9091 internal) | auth-net | none |
| `cloudflared` | Cloudflare Tunnel — the only internet ingress | edge-net | **`internet`** |
| `portal-alerter` | Deno alert/digest → Gmail (:8080); the plane's only internet egress | auth-net, notify-net | none |
| `authelia-watcher` | tails auth/access logs → alerts (new-IP, etc.) | auth-net | none |
| `authelia-notif-bridge` | forwards Authelia OTP/notifications → alerter | auth-net | none |
| `integrity-tripwire` | hashes Caddyfile/Authelia configs; alerts on drift (`TRIPWIRE_CRON`, default `0 4 * * *`) | auth-net | none |
| `portal-cron` | supercronic → triggers the daily portal digest (`PORTAL_DIGEST_CRON`, default `0 7 * * *`) | notify-net | none |
| `tunnel-watcher` | probes `cloudflared:2000/ready`; alerts on tunnel down | edge-net, auth-net | **`internet`** |

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
> repo root. The `..`-rooted BIND mounts (`../status-pipe`, `../system-prompts`,
> `../data/tailscale`, `../backup`, `../backups`) are unchanged: those trees are
> not plane-internal. The root `.dockerignore` moved with them to
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
for `emergency-recovery.ps1`, for `scripts/recovery/quick-fixes.bat`'s four
tailscale calls, and for the `stop tailscale openwebui` recipe documented in
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

| Profile | Services | For |
|---------|----------|-----|
| *(none)* | `openwebui-backup` only | nothing useful — a misconfigured `.env` looks like this |
| `stock` | `openwebui-stock` (+ backup) | a fresh clone: pinned upstream image, no GPU, no local build, no anchor network, no other plane |
| `gpu` | `openwebui` (+ backup) | this host: the CUDA local build, the nvidia device reservation, the `llm-net`/`app-net` seams, the status-pipe mount |
| `tailscale` | `tailscale`, `tailscale-backup` | the tailnet node. Requires `gpu` — `network_mode: service:openwebui` names that service |

`openwebui-stock` and `openwebui` both declare `container_name: openwebui` and
share `frontend_openwebui-data`, so exactly one may be active: turning on both
is refused by `docker compose config` before anything starts. The container
table below is the `gpu,tailscale` deployment.

| Container | Role | Host port | Networks | GPU |
|-----------|------|-----------|----------|-----|
| `openwebui` | Open WebUI chat surface (service `openwebui` under `gpu`; service `openwebui-stock` under `stock`) | 127.0.0.1:3000 | default, llm-net, app-net (external) + owui-net (project-local) | yes (`gpu` only) |
| `tailscale` | Tailnet VPN; shares openwebui netns; 8 serve routes (OWUI, llama-cpp aliases — probe = `/health/liveliness` since J.1, ON :8443/:5055, wiki :8444 via caddy:8446, LiteLLM UI :8445, Mattermost :8446) | — (`network_mode: service:openwebui`) | — | no |
| `openwebui-backup` | openwebui-data (mem-capped 1g; output still `./backups/openwebui`). No profile — it runs in every deployment, which is why it sits on `owui-net` (the one net both openwebui definitions share) rather than `ai-stack_default` | — | owui-net (project-local) |  |
| `tailscale-backup` | tailscale state dir (bind mount). Gets the project's `default` net implicitly (= `ai-stack_default`) — its compose comment saying "no network attachment" describes the mounts, not the render | — | default (external) |  |

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
> **Profile `local`** gates the four host-specific services marked below
> (`llama-cpp-upstream`, `llama-cpp-embed-upstream`, `llm-queue`,
> `lm-models-backup`). Off, the project renders as `llm-gateway` +
> `llm-gateway-db` + `llm-gateway-ui` + `llm-gateway-backup`: a LiteLLM front
> door that can serve CLOUD models on a machine with no GPU. **The operator's
> deployment runs with it ON**, through `COMPOSE_PROFILES` in **`inference/.env`**
> (which `inference/.env.example` ships COMMENTED OUT, so a straight copy of the
> example is the cloud-only four) and NOT through `--profile local` — `llm-gateway` reads `COMPOSE_PROFILES` to decide which
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

| Container | Role | Host port | Networks | GPU |
|-----------|------|-----------|----------|-----|
| `llm-gateway` | **LiteLLM analytics front door** (holds the `llama-cpp` + `llama-cpp-embed` network aliases on :8080; all callers reach inference through it). Routes `/v1/*` by model name; **both chat AND embed** forward to **`llm-queue`** (api_base, since B2/P4); `num_retries:3` (a queue 429 → retry → hold-and-dispatch); read-only `/observe/*` pass-through to `llm-queue` for the live board; master_key + per-caller virtual keys since J.1 2026-08-21 (x-ai-stack-caller lane header) — per-caller spend ledger; `background_health_checks:false` (a model health-probe forces a llama-swap load → thrash) | — (internal-only; admin/ledger via `docker exec`, not host :4000 — `llm-net` is `internal:true` so host publish is inert) | llm-net, llm-backend-net (sole bridge) | no |
| `llm-queue` **[profile `local`]** | **B2 front-ended inference admission controller** (`inference/llm-queue/` — the source tree moved INTO the plane at sl-colo-inference 2026-09-19, design `DESIGN-B2-inference-queue.md`). Sits between LiteLLM and the `*-upstream` servers (chat + embed): holds-and-dispatches (release-on-completion semaphore, priority heap w/ per-key caps, rolling-T wait estimate, per-model depth backstop — chat 24, embed 256) instead of llama-swap dropping overflow with a flat `429`. Replaces the bare `Too many requests` with a structured 429 + `Retry-After`; `enforce_budget:true` (per-service wait budgets §8b). Read-only state reachable from `llm-net` via the gateway's `/observe/*` pass-through; the **mutating** control API (`POST /queue/{id}/priority`/`cancel`, `/keys/{key}/policy`) is operator-only (`docker exec`, never `llm-net`). Analytics events → own SQLite (`llm-queue-data` volume). Tuning invariant: `LLM_QUEUE_SLOTS` == llama-swap `--parallel` (3) and llama-swap `concurrencyLimit: 0` | — (internal-only) | llm-backend-net | no |
| `llm-gateway-ui` | **LiteLLM Admin-UI sidecar** (analytics dashboard at `/ui`, added 2026-06-14). A SECOND LiteLLM instance run **with** a `master_key` (`inference/config/litellm.ui.config.yaml` + `.env` `LITELLM_UI_*`) — which LiteLLM 1.88.1 requires for the UI to log in. Serves **no inference** (carries NO `llama-cpp` alias, no caller points at it), shares `llm-gateway-db` so the dashboard reads the SAME spend ledger `llm-gateway` writes. The master_key is isolated here so the permissive main gateway + its junk-key callers stay untouched. Reached via the tailnet **:8445** serve route (`frontend/entrypoint.sh`) and by the portal Caddy, which is why it also joins `app-net` | — (internal-only; tailnet :8445/ui) | llm-net, app-net | no |
| `llm-gateway-db` | Postgres for the LiteLLM spend-log ledger (`llm-gateway-db-data` volume) — shared by `llm-gateway` (writes) and `llm-gateway-ui` (reads) | — | llm-net | no |
| `llama-cpp-upstream` **[profile `local`]** | llama-swap inference (was `llama-cpp`) — `qwen36-27b` (∥2); 35B is in llama-swap config but **not registered in the gateway**; one model resident at a time; `--no-mmap` (mmap over the C: bind mount hangs) | 127.0.0.1:8081 | llm-backend-net (isolated) | yes (device 0) |
| `llama-cpp-embed-upstream` **[profile `local`]** | bge-m3 embeddings server (was `llama-cpp-embed`) | 127.0.0.1:8082 | llm-backend-net (isolated) | yes (device 1) |
| `llm-gateway-backup` | nightly `pg_dump` of the LiteLLM spend ledger (output still `./backups/llm-gateway`) | — | llm-net | no |
| `lm-models-backup` **[profile `local`]** | weekly tar of the GGUF model store (HEALTH_TCP liveness probe to `llama-cpp-upstream`; output still `./backups/lm-models`) | — | llm-backend-net | no |

---

## 1c. Memory — compose project `memory` (SEPARATE since 2026-08-21, Part K.2)

> `memory/docker-compose.yml` — mnemory + its cloud privacy gateway + backup.
> Attaches to `ai-stack_llm-net` (external); owns `memory_mnemory-data` (data
> migrated) and a project-local default bridge (host-publishes :8060, carries
> the backup's HEALTH_TCP probe). Doors rule unchanged: local callers hit
> mnemory on llm-net; cloud clients get ONLY the gateway. TRAP: `mnemory` is
> pinned to image `mnemory:local` — a fresh build pulls an unpinned newer
> `mcp` package that crash-loops (fastmcp moved); rebuild deliberately only.

| Container | Role | Host port | Networks |
|-----------|------|-----------|----------|
| `mnemory` | Unified memory layer (mgmt :8051) | — (internal only) | llm-net |
| `mnemory-cloud-gateway` | Privacy-enforcing MCP proxy for cloud clients | 127.0.0.1:8060 | llm-net, default (project-local `memory_default`) |
| `mnemory-backup` | nightly tar of mnemory-data (output still `./backups/mnemory`) | — | default (project-local) |

---

## 1d. Search — compose project `search` (SEPARATE since 2026-08-21, Part K.3)

> `search/docker-compose.yml` — the Private Search Gateway (all egress over
> Mullvad WireGuard). Owns `search-net` (internal) natively; `vpn` + `gateway`
> attach EXTERNALLY to the anchor's `ai-stack_default` so their DNS names keep
> resolving for OB1 (openbrain-research/podcast FETCH_PROXY `http://vpn:8888`)
> and OWUI. No data volumes (redis is deliberately in-memory).

| Container | Compose service | Role | Host port | Networks |
|-----------|-----------------|------|-----------|----------|
| `search-vpn` | `vpn` | Mullvad WireGuard (gluetun) — engine-query AND page-fetch egress + kill-switch (HTTP proxy :8888) | — | search-net, ai-stack_default (external) |
| `search-redis` | `redis` | SearXNG cache | — | search-net |
| `searxng` | `searxng` | Metasearch engine | — | search-net |
| `search-gateway` | `gateway` | REST / Tavily-shim API | 127.0.0.1:8085 | search-net, ai-stack_default (external) |

---

## 1e. Coder — compose project `coder` (SEPARATE since 2026-08-21, Part K.4)

> `coder/docker-compose.yml` — the little-coder control plane. `open-terminal`
> moved in from core (it is this plane's executor; control-plane DECIDES /
> open-terminal EXECUTES), making `lc-net` fully plane-native. Owns the seven
> coder_little-coder-* volumes (expertise ×5 + sessions + workspace; data
> migrated). llm-net external (inference + the OWUI/agent-org callers of the
> daemon); lc-egress gets internet via a project-local bridge.

| Container | Role | Host port | Networks |
|-----------|------|-----------|----------|
| `open-terminal` | Workspace plane — executes agent commands (egress via `lc-egress`) | — | lc-net, llm-net |
| `little-coder` | Control daemon — decides (daemon :8090) | 127.0.0.1:9091 (metrics) | lc-net, llm-net |
| `lc-egress` | Egress allowlist proxy (git host only) | — | lc-net, default (project-local) |
| `little-coder-backup` | nightly tar of the expertise volumes (output still `./backups/little-coder`) | — | default (project-local `coder_default`; it declares no `networks:` key, so compose attaches the project default) |

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
> the pinned compose file, and the tables below mark each gated container
> **[profile `x`]**. Measured at `fe3e045` with `config --services`:
>
> | Render | Services |
> |---|---|
> | bare (`docker compose -f OB1/docker/docker-compose.yml config --services`) | **20** |
> | `--profile idea-refinery` | 21 (+1) |
> | `--profile research` | 22 (+2) |
> | `--profile notebook` | 23 (+3) |
> | `--profile wiki` | 24 (+4) |
> | `--profile idea-refinery --profile research` (**what the driver passes by default**) | **23** |
> | all four | **30** - the same 30 names `docker ps` lists for the `open-brain` project |
>
> Every row is a RENDER, including the two-profile one. It is listed because the set
> the driver actually passes deserves a measurement of its own: attempt 1 of this item
> described it with the **22** from the `research` row - a real number for a different
> set - and shipped that to eight files. Render the set you are about to describe.
>
> So a bare `up` starts **20, not 30**: ten containers are now gated, and seven of
> them (`wiki` + `notebook`) are ones no driver default passes. It is not only `up` —
> **a bare `stop` addresses 20 of 30, and a bare `down` removes 20, leaves the ten
> gated ones running, and then FAILS to drop the project network**
> (`Resource is still in use`), measured 2026-09-20 in a throwaway two-service
> compose project. Seven of the ten gated OB1 containers hold endpoints on
> `ai-stack_llm-net` / `app-net` / `default` — the anchor networks
> `emergency-recovery.ps1` tears OB1 down first in order to free.
>
> **THE LANDING STEP for this host, both halves:**
>
> - **`COMPOSE_PROFILES=research,wiki,notebook,idea-refinery` in `OB1/docker/.env`** —
>   the per-plane env file (D17). Read 2026-09-20: `frontend/.env` carries
>   `gpu,tailscale`, `inference/.env` carries `local`, and `OB1/docker/.env` carries
>   nothing. (`portal` deliberately has none — CLAUDE.md, it is started by hand — and
>   `agent-org`'s `workers`/`cloud` are operator-driven slices. OB1 is the one whose
>   absence now bites, because its bare verbs sit in the recovery path.) Raw
>   `docker compose` honours it (renders the same 30), and it is what repairs a bare
>   `stop`/`down`.
> - **the driver's state** —
>   `python scripts/stack/stack.py init --product research --force`, or
>   `enable research`, writes `idea-refinery, research, wiki, notebook` into
>   `.stack/state.json`, and `up --dry-run` then prints all four `--profile` flags
>   on the OB1 line.
>
> They do not fight — `stack.py` UNIONS the plane env's list into whatever flags it
> passes (`effective_profiles`). **The consequence, stated rather than discovered:**
> because it unions, a host whose `OB1/docker/.env` carries all four makes
> **`--headless` a no-op for this plane** — the driver drops `wiki`/`notebook` and the
> env file puts them back. On this host, which runs all 30, that is the right trade;
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
> `requires` closure), which renders **23** — seven short. (23, not 22: 22 is the
> `--profile research` row above, on its own. Render the pair; do not add deltas.)
> Note that a bare `docker compose --profile X`
> **REPLACES** `COMPOSE_PROFILES` rather than adding to it - measured here: with all
> four in the env file, `--profile research` alone renders 22.
>
> The rows below name what each profile gates:
>
> | Profile | Turns on | Why not core |
> |---------|----------|--------------|
> | `research` | `openbrain-curator`, `openbrain-research` | the research ENGINE; the store captures, embeds, chunks and serves without it |
> | `wiki` | `openbrain-wiki`, `-wiki-viewer`, `-workbench`, `-wiki-backup` | a reading/writing SURFACE onto the store |
> | `notebook` | `surrealdb`, `open_notebook`, `open-notebook-backup` | a second SURFACE onto the store (openbrain-db is canonical since IKS) |
> | `idea-refinery` | `openbrain-idea-refinery` | pre-existing profile, and **running on this host** — both drivers pass it on every invocation. It is gated because it needs a Mattermost bot token to deliver dossiers, not because it is waiting for one. `requires` the `research` profile (its only engine) — see below |
>
> The full set is all four:
> `docker compose -f OB1/docker/docker-compose.yml --profile research --profile wiki --profile notebook --profile idea-refinery up -d`
> — and `python scripts/stack/stack.py up ob1` passes `idea-refinery` (the
> plane's one `default = true` profile) plus `research` (its `requires` closure)
> unless the state file or `OB1/docker/.env` enables more; `stack.ps1` is a shim
> with no profile list of its own. Before the gitlink bumped, two profiles and four
> rendered the same 30 services because compose ignores a profile it does not know;
> at `fe3e045` that is over — that pair renders 23 and four render 30.
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
> and `status-pipe/modules/system-health/` probes `open_notebook` and
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
| Container | Role | Host port | Networks |
|-----------|------|-----------|----------|
| `openbrain-db` | PostgreSQL 16 + pgvector | — | obnet |
| `openbrain-mcp` | Core MCP server | — (internal only) | obnet, llm-net |
| `openbrain-ext` | Extensions MCP server (39 tools) | — (internal only) | obnet |
| `openbrain-gateway` | Privacy-enforcing MCP proxy for cloud clients | 127.0.0.1:8061 | obnet |
| `openbrain-ops-gateway` | Same image, OPS profile: agent-memory tools for HOST processes, own key | 127.0.0.1:8062 | obnet |
| `openbrain-mcpo` | MCP→OpenAPI bridge (core) | — | obnet, llm-net |
| `openbrain-mcpo-ext` | MCP→OpenAPI bridge (extensions) | — | obnet, llm-net |
| `openbrain-postgrest` | PostgREST API over openbrain-db | — | obnet |
| `openbrain-rest` | Caddy `/rest/v1` path-stripping proxy | 127.0.0.1:3001 | obnet |
| `openbrain-entity-worker` | Entity-extraction worker | 127.0.0.1:8810 | obnet, llm-net |
| `openbrain-suggestion-worker` | Cross-thread suggestion worker (Integrated Knowledge System; `POST /suggest`) | 127.0.0.1:8813 | obnet, llm-net |
| `openbrain-curator` **[profile `research`]** | Research-package ingestion inlet (`POST /ingest/research-package`); resolves deep-research onto the best existing thread (pgvector shortlist + LLM decision), delegates the write to openbrain-mcp `/research/persist`, writes grounded claim→source edges (Research Engine P2); deno-postgres + llama-cpp + llama-cpp-embed | 127.0.0.1:8816 | obnet, llm-net |
| `openbrain-research` **[profile `research`]** | Shared research harness (Research Engine P3/P4; `POST /research` → job_id, `GET /research/jobs/:id[/stream]`); reuses grounded claims → gap analysis → stages gaps (SearXNG + per-page fetch) → synthesizes verbatim with `[Source N]` citations → enforces grounding (honest `[GAP]`s, never fabricates) → delegates placement+claims to openbrain-curator; deno-postgres + llama-cpp + llama-cpp-embed + SearXNG gateway | 127.0.0.1:8818 | obnet, llm-net, search-gw-net (=ai-stack_default, to reach the private `gateway`) |
| `openbrain-chunk-worker` | Writer-agnostic chunk-embedding worker (Integrated Knowledge System); chunks any OB1 source into `source_chunks` (1200/150 + bge-m3) so passage-level vector retrieval works for every frontend, incl. Open Notebook "ask your knowledge base"; periodic scan + `POST /chunks`; deno-postgres + llama-cpp-embed | 127.0.0.1:8817 | obnet, llm-net |
| `openbrain-grounding-backfiller` | S2 brain-health worker. (1) Drains `ungrounded_claims` — per claim extracts its entity (local `:nothink` LLM) → fetches the Wikipedia page (through the Mullvad `vpn` proxy) → `find_or_create_source` + `link_claim_to_source 'corroborates'` so confidence recomputes and the claim leaves the view; `POST /backfill?limit=N {thread_ids?}`. (2) Heals thin/failed web **sources** whose ingestion truncated them (~150-char stubs) — `POST /refetch?limit=N` re-fetches (proxy-first, direct fallback), updates content (chunk-worker re-embeds), 3-attempt cap then `refetch_failed`. Cron: backfill 07:00 UTC, refetch 07:30 UTC. deno-postgres | 127.0.0.1:8819 | obnet, llm-net, search-gw-net (the `vpn` proxy) |
| `openbrain-wiki` **[profile `wiki`]** | Wiki compiler + scheduler | 127.0.0.1:8811 | obnet, llm-net |
| `openbrain-wiki-viewer` **[profile `wiki`]** | Quartz 4 read-only wiki viewer (also tailnet HTTPS `:8444` + Caddy `wiki.${PUBLIC_DOMAIN}`) | 127.0.0.1:8812 | obnet, app-net |
| `openbrain-workbench` **[profile `wiki`]** | Deno+Hono read/write API behind the viewer (`/workbench/*` via portal Caddy `handle`, X-Brain-Key injected); deno-postgres writes + PostgREST reads | 127.0.0.1:8814 (debug only) | obnet, llm-net, app-net |
| `openbrain-extract` | FastAPI content-extraction sidecar (`POST /extract`: PDF/DOCX/PPTX/image-OCR/audio-STT registry); sandboxed (non-root, cap_drop, read-only FS); reaches host STT via `host.docker.internal` | 127.0.0.1:8815 (debug only) | obnet |
| `openbrain-cron` | supercronic + curl; fires HTTP-trigger chain (no docker.sock) | — (internal only) | obnet |
| `openbrain-gmail-pull` | HTTP-triggered Gmail ingest; chains to prune on success | — (internal only) | obnet, llm-net |
| `openbrain-gmail-prune` | HTTP-triggered short-term prune; chains to digest + wiki recompile | — (internal only) | obnet, llm-net |
| `openbrain-digest` | HTTP-triggered daily digest; mechanical formatting, Gmail send; chains to podcast after delivery | — (internal only) | obnet, llm-net |
| `openbrain-podcast` | HTTP-triggered chain tail (digest → podcast); spawns the link-enrich pipeline — follow newsletter links (through the `vpn` proxy) → grounded research via openbrain-research (article mode) → two-host script → Open Notebook audio → loop-close (episode source linked to the day's threads); best-effort, never blocks the email | — (internal only) | obnet, llm-net, search-gw-net (=ai-stack_default; the `vpn` proxy) |
| `openbrain-idea-refinery` | **Idea Refinery drain** (IR.1–IR.5/IR.7): `POST /run` walks the owed-idea queue (`ideas`/`idea_revisions`, init-ideas.sql), researches each via openbrain-research (bounded submit-on-complete + rollover), posts the gap-centered dossier to Mattermost `#ideas` (via `host.docker.internal:8065`), ages to dormant + resurfaces. Stand-alone cron `03:00 UTC` (before the 05:00-UTC gmail/wiki chain → new claims feed the 1am-local wiki compile). **PROFILE-GATED (`idea-refinery`)** — NOT started by a plain `up`. deno-postgres | — (internal only) | obnet, llm-net |
| `openbrain-db-backup` | Nightly `pg_dump` of `openbrain-db` (moved from ai-stack 2026-08-21 — OB1 owns its backups; output still lands in `ai-stack/backups/openbrain-db` for the NAS mirror + freshness watchers) | — | obnet |
| `openbrain-wiki-backup` **[profile `wiki`]** | Daily tar of `openbrain-wiki-data` + `wiki-assets` (moved from ai-stack 2026-08-21; output still `ai-stack/backups/openbrain-wiki`) | — | default (project-local `open-brain_default`; it declares no `networks:` key) |

**Scheduled-job slice:** the five always-on services (`openbrain-cron` + the
four HTTP-triggered jobs) — plus the **profile-gated `openbrain-idea-refinery`**
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

| Container | Purpose | Host port | Networks |
|-----------|---------|-----------|----------|
| `surrealdb` **[profile `notebook`]** | Open Notebook local store (SurrealDB v2, digest-pinned) | 127.0.0.1:8003 | default (open-brain) |
| `open_notebook` **[profile `notebook`]** | Open Notebook UI + API (IKS fork — openbrain-db is the canonical store) | 127.0.0.1:8503 / :5055 | default, obnet, ai-stack_llm-net + app-net (external) |
| `open-notebook-backup` **[profile `notebook`]** | SurrealDB logical export + notebook_data tar (output still `ai-stack/backups/open-notebook`) | — | default (open-brain) |


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
| Container | Role | Host port | Networks | Profile |
|-----------|------|-----------|----------|---------|
| `mattermost-db` | Postgres for Mattermost | — | ao-net | default |
| `mattermost` | Chat platform + mobile (Team Edition); on llm-net too so the `tailscale` netns can reach it for `tailscale serve` (P7.4) | 127.0.0.1:8065 | ao-net, llm-net | default |
| `agent-bridge` | Orchestration + the governance gate (FastAPI); WebSocket consumer + REST poster; floor-hook endpoint | 127.0.0.1:8830 | ao-net, llm-net | default |
| `agent-bridge-db` | Postgres — the bridge's fail-safe state store (gate/effort/parked-effort/project/scope/audit) | — | ao-net | default |
| `agent-bridge-db-backup` | Nightly `pg_dump` of `agent-bridge-db` (governance/effort/project state) → repo-root `./backups/agent-bridge-db/` (generic `backup/pg-backup.sh`) | — | ao-net | default |
| `mattermost-db-backup` | Nightly `pg_dump` of `mattermost-db` (conversation content) → `./backups/mattermost-db/` | — | ao-net | default |
| `ao-worker-1-journals-backup` / `ao-worker-2-journals-backup` | Nightly tar of each worker's append-only task journals → `./backups/ao-worker-{1,2}-journals/` (generic `backup/generic-tar-backup.sh`). One sidecar per volume so each archive restores 1:1; profile-gated with the workers | — | default (project-local `agent-org_default`; they declare no `networks:` key, so compose attaches the project default - the volume is their only SOURCE, but the render gives them a network) | workers |
| `ao-worker-1` / `ao-worker-2` | Pooled `little-coder` control daemons (reuse `little-coder:local`) | — | ao-worker-net, llm-net | workers |
| `ao-ot-1` / `ao-ot-2` | Per-worker `open-terminal` workspace planes (reuse `little-coder-open-terminal:local`) | — | ao-worker-net, llm-net | workers |
| `ao-git-egress` | Shared git-allowlist egress for the worker pool (mirrors `lc-egress`); allowlist is the **bridge-written** `ao-egress-config` file, reloaded on change (custom `docker/egress/tinyproxy.conf` + `egress-reload.sh` command override) so the org can work on any onboarded repo | — | ao-worker-net, default | workers |
| `llm-gateway-cloud` | **CONDITIONAL** separate LiteLLM for OpenRouter (master_key + per-role budgets); the only egress, via `ao-egress` | — | ao-net, ao-cloud-egress-net | cloud |
| `llm-gateway-cloud-db` | Postgres for the cloud LiteLLM spend ledger | — | ao-net | cloud |
| `ao-egress` | Allowlist egress proxy pinned to `openrouter.ai` (mirrors `lc-egress`); the ONLY agent-org internet path | — | ao-cloud-egress-net, default | cloud |

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
| `scripts/recovery/quick-fixes.bat`, `scripts/recovery/update-stack.bat` | Present, but NOT a recovery path any more: both issue bare `docker compose` commands at the repo root, which since Part K is the zero-service anchor. Use the `.ps1` above. (The `emergency-recovery.bat` twin was archived 2026-08-21.) |
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
A nuclear `docker compose down` stops a running portal; recovery detects this and
**warns** rather than auto-restoring the internet front-end.

---

## Cross-stack dependency order

**The order is DATA, not prose**: it is a topological sort of the `requires`
edges in `stack.manifest.toml`, with ties broken by the order the `[planes.*]`
tables are declared. `python scripts/stack/stack.py up --all --dry-run` prints
the exact `docker compose` line for each plane, in order, and runs nothing -
prefer that over reading this list. `emergency-recovery.ps1` uses the same
relative order.

Plane by plane (start in this order; `down` reverses it):

1. **anchor** (`docker compose up -d` at the root) — creates
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
    which names ten of the twelve (see the portal note in section 1).
