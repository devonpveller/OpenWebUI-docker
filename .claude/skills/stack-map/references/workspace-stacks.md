# Workspace Stack Map

Authoritative inventory of the Docker stacks in this `ai-stack` workspace.
Cross-check against the live compose files before relying on it — the files
are the source of truth; this doc is the curated summary.

**Last reconciled against live compose: 2026-06-11** — added the portal/auth slice
(Authelia/Caddy/Cloudflared + watchers/tripwire), the unified-backup sidecars, the
`qwen36-35b-a3b` model, and the portal networks (`edge/auth/app/notify-net`).

Source files:
- `docker-compose.yml` + `docker-compose.override.yml` — the **main** project
- `docker-compose.local-test.override.yml` — portal `local-test` profile (no Cloudflare)
- `OB1/docker/docker-compose.yml` (+ `docker-compose.scheduled.yml`) — the **open-brain** project (separate)

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
| `llm-net`    | internal (no internet) | llama-cpp inference reachable only by peers on this net |
| `search-net` | internal (no internet) | search gateway isolation — only `tor` bridges out |
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
| `tailscale` | Tailnet VPN; shares openwebui netns; serves ON :8443/:5055 + wiki :8444 (via caddy:8446) | — (`network_mode: service:openwebui`) | — | no |
| `llama-cpp` | llama-swap inference — `qwen36-27b` (∥2) **+** `qwen36-35b-a3b` (∥1); **one model resident at a time** (swap thrash avoided by pinning same model) | 127.0.0.1:8081 | llm-net | yes (device 0) |
| `llama-cpp-embed` | bge-m3 embeddings server | 127.0.0.1:8082 | llm-net | yes (device 1) |
| `watchtower` | container auto-update monitor | — | default | no |

**Memory (mnemory)**
| Container | Role | Host port | Networks |
|-----------|------|-----------|----------|
| `mnemory` | Unified memory layer (mgmt :8051) | — (internal only) | llm-net |
| `mnemory-gateway` | Privacy-enforcing MCP proxy for cloud clients | 127.0.0.1:8060 | llm-net, default |

**Search (Private Search Gateway — SearXNG over Tor)**
| Container | Compose service | Role | Host port | Networks |
|-----------|-----------------|------|-----------|----------|
| `search-tor` | `tor` | Tor egress — only service bridging out | — | search-net, default |
| `search-redis` | `redis` | SearXNG cache | — | search-net |
| `searxng` | `searxng` | Metasearch engine | — | search-net |
| `search-gateway` | `gateway` | REST / Tavily-shim API | 127.0.0.1:8085 | search-net, default |
| `search-mcpo` | `mcpo` | MCP-as-OpenAPI bridge | 127.0.0.1:8001 | search-net |

**Coder (little-coder control plane)**
| Container | Role | Host port | Networks |
|-----------|------|-----------|----------|
| `open-terminal` | Workspace plane — executes agent commands (egress via `lc-egress`) | — | lc-net, llm-net |
| `little-coder` | Control daemon — decides (daemon :8090) | 127.0.0.1:9091 (metrics) | lc-net, llm-net |
| `lc-mcpo` | MCP→OpenAPI edge (task triggers) | 127.0.0.1:8002 | lc-net, llm-net |
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
| `openbrain-db-backup` | `pg_dump` of OB1 Postgres | obnet (external) | default |
| `openbrain-wiki-backup` | openbrain-wiki-data + wiki-assets | — | default |
| `open-notebook-backup` | SurrealDB logical export + notebook_data | default | default |
| `smolcrawl-backup` | smolcrawl-data | — | default |
| `tailscale-backup` | tailscale state dir | — | default |
| `lm-models-backup` | LM Studio models (**WEEKLY**; disableable) | — | default |
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
**External** (owned by the open-brain project): `openbrain-wiki-data`
(= `open-brain_openbrain-wiki-data`), `wiki-assets` (= `open-brain_wiki-assets`).

---

## 2. Open Brain — compose project `open-brain` (SEPARATE)

File: `OB1/docker/docker-compose.yml`.
Run with: `docker compose -f OB1/docker/docker-compose.yml ...`.
`.env` lives next to the file at `OB1/docker/.env`.

> **Why separate:** OB1 is its own compose project (`name: open-brain`). It
> attaches to the main stack's `ai-stack_llm-net` as an **external** network,
> so it depends on the main stack being up. Bring OB1 up *after* `llama-cpp`
> and `llama-cpp-embed` are healthy; tear it down *before* the main stack so
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

**Scheduled-job slice:** the five trailing services (`openbrain-cron` + the
four HTTP-triggered jobs) live in [`OB1/docker/docker-compose.scheduled.yml`](../../../OB1/docker/docker-compose.scheduled.yml),
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

## 3. Recovery stack

The **recovery stack** keeps every container above runnable after a crash,
update, or network-namespace break.

| File | Role |
|------|------|
| `scripts/emergency-recovery.ps1` | Primary recovery — `recover` / `nuclear` / `gpu-reset`; 5-phase ordered restart that also drives the OB1 project |
| `scripts/emergency-recovery.bat` | Legacy linear equivalent (PowerShell version preferred) |
| `modules/emergency-recovery/` | OWUI guidance module — **stale**: its startup config still names the disabled `ollama` container |

The recovery scripts hold a service inventory (`MainStackServices` — now incl. the
backup sidecars, `OB1Services`) plus `$MainBackups` / `$OB1Backups` helper groups
(OB1-attached backups start only after OB1 is up). **When you add a container to
either compose file, add it to that inventory and to the shutdown/startup
sequences** so the recovery stack stays complete.

**The Portal plane is deliberately excluded** from recovery: it is profile-gated
(`profiles: [internet]`) and managed by `scripts/portal-on.ps1` / `portal-off.ps1`.
A nuclear `docker compose down` stops a running portal; recovery detects this and
**warns** rather than auto-restoring the internet front-end.

---

## Cross-stack dependency order

Bottom-up (start in this order; stop in reverse):

1. `openwebui` (provides the network namespace for `tailscale`)
2. `llama-cpp`, `llama-cpp-embed` (inference — consumed by mnemory, coder, OB1)
3. `tailscale`
4. `mnemory` → `mnemory-gateway` → `mnemory-backup`
5. `openwebui-backup`, `smolcrawl-pipelines`
6. `surrealdb` → `open_notebook`
7. Search: `tor` → `redis` → `searxng` → `gateway` → `mcpo`
8. Coder: `open-terminal` → `little-coder` → `lc-mcpo` / `lc-egress`
9. `watchtower`
10. **Backup sidecars** — each starts after its target is healthy; idle cron otherwise
    (`mnemory-backup`, `openwebui-backup`, `little-coder-backup`, `smolcrawl-backup`,
    `tailscale-backup`, `lm-models-backup`, and — needs OB1 up — `openbrain-db-backup`,
    `openbrain-wiki-backup`, `open-notebook-backup`).
11. **OB1** (`docker compose -f OB1/docker/docker-compose.yml up -d`) — after `llama-cpp`.
12. **Portal** (profile-gated, **separate lifecycle**): `scripts/portal-on.ps1` →
    `portal-init` → `authelia` → `caddy` → `cloudflared`, plus `portal-alerter` and the
    watchers/tripwire/cron + `caddy-backup`/`authelia-backup`. Not part of the default `up`;
    tear down with `portal-off.ps1`.
