# Guide — LiteLLM Proxy for the ai-stack inference plane

**Status:** Source of truth (design + audit). Plan and task documents will be
generated from this file later — keep it authoritative.

**Last verified against the live stack:** 2026-05-23.

---

## 1. Purpose

Today, every chat and embedding call inside the workspace hits `llama-cpp` or
`llama-cpp-embed` directly by container DNS. The inference servers log requests
to stdout but with **no client attribution** — only source IP and user-agent.
That makes three things hard:

1. **Live attribution** — "which container is currently driving GPU 0?" requires
   manual `docker logs` + `docker network inspect` correlation (the workflow
   exercised in the conversation that motivated this guide).
2. **Historical demand analysis** — log retention is bounded by docker's rolling
   window; once rotated, nothing remains.
3. **Per-service cost / throughput / latency views** — there is no SQL-able
   request ledger to slice by caller, model, time window, prompt size, etc.

This guide specifies a **LiteLLM Proxy** layer that sits between every caller
and the two inference servers, persists a structured request log to Postgres,
and exposes per-caller observability surfaces — both via LiteLLM's own REST
admin API and a new module in `scripts/ai_pipes/unified_openwebui_pipe.py`.

The pattern (gateway-in-front-of-OpenAI-compatible-backend) is the industry
standard for self-hosted LLM observability and matches the same architectural
shape already used elsewhere in this workspace (`mnemory-gateway`,
`search-gateway`, `mcpo`, `lc-mcpo`).

## 2. Industry-standard pattern alignment

For "OpenAI-compatible inference + per-caller attribution + persistent
telemetry," the dominant self-hosted choice is **LiteLLM Proxy**:

| Concern | LiteLLM provides |
|---|---|
| Drop-in compatibility | Accepts OpenAI `/v1/chat/completions`, `/v1/embeddings`, `/v1/models` — callers change a base URL, nothing else |
| Per-caller attribution | Virtual API keys (`sk-...`) per service, each with name + metadata; appears in every log row |
| Persistent ledger | Postgres backend with `LiteLLM_SpendLogs` table — model, tokens in/out, latency, key, cost, metadata, timestamp |
| Admin REST API | `/spend/logs`, `/spend/calculate`, `/key/info`, `/model/info`, `/health` — directly consumable by the OWUI pipe |
| Prometheus metrics | Native Prometheus exporter for dashboards (phase 2) |
| Multi-model routing | Supports the `qwen36-27b` / `qwen36-27b:nothink` / `bge-m3` model-id split llama-swap already exposes |

Alternatives considered and rejected for this stack:

- **Custom log-parsing sidecar** — reinvents 80% of LiteLLM, no community,
  fragile against llama.cpp log-format changes.
- **Helicone / Portkey** — SaaS-first; runs counter to the workspace's
  no-cloud-API-keys posture (OB1 CLAUDE.md guard rail; mnemory privacy model).
- **Langfuse** — observability platform, but expects to be *fed* by a gateway
  or SDK rather than acting as one. Can be added downstream of LiteLLM later
  if richer trace UX is wanted.
- **Fluent Bit / Vector / Loki shipping raw access logs** — solid generic
  pattern, but loses structured fields at the source and requires downstream
  parsing of freeform log lines.
- **OpenTelemetry GenAI semconv** — emerging, but llama.cpp does not emit
  OTel natively; we would still need an instrumentation layer in front of it.

LiteLLM is the simplest path that satisfies the stated goal (rich statistical
tracking + reflection later) without bolting on a custom pipeline.

## 3. Locked design decisions

