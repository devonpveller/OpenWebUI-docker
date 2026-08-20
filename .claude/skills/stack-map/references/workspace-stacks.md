# Workspace Stack Map

Authoritative inventory of the Docker stacks in this `ai-stack` workspace.
Cross-check against the live compose files before relying on it — the files
are the source of truth; this doc is the curated summary.

**Last reconciled against live compose: 2026-08-20** — CLEANUP-PLAN v3 execution
day: the root compose is now a thin include of `compose/<plane>.yml` files
(rendered model proven identical); **retired**: `watchtower`, `search-mcpo`,
`lc-mcpo` (main = **31 default + 12 portal services**); the status-pipe
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
- `docker-compose.yml` + `docker-compose.override.yml` — the **main** project
- `docker-compose.local-test.override.yml` — portal `local-test` profile (no Cloudflare)
- `OB1/docker/docker-compose.yml` (+ `docker-compose.scheduled.yml`) — the **open-brain** project (separate)
- `agent-org/docker/docker-compose.yml` — the **agent-org** project (separate; teams-chat orchestration)

---

## 1. Main stack — compose project `ai-stack`

Files: `docker-compose.yml`, `docker-compose.override.yml` (+ `docker-compose.local-test.override.yml`).
Run with: `docker compose ...` from the workspace root.

> **Profiles:** the **Portal** plane below is gated behind `profiles: [internet]`
> (or `[internet, local-test]`) and does **NOT** start with a plain `docker compose up -d` —
> it's driven by `scripts/portal-on.ps1` / `portal-off.ps1`. Everything else starts by default.

### Networks
| Network      | Type            | Purpose |
|--------------|-----------------|---------|
| `llm-net`    | internal (no internet) | **caller plane**: every inference consumer sits here and reaches inference ONLY via the `llama-cpp` / `llama-cpp-embed` aliases on **`llm-gateway`** (LiteLLM). The `*-upstream` real servers are NOT here (isolated on `llm-backend-net`, 2026-06-13) so callers cannot route around LiteLLM |
| `llm-backend-net` | internal (no internet) | **backend plane**: the `*-upstream` real inference servers + the sole ingress `llm-gateway` + the `llm-queue` admission controller (downstream of the gateway) + the `lm-models-backup` liveness probe. Nothing else attaches → inference is reachable only through the gateway. Enforced by `scripts/check-llm-gateway-routing.ps1` |
| `search-net` | internal (no internet) | search gateway isolation — only `vpn` (search egress) + `tor` (fetch egress) bridge out |
| `lc-net`     | internal (no internet) | little-coder control plane isolation |
| `auth-net`   | bridge, **internal** | portal: caddy ↔ authelia ↔ portal-alerter ↔ watchers (no internet) |
| `app-net`    | bridge          | caddy ↔ openwebui / open_notebook (backends reached only via caddy) |
| `edge-net`   | bridge          | portal ingress: cloudflared ↔ caddy |
| `notify-net` | bridge          | portal egress chokepoint (portal-alerter → Gmail; portal-cron) |
| `default`    | bridge          | host-reachable / internet egress |
| `obnet`      | external (`open-brain_obnet`) | so `openbrain-db-backup` can reach OB1's Postgres |

### Planes & containers

