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

## 15. Backpressure & queue feedback — capacity-planning signal

A second-order motivation for the gateway: **measure how often the inference
plane is genuinely saturated so capacity decisions ("do I need a second
3090?") are evidence-driven rather than vibes-driven.** The OpenAI-compatible
API contract has no native "you're in queue, position 3" response, so the
honest options are bounded.

### 15.1 What the OpenAI contract allows

- `200 OK` (sync) / `200 OK` SSE stream
- `429 Too Many Requests` with `Retry-After: <seconds>` header
- `503 Service Unavailable`

There is no `202 Accepted, queued` for chat completions. Anything that
returns a synthetic "servers are busy" string inside a `200 OK` completion
body will corrupt downstream parsers (entity-worker writes garbage to
Postgres, OWUI displays it as the chat response). Do not do that.

### 15.2 Adopted pattern

**429 + `Retry-After`, driven by llama.cpp's `/slots` endpoint, plus per-key
TPM/RPM limits.** Three knobs the gateway provides:

1. **Slot-aware admission control** — a LiteLLM `async_pre_call_hook`
   polls `http://llama-cpp:8080/slots` and `http://llama-cpp-embed:8080/slots`
   (the same endpoints `little-coder` already queries via `metrics.poll_llama_slots`).
   When every slot is busy, the hook short-circuits the request and returns
   429 with `Retry-After: <estimate>`. Estimate = mean recent latency × queue
   depth ahead of the caller, capped at the caller's existing timeout.
2. **Per-key TPM/RPM caps** — set via `/key/update` on each virtual key.
   Prevents `openbrain-entity-worker` (which today retries aggressively into
   60s timeouts) from drowning `openwebui` interactive chat. Suggested
   starting caps in §15.4.
3. **Streaming SSE comments for OWUI** (optional, nice-to-have) — for the
   one caller that streams to a human (OWUI chat), emit SSE comments like
   `: queued, position 2` before the `data:` events start. OWUI's
   streaming consumer renders these as in-progress UI; entity-worker and
   other sync callers never see them.

### 15.3 What the data unlocks — capacity-planning queries

Every 429 is a Postgres row. With the spend-log schema in §6 plus the 429
events, the OWUI pipe module (§9) gains views like:

| Query | What it answers |
|---|---|
| `SELECT date_trunc('day', created_at), count(*) FROM "LiteLLM_SpendLogs" WHERE response_status = 429 GROUP BY 1` | "How often is the GPU genuinely saturated per day?" |
| `SELECT api_key, count(*) FROM "LiteLLM_SpendLogs" WHERE response_status = 429 GROUP BY api_key ORDER BY 2 DESC` | "Which caller is hitting the wall most?" |
| `SELECT extract(hour from created_at), count(*) FROM "LiteLLM_SpendLogs" WHERE response_status = 429 GROUP BY 1` | "Are saturation events clustered around predictable hours?" → suggests scheduling vs scale-up decision |
| `SELECT model, percentile_cont(0.95) WITHIN GROUP (ORDER BY end_user_response_time_ms) FROM "LiteLLM_SpendLogs" WHERE created_at > now() - interval '7 days' GROUP BY 1` | "What's the p95 latency per model over the last week?" |
| `SELECT date_trunc('week', created_at), sum(total_tokens) FROM "LiteLLM_SpendLogs" GROUP BY 1` | Weekly token-volume trend → demand growth curve |

The pipe-module trigger `llm demand last month`, `llm saturation`, or
`scale signal` materializes these as markdown tables in OWUI.

### 15.4 Recommended starting caps

To be tuned after a week of baseline data — these are intentionally
conservative to absorb the first cycle of real load without surprise:

| Virtual key | RPM cap | TPM cap | Notes |
|---|---|---|---|
| `sk-owui-chat` | 60 | 200_000 | Human-interactive, lowest-latency priority |
| `sk-owui-embed` | 120 | 400_000 | Embeddings are short; high RPM, modest TPM |
| `sk-lc-coder` | 30 | 150_000 | Inner-loop drafting; bursty but bounded by queue depth |
| `sk-ob-entity` | 20 | 100_000 | The current heavy hitter; intentionally throttled below interactive caps to prevent starvation |
| `sk-ob-wiki` | 10 | 80_000 | Scheduled at 01:00 — bursts to the wall by design; cap protects the rest of the day |
| `sk-ob-mcp` | 30 | 80_000 | Sporadic tool calls |
| `sk-mnemory` | 30 | 80_000 | Low volume, mixed chat + embed |

Caps live in the gateway's Postgres alongside the keys — adjustable via
`/key/update` without restarting anything. The pipe module exposes a `llm
caps` trigger that prints the current values so an operator can spot drift.

### 15.5 Caller-side compliance check

For the 429 + `Retry-After` pattern to actually work, callers must honor the
header. Audit per caller:

| Caller | HTTP client | Honors `Retry-After`? | Action |
|---|---|---|---|
| `openbrain-entity-worker` | Deno `fetch` | No (Deno fetch doesn't auto-retry) | Add manual retry-with-backoff loop in the worker — small upstream patch |
| `openbrain-wiki` | Node SDK (`openai` / `@anthropic-ai/sdk`?) | TBD — verify in `wiki-service` source | Patch if missing |
| `openbrain-mcp` | TBD | TBD | Verify |
| `mnemory` | Python `openai`-style client (per `LLM_API_KEY=ollama` pattern) | The official OpenAI Python SDK ≥1.0 honors `Retry-After` natively | No change |
| `little-coder` | pi CLI's internal client | TBD — verify in upstream | Patch if missing |
| `openwebui` | Internal aiohttp / openai-python | Honors via SDK | No change |
| `filters/githelper-pipe.py` (OWUI filter) | `requests` | No (requests doesn't auto-retry) | Add wrapper in the pipe |

Compliance gaps are listed as deferred tasks in the plan document — they
don't block phase 1, because LiteLLM will still log every 429 and the data
remains useful even if non-compliant callers ignore the header.

## 16. Comprehensive audit — every file touched

Every file in the workspace that mentions `llama-cpp` or `llama-cpp-embed`
was inventoried (36 files total). They sort into six categories below. The
plan document derived from this guide must walk each category in order.

### 16.1 Category A — direct API callers (MUST change)

These are the only files where inference traffic is actually emitted. Every
line listed is a target for the §8.1 substitution rules.

| File | Lines | Field | Current | After |
|---|---|---|---|---|
| [docker-compose.yml:295-299](docker-compose.yml#L295) | 295 / 296 / 298 / 299 | `mnemory.environment` block | `LLM_API_KEY=ollama`, `LLM_BASE_URL=http://llama-cpp:8080/v1`, `EMBED_BASE_URL=http://llama-cpp-embed:8080/v1`, `EMBED_MODEL=qllama/bge-m3:latest` | LiteLLM key + `http://llm-gateway:4000/v1` ×2 + `EMBED_MODEL=bge-m3` |
| [docker-compose.yml:313-316](docker-compose.yml#L313) | 313 / 315 | `mnemory.depends_on` | `llama-cpp` + `llama-cpp-embed` | `llm-gateway` |
| [OB1/docker/docker-compose.yml:57-63](OB1/docker/docker-compose.yml#L57) | 57 / 58 / 61 / 62 | `openbrain-mcp.environment` | chat/embed base + `not-needed` keys | gateway URL + `${LITELLM_KEY_OB_MCP}` |
| [OB1/docker/docker-compose.yml:224-230](OB1/docker/docker-compose.yml#L224) | 224 / 225 / 228 / 229 | `openbrain-entity-worker.environment` | identical | gateway URL + `${LITELLM_KEY_OB_ENTITY}` |
| [OB1/docker/docker-compose.yml:261-266](OB1/docker/docker-compose.yml#L261) | 261 / 262 / 264 / 265 | `openbrain-wiki.environment` | `LLM_BASE_URL`, `LLM_API_KEY`, `EMBEDDING_BASE_URL`, `EMBEDDING_API_KEY` | gateway URL + `${LITELLM_KEY_OB_WIKI}` |
| [little-coder/config/little-coder.config.yaml:11](little-coder/config/little-coder.config.yaml#L11) | 11 | `inference.base_url` | `http://llama-cpp:8080/v1` | `http://llm-gateway:4000/v1` |
| [little-coder/config/models.json:6](little-coder/config/models.json#L6) | 6 | `models[].baseUrl` | `http://llama-cpp:8080/v1` | `http://llm-gateway:4000/v1` |
| [little-coder/config/little-coder.schema.json:102](little-coder/config/little-coder.schema.json#L102) | 102 / 116 | `inference.base_url.default`, embeddings default | `http://llama-cpp:8080/v1`, `http://llama-cpp-embed:8080/v1` | gateway defaults |
| [docker-compose.yml:716](docker-compose.yml#L716) | 716 | `little-coder.depends_on` | `llama-cpp` | `llm-gateway` |

### 16.2 Category B — OWUI filter pipes with hardcoded base URL (MUST change)

These are Python files loaded into OWUI as filter pipes (Admin → Functions).
The `TARGET_BASE_URL` Valve is overridable per-deployment in the OWUI UI,
but the file default is hardcoded and ships into new pipe instances.

| File | Lines | Field | Note |
|---|---|---|---|
| [filters/githelper-pipe.py:117-118](filters/githelper-pipe.py#L117) | 117-118 | `Valves.TARGET_BASE_URL` default | Change default to `http://llm-gateway:4000/v1`. Operator should also update any already-deployed instance via OWUI Admin → Functions → githelper → Valves. Will need a virtual key here too — issue `sk-owui-githelper` if this filter sees real traffic. |
| [filters/githelper-pipe-v1-backup.py:94-95](filters/githelper-pipe-v1-backup.py#L94) | 94-95 | same as above (historical backup) | **Leave as-is** — backup files preserve prior state. Document the divergence. |

### 16.3 Category C — OWUI runtime configuration (UI step, no code change)

OWUI's chat + embedding endpoints are stored in `openwebui-data` (the OWUI
SQLite/Postgres DB), not in compose env. Cutover is via Admin UI. Restated
from §8.2 for audit completeness:

- Admin → Settings → Connections → OpenAI API → base URL + key
- Admin → Settings → Documents → Embedding base URL + key

`open_notebook` has the same UI-driven pattern; operator action when ready.

### 16.4 Category D — tailscale serve registration (DECISION NEEDED)

The current entrypoint script provisions tailnet-served paths
(`/llama-cpp`, `/llama-cpp-embed`) so off-host clients can reach inference
over Tailscale. After the gateway, the question is: do off-host clients
talk to the gateway too (so virtual keys + spend tracking apply) or do
they keep hitting llama-cpp directly?

| File | Lines | What it does |
|---|---|---|
| [entrypoint.sh:229-297](entrypoint.sh#L229) | 229-297 | First-pass setup of `/llama-cpp` tailnet path + socat proxy |
| [entrypoint.sh:300-366](entrypoint.sh#L300) | 300-366 | Same for `/llama-cpp-embed` |
| [entrypoint.sh:488-491, 843-846](entrypoint.sh#L488) | several | URL announcement in setup banner |
| [entrypoint.sh:531-558, 626-653, 737-748, 797-808](entrypoint.sh#L531) | several | Deferred setup + reconnection re-setup loops |
| [docker-compose.yml:104-109](docker-compose.yml#L104) | 104-109 | `tailscale.environment` — `LLAMA_CPP_HOST` / `LLAMA_CPP_PORT` / enabled flags |
| [scripts/ai_pipes/tailscale_serve_pipe.py:73-94](scripts/ai_pipes/tailscale_serve_pipe.py#L73) | 73-94 | Service registry for tailnet-served services |
| [scripts/ai_pipes/tailscale_serve_pipe.py:139,361,380-395,432-433,587-593](scripts/ai_pipes/tailscale_serve_pipe.py#L139) | scattered | Help text, registry duplicates |

**Recommendation:** add a `/llm-gateway` tailnet path **in addition to** the
existing ones — don't remove the direct llama-cpp paths yet. Off-host
clients (Claude Code on a second machine, Codex, etc.) migrate to the
gateway path opportunistically; the direct paths remain for emergency
debugging and for clients that need to bypass the gateway. Phase 2 can
deprecate the direct paths once nothing legitimate uses them.

Changes implied: add `llm-gateway` block to `tailscale_serve_pipe.py`
registry; add `LLM_GATEWAY_*` env vars to `tailscale` service in compose;
add a corresponding `setup_llm_gateway_serve` function in `entrypoint.sh`
mirroring the existing pair.

### 16.5 Category E — recovery / health / smoke scripts (MUST add to inventory; do NOT redirect existing checks)

These scripts probe inference plane health directly so they can detect
inference-plane failures independent of the gateway. The existing
llama-cpp references stay; the new `llm-gateway` + `llm-gateway-db` are
**added** to the inventory and the startup/shutdown ordering.

| File | Existing llama-cpp refs (preserve) | Additions needed |
|---|---|---|
| [scripts/emergency-recovery.ps1:30](scripts/emergency-recovery.ps1#L30) | line 30 `MainStackServices`; lines 177-178, 202-203, 261, 428-432, 472-493, 626-627, 745, 754-792 (startup/shutdown/probes) | Add `"llm-gateway", "llm-gateway-db", "llm-gateway-backup"` to `MainStackServices`; insert startup between `llama-cpp-embed` healthy and the consumer planes (mnemory, openwebui, OB1); insert shutdown as the inverse |
| [scripts/emergency-recovery.bat:6](scripts/emergency-recovery.bat#L6) | line 6 header; lines 39-42, 120-124, 156-167, 243, 261-266, 354-359 | Mirror the .ps1 additions linearly |
| [scripts/quick-fixes.bat:20](scripts/quick-fixes.bat#L20) | lines 20, 43, 226, 238-292, 310, 347-354, 387-392, 505-543, 693-729 | Add `llm-gateway` to menu option 11; new probe + restart helpers paralleling the llama-cpp pair |
| [scripts/check-tailscale-health.ps1:158](scripts/check-tailscale-health.ps1#L158) | lines 158-228 (OpenWebUI↔llama-cpp connectivity recovery), 364-470, 640-671 (test/repair functions) | Optional: add a `Test-LlmGatewayConnectivity` function + `Repair-LlmGateway`. Non-blocking — the gateway has its own healthcheck. |
| [scripts/gpu_check.py:116](scripts/gpu_check.py#L116) | lines 116-167 (llama-cpp probes inside `docker compose exec`), 216-275 | No change — probes inference plane directly, which is correct |
| [scripts/update-stack.bat:10](scripts/update-stack.bat#L10) | lines 10, 23-24, 49-50, 73-296, 348-502 (update flow for llama-cpp image) | Add an `llm-gateway` update menu item — LiteLLM updates separately from llama-cpp |
| [scripts/status_check.py](scripts/status_check.py) | (file checked separately) | Add `llm-gateway` row to whatever service table it prints |
| [modules/system-health/service/system_health.py:38-39](modules/system-health/service/system_health.py#L38) | lines 38-39 probe definitions, line 93 `expected_services`, line 266 narrative | Add probe row: `{"name": "llm-gateway", "plane": "Core", "host": "llm-gateway", "port": 4000, "path": "/health/liveliness", "critical": True}`. Add `"llm-gateway"` to `expected_services`. |
| [modules/gpu-status/service/gpu_status.py:239-318](modules/gpu-status/service/gpu_status.py#L239) | lines 239-240 container→GPU mapping, lines 315-318 hostname mapping | No change — gateway has no GPU; metric reads stay against the inference servers |
| [scripts/ai_pipes/unified_openwebui_pipe.py:302](scripts/ai_pipes/unified_openwebui_pipe.py#L302) | `_format_response()` module-id allowlist | Add `"llm-traffic"` to the allowlist (§9.4). Update the COMMAND LIST docstring header per §9.1. |

### 16.6 Category F — documentation (MUST update; semantic, not mechanical)

These files describe the stack and will read incorrectly after the gateway
lands. The plan document will sequence these — most can wait until after
phase 1 cutover so they describe the post-state in one pass.

| File | Lines | What needs to change |
|---|---|---|
| [CLAUDE.md:15](CLAUDE.md#L15), [CLAUDE.md:20](CLAUDE.md#L20) | 15, 20 | Add `llm-gateway` to the "core" plane listing in the stacks-at-a-glance table; mention the gateway in the OB1 bring-up dependency note |
| [.claude/skills/stack-map/SKILL.md:75](.claude/skills/stack-map/SKILL.md#L75) | 75 | Add `llm-gateway`, `llm-gateway-db` to the "Main · core" listing |
| [.claude/skills/stack-map/references/workspace-stacks.md:21](.claude/skills/stack-map/references/workspace-stacks.md#L21) | 21, 33-34, 85-86, 93, 137 | New core-plane rows; updated cross-stack dependency order (`llm-gateway-db → llm-gateway` between step 2 and step 3); volume `llm-gateway-db-data` |
| [.github/copilot-instructions.md:37-38](.github/copilot-instructions.md#L37) | 37-38 | Add `llm-gateway ← (all callers)` arrow above the existing llama-cpp arrows in the ASCII dependency diagram |
| [documentation/little-coder/Self-improving-little-coder-design.md:47,78,120,124,391,401,535](documentation/little-coder/Self-improving-little-coder-design.md#L47) | scattered | Update the design narrative — "little-coder talks to llama-cpp" becomes "little-coder talks to the gateway, which routes to llama-cpp"; slot-occupancy gating still polls `/slots` directly (§15.2) |
| [documentation/little-coder/integration-plan.md:41,106,114,138,280](documentation/little-coder/integration-plan.md#L41) | scattered | Same recasting |
| [documentation/little-coder/integration-tasks.md:58,85,88,183,398,568,581](documentation/little-coder/integration-tasks.md#L58) | scattered | Update the "verified working" notes — the verification needs to be re-done post-cutover and the verification target shifts to the gateway |
| [documentation/Systems-of-structured-data/INTEGRATION-PLAN.md:45,56,94,118,157,193](documentation/Systems-of-structured-data/INTEGRATION-PLAN.md#L45) | scattered | OB1 integration plan — `LLM_BASE_URL→llama-cpp` becomes `→llm-gateway` |
| [documentation/Systems-of-structured-data/INTEGRATION-TASKS.md:14,30,52,85,133,148,162,172](documentation/Systems-of-structured-data/INTEGRATION-TASKS.md#L14) | scattered | Same recasting |
| [documentation/implementation-guide/open-source authentication front ends for ai stack/plan-internet-exposed-front-end.md:94,196,1147,1452](documentation/implementation-guide/open-source%20authentication%20front%20ends%20for%20ai%20stack/plan-internet-exposed-front-end.md#L94) | scattered | Security audit doc: `llm-gateway` joins the "must NOT be internet-exposed" list alongside llama-cpp |
| [scripts/ai_pipes/unified_openwebui_pipe.py:36,61-62,86](scripts/ai_pipes/unified_openwebui_pipe.py#L36) | header docstring | Add `llm-gateway`, `llm-gateway-db` to the core-services line in the COMMAND LIST docstring (separate from the §9.4 code change) |

### 16.7 Category G — not-actually-callers (verify-only, no change)

Listed for audit completeness so a future maintainer doesn't mistakenly
edit them. These mention llama-cpp as a string but don't talk to it.

| File | Lines | Why no change |
|---|---|---|
| [.env.example:11,72,124](.env.example#L11) | 11, 72, 124 | Comment headers; `LC_LLAMA_API_KEY` env (kept; populated with virtual key after cutover) |
| [docker-compose.yml:144,155-156,684,832](docker-compose.yml#L144) | 144 / 155-156 / 684 / 832 | Network comments, open-terminal `NO_PROXY` (proxies internal addresses — adding `llm-gateway` here is optional; the agent doesn't egress) |
| [docker-compose.yml:174](docker-compose.yml#L174) | 174 | Disabled `ollama` block comment |
| [config/llama-swap.config.yaml:3](config/llama-swap.config.yaml#L3) | 3 | llama-swap's own config — upstream of LiteLLM, unchanged |
| [little-coder/src/littlecoder/agent.py:183](little-coder/src/littlecoder/agent.py#L183) | 183 | Inline comment in agent code |
| [little-coder/tests/test_similarity.py:6](little-coder/tests/test_similarity.py#L6) | 6 | Comment in a test docstring |
| [filters/githelper-pipe.py:450](filters/githelper-pipe.py#L450) | 450 | Inline comment about Qwen3 thinking — not a URL |

### 16.8 Summary counts

| Category | Files | Action |
|---|---|---|
| A — direct API callers | 9 (5 compose entries + 4 little-coder files) | Code change required |
| B — OWUI filter pipes | 1 (+ 1 backup) | Code change required; backup left as-is |
| C — OWUI runtime UI | 1 (OWUI) + 1 (open_notebook) | Admin-UI step |
| D — tailscale serve | 3 (entrypoint.sh, compose, pipe) | Decision needed; optional addition |
| E — recovery / probes | 10 | Additive changes (inventory + probes for the new services) |
| F — documentation | 9 (CLAUDE.md, skills, design docs, integration docs, security doc) | Semantic rewrites, post-cutover |
| G — verify-only | 7 | No change |
| **Total touched files** | **36** (some span multiple categories) | |

The plan document derived from this guide will sequence these as: A → B
→ E (so the new services exist and are probed) → C (UI flip) → §15 caps
applied → F (docs catch up). Category D is parallel and optional.

---

**Next document** (to be generated from this guide):
`documentation/LiteLLM-Proxy/integration-plan-LiteLLM-Proxy.md` — a phased,
checklist-form plan that expands §11 into discrete tasks with owners and
acceptance criteria, plus
`documentation/LiteLLM-Proxy/integration-tasks-LiteLLM-Proxy.md` mirroring the
little-coder / search-gateway pattern.