| # | Decision | Rationale |
|---|---|---|
| D1 | Embeddings traffic goes **through** LiteLLM (not bypassed) | The motivating workload is `openbrain-entity-worker`, whose embedding calls are a large fraction of its GPU demand. Excluding them would leave the demand-over-time view incomplete. LiteLLM proxies `/v1/embeddings` natively. |
| D2 | Dedicated `llm-gateway-db` Postgres container — **not** shared with `openbrain-db` | `open-brain` is a separate compose project (per workspace CLAUDE.md). Sharing the DB would couple OB1's lifecycle to the gateway and cross the project boundary the workspace deliberately maintains. |
| D3 | Per-service **virtual API keys** issued by LiteLLM (not a single shared key with header attribution) | Idiomatic LiteLLM; survives caller restarts and IP changes; gives per-caller `/spend/logs` slicing out of the box; supports per-caller rate limits / budgets later. |
| D4 | New service named **`llm-gateway`** on the existing `ai-stack_llm-net` | Consistent with `mnemory-gateway`, `search-gateway`. Same internal-network membership as `llama-cpp` so callers reach it by container DNS. |
| D5 | **Per-caller cutover**, not big-bang | Each caller can revert independently if behavior regresses. Order is chosen to start with lowest-risk consumers. |
| D6 | OWUI's chat + embedding wiring is **re-pointed via OWUI Admin → Connections** (UI step, persisted in OWUI's database), not via compose env | OWUI's compose file does not currently set `OPENAI_API_BASE_URLS`; the configuration lives in OWUI's data volume. This is a documented operator action in the cutover, not a code change. |
| D7 | Prometheus + Grafana dashboards are **phase 2** | The LiteLLM REST API + pipe module covers the immediate need ("who is using the GPU and how much"). Dashboards add value but also infrastructure cost; defer until the request ledger has accumulated something worth charting. |
| D8 | llama-swap stays in place behind LiteLLM | llama-swap is a model-swap router, not a request gateway. The hop chain `caller → LiteLLM → llama-swap → llama-server` adds ~5–10 ms total and preserves the model-swap behavior callers already rely on. |

## 4. Caller inventory — complete audit

Two categories: **direct** callers (hit `llama-cpp` / `llama-cpp-embed` over
`llm-net`) and **indirect** callers (reach the inference plane only through
another stack component, typically OWUI).

### 4.1 Direct chat callers — `http://llama-cpp:8080/v1`

| # | Container | Where configured | Variable / field | Model id in flight |
|---|---|---|---|---|
| C1 | `mnemory` | `docker-compose.yml` (main, env) | `LLM_BASE_URL` | `qwen36-27b:nothink` |
| C2 | `openbrain-mcp` | `OB1/docker/docker-compose.yml` (env) | `CHAT_API_BASE` | `qwen36-27b:nothink` |
| C3 | `openbrain-entity-worker` | `OB1/docker/docker-compose.yml` (env) | `CHAT_API_BASE` | `qwen36-27b:nothink` |
| C4 | `openbrain-wiki` | `OB1/docker/docker-compose.yml` (env) | `LLM_BASE_URL` | `qwen36-27b:nothink` |
| C5 | `little-coder` | `little-coder/config/little-coder.config.yaml` + `little-coder/config/models.json` | `inference.base_url`, `models[].baseUrl` | `qwen36-27b` / `qwen36-27b:nothink` |

> `open-terminal` declares a `depends_on: llama-cpp` and lists `llama-cpp` in
> its `NO_PROXY`, but the agent process inside `open-terminal` is invoked by
> `little-coder`, which is the actual API client. `open-terminal` itself does
> **not** call llama-cpp directly. No change required there.

### 4.2 Direct embedding callers — `http://llama-cpp-embed:8080/v1`

| # | Container | Where configured | Variable / field | Model id |
|---|---|---|---|---|
| E1 | `mnemory` | `docker-compose.yml` (main, env) | `EMBED_BASE_URL`, `EMBED_MODEL` | `qllama/bge-m3:latest` (1024-dim) |
| E2 | `openbrain-mcp` | `OB1/docker/docker-compose.yml` (env) | `EMBEDDING_API_BASE`, `EMBEDDING_MODEL` | `bge-m3` |
| E3 | `openbrain-entity-worker` | `OB1/docker/docker-compose.yml` (env) | `EMBEDDING_API_BASE`, `EMBEDDING_MODEL` | `bge-m3` |
| E4 | `openbrain-wiki` | `OB1/docker/docker-compose.yml` (env) | `EMBEDDING_BASE_URL`, `EMBEDDING_MODEL` | `bge-m3` |

### 4.3 Indirect callers (no direct change required)

These hit OWUI, which then hits the inference plane. Once OWUI is repointed
(I1 below), every consumer here is covered automatically.

