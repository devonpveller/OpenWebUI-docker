# Workspace Stack Map

Authoritative inventory of the Docker stacks in this `ai-stack` workspace.
Cross-check against the live compose files before relying on it — the files
are the source of truth; this doc is the curated summary.

Source files:
- `docker-compose.yml` + `docker-compose.override.yml` — the **main** project
- `OB1/docker/docker-compose.yml` — the **open-brain** project (separate)

---

## 1. Main stack — compose project `ai-stack`

Files: `docker-compose.yml`, `docker-compose.override.yml`.
Run with: `docker compose ...` from the workspace root.

### Networks
| Network      | Type            | Purpose |
|--------------|-----------------|---------|
| `llm-net`    | internal (no internet) | llama-cpp inference reachable only by peers on this net |
| `search-net` | internal (no internet) | search gateway isolation — only `tor` bridges out |
| `lc-net`     | internal (no internet) | little-coder control plane isolation |
| `default`    | bridge          | host-reachable / internet egress |

### Planes & containers

**Core**
| Container | Role | Host port | Networks | GPU |
|-----------|------|-----------|----------|-----|
| `openwebui` | Open WebUI chat surface | 127.0.0.1:3000 | default, llm-net | yes (aistack) |
| `tailscale` | Tailnet VPN; shares openwebui netns | — (`network_mode: service:openwebui`) | — | no |
| `llama-cpp` | llama-swap LLM inference | 127.0.0.1:8081 | llm-net | yes (device 0) |
| `llama-cpp-embed` | bge-m3 embeddings server | 127.0.0.1:8082 | llm-net | yes |
| `watchtower` | container auto-update monitor | — | default | no |

**Memory (mnemory)**
| Container | Role | Host port | Networks |
|-----------|------|-----------|----------|
| `mnemory` | Unified memory layer (mgmt :8051) | — (internal only) | llm-net |
| `mnemory-gateway` | Privacy-enforcing MCP proxy for cloud clients | 127.0.0.1:8060 | llm-net, default |
| `mnemory-backup` | Nightly backup cron sidecar | — | — |

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
| `open-terminal` | Workspace plane — executes agent commands | — | lc-net, llm-net |
| `little-coder` | Control daemon — decides (daemon :8090) | 127.0.0.1:9091 (metrics) | lc-net, llm-net |
| `lc-mcpo` | MCP→OpenAPI edge (task triggers) | 127.0.0.1:8002 | lc-net, llm-net |
| `lc-egress` | Egress allowlist proxy (git host only) | — | lc-net, default |
| `little-coder-backup` | Nightly backup of the 4 expertise volumes | — | — |

**Aux**
| Container | Role | Host port | Networks |
|-----------|------|-----------|----------|
| `smolcrawl-pipelines` | Deep-research crawl pipelines | 127.0.0.1:9099 | default |
| `surrealdb` | Open Notebook database | 8003 | default |
| `open_notebook` | Open Notebook UI + API | 8503, 5055 | default, llm-net |
| `openwebui-backup` | Nightly backup cron sidecar | — | — |

### Volumes
`openwebui-data`, `mnemory-data`, `smolcrawl-data`,
`little-coder-journals`, `little-coder-skill`, `little-coder-cohorts`,
`little-coder-polyglot`, `little-coder-workspace`.

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
| `openbrain-curator` | Research-package ingestion inlet (`POST /ingest/research-package`); resolves deep-research onto the best existing thread (pgvector shortlist + LLM decision), delegates the write to openbrain-mcp `/research/persist`; deno-postgres + llama-cpp + llama-cpp-embed | 127.0.0.1:8816 | obnet, llm-net |
| `openbrain-wiki` | Wiki compiler + scheduler | 127.0.0.1:8811 | obnet, llm-net |
| `openbrain-wiki-viewer` | Quartz 4 read-only wiki viewer (also tailnet HTTPS `:8444` + Caddy `wiki.${PUBLIC_DOMAIN}`) | 127.0.0.1:8812 | obnet, app-net |
| `openbrain-workbench` | Deno+Hono read/write API behind the viewer (`/workbench/*` via portal Caddy `handle`, X-Brain-Key injected); deno-postgres writes + PostgREST reads | 127.0.0.1:8814 (debug only) | obnet, llm-net, app-net |
| `openbrain-extract` | FastAPI content-extraction sidecar (`POST /extract`: PDF/DOCX/PPTX/image-OCR/audio-STT registry); sandboxed (non-root, cap_drop, read-only FS); reaches host STT via `host.docker.internal` | 127.0.0.1:8815 (debug only) | obnet |
| `openbrain-cron` | supercronic + curl; fires HTTP-trigger chain (no docker.sock) | — (internal only) | obnet |
| `openbrain-gmail-pull` | HTTP-triggered Gmail ingest; chains to prune on success | — (internal only) | obnet, llm-net |
| `openbrain-gmail-prune` | HTTP-triggered short-term prune; chains to digest + wiki recompile | — (internal only) | obnet, llm-net |
| `openbrain-digest` | HTTP-triggered daily digest; mechanical formatting, Gmail send | — (internal only) | obnet |

**Scheduled-job slice:** the four trailing services (`openbrain-cron` + the
three HTTP-triggered jobs) live in [`OB1/docker/docker-compose.scheduled.yml`](../../../OB1/docker/docker-compose.scheduled.yml),
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

The recovery scripts hold a service inventory (`MainStackServices`,
`OB1Services`). **When you add a container to either compose file, add it to
that inventory and to the shutdown/startup sequences** so the recovery stack
stays complete.

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
8. Coder: `open-terminal` → `little-coder` → `lc-mcpo` / `lc-egress` / `little-coder-backup`
9. `watchtower`
10. **OB1** (`docker compose -f OB1/docker/docker-compose.yml up -d`) — last
