# Workspace Stack Map

Authoritative inventory of the Docker stacks in this `ai-stack` workspace.
Cross-check against the live compose files before relying on it — the files
are the source of truth; this doc is the curated summary.
Per-container purpose & justification: [documentation/CONTAINER-REGISTRY.md](../../../documentation/CONTAINER-REGISTRY.md).

**Last reconciled against live compose: 2026-08-21** — Part K restructure
COMPLETE: the root `ai-stack` project is a **pure network anchor (0
services)**; each plane is its own compose project — `frontend` (K.5, incl.
the openwebui+tailscale netns pair), `inference` (K.1, owns
`llm-backend-net`), `memory` (K.2), `search` (K.3, owns `search-net`),
`coder` (K.4, owns `lc-net`, adopted open-terminal); the Open Notebook trio
joined OB1 (K.5b). Driver: `scripts/stack/stack.ps1`. Earlier that day the
openbrain-db/wiki backups moved into OB1 and `smolcrawl-pipelines`/`-backup`
retired. 2026-08-20 CLEANUP-PLAN v3 execution day: the root compose became a
thin include of `compose/<plane>.yml` files (rendered model proven identical);
**retired**: `watchtower`, `search-mcpo`, `lc-mcpo`; the **portal became its
own compose project `portal`** on 2026-08-21 (12 services,
`portal/docker-compose.yml`, data migrated to `portal_*` volumes, joins
`ai-stack_app-net` externally); the status-pipe
subsystem consolidated to `status-pipe/` and OWUI's whole-repo mount replaced
by three narrow ro mounts; entrypoint.sh rewritten to a 318-line route table
(ollama/LM Studio blocks gone). Prior reconcile (2026-07-01) — added the **`agent-org`** project
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
- `docker-compose.yml` — the **main** (anchor) project (the watchtower-era
  `docker-compose.override.yml` was archived at K.5 — its settings live in
  `frontend/docker-compose.yml` now)
- `portal/local-test.override.yml` — portal test mode, no Cloudflare (own project since 2026-08-21)
- `OB1/docker/docker-compose.yml` (+ `docker-compose.scheduled.yml`) — the **open-brain** project (separate)
- `agent-org/docker/docker-compose.yml` — the **agent-org** project (separate; teams-chat orchestration)

---

## 1. Root anchor — compose project `ai-stack` (0 services since K.5b)

Files: `docker-compose.yml` (thin include of `compose/<plane>.yml`; the network ANCHOR — owns `llm-net`/`app-net`/`default`); plane projects at `frontend|inference|memory|search|coder/docker-compose.yml`; portal = `portal/docker-compose.yml` (own project).
Run with: `docker compose ...` from the workspace root.

> **Profiles:** the **Portal** plane below is gated behind `profiles: [internet]`
> (or `[internet, local-test]`) and does **NOT** start with a plain `docker compose up -d` —
> it's driven by `scripts/portal/portal-on.ps1` / `portal-off.ps1`. Everything else starts by default.