| Caller | How it reaches inference | After OWUI cutover |
|---|---|---|
| Open WebUI itself (chat, RAG, title gen, web-search summarization) | OWUI Admin → Connections → "OpenAI" base URL (currently points at `llama-cpp`) | Repoint to `llm-gateway` (I1) |
| Open WebUI embeddings (RAG ingest, web-search ingest) | OWUI Admin → Documents → Embedding endpoint (points at `llama-cpp-embed`) | Repoint to `llm-gateway` (I2) |
| `smolcrawl-pipelines` deep_research tool | Calls back into OWUI via `owui_base_url=http://openwebui:8080` (verified in `smolcrawl/deep_research/*.py` and `smolcrawl/deep_research_tool.py`) | Inherits OWUI's new endpoint — no change |
| `open_notebook` | Configured via its own admin UI; does **not** appear in the codebase as a direct llama-cpp caller | Owner action: repoint inside open_notebook UI when convenient — independent of the cutover |
| `scripts/ai_pipes/unified_openwebui_pipe.py` and refactored modules | Help / health / status — does not perform inference | No change |

### 4.4 Observability-only references (not API callers — do **not** modify)

These mention `llama-cpp` as a string for health probes, GPU-mapping
diagnostics, or tailscale-serve registration. They must keep pointing at the
inference servers, **not** the gateway, so that probes reflect the actual
inference plane health independent of LiteLLM.

- `tailscale` env vars `LLAMA_CPP_HOST` / `LLAMA_CPP_PORT` /
  `LLAMA_CPP_EMBED_*` — register the inference servers for tailnet
  serving. Tailnet exposure of the gateway, if wanted, is a separate
  follow-up.
- `modules/system-health/`, `modules/gpu-status/`, `scripts/status_check.py`,
  `scripts/check-tailscale-health.ps1`, `scripts/gpu_check.py` —
  probes / smoke tests.
- `scripts/emergency-recovery.{ps1,bat}` — service inventory + ordered
  restart. Must be updated to **add** `llm-gateway` and `llm-gateway-db`
  to the inventory (see §10), but the existing llama-cpp references stay.
- `.claude/skills/stack-map/references/workspace-stacks.md`, this guide,
  `little-coder` design docs, etc. — documentation.

## 5. Architecture

```
                                        ┌─────────────────────┐
                                        │  llm-gateway-db     │
                                        │  (Postgres 16)      │
                                        │   spend logs,       │
                                        │   keys, budgets     │
                                        └──────────▲──────────┘
                                                   │
                                                   │ writes
                                                   │
  callers on llm-net                ┌──────────────┴──────────────┐
  ──────────────────                │       llm-gateway           │
  little-coder ─────────────────────►   (LiteLLM Proxy)           │
  mnemory ──────────────────────────►   :4000  (OpenAI-compat)    │
  openbrain-mcp ────────────────────►   :4000/v1/chat/completions │
  openbrain-entity-worker ──────────►   :4000/v1/embeddings       │
  openbrain-wiki ───────────────────►   :4000/health              │
  openwebui (after I1/I2) ──────────►   :4000/spend/logs (admin)  │
                                    └──────────┬──────────────────┘
                                               │ upstream forward
                                               │
                                  ┌────────────┴────────────┐
                                  │                         │
                          ┌───────▼─────────┐    ┌──────────▼────────────┐
                          │  llama-cpp      │    │  llama-cpp-embed      │
                          │  (llama-swap)   │    │  (bge-m3, embeddings) │
                          │  GPU 0          │    │  GPU 1                │
                          └─────────────────┘    └───────────────────────┘
```

- Both `llm-gateway` and `llm-gateway-db` live on `llm-net` (internal —
  no internet). The gateway publishes one host port (`127.0.0.1:4000`) for
  out-of-band admin access from the host.
- `llm-gateway-db` is reachable only by `llm-gateway` — no host port, no
  exposure outside `llm-net`. Backup pattern follows `mnemory-backup`.

## 6. LiteLLM configuration sketch (`config/litellm.config.yaml`)