**Core**
| Container | Role | Host port | Networks | GPU |
|-----------|------|-----------|----------|-----|
| `openwebui` | Open WebUI chat surface | 127.0.0.1:3000 | default, llm-net, app-net | yes |
| `tailscale` | Tailnet VPN; shares openwebui netns; serves ON :8443/:5055 + wiki :8444 (via caddy:8446) + LiteLLM Admin UI :8445 (→ `llm-gateway-ui`) | — (`network_mode: service:openwebui`) | — | no |
| `llm-gateway` | **LiteLLM analytics front door** (holds the `llama-cpp` + `llama-cpp-embed` network aliases on :8080; all callers reach inference through it). Routes `/v1/*` by model name; **both chat AND embed** forward to **`llm-queue`** (api_base, since B2/P4); `num_retries:3` (a queue 429 → retry → hold-and-dispatch); read-only `/observe/*` pass-through to `llm-queue` for the live board; permissive (no master_key) per-caller spend ledger; `background_health_checks:false` (a model health-probe forces a llama-swap load → thrash) | — (internal-only; admin/ledger via `docker exec`, not host :4000 — `llm-net` is `internal:true` so host publish is inert) | llm-net, llm-backend-net (sole bridge) | no |
| `llm-queue` | **B2 front-ended inference admission controller** (`llm-queue/`, design `DESIGN-B2-inference-queue.md`). Sits between LiteLLM and the `*-upstream` servers (chat + embed): holds-and-dispatches (release-on-completion semaphore, priority heap w/ per-key caps, rolling-T wait estimate, per-model depth backstop — chat 24, embed 256) instead of llama-swap dropping overflow with a flat `429`. Replaces the bare `Too many requests` with a structured 429 + `Retry-After`; `enforce_budget:true` (per-service wait budgets §8b). Read-only state reachable from `llm-net` via the gateway's `/observe/*` pass-through; the **mutating** control API (`POST /queue/{id}/priority`/`cancel`, `/keys/{key}/policy`) is operator-only (`docker exec`, never `llm-net`). Analytics events → own SQLite (`llm-queue-data` volume). Tuning invariant: `LLM_QUEUE_SLOTS` == llama-swap `--parallel` (3) and llama-swap `concurrencyLimit: 0` | — (internal-only) | llm-backend-net | no |
| `llm-gateway-ui` | **LiteLLM Admin-UI sidecar** (analytics dashboard at `/ui`, added 2026-06-14). A SECOND LiteLLM instance run **with** a `master_key` (`config/litellm.ui.config.yaml` + `.env` `LITELLM_UI_*`) — which LiteLLM 1.88.1 requires for the UI to log in. Serves **no inference** (carries NO `llama-cpp` alias, no caller points at it), shares `llm-gateway-db` so the dashboard reads the SAME spend ledger `llm-gateway` writes. The master_key is isolated here so the permissive main gateway + its junk-key callers stay untouched. Reached only via the tailnet **:8445** serve route (`entrypoint.sh`) | — (internal-only; tailnet :8445/ui) | llm-net | no |
| `llm-gateway-db` | Postgres for the LiteLLM spend-log ledger (`llm-gateway-db-data` volume) — shared by `llm-gateway` (writes) and `llm-gateway-ui` (reads) | — | llm-net | no |
| `llama-cpp-upstream` | llama-swap inference (was `llama-cpp`) — `qwen36-27b` (∥2); 35B is in llama-swap config but **not registered in the gateway**; one model resident at a time; `--no-mmap` (mmap over the C: bind mount hangs) | 127.0.0.1:8081 | llm-backend-net (isolated) | yes (device 0) |
| `llama-cpp-embed-upstream` | bge-m3 embeddings server (was `llama-cpp-embed`) | 127.0.0.1:8082 | llm-backend-net (isolated) | yes (device 1) |

**Memory (mnemory)**
| Container | Role | Host port | Networks |
|-----------|------|-----------|----------|
| `mnemory` | Unified memory layer (mgmt :8051) | — (internal only) | llm-net |
| `mnemory-gateway` | Privacy-enforcing MCP proxy for cloud clients | 127.0.0.1:8060 | llm-net, default |

**Search (Private Search Gateway — SearXNG engine queries over Mullvad WireGuard; page-fetch over Tor)**
| Container | Compose service | Role | Host port | Networks |
|-----------|-----------------|------|-----------|----------|
| `search-vpn` | `vpn` | Mullvad WireGuard (gluetun) — SearXNG's engine-query egress + kill-switch | — | search-net, default |
| `search-tor` | `tor` | Tor egress — page-FETCH leg (openbrain-research/digest/podcast `FETCH_PROXY_URL`); bridges out | — | search-net, default |
| `search-redis` | `redis` | SearXNG cache | — | search-net |
| `searxng` | `searxng` | Metasearch engine | — | search-net |
| `search-gateway` | `gateway` | REST / Tavily-shim API | 127.0.0.1:8085 | search-net, default |

**Coder (little-coder control plane)**
| Container | Role | Host port | Networks |
|-----------|------|-----------|----------|
| `open-terminal` | Workspace plane — executes agent commands (egress via `lc-egress`) | — | lc-net, llm-net |
| `little-coder` | Control daemon — decides (daemon :8090) | 127.0.0.1:9091 (metrics) | lc-net, llm-net |
| `lc-egress` | Egress allowlist proxy (git host only) | — | lc-net, default |