### Networks
| Network      | Type            | Purpose |
|--------------|-----------------|---------|
| `llm-net`    | internal (no internet) | **caller plane / shared seam**: every inference consumer sits here and reaches inference ONLY via the `llama-cpp` / `llama-cpp-embed` aliases on **`llm-gateway`** (LiteLLM, in the **inference** project — it attaches externally). The `*-upstream` real servers are NOT here (isolated on the inference project's native `llm-backend-net`) so callers cannot route around LiteLLM |
| `search-net` | internal (no internet) | search gateway isolation — only `vpn` (Mullvad; engine queries AND page fetches since tor retired 2026-08-21) bridges out |
| `lc-net`     | internal (no internet) | little-coder control plane isolation |
| `auth-net`   | bridge, **internal** | portal: caddy ↔ authelia ↔ portal-alerter ↔ watchers (no internet) |
| `app-net`    | bridge          | caddy ↔ openwebui / open_notebook (backends reached only via caddy) |
| `edge-net`   | bridge          | portal ingress: cloudflared ↔ caddy |
| `notify-net` | bridge          | portal egress chokepoint (portal-alerter → Gmail; portal-cron) |
| `default`    | bridge          | host-reachable / internet egress |
| `obnet`      | external (`open-brain_obnet`) | so `open_notebook` (IKS) can reach OB1's Postgres |

### Planes & containers

**Backups (unified snapshot sidecars — `backup/` scripts, nightly cron; NAS-synced)**
| Container | Backs up | Networks | Profile |
|-----------|----------|----------|---------|
| `openbrain-db-backup` | `pg_dump` of OB1 Postgres (**open-brain** project since 2026-08-21; output still `./backups/openbrain-db`) | obnet (native) | default (open-brain) |
| `openbrain-wiki-backup` | openbrain-wiki-data + wiki-assets (**open-brain** project since 2026-08-21; output still `./backups/openbrain-wiki`) | — | default (open-brain) |
| `agent-bridge-db-backup` | `pg_dump` of `agent-bridge-db` (**agent-org** project; governance/effort/project state) | ao-net | default (agent-org) |
| `mattermost-db-backup` | `pg_dump` of `mattermost-db` (**agent-org** project; conversation content) | ao-net | default (agent-org) |
| `caddy-backup` | caddy-data | default, edge-net | internet, local-test |
| `authelia-backup` | authelia-data | default, auth-net | internet, local-test |

**Portal (internet-exposed front-end — profile-gated; NOT in a default `up`)**
| Container | Role | Networks | Profile |
|-----------|------|----------|---------|
| `portal-init` | one-shot: chown portal volumes, exits | none (`network_mode: none`) | internet, local-test |
| `caddy` | reverse proxy + `forward_auth`; sole ingress (no host port) | edge-net, auth-net, app-net | internet, local-test |
| `authelia` | SSO / 2FA auth gateway (:9091 internal) | auth-net | internet, local-test |
| `cloudflared` | Cloudflare Tunnel — the only internet ingress | edge-net | internet |
| `portal-alerter` | Deno alert/digest → Gmail (:8080) | auth-net, notify-net | internet, local-test |
| `authelia-watcher` | tails auth/access logs → alerts (new-IP, etc.) | auth-net | internet, local-test |
| `authelia-notif-bridge` | forwards Authelia OTP/notifications → alerter | auth-net | internet, local-test |
| `integrity-tripwire` | hashes Caddyfile/Authelia configs; alerts on drift | auth-net | internet, local-test |
| `portal-cron` | supercronic → triggers the daily portal digest | notify-net | internet, local-test |
| `tunnel-watcher` | probes `cloudflared:/ready`; alerts on tunnel down | edge-net, auth-net | internet |

### Volumes
`openwebui-data`, `mnemory-data`, `smolcrawl-data`,
`little-coder-journals`, `little-coder-skill`, `little-coder-cohorts`,
`little-coder-polyglot`, `little-coder-sessions`, `little-coder-workspace`,
`caddy-data`, `caddy-config`, `authelia-data`, `tripwire-data`.
(`llm-gateway-db-data` + `llm-queue-data` migrated to the inference project
2026-08-21 — now `inference_*` volumes.)
**External** (owned by the open-brain project): `openbrain-wiki-data`
(= `open-brain_openbrain-wiki-data`), `wiki-assets` (= `open-brain_wiki-assets`).

---


## 1a. Frontend — compose project `frontend` (SEPARATE since 2026-08-21, Part K.5)

> `frontend/docker-compose.yml` — openwebui + its tailscale netns companion +
> their backups. NETNS RULE unchanged (never restart openwebui alone); the
> project's depends_on encodes the openwebui→tailscale order. All three nets
> attached externally (ai-stack_default / llm-net / app-net) so every DNS
> seam holds. openwebui-data migrated to `frontend_openwebui-data` (~10 GB).
> Images pinned (`openwebui:local` / `tailscale:local`) — rebuilds are
> deliberate, per the UPDATE-MANAGEMENT runbook, never an `up` side effect.

| Container | Role | Host port | Networks | GPU |
|-----------|------|-----------|----------|-----|
| `openwebui` | Open WebUI chat surface | 127.0.0.1:3000 | default, llm-net, app-net (all external) | yes |
| `tailscale` | Tailnet VPN; shares openwebui netns; 8 serve routes (OWUI, llama-cpp aliases — probe = `/health/liveliness` since J.1, ON :8443/:5055, wiki :8444 via caddy:8446, LiteLLM UI :8445, Mattermost :8446) | — (`network_mode: service:openwebui`) | — | no |
| `openwebui-backup` | openwebui-data (mem-capped 1g; output still `./backups/openwebui`) | — | default (external) |  |
| `tailscale-backup` | tailscale state dir (bind mount) | — | — |  |

---

## 1b. Inference — compose project `inference` (SEPARATE since 2026-08-21, Part K.1)

> `inference/docker-compose.yml` — the LLM host is its own service tree. Drive it
> with `scripts/stack/stack.ps1` or `docker compose -f inference/docker-compose.yml
> --env-file .env ...` from the repo root (fail-loud without the env file).
> It ATTACHES to the anchor's `ai-stack_llm-net` (external; llm-gateway carries the
> `llama-cpp`/`llama-cpp-embed` aliases there) and OWNS the internal `llm-backend-net`
> plus the `inference_llm-gateway-db-data` / `inference_llm-queue-data` volumes
> (data migrated from the ai-stack_* volumes at the split).