```yaml
model_list:
  # Chat — Qwen 3.6 27B, both variants share the same upstream slot
  - model_name: qwen36-27b
    litellm_params:
      model: openai/qwen36-27b
      api_base: http://llama-cpp:8080/v1
      api_key: ${LC_LLAMA_API_KEY}     # llama-swap accepts any value
  - model_name: qwen36-27b:nothink
    litellm_params:
      model: openai/qwen36-27b:nothink
      api_base: http://llama-cpp:8080/v1
      api_key: ${LC_LLAMA_API_KEY}

  # Chat — Qwen 3.6 35B-A3B (registered for completeness)
  - model_name: qwen36-35b-a3b
    litellm_params:
      model: openai/qwen36-35b-a3b
      api_base: http://llama-cpp:8080/v1
      api_key: ${LC_LLAMA_API_KEY}

  # Embeddings — bge-m3, 1024-dim
  - model_name: bge-m3
    litellm_params:
      model: openai/bge-m3
      api_base: http://llama-cpp-embed:8080/v1
      api_key: not-needed

general_settings:
  master_key: ${LITELLM_MASTER_KEY}
  database_url: postgres://litellm:${LITELLM_DB_PASSWORD}@llm-gateway-db:5432/litellm
  store_model_in_db: true
  # Phase 1: log every successful + failed request to Postgres
  success_callback: ["postgres"]
  failure_callback: ["postgres"]
  # Phase 2 (optional): add "prometheus" once dashboards land

litellm_settings:
  drop_params: true       # tolerate caller params llama.cpp doesn't recognize
  set_verbose: false
  request_timeout: 600    # match LLAMA_CPP_TIMEOUT headroom for long completions
```

## 7. Virtual-key scheme

Generated **once** during cutover via the gateway's `/key/generate` admin
endpoint. Each key is named for the caller, gets a metadata dict for grouping,
and is injected into the caller's existing API-key env var.

| Key alias | Issued for | Replaces what value |
|---|---|---|
| `sk-lc-coder` | `little-coder` chat | `LC_LLAMA_API_KEY` (`llama`) |
| `sk-mnemory` | `mnemory` chat + embed | `LLM_API_KEY` (`ollama`) |
| `sk-ob-mcp` | `openbrain-mcp` chat + embed | `CHAT_API_KEY` / `EMBEDDING_API_KEY` (`not-needed`) |
| `sk-ob-entity` | `openbrain-entity-worker` chat + embed | `CHAT_API_KEY` / `EMBEDDING_API_KEY` (`not-needed`) |
| `sk-ob-wiki` | `openbrain-wiki` chat + embed | `LLM_API_KEY` / `EMBEDDING_API_KEY` (`not-needed`) |
| `sk-owui-chat` | OWUI's main chat connection | OWUI Admin → Connections value (currently empty / `not-needed`) |
| `sk-owui-embed` | OWUI's embedding connection | OWUI Admin → Documents → embedding key |
| `sk-admin` | Host-side admin probes / pipe-module REST queries | New |

All keys carry `metadata: {caller: "<service>", plane: "<core|memory|coder|ob1>"}`
so `/spend/logs?metadata.plane=ob1` yields a plane-scoped view.

Keys are stored in `.env` as `LITELLM_KEY_LC_CODER`, `LITELLM_KEY_MNEMORY`, etc.
(or, preferable, in the existing `secrets/` directory used by openbrain-wiki).
A one-page `documentation/LiteLLM-Proxy/key-rotation.md` is added in phase 2 —
not blocking.

## 8. Concrete change list — per caller

Each row is one git-trackable change. Variables marked **(no change)** are
listed so the diff reviewer can confirm the field stays as-is.

### 8.1 Compose / config changes