**Aux**
| Container | Role | Host port | Networks |
|-----------|------|-----------|----------|
| `smolcrawl-pipelines` | Deep-research crawl pipelines | 127.0.0.1:9099 | default |
| `surrealdb` | Open Notebook database (SurrealDB v2) | 127.0.0.1:8003 | default |
| `open_notebook` | Open Notebook UI + API (IKS fork → OB1 Postgres) | 127.0.0.1:8503 / :5055 | default, llm-net, app-net, obnet |

**Backups (unified snapshot sidecars — `backup/` scripts, nightly cron; NAS-synced)**
| Container | Backs up | Networks | Profile |
|-----------|----------|----------|---------|
| `mnemory-backup` | mnemory-data | — | default |
| `openwebui-backup` | openwebui-data (mem-capped 1g) | — | default |
| `little-coder-backup` | the little-coder expertise volumes | — | default |
| `llm-gateway-backup` | nightly `pg_dump` of the LiteLLM spend ledger (`llm-gateway-db`) | llm-net | default |
| `openbrain-db-backup` | `pg_dump` of OB1 Postgres | obnet (external) | default |
| `openbrain-wiki-backup` | openbrain-wiki-data + wiki-assets | — | default |
| `agent-bridge-db-backup` | `pg_dump` of `agent-bridge-db` (**agent-org** project; governance/effort/project state) | ao-net | default (agent-org) |
| `mattermost-db-backup` | `pg_dump` of `mattermost-db` (**agent-org** project; conversation content) | ao-net | default (agent-org) |
| `open-notebook-backup` | SurrealDB logical export + notebook_data | default | default |
| `smolcrawl-backup` | smolcrawl-data | — | default |
| `tailscale-backup` | tailscale state dir | — | default |
| `lm-models-backup` | LM Studio models (**WEEKLY**; disableable) | — | llm-backend-net (HEALTH_TCP liveness probe to `llama-cpp-upstream`) |
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
`caddy-data`, `caddy-config`, `authelia-data`, `tripwire-data`, `llm-gateway-db-data`,
`llm-queue-data` (llm-queue's own analytics event store — SQLite, NOT LiteLLM's schema).
**External** (owned by the open-brain project): `openbrain-wiki-data`
(= `open-brain_openbrain-wiki-data`), `wiki-assets` (= `open-brain_wiki-assets`).

---

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
Mirrors the mnemory-gateway pattern (`mnemory-gateway/app.py`). Local
trusted clients (OWUI via the mcpo bridges, recipes on obnet, the
entity worker, the wiki compiler) keep talking to `openbrain-mcp` /
`openbrain-ext` directly on internal networks and are unaffected.

### Volumes
`openbrain-db-data`, `openbrain-wiki-data`, `wiki-assets` (binary assets —
images now, audio later — written by `openbrain-workbench`, served read-only by
`openbrain-wiki-viewer`; deliberately NOT mounted into `openbrain-wiki` so
binaries never enter the vault git history).

---

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
| `ao-worker-1` / `ao-worker-2` | Pooled `little-coder` control daemons (reuse `little-coder:local`) | — | ao-worker-net, llm-net | workers |
| `ao-ot-1` / `ao-ot-2` | Per-worker `open-terminal` workspace planes (reuse `little-coder-open-terminal:local`) | — | ao-worker-net, llm-net | workers |
| `ao-git-egress` | Shared git-allowlist egress for the worker pool (mirrors `lc-egress`); allowlist is the **bridge-written** `ao-egress-config` file, reloaded on change (custom `docker/egress/tinyproxy.conf` + `egress-reload.sh` command override) so the org can work on any onboarded repo | — | ao-worker-net, default | workers |
| `llm-gateway-cloud` | **CONDITIONAL** separate LiteLLM for OpenRouter (master_key + per-role budgets); the only egress, via `ao-egress` | — | ao-net, ao-cloud-egress-net | cloud |
| `llm-gateway-cloud-db` | Postgres for the cloud LiteLLM spend ledger | — | ao-net | cloud |
| `ao-egress` | Allowlist egress proxy pinned to `openrouter.ai` (mirrors `lc-egress`); the ONLY agent-org internet path | — | ao-cloud-egress-net, default | cloud |