| Container | Role | Host port | Networks | GPU |
|-----------|------|-----------|----------|-----|
| `llm-gateway` | **LiteLLM analytics front door** (holds the `llama-cpp` + `llama-cpp-embed` network aliases on :8080; all callers reach inference through it). Routes `/v1/*` by model name; **both chat AND embed** forward to **`llm-queue`** (api_base, since B2/P4); `num_retries:3` (a queue 429 → retry → hold-and-dispatch); read-only `/observe/*` pass-through to `llm-queue` for the live board; master_key + per-caller virtual keys since J.1 2026-08-21 (x-ai-stack-caller lane header) — per-caller spend ledger; `background_health_checks:false` (a model health-probe forces a llama-swap load → thrash) | — (internal-only; admin/ledger via `docker exec`, not host :4000 — `llm-net` is `internal:true` so host publish is inert) | llm-net, llm-backend-net (sole bridge) | no |
| `llm-queue` | **B2 front-ended inference admission controller** (`llm-queue/`, design `DESIGN-B2-inference-queue.md`). Sits between LiteLLM and the `*-upstream` servers (chat + embed): holds-and-dispatches (release-on-completion semaphore, priority heap w/ per-key caps, rolling-T wait estimate, per-model depth backstop — chat 24, embed 256) instead of llama-swap dropping overflow with a flat `429`. Replaces the bare `Too many requests` with a structured 429 + `Retry-After`; `enforce_budget:true` (per-service wait budgets §8b). Read-only state reachable from `llm-net` via the gateway's `/observe/*` pass-through; the **mutating** control API (`POST /queue/{id}/priority`/`cancel`, `/keys/{key}/policy`) is operator-only (`docker exec`, never `llm-net`). Analytics events → own SQLite (`llm-queue-data` volume). Tuning invariant: `LLM_QUEUE_SLOTS` == llama-swap `--parallel` (3) and llama-swap `concurrencyLimit: 0` | — (internal-only) | llm-backend-net | no |
| `llm-gateway-ui` | **LiteLLM Admin-UI sidecar** (analytics dashboard at `/ui`, added 2026-06-14). A SECOND LiteLLM instance run **with** a `master_key` (`config/litellm.ui.config.yaml` + `.env` `LITELLM_UI_*`) — which LiteLLM 1.88.1 requires for the UI to log in. Serves **no inference** (carries NO `llama-cpp` alias, no caller points at it), shares `llm-gateway-db` so the dashboard reads the SAME spend ledger `llm-gateway` writes. The master_key is isolated here so the permissive main gateway + its junk-key callers stay untouched. Reached only via the tailnet **:8445** serve route (`entrypoint.sh`) | — (internal-only; tailnet :8445/ui) | llm-net | no |
| `llm-gateway-db` | Postgres for the LiteLLM spend-log ledger (`llm-gateway-db-data` volume) — shared by `llm-gateway` (writes) and `llm-gateway-ui` (reads) | — | llm-net | no |
| `llama-cpp-upstream` | llama-swap inference (was `llama-cpp`) — `qwen36-27b` (∥2); 35B is in llama-swap config but **not registered in the gateway**; one model resident at a time; `--no-mmap` (mmap over the C: bind mount hangs) | 127.0.0.1:8081 | llm-backend-net (isolated) | yes (device 0) |
| `llama-cpp-embed-upstream` | bge-m3 embeddings server (was `llama-cpp-embed`) | 127.0.0.1:8082 | llm-backend-net (isolated) | yes (device 1) |
| `llm-gateway-backup` | nightly `pg_dump` of the LiteLLM spend ledger (output still `./backups/llm-gateway`) | — | llm-net | no |
| `lm-models-backup` | weekly tar of the GGUF model store (HEALTH_TCP liveness probe to `llama-cpp-upstream`; output still `./backups/lm-models`) | — | llm-backend-net | no |

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
| `mnemory-cloud-gateway` | Privacy-enforcing MCP proxy for cloud clients | 127.0.0.1:8060 | llm-net, default |
| `mnemory-backup` | nightly tar of mnemory-data (output still `./backups/mnemory`) | — | default |

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
| `little-coder-backup` | nightly tar of the expertise volumes (output still `./backups/little-coder`) | — | — |

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