| Caller | File | Before | After |
|---|---|---|---|
| **mnemory** (C1, E1) | `docker-compose.yml` → `services.mnemory.environment` | `LLM_API_KEY=ollama` | `LLM_API_KEY=${LITELLM_KEY_MNEMORY}` |
| | | `LLM_BASE_URL=http://llama-cpp:8080/v1` | `LLM_BASE_URL=http://llm-gateway:4000/v1` |
| | | `EMBED_BASE_URL=http://llama-cpp-embed:8080/v1` | `EMBED_BASE_URL=http://llm-gateway:4000/v1` |
| | | `EMBED_MODEL=qllama/bge-m3:latest` | `EMBED_MODEL=bge-m3` (matches LiteLLM `model_name`) |
| | | `depends_on: [llama-cpp, llama-cpp-embed]` | `depends_on: [llm-gateway]` (gateway depends on the inference servers) |
| **openbrain-mcp** (C2, E2) | `OB1/docker/docker-compose.yml` → `services.openbrain-mcp.environment` | `CHAT_API_BASE=http://llama-cpp:8080/v1` | `CHAT_API_BASE=http://llm-gateway:4000/v1` |
| | | `CHAT_API_KEY=not-needed` | `CHAT_API_KEY=${LITELLM_KEY_OB_MCP}` |
| | | `EMBEDDING_API_BASE=http://llama-cpp-embed:8080/v1` | `EMBEDDING_API_BASE=http://llm-gateway:4000/v1` |
| | | `EMBEDDING_API_KEY=not-needed` | `EMBEDDING_API_KEY=${LITELLM_KEY_OB_MCP}` |
| **openbrain-entity-worker** (C3, E3) | same file, `services.openbrain-entity-worker.environment` | identical `CHAT_API_*` / `EMBEDDING_API_*` block | same swap as openbrain-mcp, with `${LITELLM_KEY_OB_ENTITY}` |
| **openbrain-wiki** (C4, E4) | same file, `services.openbrain-wiki.environment` | `LLM_BASE_URL=http://llama-cpp:8080/v1`, `LLM_API_KEY=not-needed`, `EMBEDDING_BASE_URL=http://llama-cpp-embed:8080/v1`, `EMBEDDING_API_KEY=not-needed` | `…=http://llm-gateway:4000/v1`, `…_API_KEY=${LITELLM_KEY_OB_WIKI}` |
| **little-coder** (C5) | `little-coder/config/little-coder.config.yaml` → `inference.base_url` | `http://llama-cpp:8080/v1` | `http://llm-gateway:4000/v1` |
| **little-coder** (C5) | `little-coder/config/models.json` → `models[].baseUrl` | `http://llama-cpp:8080/v1` | `http://llm-gateway:4000/v1` |
| **little-coder** (C5) | `little-coder/config/little-coder.schema.json` → defaults | `http://llama-cpp:8080/v1`, `http://llama-cpp-embed:8080/v1` | corresponding `llm-gateway:4000/v1` defaults |
| **little-coder** (C5) | `.env` → `LC_LLAMA_API_KEY` | `llama` | `${LITELLM_KEY_LC_CODER}` |

### 8.2 OWUI configuration (I1, I2) — UI step, not compose

Performed in OWUI Admin → Settings, persisted in `openwebui-data` volume.
Backup OWUI data **before** the cutover (the `openwebui-backup` sidecar runs
nightly at 02:00 — run it once on demand right before the change).

| Field | Path in OWUI | Before | After |
|---|---|---|---|
| OpenAI chat endpoint | Admin → Settings → Connections → OpenAI API | `http://llama-cpp:8080/v1` | `http://llm-gateway:4000/v1` |
| OpenAI chat key | same | (empty / `not-needed`) | `${LITELLM_KEY_OWUI_CHAT}` |
| Embedding endpoint | Admin → Settings → Documents → Embedding Model Engine = OpenAI; Embedding base URL | `http://llama-cpp-embed:8080/v1` | `http://llm-gateway:4000/v1` |
| Embedding key | same | (empty / `not-needed`) | `${LITELLM_KEY_OWUI_EMBED}` |
| Embedding model | same | `bge-m3` (or whatever is set) | `bge-m3` (matches LiteLLM `model_name` — likely no change) |

### 8.3 New compose services — `docker-compose.yml` additions