### Volumes
`mattermost-db-data`, `mattermost-data`, `mattermost-config`, `mattermost-logs`,
`mattermost-plugins`, `mattermost-client-plugins`, `agent-bridge-db-data`,
`ao-worker-1-workspace`, `ao-worker-1-sessions`, `ao-worker-2-workspace`,
`ao-worker-2-sessions`, `ao-egress-config` (bridge-written git-egress allowlist, shared with
`ao-git-egress`), `llm-gateway-cloud-db-data`.

---

## 4. Recovery stack

The **recovery stack** keeps every container above runnable after a crash,
update, or network-namespace break.

| File | Role |
|------|------|
| `scripts/emergency-recovery.ps1` | Primary recovery — `recover` / `nuclear` / `gpu-reset`; 5-phase ordered restart that also drives the OB1 project |
| `scripts/emergency-recovery.bat` | Legacy linear equivalent (PowerShell version preferred) |
| `scripts/archive/emergency-recovery-module/` | ARCHIVED 2026-08-20 (was OWUI-reachable stale guidance; recovery keywords now route to help-system) |

The recovery scripts hold a service inventory (`MainStackServices` — now incl. the
backup sidecars, `OB1Services`, `AgentOrgServices`) plus `$MainBackups` / `$OB1Backups`
helper groups (OB1-attached backups start only after OB1 is up). agent-org is driven as a
separate project (`Start-/Stop-/Reset-AgentOrgStack`, `$AgentOrgCompose`), stopped first and
started last (downstream of OB1). **When you add a container to any of the three compose
files, add it to that inventory and to the shutdown/startup sequences** so the recovery stack
stays complete.

**The Portal plane is deliberately excluded** from recovery: it is profile-gated
(`profiles: [internet]`) and managed by `scripts/portal-on.ps1` / `portal-off.ps1`.
A nuclear `docker compose down` stops a running portal; recovery detects this and
**warns** rather than auto-restoring the internet front-end.

---

## Cross-stack dependency order

Bottom-up (start in this order; stop in reverse):

1. `openwebui` (provides the network namespace for `tailscale`)
2. `llama-cpp-upstream`, `llama-cpp-embed-upstream` (real inference = llama-swap → llama.cpp)
2.4. `llm-queue` (B2 admission controller — between the upstreams and LiteLLM;
    starts AFTER the `*-upstream` servers are healthy, BEFORE the gateway that
    forwards chat through it; restart-fast, no model load)
2.5. `llm-gateway-db` → `llm-gateway` (the LiteLLM front door — all callers reach
    inference through its `llama-cpp`/`llama-cpp-embed` aliases; chat forwards via
    `llm-queue`; starts AFTER `llm-queue`, BEFORE the callers).
    `llm-gateway-ui` (Admin-UI sidecar) also starts here — depends only on
    `llm-gateway-db`, serves no inference, non-critical (best-effort start)
3. `tailscale`
4. `mnemory` → `mnemory-gateway` → `mnemory-backup`
5. `openwebui-backup`, `smolcrawl-pipelines`
6. `surrealdb` → `open_notebook`
7. Search: `vpn` → `tor` → `redis` → `searxng` → `gateway`
8. Coder: `open-terminal` → `little-coder` → `lc-egress`
9. **Backup sidecars** — each starts after its target is healthy; idle cron otherwise
    (`mnemory-backup`, `openwebui-backup`, `little-coder-backup`, `smolcrawl-backup`,
    `tailscale-backup`, `lm-models-backup`, `llm-gateway-backup`, and — needs OB1 up —
    `openbrain-db-backup`, `openbrain-wiki-backup`, `open-notebook-backup`).
11. **OB1** (`docker compose -f OB1/docker/docker-compose.yml up -d`) — after `llm-gateway`.
11.5. **agent-org** (`docker compose -f agent-org/docker/docker-compose.yml up -d`) — after OB1
    (downstream of it: attaches to `ai-stack_llm-net`, optionally mirrors audit to OB1's
    gateway). Default plane only; `workers`/`cloud` profiles are operator-driven. Stop it
    first (before OB1) on the way down.
12. **Portal** (profile-gated, **separate lifecycle**): `scripts/portal-on.ps1` →
    `portal-init` → `authelia` → `caddy` → `cloudflared`, plus `portal-alerter` and the
    watchers/tripwire/cron + `caddy-backup`/`authelia-backup`. Not part of the default `up`;
    tear down with `portal-off.ps1`.