### Networks
| Network   | Type                         | Purpose |
|-----------|------------------------------|---------|
| `obnet`   | bridge                       | OB1 internal + host-published ports |
| `llm-net` | external (`ai-stack_llm-net`)| reach llama-cpp / llama-cpp-embed |
| `app-net` | external (`ai-stack_app-net`)| wiki-viewer / workbench reachable by the portal Caddy |
| `search-gw-net` | external (`ai-stack_default`) | research/grounding/podcast reach the private SearXNG `gateway` + `tor` |

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
| `openbrain-curator` | Research-package ingestion inlet (`POST /ingest/research-package`); resolves deep-research onto the best existing thread (pgvector shortlist + LLM decision), delegates the write to openbrain-mcp `/research/persist`, writes grounded claim→source edges (Research Engine P2); deno-postgres + llama-cpp + llama-cpp-embed | 127.0.0.1:8816 | obnet, llm-net |
| `openbrain-research` | Shared research harness (Research Engine P3/P4; `POST /research` → job_id, `GET /research/jobs/:id[/stream]`); reuses grounded claims → gap analysis → stages gaps (SearXNG + per-page fetch) → synthesizes verbatim with `[Source N]` citations → enforces grounding (honest `[GAP]`s, never fabricates) → delegates placement+claims to openbrain-curator; deno-postgres + llama-cpp + llama-cpp-embed + SearXNG gateway | 127.0.0.1:8818 | obnet, llm-net, search-gw-net (=ai-stack_default, to reach the private `gateway`) |
| `openbrain-chunk-worker` | Writer-agnostic chunk-embedding worker (Integrated Knowledge System); chunks any OB1 source into `source_chunks` (1200/150 + bge-m3) so passage-level vector retrieval works for every frontend, incl. Open Notebook "ask your knowledge base"; periodic scan + `POST /chunks`; deno-postgres + llama-cpp-embed | 127.0.0.1:8817 | obnet, llm-net |
| `openbrain-grounding-backfiller` | S2 brain-health worker. (1) Drains `ungrounded_claims` — per claim extracts its entity (local `:nothink` LLM) → fetches the Wikipedia page (Tor) → `find_or_create_source` + `link_claim_to_source 'corroborates'` so confidence recomputes and the claim leaves the view; `POST /backfill?limit=N {thread_ids?}`. (2) Heals thin/failed web **sources** whose ingestion truncated them (~150-char stubs) — `POST /refetch?limit=N` re-fetches (Tor-first, direct fallback), updates content (chunk-worker re-embeds), 3-attempt cap then `refetch_failed`. Cron: backfill 07:00 UTC, refetch 07:30 UTC. deno-postgres | 127.0.0.1:8819 | obnet, llm-net, search-gw-net (Tor) |
| `openbrain-wiki` | Wiki compiler + scheduler | 127.0.0.1:8811 | obnet, llm-net |
| `openbrain-wiki-viewer` | Quartz 4 read-only wiki viewer (also tailnet HTTPS `:8444` + Caddy `wiki.${PUBLIC_DOMAIN}`) | 127.0.0.1:8812 | obnet, app-net |
| `openbrain-workbench` | Deno+Hono read/write API behind the viewer (`/workbench/*` via portal Caddy `handle`, X-Brain-Key injected); deno-postgres writes + PostgREST reads | 127.0.0.1:8814 (debug only) | obnet, llm-net, app-net |
| `openbrain-extract` | FastAPI content-extraction sidecar (`POST /extract`: PDF/DOCX/PPTX/image-OCR/audio-STT registry); sandboxed (non-root, cap_drop, read-only FS); reaches host STT via `host.docker.internal` | 127.0.0.1:8815 (debug only) | obnet |
| `openbrain-cron` | supercronic + curl; fires HTTP-trigger chain (no docker.sock) | — (internal only) | obnet |
| `openbrain-gmail-pull` | HTTP-triggered Gmail ingest; chains to prune on success | — (internal only) | obnet, llm-net |
| `openbrain-gmail-prune` | HTTP-triggered short-term prune; chains to digest + wiki recompile | — (internal only) | obnet, llm-net |
| `openbrain-digest` | HTTP-triggered daily digest; mechanical formatting, Gmail send; chains to podcast after delivery | — (internal only) | obnet |
| `openbrain-podcast` | HTTP-triggered chain tail (digest → podcast); spawns the link-enrich pipeline — follow newsletter links (Tor) → grounded research via openbrain-research (article mode) → two-host script → Open Notebook audio → loop-close (episode source linked to the day's threads); best-effort, never blocks the email | — (internal only) | obnet, llm-net, search-gw-net (=ai-stack_default, Tor) |
| `openbrain-idea-refinery` | **Idea Refinery drain** (IR.1–IR.5/IR.7): `POST /run` walks the owed-idea queue (`ideas`/`idea_revisions`, init-ideas.sql), researches each via openbrain-research (bounded submit-on-complete + rollover), posts the gap-centered dossier to Mattermost `#ideas` (via `host.docker.internal:8065`), ages to dormant + resurfaces. Stand-alone cron `03:00 UTC` (before the 05:00-UTC gmail/wiki chain → new claims feed the 1am-local wiki compile). **PROFILE-GATED (`idea-refinery`)** — NOT started by a plain `up`. deno-postgres | — (internal only) | obnet, llm-net |
| `openbrain-db-backup` | Nightly `pg_dump` of `openbrain-db` (moved from ai-stack 2026-08-21 — OB1 owns its backups; output still lands in `ai-stack/backups/openbrain-db` for the NAS mirror + freshness watchers) | — | obnet |
| `openbrain-wiki-backup` | Daily tar of `openbrain-wiki-data` + `wiki-assets` (moved from ai-stack 2026-08-21; output still `ai-stack/backups/openbrain-wiki`) | — | — |

**Scheduled-job slice:** the five always-on services (`openbrain-cron` + the
four HTTP-triggered jobs) — plus the **profile-gated `openbrain-idea-refinery`**
drain (Idea Refinery; enable with `--profile idea-refinery`) — live in [`OB1/docker/docker-compose.scheduled.yml`](../../../OB1/docker/docker-compose.scheduled.yml),
included from the main OB1 compose file. Trigger model is event-chained:
cron fires `openbrain-gmail-pull` at 01:00; pull→prune→digest is wired
via `NEXT_TRIGGER_URL` env vars, not multiple cron entries. Schedules
live in [`OB1/docker/cron/crontab`](../../../OB1/docker/cron/crontab)
(bind-mounted; edit + `docker compose restart openbrain-cron` to reload).
No docker.sock anywhere — chain hops are HTTP `POST /run` calls on
`obnet` between long-running services.

**Cloud privacy split:** `openbrain-mcp` and `openbrain-ext` no longer publish
host ports — cloud services (Claude Code, ChatGPT) must enter through
`openbrain-gateway` at `127.0.0.1:8061`. Gateway forces
`metadata.share=cloud` on reads and stamps `metadata.origin=cloud,
share=cloud` on writes; the 39 extension tools are blocked entirely.
Mirrors the mnemory-cloud-gateway pattern (`mnemory-gateway/app.py`). Local
trusted clients (OWUI via the mcpo bridges, recipes on obnet, the
entity worker, the wiki compiler) keep talking to `openbrain-mcp` /
`openbrain-ext` directly on internal networks and are unaffected.

### Volumes
`openbrain-db-data`, `openbrain-wiki-data`, `wiki-assets` (binary assets —
images now, audio later — written by `openbrain-workbench`, served read-only by
`openbrain-wiki-viewer`; deliberately NOT mounted into `openbrain-wiki` so
binaries never enter the vault git history).

---

### Open Notebook trio (moved from ai-stack 2026-08-21, Part K.5b — NOT retiring; stays until the wiki workbench matures)

| Container | Purpose | Host port | Networks |
|-----------|---------|-----------|----------|
| `surrealdb` | Open Notebook local store (SurrealDB v2, digest-pinned) | 127.0.0.1:8003 | default (open-brain) |
| `open_notebook` | Open Notebook UI + API (IKS fork — openbrain-db is the canonical store) | 127.0.0.1:8503 / :5055 | default, obnet, ai-stack_llm-net + app-net (external) |
| `open-notebook-backup` | SurrealDB logical export + notebook_data tar (output still `ai-stack/backups/open-notebook`) | — | default (open-brain) |


## 3. agent-org — compose project `agent-org` (SEPARATE)

File: `agent-org/docker/docker-compose.yml`. Run with:
`docker compose -f agent-org/docker/docker-compose.yml ...`. `.env` lives next to the file at
`agent-org/docker/.env` (template: `.env.example`). Design corpus:
`documentation/implementation-guide/teams-chat-agent-orchestration/`.

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
| `ao-worker-1-journals-backup` / `ao-worker-2-journals-backup` | Nightly tar of each worker's append-only task journals → `./backups/ao-worker-{1,2}-journals/` (generic `backup/generic-tar-backup.sh`). One sidecar per volume so each archive restores 1:1; profile-gated with the workers | — | none (volume-only) | workers |
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
| `scripts/recovery/emergency-recovery.ps1 (the .bat twin was archived 2026-08-21)` | Legacy linear equivalent (PowerShell version preferred) |
| `scripts/archive/emergency-recovery-module/` | ARCHIVED 2026-08-20 (was OWUI-reachable stale guidance; recovery keywords now route to help-system) |

The recovery scripts hold a service inventory (`MainStackServices` — now incl. the
backup sidecars, `OB1Services`, `AgentOrgServices`) plus a `$MainBackups`
helper group (the openbrain-db/wiki backups live in the OB1 project since
2026-08-21 and start with it). agent-org is driven as a
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

Bottom-up (start in this order; stop in reverse):

1. `openwebui` (provides the network namespace for `tailscale`)
2. **the `inference` project** (`docker compose -f inference/docker-compose.yml
    --env-file .env up -d`) — its internal depends_on runs upstreams →
    `llm-queue` → `llm-gateway-db` → `llm-gateway` (+ ui/backups); one command,
    ordered + health-gated. Needs the anchor networks (any root `up` creates
    them), and every caller in every other project needs IT
3. `tailscale`
4. `mnemory` → `mnemory-cloud-gateway` → `mnemory-backup`
5. `openwebui-backup`
6. `surrealdb` → `open_notebook`
7. Search: `vpn` → `redis` → `searxng` → `gateway`
8. Coder: `open-terminal` → `little-coder` → `lc-egress`
9. **Backup sidecars** — each starts after its target is healthy; idle cron otherwise
    (`mnemory-backup`, `openwebui-backup`, `little-coder-backup`,
    `tailscale-backup`, `lm-models-backup`, `llm-gateway-backup`,
    `open-notebook-backup`; the openbrain-db/wiki backups belong to the OB1
    project and come up with it).
11. **OB1** (`docker compose -f OB1/docker/docker-compose.yml up -d`) — after `llm-gateway`.
11.5. **agent-org** (`docker compose -f agent-org/docker/docker-compose.yml up -d`) — after OB1
    (downstream of it: attaches to `ai-stack_llm-net`, optionally mirrors audit to OB1's
    gateway). Default plane only; `workers`/`cloud` profiles are operator-driven. Stop it
    first (before OB1) on the way down.
12. **Portal** (profile-gated, **separate lifecycle**): `scripts/portal/portal-on.ps1` →
    `portal-init` → `authelia` → `caddy` → `cloudflared`, plus `portal-alerter` and the
    watchers/tripwire/cron + `caddy-backup`/`authelia-backup`. Not part of the default `up`;
    tear down with `portal-off.ps1`.