```yaml
  llm-gateway:
    image: ghcr.io/berriai/litellm:main-stable
    container_name: llm-gateway
    networks:
      - llm-net
    ports:
      - "127.0.0.1:4000:4000"   # admin / out-of-band only
    volumes:
      - ./config/litellm.config.yaml:/app/config.yaml:ro
    command: ["--config", "/app/config.yaml", "--port", "4000"]
    env_file:
      - .env
    environment:
      - LITELLM_MASTER_KEY=${LITELLM_MASTER_KEY}
      - LITELLM_DB_PASSWORD=${LITELLM_DB_PASSWORD}
      - LC_LLAMA_API_KEY=${LC_LLAMA_API_KEY}
    depends_on:
      llama-cpp:
        condition: service_healthy
      llama-cpp-embed:
        condition: service_healthy
      llm-gateway-db:
        condition: service_healthy
    restart: unless-stopped
    security_opt:
      - no-new-privileges:true
    healthcheck:
      test: ["CMD", "curl", "-fsS", "--max-time", "5", "http://localhost:4000/health/liveliness"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 30s
    labels:
      - "com.centurylinklabs.watchtower.enable=false"

  llm-gateway-db:
    image: postgres:16-alpine
    container_name: llm-gateway-db
    networks:
      - llm-net
    environment:
      - POSTGRES_DB=litellm
      - POSTGRES_USER=litellm
      - POSTGRES_PASSWORD=${LITELLM_DB_PASSWORD}
    volumes:
      - llm-gateway-db-data:/var/lib/postgresql/data
    restart: unless-stopped
    security_opt:
      - no-new-privileges:true
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U litellm -d litellm"]
      interval: 10s
      timeout: 5s
      retries: 10
      start_period: 20s
    labels:
      - "com.centurylinklabs.watchtower.enable=false"

  llm-gateway-backup:
    image: alpine:3.21
    container_name: llm-gateway-backup
    volumes:
      - llm-gateway-db-data:/data:ro
      - ./backups/llm-gateway:/backups
      - ./backup/llm-gateway-backup.sh:/scripts/backup.sh:ro
    environment:
      - BACKUP_DIR=/backups
      - DATA_DIR=/data
      - RETAIN_DAYS=${LITELLM_BACKUP_RETAIN_DAYS:-7}
      - BACKUP_CRON=${LITELLM_BACKUP_CRON:-0 2 * * *}
    # entrypoint pattern follows mnemory-backup / little-coder-backup
```

Add to the top-level `volumes:` block: `llm-gateway-db-data:`.

### 8.4 `.env.example` additions

```
# ---------------------------------------------------------------------------
# LiteLLM Proxy — unified inference gateway
# Docs: documentation/LiteLLM-Proxy/guide-LiteLLM-Proxy.md
# ---------------------------------------------------------------------------
# Master admin key (issues virtual keys, hits /spend/logs etc).
# Generate: openssl rand -hex 32
LITELLM_MASTER_KEY=change-me-to-a-long-random-string

# Postgres password for llm-gateway-db
LITELLM_DB_PASSWORD=change-me-to-another-long-random-string

# Per-caller virtual keys (issued via /key/generate after gateway is up).
# Empty in .env.example — populated during cutover.
LITELLM_KEY_LC_CODER=
LITELLM_KEY_MNEMORY=
LITELLM_KEY_OB_MCP=
LITELLM_KEY_OB_ENTITY=
LITELLM_KEY_OB_WIKI=
LITELLM_KEY_OWUI_CHAT=
LITELLM_KEY_OWUI_EMBED=
LITELLM_KEY_ADMIN=

# Backup retention + schedule for the spend-log database
LITELLM_BACKUP_RETAIN_DAYS=7
LITELLM_BACKUP_CRON=0 2 * * *
```

## 9. Pipe-module spec — `modules/llm-traffic/`

Mirrors the existing manifest-driven modules (`modules/gpu-status/`,
`modules/system-health/`). One module, one router entry, no changes to
`unified_openwebui_pipe.py` beyond appending the module-id to the recognized
set in `_format_response()`.

### 9.1 Router triggers

Added in `core/router.py`:

```
─ LLM traffic / GPU demand attribution  ────────── → modules/llm-traffic
  Triggers: llm traffic · who is using gpu · who's using gpu · llm demand ·
            llm spend · llm cost · gateway traffic · llama traffic
            Append "today" / "last 24h" / "last week" / "since boot" to
            scope a time window.
  Output:   Per-caller breakdown — requests · tokens in/out · avg latency ·
            current in-flight — pulled from LiteLLM's /spend/logs.
            Plus the existing "live snapshot" view (current GPU 0
            attribution) sourced from /key/info + recent logs.
  Coverage: Every caller holding a virtual key (see §7).
```

### 9.2 Module behavior

- On invocation, queries LiteLLM via `http://llm-gateway:4000` using
  `LITELLM_KEY_ADMIN` (read-only-ish — has admin scope for `/spend/*`).
- Endpoints called:
  - `GET /health` — gateway liveness summary header.
  - `GET /spend/logs?start_date=…&end_date=…` — per-request rows.
  - `GET /spend/calculate?start_date=…&end_date=…&group_by=api_key` —
    aggregated.
  - `GET /key/info` — alias→metadata lookup for pretty caller names.
- Aggregates client-side (small N — < 8 keys), renders a markdown table.
- Default window when no qualifier given: `last 1h`.

### 9.3 Module file layout (mirrors gpu-status)

```
modules/llm-traffic/
├── manifest.yaml
├── service/
│   └── llm_traffic.py
└── tests/
    └── test_llm_traffic.py
```

`manifest.yaml` declares `module_id: llm-traffic`, capabilities, trigger
phrases — same shape as `modules/gpu-status/manifest.yaml`.

### 9.4 Update to `unified_openwebui_pipe.py`

Single-line change in `_format_response()`:

```python
# Before
if result.get("module_id") in ["system-health", "gpu-status",
    "emergency-recovery", "custom-tools", "help-system",
    "system-orchestrator"] and "content" in result:

# After
if result.get("module_id") in ["system-health", "gpu-status",
    "emergency-recovery", "custom-tools", "help-system",
    "system-orchestrator", "llm-traffic"] and "content" in result:
```

The header docstring "COMMAND LIST" gets a new `LLM traffic` section
matching §9.1.

## 10. Recovery-stack updates

Per the workspace CLAUDE.md three-place rule, **adding a container = updating
all three of these together**. The plan/task doc must cover every line.

### 10.1 `scripts/emergency-recovery.ps1`

- Add `llm-gateway`, `llm-gateway-db`, `llm-gateway-backup` to
  `MainStackServices`.
- Insert into the **startup sequence** between `llama-cpp` / `llama-cpp-embed`
  (which must be healthy first) and every caller (`mnemory`, `openwebui`,
  `little-coder`, and the OB1 stack):
  ```
  llama-cpp + llama-cpp-embed → llm-gateway-db → llm-gateway → callers
  ```
- Insert into the **shutdown sequence** as the inverse: callers stop first,
  then `llm-gateway`, then `llm-gateway-db`, then llama-cpp.

### 10.2 `scripts/emergency-recovery.bat`

Mirror the same inventory + ordering changes. The `.bat` is the linear legacy
equivalent; keep the two scripts in lock-step.

### 10.3 `.claude/skills/stack-map/references/workspace-stacks.md`

- Add a new row in **Core** for `llm-gateway` (host port 4000, llm-net) and
  a row for `llm-gateway-db` (no host port, llm-net).
- Add `llm-gateway-db-data` to the **Volumes** list.
- Update **Cross-stack dependency order** §3 to add
  `llm-gateway-db → llm-gateway` between step 2 (llama-cpp servers) and
  the consumers.
- Cross-reference this guide.

## 11. Cutover plan (high-level — task doc will expand)

Each step is independently reversible. After every step, hit
`http://llm-gateway:4000/spend/logs?api_key=<that-key>` and confirm the new
caller appears in the ledger before moving on.

1. **Backups taken** — OWUI nightly run on demand, OB1 db dump, mnemory dump.
2. **Stand up the gateway, additively.** Add `llm-gateway` + `llm-gateway-db`
   to compose, bring up. Nothing routes through it yet. Verify `/health`
   shows both upstream models reachable.
3. **Issue virtual keys** via `/key/generate` (CLI one-liners — task doc
   captures the exact commands). Populate `.env` values.
4. **Cut over little-coder** (lowest risk, single config file). Restart
   `little-coder` only. Run a known-good `lc task` flow; confirm the request
   appears in `/spend/logs?api_key=sk-lc-coder`.
5. **Cut over mnemory.** Restart `mnemory`. Run a known-good `mcp__mnemory__
   search_memories` from a connected client; confirm log row.
6. **Cut over the OB1 trio** (entity-worker, openbrain-mcp, openbrain-wiki)
   together — they share configuration patterns and restart cheaply via
   `docker compose -f OB1/docker/docker-compose.yml up -d --force-recreate`.
7. **Cut over OWUI** (I1, I2) via the admin UI. Test a chat, a RAG query,
   and a web-search summarization.
8. **Add the pipe module.** Restart `openwebui` so the unified pipe reloads.
   Run `who is using gpu` in OWUI; confirm the new view renders.
9. **Update recovery scripts + stack-map doc.** This is the third
   "three-place" leg — the workspace CLAUDE.md will flag drift otherwise.
10. **Soak for 7 days.** Then decide on phase 2 (Prometheus + Grafana).

## 12. Risks & rollback

| Risk | Likelihood | Mitigation / rollback |
|---|---|---|
| Added latency on inference critical path | Low (LiteLLM is ~5–10 ms; inference dominates) | Per-step measurement during cutover. Per-caller revert = one env var. |
| LiteLLM bug or restart drops requests in flight | Medium | All callers already retry on transport error (verified for entity-worker via the `fetch timeout after 60000ms` log behavior); `restart: unless-stopped` recovers within seconds. |
| Postgres outage on `llm-gateway-db` | Low — Postgres is the most boring component in the stack | Gateway falls back to in-memory accounting when DB is down (LiteLLM behavior — confirm in cutover step 2). Backup sidecar restores from nightly dump. |
| Operator forgets to re-point one caller, leaves a dark traffic stream | Medium | Pipe-module's "live snapshot" view shows any source IP **not** holding a known key — surfaces drift immediately. Also covered by the §4.1/§4.2 audit checklist in the task doc. |
| Virtual key leak via .env commit | Low — .env is gitignored | Keys can be rotated via `/key/regenerate` without touching anything else. |
| LiteLLM image version drift | Medium | Pin to a specific `litellm:main-stable-vYY.MM.DD` tag once cutover stabilizes (matches the `LITTLE_CODER_VERSION` discipline). |
| Embedding model id mismatch (`bge-m3` vs `qllama/bge-m3:latest`) | High if not handled | LiteLLM `model_name: bge-m3` is the public alias; the upstream `model: openai/bge-m3` resolves to whatever llama-cpp-embed exposes. mnemory currently uses `qllama/bge-m3:latest` — must change to `bge-m3` to match LiteLLM's published alias. Called out explicitly in §8.1. |

## 13. Out of scope (phase 2)

- **Prometheus + Grafana** for dashboards. LiteLLM exports `/metrics` natively;
  llama.cpp already exposes `/metrics` (slot occupancy, KV cache, throughput).
  Adds value once weeks of data have accumulated.
- **Tailnet-served `llm-gateway`** — exposing the gateway over Tailscale Serve
  so off-host clients (Claude Code, Codex on other machines) can use it with
  authenticated virtual keys. Mirrors what `tailscale_serve_pipe.py` does for
  llama-cpp today.
- **Budgets / rate limits per key.** LiteLLM supports both natively
  (`/key/update` with `max_budget`, `tpm_limit`, `rpm_limit`). Phase-2 once
  the ledger reveals which callers actually need throttling.
- **Langfuse integration.** LiteLLM has a first-class Langfuse callback.
  Adds trace-grade UX on top of the SQL log. Only worth doing if the
  pipe-module view turns out to be too coarse.
- **Cost columns.** Local-only inference has no real $ cost, but LiteLLM can
  attribute pseudo-cost from token counts × a configured per-1k rate, which
  is useful for relative-share analysis. One-line config; flip on if useful.

## 14. Open questions for the operator

- **Q1.** Should `open_notebook` get a dedicated virtual key, or share
  `sk-owui-chat`? (Answer drives whether step 7's UI step covers it too.)
- **Q2.** Backup pattern: dedicated `llm-gateway-backup` sidecar (as drafted
  in §8.3) or fold into the existing `openwebui-backup` / `mnemory-backup`
  cron? Dedicated keeps the pattern uniform but adds a container.
- **Q3.** When the gateway is down, do callers fall back to direct
  llama-cpp access (preserving the old base URLs as `…_FALLBACK_BASE_URL`) or
  hard-fail and rely on `restart: unless-stopped` to recover? Hard-fail is
  simpler and matches how the search gateway behaves; fallback adds
  resilience but complicates the attribution story.
- **Q4.** Do we want a one-time bulk import of the prior week of llama-cpp
  access logs into the new `LiteLLM_SpendLogs` table for continuity, or
  start the ledger at cutover? (Operator value judgment — bulk import is
  doable but the IP-resolution is fuzzy for already-rotated containers.)

---

**Next document** (to be generated from this guide):
`documentation/LiteLLM-Proxy/integration-plan-LiteLLM-Proxy.md` — a phased,
checklist-form plan that expands §11 into discrete tasks with owners and
acceptance criteria, plus
`documentation/LiteLLM-Proxy/integration-tasks-LiteLLM-Proxy.md` mirroring the
little-coder / search-gateway pattern.
