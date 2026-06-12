# Guide — LiteLLM Proxy for the ai-stack inference plane

**Status:** Source of truth (design + audit). Plan and task documents are
generated from this file — keep it authoritative.

**Last verified against the live stack:** 2026-05-23.

**⚠️ ARCHITECTURE REVISION — 2026-06-12.** The deployment approach changed from a
**per-caller migration** to **transparent interposition** (network-alias). Read
**§1A first** — it is now the authoritative architecture and supersedes the
per-caller cutover described in §4 (edit tables), §7, §8.1/§8.2, §11, §16.1, and
§17 (OWUI UI flips). Those sections are retained: their model-id inventory still
defines what the gateway must serve (§6), and they become the optional
**lazy-key roster** (§1A.4), not a required big-bang. The supply-chain hardening
(§19), model-alias coverage (§6), pipe module (§9), and capacity-planning (§15)
are unchanged. The trigger for the revision: a 2026-06-12 re-audit found the
per-caller inventory had already gone stale (five new OB1 inference callers since
2026-05-23 — see §1A.1), proving the per-caller model can't keep up with stack
drift.

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

## 1A. Architecture revision — Transparent interposition (2026-06-12)

**This section is authoritative.** Where it conflicts with the per-caller
migration described later, this section wins.

### 1A.1 Why the per-caller plan was abandoned

The original design (§4–§8, §11, §16–§17) migrated each caller individually:
edit its `base_url` from `http://llama-cpp:8080/v1` to `http://llm-gateway:4000/v1`,
swap its key, restart, verify — across ~10 callers in two compose projects plus
OWUI admin-UI flips, in a gated window.

A re-audit on **2026-06-12** found the caller inventory had **already drifted**.
The research-engine + IKS work added **five new direct inference callers** since
the 2026-05-23 baseline, none of them in the §4 inventory:

| New caller (since 2026-05-23) | Calls | Loopback | In §4? |
|---|---|---|---|
| `openbrain-curator` | `llama-cpp` (chat) + `llama-cpp-embed` | :8816 | ❌ |
| `openbrain-research` | `llama-cpp` (chat) + `llama-cpp-embed` | :8818 | ❌ |
| `openbrain-suggestion-worker` | `llama-cpp-embed` | :8813 | ❌ |
| `openbrain-chunk-worker` | `llama-cpp-embed` | :8817 | ❌ |
| `openbrain-workbench` | `llama-cpp-embed` | (via Caddy) | ❌ |

The OB1 side roughly **doubled** its direct callers (4 → 9) in three weeks. A
per-caller migration is structurally a moving target: the inventory is stale the
moment another service ships, and a missed caller is silent **dark traffic**
(bypasses the ledger). This is unacceptable for a system whose entire purpose is
*complete* attribution.

### 1A.2 The transparent-interposition model

Instead of changing callers, **make the gateway answer at the names the callers
already use.** Callers are never touched; they cannot tell the gateway from the
inference servers.

Mechanism — **Docker network aliases** on `llm-net`:

1. **Rename the real servers** (internal only): `llama-cpp` → `llama-cpp-upstream`,
   `llama-cpp-embed` → `llama-cpp-embed-upstream`. Their host ports (:8081
   llama-swap admin, :8082 embed) move with them.
2. **The single LiteLLM gateway** is given the network aliases **`llama-cpp`**
   and **`llama-cpp-embed`** and listens on **`:8080`** (the port callers use).
   Both aliases resolve to the one gateway; it routes by **model name**
   (chat models → upstream chat, `bge-m3` → upstream embed) and forwards to the
   renamed upstreams.

```
  every caller (unchanged)                         ┌──────────────────────────┐
  ─────────────────────────                        │   LiteLLM gateway         │
  http://llama-cpp:8080/v1 ──────┐   llm-net DNS    │   listens :8080           │
  http://llama-cpp-embed:8080/v1 ┤   aliases ──────►│   aliases:                │
                                 │                  │     llama-cpp             │
   (mnemory, little-coder,       │                  │     llama-cpp-embed       │
    all 9 OB1 callers, OWUI,     │                  │   routes by model name    │
    recipes, future callers)     │                  └───────┬───────────┬──────┘
                                 │              chat models  │           │  bge-m3
                                 │                           ▼           ▼
                                 │              ┌──────────────────┐  ┌──────────────────────┐
                                 │              │ llama-cpp-       │  │ llama-cpp-embed-      │
                                 │              │   upstream :8080 │  │   upstream :8080      │
                                 │              │ (llama-swap)     │  │ (bge-m3 embeddings)   │
                                 │              └──────────────────┘  └──────────────────────┘
```

**Result:** zero caller edits; the five un-inventoried callers (and any future
caller) are captured automatically; the dark-traffic problem (§12, old §17.9)
**cannot occur** — there is no direct path left to bypass.

### 1A.3 Attribution: permissive auth + the SQL ledger

LiteLLM tags every logged row by the **API key in the request header**, not the
URL. Callers send junk keys today (`ollama`, `llama`, `not-needed`). To accept
them transparently the gateway runs **permissive** — no enforced `master_key`
(see §6) — and still writes a `LiteLLM_SpendLogs` row per request (model, tokens
in/out, latency, timestamp, **and the key string presented**).

So on day one you get the **persistent SQL request ledger** — the historical
demand + capacity goal (§1, §15) — with attribution at **source-key / source-IP**
granularity. That is already strictly more than today's ephemeral llama.cpp
stdout logs.

> **⚠️ Linchpin spike (gates everything — see plan Phase 0).** This rests on one
> unverified assumption: that LiteLLM in **permissive mode** (no `master_key`)
> still writes a spend-log row per request carrying the **presented key string**.
> LiteLLM auth is generally all-or-nothing, so this must be proven before
> committing. If it does **not** log distinct keys permissively, fall back to
> running keyed from the start — which reintroduces a coordinated cutover (the
> per-caller plan below becomes the path again).

### 1A.4 Lazy keys — incremental clean attribution, no migration

Because the URL never changes, a caller gets **clean** attribution by changing
**only its key env var** — one line, one restart, independent of every other
caller, on no schedule:

```diff
  LLM_BASE_URL=http://llama-cpp:8080/v1     # ← unchanged, forever
- LLM_API_KEY=ollama                         # ← junk, blurs with other callers
+ LLM_API_KEY=mnemory                        # ← distinct string → its own ledger bucket
```

- A **distinct plain string** (`mnemory`, `lc-coder`) → attribution only; zero
  setup.
- A **real virtual key** (`/key/generate` → `sk-…`, requires `master_key`) →
  attribution **plus** budgets / rate-limits / revocation (§15.4).

The **per-caller tables in §4 / §8.1 / §16.1 are not deleted — they become this
lazy-key roster**: the list of callers, their key env-var names, and their
files, to work through opportunistically. The §15.4 cap table is the target
end-state once callers carry real virtual keys.

### 1A.5 What changes, vs the per-caller plan

| Concern | Per-caller plan (superseded) | Transparent interposition (now) |
|---|---|---|
| Caller `base_url` edits | ~10 callers, 2 compose projects | **none** |
| OWUI admin-UI flips (old §8.2/§16.3/§17) | required, operator-gated | **none** — OWUI is just another unchanged caller |
| Coverage of new/unknown callers | manual re-audit each time | **automatic** |
| What you edit instead | the callers | **rename 2 upstream services + add 2 gateway aliases** (§8.3 revised) + **repoint observability** (§16.4, health/GPU/tailscale/recovery refs that must watch the *real* servers, now `*-upstream`) |
| Attribution at cutover | full per-key (after big-bang) | ledger + source-key/IP; clean per-key added **lazily** (§1A.4) |
| Gateway role | one caller at a time routes through | **100% of inference** flows through immediately |
| Rollback | per-caller env revert | **rename the aliases back** onto the real servers — one `git revert` + recreate |

### 1A.6 The new trade — and its mitigations

Transparent interposition makes the gateway **critical path for all inference at
once** (big-bang, not incremental). Honest consequences + mitigations:

- **Blast radius:** a gateway outage stops *all* chat + embed, not just
  attribution. Mitigate: `restart: unless-stopped`, health-gating, digest-pin
  (§19), and the upstreams remain intact so rollback is just removing the
  aliases (§1A.5).
- **No per-caller revertibility:** rollback is all-or-nothing — but it's a single
  `git revert` (move the aliases back), which is simpler than unwinding N
  per-caller edits.
- **Model-id coverage is now mandatory, not best-effort:** since callers can't be
  fixed, the gateway **must** register every model id any caller sends or that
  caller gets "model not found." §6's alias coverage (all qwen variants + all
  three bge-m3 aliases) is a hard requirement here. The audit confirms the five
  new callers send only already-covered ids (`qwen36-27b:nothink`, `bge-m3`).
- **Observability must be repointed, not the callers:** health probes, GPU
  diagnostics, `tailscale serve` (`LLAMA_CPP_HOST`), `emergency-recovery`,
  `status_check.py`, `gpu_status.py` must watch the **real** servers — now
  `llama-cpp-upstream` / `llama-cpp-embed-upstream` — *not* the gateway. This is
  the one set of files that genuinely must change (§16.4-revised in the plan).

### 1A.7 High-level cutover (replaces §11 for transparent mode)

1. **Phase 0 spike** — prove permissive-mode per-key logging (§1A.3) **and** the
   §19 digest-pin/watchtower questions. If the spike fails, revert to the
   per-caller path.
2. **Stand up the gateway** (digest-pinned, permissive, `--port 8080`,
   `config` api_base → the *current* `llama-cpp` / `llama-cpp-embed`), **without
   aliases yet** — verify it serves every model id (§6) and writes spend logs.
3. **The flip (one commit):** rename the two real services to `*-upstream`,
   point the gateway config api_base at the `*-upstream` names, add the
   `llama-cpp` + `llama-cpp-embed` aliases to the gateway, repoint the
   observability refs (§1A.6) to `*-upstream`, recreate. All callers are now
   transparently proxied; verify the ledger fills and every healthcheck passes.
4. **Soak** on the ledger (attribution by source-key/IP).
5. **Lazy keys (optional, ongoing):** flip caller key env vars one at a time
   (§1A.4) for clean attribution; once all carry real virtual keys, optionally
   enable `master_key` enforcement + caps (§15.4).
6. **Three-place rule + docs** (§10 / §16 / stack-map) — the gateway and the
   `*-upstream` renames must land in recovery scripts + stack-map.

Rollback at any point: `git revert` the flip commit (aliases move back to the
real servers, which never stopped existing) + recreate.

### 1A.8 Validation = reversible live canary (operator's strategy, 2026-06-12)

The transparency *is* the safety net, so the flip doubles as its own test: in a
maintenance window, **perform the flip for real and watch the live services**. If
anything misbehaves, **`git revert` + `compose up` brings the originals back and
the callers never knew** — because no caller config ever changed, the rollback is
invisible to them. This is lower-risk than a synthetic harness and tests the real
integration (routing, model-name matching, streaming, embedding dimensions,
timeouts) end-to-end.

Mechanics that make it clean (and the one trap to avoid):

- **The upstream must stay running.** Don't literally stop `llama-cpp` and leave
  the gateway with no backend — the gateway *forwards* to the real server. The
  flip brings the real server back up **renamed** (`llama-cpp-upstream`) in the
  same `compose up`, and the gateway claims the `llama-cpp` alias.
- **Free the name + container_name.** The real services set
  `container_name: llama-cpp`. Renaming the service means stopping/removing the
  old container so the name *and* the `llama-cpp` network alias are free for the
  gateway: `docker compose stop llama-cpp llama-cpp-embed && docker compose rm -f
  llama-cpp llama-cpp-embed`, then `docker compose up -d --remove-orphans` with
  the flip definitions.
- **Watch during the canary:** every caller's healthcheck; `LiteLLM_SpendLogs`
  filling from multiple source IPs/keys (this also confirms the §1A.3 permissive
  logging in situ — the live canary subsumes the offline spike); `docker logs
  llama-cpp-upstream` showing the gateway as its only client; and any caller
  holding a **long-lived connection pool** to the old container IP — it will
  reconnect through the gateway on the next request, but a sticky caller can be
  nudged with a single `docker restart <caller>` (no config change).
- **Rollback drill:** `git revert <flip-commit> && docker compose up -d
  --remove-orphans`. The originals reclaim `llama-cpp`/`llama-cpp-embed`, the
  gateway is gone, callers are unaffected. **Worst case for a harder issue:** the
  same revert — bring the originals back, services none the wiser, regroup.

Because rollback is this clean, the offline permissive-logging spike (§1A.3)
becomes an *optional* pre-check rather than a hard prerequisite — the canary
tests the same fact under real load with an instant, invisible undo.

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
| D9 | The `llm-gateway` image is **digest-pinned** (`ghcr.io/berriai/litellm@sha256:…`), **never** the floating `:main-stable` tag | LiteLLM was the target of a supply-chain compromise (CVE-2025-55182, Mar 2026 — poisoned PyPI `1.82.7`/`1.82.8` carrying a credential-stealer + backdoor). A floating tag means any future `docker compose pull` / recreate silently adopts whatever ghcr serves; a digest pins exactly the image the operator reviewed. Mirrors the existing `portal-alerter` digest-pin convention (and its documented bump procedure). See §19. |
| D10 | The gateway runs **fully offline** — `LITELLM_LOCAL_MODEL_COST_MAP=True` + `telemetry: false` — and stays on the internal `llm-net` (no internet route) | The gateway is the credential-richest service in the stack: it holds the master key, every per-caller virtual key, and the DB password. Keeping it on `internal: true` `llm-net` means even a fully compromised image cannot exfiltrate those secrets. The two settings stop LiteLLM's startup model-cost-map fetch (from GitHub) and its anonymous telemetry, so the gateway makes **zero** outbound calls and boots cleanly with no internet — making the no-beacon property independent of any future network change. See §19. |

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

> **Transparent-mode deltas (§1A) — apply on top of the sketch below:**
> 1. **`api_base` targets the renamed real servers.** After the flip the gateway
>    *is* `llama-cpp`/`llama-cpp-embed`, so every `api_base` in `model_list`
>    must point at `http://llama-cpp-upstream:8080/v1` /
>    `http://llama-cpp-embed-upstream:8080/v1` (not `llama-cpp` — that would be a
>    self-loop). During pre-flip standup (before the rename) they stay at the
>    current `llama-cpp`/`llama-cpp-embed` names.
> 2. **Permissive auth during the lazy-key period.** Leave `master_key` **unset**
>    so the gateway accepts the junk/empty keys callers send today and logs each
>    request under the key string presented (§1A.3/§1A.4). Re-enable `master_key`
>    + caps only at the end-state, once callers carry real virtual keys.
> 3. **Model-id coverage is mandatory** (callers can't be fixed) — every id any
>    caller sends must be registered (all qwen variants + all three bge-m3
>    aliases below).

**Model-alias coverage** — re-audit found three different embedding model
IDs are sent to `llama-cpp-embed` today (`bge-m3` from OB1, `qllama/bge-m3:latest`
from mnemory, `bge-m3-f16.gguf` from little-coder's source default).
llama-cpp-embed ignores the `model` field and serves whatever GGUF was loaded,
so this works today. **LiteLLM routes by model name** — every alias clients
actually send must be registered or the gateway returns "model not found."
Same for the qwen `:nothink` variants — register all four chat variants
llama-swap can serve (the 35B-A3B `:nothink` was previously omitted).

```yaml
model_list:
  # ─── Chat — Qwen 3.6 27B (both variants share the same upstream slot) ───
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

  # ─── Chat — Qwen 3.6 35B-A3B (both variants) ───
  - model_name: qwen36-35b-a3b
    litellm_params:
      model: openai/qwen36-35b-a3b
      api_base: http://llama-cpp:8080/v1
      api_key: ${LC_LLAMA_API_KEY}
  - model_name: qwen36-35b-a3b:nothink
    litellm_params:
      model: openai/qwen36-35b-a3b:nothink
      api_base: http://llama-cpp:8080/v1
      api_key: ${LC_LLAMA_API_KEY}

  # ─── Embeddings — bge-m3, 1024-dim. THREE aliases for the same upstream. ───
  # See model-alias coverage note above. Phase 3+ can normalize callers to a
  # single canonical name; for cutover safety, all three are routed.
  - model_name: bge-m3
    litellm_params:
      model: openai/bge-m3
      api_base: http://llama-cpp-embed:8080/v1
      api_key: not-needed
  - model_name: bge-m3-f16.gguf            # little-coder default in config.py
    litellm_params:
      model: openai/bge-m3
      api_base: http://llama-cpp-embed:8080/v1
      api_key: not-needed
  - model_name: qllama/bge-m3:latest       # mnemory's current EMBED_MODEL value
    litellm_params:
      model: openai/bge-m3
      api_base: http://llama-cpp-embed:8080/v1
      api_key: not-needed

general_settings:
  # TRANSPARENT MODE (§1A.3): leave master_key UNSET during the lazy-key period so
  # the gateway accepts the junk keys callers send and logs each one. Re-enable
  # (uncomment) only at the end-state once all callers carry real virtual keys.
  # master_key: ${LITELLM_MASTER_KEY}
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
  telemetry: false        # §19/D10 — no anonymous outbound telemetry beacon
```

> **Offline operation (§19/D10):** `telemetry: false` above stops LiteLLM's
> anonymous usage beacon. The companion setting — `LITELLM_LOCAL_MODEL_COST_MAP=True`
> — is an **environment variable** (set in the §8.3 compose block, not this
> YAML) that tells LiteLLM to use its *bundled* model-price map instead of
> fetching `model_prices_and_context_window.json` from GitHub at startup.
> Together they mean the gateway makes no outbound internet calls, which is
> both a supply-chain hardening (no exfil channel) and a correctness fix for
> living on the internal-only `llm-net` (no failed-fetch hang/log-noise on boot).

## 7. Virtual-key scheme

> **§1A note:** In transparent mode keys are **not** issued up-front or
> required. This table is the **lazy-key roster** (§1A.4) — the target end-state
> if/when you enable `master_key` enforcement + caps. During the permissive
> period a caller's "key" can be a plain distinct string; full virtual keys are
> an opt-in upgrade per caller.

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

> **⚠️ SUPERSEDED by §1A (transparent interposition).** In transparent mode the
> caller edits in §8.1 and the OWUI UI flips in §8.2 are **not performed at
> cutover** — callers are untouched. This table is retained as the **lazy-key
> roster** (§1A.4): the per-caller key env-var names + files to work through
> *opportunistically* for clean attribution, never as a gated big-bang. The
> compose **services** in §8.3 are still added (the gateway), but with the
> transparent deltas of §8.3-revised below (aliases, `--port 8080`, permissive,
> upstream api_base). What you actually edit at cutover is the **two upstream
> renames + gateway aliases + observability repoint** (§1A.5/§1A.7).

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

> **✅ DEPLOYED + VERIFIED 2026-06-12 — the live `docker-compose.yml` is canonical.**
> The standup gateway is running alongside the live servers (no aliases yet) and
> verified (serves all 7 model ids; chat + embeddings proxy 200; permissive mode
> logs distinct per-key rows). The **verified form differs from the sketch below**:
> - listens on **`--port 8080`** (not 4000); host map `127.0.0.1:4000:8080`.
> - **no `master_key`** (permissive, guide §1A.3); `LITELLM_MASTER_KEY`/`LC_LLAMA_API_KEY`
>   env removed; DB via `DATABASE_URL=postgres://litellm:${LITELLM_DB_PASSWORD}@llm-gateway-db:5432/litellm`.
> - healthcheck uses **python** (`urllib`), not `curl` — the litellm image is
>   wolfi-based with no curl; `start_period: 120s` for first-boot prisma migrations.
> - image digest resolved: `ghcr.io/berriai/litellm@sha256:c98c9395c56a35b7abacff8269d43ff99aabacb62bbf42a04cc1514fcb9bde4a`.
> - `llm-gateway-backup` uses `postgres:16-alpine` (needs `pg_dump`) on a daily
>   sleep-loop, not `alpine`.
> The sketch below is kept for design intent; for the exact deployed spec read
> the `llm-gateway*` blocks in `docker-compose.yml`.

```yaml
  llm-gateway:
    # Digest-pinned — supply-chain hardening (decision D9, see §19). LiteLLM was
    # the target of CVE-2025-55182 (poisoned PyPI 1.82.7/1.82.8). Do NOT track
    # the floating :main-stable tag. Bump deliberately (mirrors portal-alerter):
    #   1. docker pull ghcr.io/berriai/litellm:main-stable
    #   2. docker inspect ghcr.io/berriai/litellm:main-stable --format '{{index .RepoDigests 0}}'
    #   3. confirm the resolved release is OUTSIDE any known LiteLLM compromise window
    #      (check github.com/BerriAI/litellm security advisories before pinning)
    #   4. paste the new sha256 below; recreate llm-gateway; re-run the §17.2 checks
    image: ghcr.io/berriai/litellm@sha256:__RESOLVED_AT_STANDUP__   # resolved + pinned in task T1.3.5
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
      - LITELLM_LOCAL_MODEL_COST_MAP=True   # §19/D10 — use bundled cost map; no GitHub fetch at boot
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

#### 8.3-revised — transparent-mode compose deltas (§1A)

The gateway block above is the *standup* form (admin on :4000, no aliases). To
make it **transparent**, apply these deltas at the flip (§1A.7 step 3):

```yaml
  # 1. RENAME the two real inference servers (internal name only). Their host
  #    ports (:8081 llama-swap admin, :8082 embed) and healthchecks move with
  #    them. Every observability ref that must watch the REAL server is repointed
  #    to these names (§16.4-revised): health probes, gpu_status, status_check,
  #    tailscale LLAMA_CPP_HOST, emergency-recovery inventory.
  llama-cpp-upstream:        # was: llama-cpp        (llama-swap, GPU 0)
    networks: { llm-net: {} }
  llama-cpp-embed-upstream:  # was: llama-cpp-embed  (bge-m3, GPU 1)
    networks: { llm-net: {} }

  # 2. The gateway ANSWERS as the old names via network aliases, and listens on
  #    :8080 (the port callers already use). It routes by model name to the
  #    upstreams (config api_base → *-upstream:8080, see §6 delta 1).
  llm-gateway:
    command: ["--config", "/app/config.yaml", "--port", "8080"]   # was 4000
    ports:
      - "127.0.0.1:4000:8080"   # host admin/out-of-band (probe /spend/logs etc.)
    networks:
      llm-net:
        aliases:
          - llama-cpp           # callers' http://llama-cpp:8080/v1 land here
          - llama-cpp-embed     # callers' http://llama-cpp-embed:8080/v1 land here
    depends_on:                 # was llama-cpp / llama-cpp-embed
      llama-cpp-upstream: { condition: service_healthy }
      llama-cpp-embed-upstream: { condition: service_healthy }
      llm-gateway-db: { condition: service_healthy }
    healthcheck:
      test: ["CMD", "curl", "-fsS", "--max-time", "5", "http://localhost:8080/health/liveliness"]
```

Notes: a single gateway with two aliases serves both chat and embed (it routes by
model name) — no second container needed. `master_key` stays unset (§6 delta 2).
The §19 digest-pin + `LITELLM_LOCAL_MODEL_COST_MAP=True` + watchtower-exclusion
are unchanged and still apply (the gateway is now the most load-bearing container
in the stack — 100% of inference — so the hardening matters *more*).

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

> **⚠️ SUPERSEDED by §1A.7** for transparent mode. The per-caller cutover below
> (steps 4–7: cut over little-coder, mnemory, the OB1 trio, OWUI) does **not**
> happen — there are no per-caller flips. The transparent cutover is: spike →
> stand up gateway → the rename+alias+repoint flip (one commit) → soak → lazy
> keys. Steps 1 (backups), 2 (stand up gateway), 8 (pipe module), 9 (recovery +
> stack-map), 10 (soak) still apply. Use §1A.7 as the cutover of record.

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
| Operator forgets to re-point one caller, leaves a dark traffic stream | ~~Medium~~ **N/A in transparent mode (§1A)** | Transparent interposition makes this **impossible** — callers are never re-pointed; the gateway answers at the names they already use, so there is no direct path left to leave behind. This was the single strongest reason for the §1A revision. |
| **Permissive-mode logging assumption is wrong** (§1A.3 linchpin) | Medium until spiked | The whole transparent approach assumes LiteLLM logs distinct presented keys with `master_key` unset. **Phase 0 spike proves it first.** If false → fall back to the keyed per-caller plan (§4–§8). |
| **Gateway is now critical-path for 100% of inference** (big-bang) | Medium | `restart: unless-stopped`, healthcheck, digest-pin (§19); the `*-upstream` servers never stop existing, so failure is recoverable. Latency is the same ~5–10 ms, now universal. |
| Transparent flip breaks something | Low/Medium | Rollback is one `git revert` of the flip commit (aliases move back to the real servers) + recreate — simpler than unwinding N per-caller edits. |
| Observability ref left pointing at the gateway instead of `*-upstream` | Medium | Health/GPU/tailscale/recovery probes must follow the real servers to `*-upstream` (§1A.6/§16.4). A probe pointing at the gateway would mis-report inference-plane health. Task-doc checklist + a post-flip verification that probes hit `*-upstream`. |
| Virtual key leak via .env commit | Low — .env is gitignored | Keys can be rotated via `/key/regenerate` without touching anything else. |
| LiteLLM image version drift | Medium | **Digest-pin from day one** (decision D9 — `ghcr.io/berriai/litellm@sha256:…`), not a floating tag. Bumps are deliberate operator actions with the procedure in §8.3 / §19. |
| **Supply-chain compromise of the LiteLLM image** (CVE-2025-55182 class) | Low per-event, High blast-radius | LiteLLM is a proven supply-chain target and the gateway holds all key material. Mitigations are stacked: digest-pin (D9), watchtower-excluded (no silent auto-pull), internal-only `llm-net` (no exfil egress), `telemetry: false` + `LITELLM_LOCAL_MODEL_COST_MAP=True` (zero outbound calls), per-caller virtual keys (single-key revocation), and master-key/DB-password gated behind G1/G2. Full threat model + response runbook in §19. |
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

> **⚠️ SUPERSEDED by §1A for transparent mode.** These callers are **not edited**
> at cutover — they keep hitting `llama-cpp` / `llama-cpp-embed`, which now
> resolve to the gateway. This table is now the **lazy-key roster** (§1A.4) +
> the **model-id coverage source** (§6). It is also **incomplete** as an edit
> list (it predates the five 2026-06-12 callers in §1A.1) — which is exactly why
> transparent interposition replaced it. The files that genuinely change at
> cutover are the **two upstream renames + gateway aliases** (§8.3-revised) and
> the **observability refs** (§16.4 + the health/GPU/recovery refs that must
> follow the real servers to `*-upstream`).

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
| [little-coder/src/littlecoder/config.py:37, 45-46](little-coder/src/littlecoder/config.py#L37) | 37 / 45 / 46 | `InferenceConfig` pydantic class defaults — **`schema.json` is GENERATED from this file** via `python -m littlecoder.config --schema`. Edit BOTH or the schema change gets reverted on next regeneration. | `base_url`, `embedding_base_url`, `embedding_model="bge-m3-f16.gguf"` | gateway URLs; embedding_model stays (LiteLLM registers the alias per §6) |
| [docker-compose.yml:716](docker-compose.yml#L716) | 716 | `little-coder.depends_on` | `llama-cpp` | `llm-gateway` |
| [OB1/recipes/email-history-import/pull-gmail.ts:68-71](OB1/recipes/email-history-import/pull-gmail.ts#L68) | 68 / 70 | Operator-run recipe — **bypasses compose env entirely**. Hardcoded `LLM_BASE` and `EMBED_BASE` defaults. Operator can override via `LOCAL_LLM_BASE` / `LOCAL_EMBED_BASE` env on invocation, but the defaults must change so an `lc` run without explicit env still routes through the gateway. | `http://llama-cpp:8080/v1` / `http://llama-cpp-embed:8080/v1` | `http://llm-gateway:4000/v1` |
| [OB1/recipes/google-activity-import/import-google-activity.mjs:33-36](OB1/recipes/google-activity-import/import-google-activity.mjs#L33) | 33 / 35 | Same shape as the gmail recipe; operator-run, hardcoded defaults | `http://llama-cpp:8080/v1` / `http://llama-cpp-embed:8080/v1` | `http://llm-gateway:4000/v1` |

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

### 16.8 Summary counts (updated after re-audit)

| Category | Files | Action |
|---|---|---|
| A — direct API callers | **12** (5 compose entries + 5 little-coder files including the Python source + 2 OB1 operator recipes) | Code change required |
| B — OWUI filter pipes | 1 (+ 1 backup) | Code change required; backup left as-is |
| C — OWUI runtime UI | 1 (OWUI) + 1 (open_notebook) | Admin-UI step |
| D — tailscale serve | 3 (entrypoint.sh, compose, pipe) | Decision needed; optional addition |
| E — recovery / probes | 11 (re-audit added `scripts/status_check.py` lines 139/153/175/189/326/338) | Additive changes (inventory + probes for the new services) |
| F — documentation | 9 (CLAUDE.md, skills, design docs, integration docs, security doc) | Semantic rewrites, post-cutover |
| G — verify-only | 7 | No change |
| **Total touched files** | **40+** (some span multiple categories) | |

The plan document derived from this guide will sequence these as: §18
pre-flight verification → A → B → E (so the new services exist and are
probed) → C (UI flip) → §15 caps applied → F (docs catch up). Category D
is parallel and optional.

## 17. Human-required changes — operator actions that cannot be diffed

> **⚠️ Largely SUPERSEDED by §1A.** Transparent interposition **eliminates the
> OWUI admin-UI flips** (17.4) and the per-caller secret wiring — OWUI and every
> caller are untouched. The operator actions that remain: provide the
> `LITELLM_DB_PASSWORD` (and `master_key` only if/when enforcement is enabled),
> run the §19.3 digest-pin bump, and — at the flip — eyeball that the rename +
> alias + observability-repoint commit is correct before recreate. The
> `Retry-After` compliance patches (§15.5) still apply if/when caps are enabled.

Every action below is hands-on: it lives in a service admin UI, a database
column, an external client config, or as a one-shot operator command. None
of these can be captured in a git diff or applied by `docker compose up`,
so the plan document treats them as explicit checklist items with operator
sign-off.

Numbered groups follow the cutover order in §11.

### 17.1 Pre-cutover — backups & dry-run (one operator session, ~15 min)

These run on the host. None of them changes runtime state; they just create
restore points so the cutover is reversible.

- [ ] **OWUI data snapshot** — run the on-demand path of the existing
      `openwebui-backup` sidecar to produce a fresh dump *before* the chat
      and embedding endpoints are repointed. Default location:
      `./backups/openwebui/`.
- [ ] **OB1 database dump** — `docker exec openbrain-db pg_dump -U postgres
      openbrain > backups/openbrain/pre-litellm.sql`. The OB1 DB is the
      authority for thoughts, sources, the wiki graph — losing it would be
      catastrophic, gateway or no gateway.
- [ ] **mnemory data snapshot** — manual trigger of the `mnemory-backup`
      cron (or `tar -czf backups/mnemory/pre-litellm.tar.gz` over the
      `mnemory-data` volume).
- [ ] **little-coder sessions snapshot** — same pattern over
      `little-coder-sessions` volume. The volume is small; cheap insurance.
- [ ] **Note the current OWUI admin settings** — operator records the
      *exact* current values of OpenAI base URL, embedding base URL,
      embedding model id, and embedding dimension into a scratch file.
      These are needed as the rollback values if step 17.5 has to be
      reversed.
- [ ] **Verify the workspace `.env` is git-ignored** — the LiteLLM virtual
      keys land here; confirm before populating in 17.3.

### 17.2 LiteLLM admin — one-time setup (operator on host)

After `docker compose up -d llm-gateway-db llm-gateway` has succeeded and
`/health/liveliness` returns 200:

- [ ] **Verify upstream connectivity from inside the gateway:**
      ```
      docker exec llm-gateway curl -fsS http://llama-cpp:8080/health
      docker exec llm-gateway curl -fsS http://llama-cpp-embed:8080/health
      ```
- [ ] **Confirm the gateway sees the configured models:**
      ```
      curl -fsS http://127.0.0.1:4000/v1/models \
        -H "Authorization: Bearer $LITELLM_MASTER_KEY"
      ```
      Expect to see `qwen36-27b`, `qwen36-27b:nothink`, `qwen36-35b-a3b`,
      `bge-m3`.
- [ ] **Generate every virtual key** via `/key/generate` (one curl per
      key, names matching §7). Operator pastes each returned `key` value
      into the matching `.env` variable. Example for the entity-worker
      key:
      ```
      curl -sX POST http://127.0.0.1:4000/key/generate \
        -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
        -H "Content-Type: application/json" \
        -d '{"key_alias":"sk-ob-entity","metadata":{"caller":"openbrain-entity-worker","plane":"ob1"}}'
      ```
- [ ] **Apply starting TPM/RPM caps from §15.4** via `/key/update` for
      each key. (LiteLLM's admin UI at `http://127.0.0.1:4000/ui` can also
      do this if the operator prefers point-and-click.)
- [ ] **Verify spend-log writes work** by issuing a test request through
      `sk-admin` and confirming a row appears in Postgres:
      ```
      docker exec llm-gateway-db psql -U litellm -d litellm \
        -c 'SELECT api_key, model, total_tokens FROM "LiteLLM_SpendLogs" ORDER BY created_at DESC LIMIT 5'
      ```

### 17.3 Per-service key population in `.env` files

Two `.env` files in two directories. **Operator must update both.**

- [ ] **Main stack `.env`** at the workspace root — populate:
      `LITELLM_MASTER_KEY`, `LITELLM_DB_PASSWORD`, `LITELLM_KEY_LC_CODER`,
      `LITELLM_KEY_MNEMORY`, `LITELLM_KEY_OWUI_CHAT`,
      `LITELLM_KEY_OWUI_EMBED`, `LITELLM_KEY_ADMIN`,
      `LITELLM_BACKUP_RETAIN_DAYS`, `LITELLM_BACKUP_CRON`. Replace
      `LC_LLAMA_API_KEY=llama` with the virtual key for little-coder.
- [ ] **OB1 stack `.env`** at `OB1/docker/.env` — populate:
      `LITELLM_KEY_OB_MCP`, `LITELLM_KEY_OB_ENTITY`, `LITELLM_KEY_OB_WIKI`.
- [ ] **(If githelper-pipe sees traffic)** generate `sk-owui-githelper`
      and record it for the OWUI UI step in 17.5.
- [ ] **Confirm `.env.example` was updated** with the new key names
      (file diff covered in §8.4) so the schema is documented for future
      contributors.

### 17.4 OWUI runtime configuration — the largest set of UI clicks

All actions are in Open WebUI Admin (gear icon top-right → "Admin
Panel"). The operator should be logged in as an admin user. Each
sub-step changes a value persisted in the `openwebui-data` volume — no
container restart required, but **a hard browser refresh** is needed
after each save for the change to take effect for in-progress chats.

#### 17.4.1 Chat endpoint repoint
- [ ] **Admin Panel → Settings → Connections** → expand the existing
      "OpenAI API" entry currently pointed at `http://llama-cpp:8080/v1`.
- [ ] Change **API Base URL** to `http://llm-gateway:4000/v1`.
- [ ] Change **API Key** to the value of `LITELLM_KEY_OWUI_CHAT`.
- [ ] Click "Verify connection" — confirm the green checkmark and that
      the model list dropdown returns `qwen36-27b`, `qwen36-27b:nothink`,
      `qwen36-35b-a3b`.
- [ ] Save.

#### 17.4.2 Embedding endpoint repoint
- [ ] **Admin Panel → Settings → Documents** → Embedding Model Engine
      should already be set to **"OpenAI"** (if not, set it now).
- [ ] Change **OpenAI API Base URL** (the embedding-specific one,
      separate from 17.4.1) to `http://llm-gateway:4000/v1`.
- [ ] Change **OpenAI API Key** for embeddings to the value of
      `LITELLM_KEY_OWUI_EMBED`.
- [ ] Confirm **Embedding Model** is `bge-m3` (must match LiteLLM's
      `model_name` from §6 — *not* the legacy `qllama/bge-m3:latest`).
- [ ] Confirm **Embedding Dimension** is `1024`. If it shows anything
      else, the previously-ingested documents may have been embedded at
      a different dimension — see 17.8 for the re-embed decision.
- [ ] Save.

#### 17.4.3 Web Search embedding endpoint (separate panel, easy to miss)
- [ ] **Admin Panel → Settings → Web Search** → if "Embedding Model Engine"
      is overridden separately for web search ingestion, repoint it to
      `http://llm-gateway:4000/v1` + `LITELLM_KEY_OWUI_EMBED`. If it
      inherits from 17.4.2, no action.

#### 17.4.4 Per-model overrides (every custom model registered in Admin → Models)
- [ ] **Admin Panel → Models** → for every entry, open it and check if
      the **Base Model** has a custom **Base URL** override. If yes,
      repoint that override to the gateway. If the model inherits from
      the global connection, no action.
- [ ] Particular attention: any model whose name contains "GitHelper",
      "deep_research", or a custom workflow name — these often carry
      their own base-URL overrides.

#### 17.4.5 Filter / pipe functions — Valves overrides
- [ ] **Admin Panel → Functions** → for each filter and pipe, click into
      it and open its **Valves** panel. Any Valve whose default is
      `http://llama-cpp:8080/v1` (the live-deployed copy may differ from
      the file default) must be updated to the gateway URL + a virtual
      key. Known cases:
      - `githelper-pipe` → `TARGET_BASE_URL` + new `TARGET_API_KEY`
      - Any deep_research pipe variants the operator has deployed
      - The unified AI Stack pipe — verify it doesn't have a hardcoded
        Valve pointing at llama-cpp (it shouldn't; it routes by
        keyword, not by base URL)
- [ ] Save each.

#### 17.4.6 Tool functions — same Valves review
- [ ] **Admin Panel → Tools** → same Valves audit as 17.4.5. Less
      common to find hardcoded base URLs here, but operator should still
      open each tool once.

#### 17.4.7 Post-flip smoke tests in OWUI
- [ ] Start a new chat with the default model — confirm a reply streams
      and the spend-log shows a row for `sk-owui-chat`.
- [ ] Upload a small document → trigger RAG retrieval → confirm
      embedding row appears for `sk-owui-embed`.
- [ ] Run a web search query → confirm both the search call (not via
      gateway) and the summarization call (via gateway) succeed.
- [ ] If githelper-pipe is in use, run a known-good GitHelper prompt and
      confirm `sk-owui-githelper` logs appear.

### 17.5 open_notebook (independent timeline)

`open_notebook` ships with its own admin UI for provider configuration.
Operator can defer this indefinitely without affecting the rest of the
stack. When ready:

- [ ] Open the open_notebook UI at `http://127.0.0.1:8503`.
- [ ] Navigate to Settings → Models / Providers (exact path varies by
      version).
- [ ] If a provider points at `http://llama-cpp:8080/v1` or
      `http://llama-cpp-embed:8080/v1`, repoint it to the gateway URL.
      Generate a `sk-open-notebook` virtual key if attribution is
      wanted; otherwise reuse `sk-owui-chat`/`sk-owui-embed`.
- [ ] Save and run a known-good notebook to verify.

### 17.6 Off-host client migrations (optional, parallel to main cutover)

These are clients running outside the workspace that reach the inference
plane over Tailscale Serve paths. Migration to `/llm-gateway` is
recommended (so virtual-key attribution applies) but not required.

- [ ] **Claude Code on the host (Windows)** — uses MCP servers
      (`mnemory-gateway`, `openbrain-mcpo*`, `lc-mcpo`). These are
      upstream of the gateway and do not change. No action.
- [ ] **Claude Code / Codex / external chat clients on other machines**
      — if any are configured against
      `https://<host>.tail<…>.ts.net/llama-cpp/v1` or
      `…/llama-cpp-embed/v1`, the operator decides whether to migrate
      them to `…/llm-gateway/v1`. If yes: update the client config and
      issue a per-client virtual key. The legacy paths remain
      functional during phase 1 — see §16.4.
- [ ] **The shared workspace's `MEMORY.md` reference cards** (operator's
      personal notes outside this repo) — if any cite the llama-cpp
      tailnet URL, update them for muscle-memory hygiene.

### 17.7 Caller-side retry-loop patches (deferred, can ship after phase 1)

§15.5 audits which callers honor `Retry-After`. The patches below are
small upstream fixes that should land in those repos *before* aggressive
TPM/RPM caps are applied — without them, throttled callers will see
their existing timeout-storm behavior instead of well-behaved backoff.

- [ ] **openbrain-entity-worker** (Deno `fetch`) — wrap inference calls
      in a `for (let attempt = 0; attempt < 5; attempt++)` loop that
      reads `Retry-After` from any 429 response and `await
      new Promise(r => setTimeout(r, parseInt(retryAfter) * 1000))`
      before retrying. Upstream patch.
- [ ] **openbrain-wiki** (Node SDK) — same pattern; verify whether the
      `openai` Node SDK ≥ 4.x already does this; patch only if not.
- [ ] **openbrain-mcp** — verify Deno client, patch if needed.
- [ ] **little-coder** — defer to upstream pi behavior; if pi's
      llama-cpp provider doesn't honor `Retry-After`, file an issue
      rather than fork.
- [ ] **filters/githelper-pipe.py** — wrap `requests.post` in a small
      retry helper. One file, ~10 lines.

### 17.8 Embedding compatibility (verify, then decide)

Both before and after the cutover, the embedding model is **bge-m3** at
**1024 dimensions** (§3 / §17.4.2). Pgvector indices on `openbrain-db`
and OWUI's RAG store expect 1024-dim vectors. If §17.4.2 surfaces a
different dimension in OWUI's UI, *something is misconfigured* — stop
and investigate before completing the cutover. Otherwise no re-embedding
is required.

- [ ] **Confirm `EMBED_MODEL` was renamed** from `qllama/bge-m3:latest`
      → `bge-m3` in `mnemory.environment` (§8.1 risk row). Without
      this, mnemory will request a model alias the gateway doesn't
      publish.
- [ ] **Spot-check a known document in OWUI** — re-trigger retrieval and
      confirm results match pre-cutover. If they don't, the dimensions
      differ and a re-embed is required.

### 17.9 Operator sign-off (final, after all groups complete)

- [ ] Pipe-module triggers `llm traffic`, `llm caps`, `llm saturation`
      all render correctly in OWUI.
- [ ] Spend-log has rows for every issued virtual key (no caller silent).
- [ ] No source IP on `llm-net` is making inference requests *without*
      a virtual key — query:
      ```
      docker logs llama-cpp --tail 200 | \
        grep '"POST /v1/' | awk '{print $3}' | sort -u
      ```
      The only address should be the gateway's IP on `llm-net`. Any
      other client IP indicates a missed cutover.
- [ ] Recovery script update (Category E in §16) has been exercised at
      least once — `scripts/emergency-recovery.ps1 recover` brings the
      gateway up in the correct order without errors.
- [ ] Documentation updates (Category F in §16) are queued as a single
      follow-up PR rather than dripped across the cutover.

### 17.10 Rollback playbook (if any step regresses)

Per-step revert, in the reverse order taken:

| Step that broke | Revert action |
|---|---|
| 17.4.* (OWUI repoint) | Restore the URL/key values noted in 17.1; hard refresh browser. The DB change persists, so no container restart needed. |
| 17.3 (`.env` repoint of a service) | Revert the one env var; `docker compose up -d <service>` |
| 17.2 (gateway broken) | `docker compose down llm-gateway` — all callers immediately revert to direct llama-cpp because the env still points at the gateway, but the gateway being down means callers fail. Quick fix: per-service env revert. Slow fix: debug the gateway. |
| Catastrophic (DB corruption etc.) | Restore the backups from 17.1; `docker compose down llm-gateway llm-gateway-db -v` to wipe gateway state; re-run from 17.2. |

The strength of this design is that **every caller is independently
revertible** — there is no single moment where the whole stack is in a
half-migrated state with no rollback. The most expensive revert is the
OWUI UI step, which is two minutes of clicking.

### 17.11 Operator-run OB1 recipes (file edits, not service restarts)

The two recipes added in §16.1 (`pull-gmail.ts`, `import-google-activity.mjs`)
are ad-hoc scripts the operator runs to backfill data — they are not
container services. They have **hardcoded `http://llama-cpp:8080/v1`
defaults** that bypass compose env entirely. Two cutover paths:

- **Edit the file defaults** (recommended for one-shot cleanness). One-time
  change; future operator runs route through the gateway by default. Plan
  Phase 3 covers this.
- **Override at invocation** via `LOCAL_LLM_BASE=http://llm-gateway:4000/v1
  deno run …`. No file edit, but every future invocation must remember
  the override. Footgun.

The plan defaults to "edit the file defaults" because the user's stated
goal is one-shot replacement with no surprises — overrides-only leaves a
trip wire for future-you.

## 18. Verified assumptions & pre-flight assertions

The re-audit surfaced four gaps the first pass missed:
1. Model-alias mismatch — three different embedding model IDs in flight
   (`bge-m3`, `bge-m3-f16.gguf`, `qllama/bge-m3:latest`) plus the
   previously-omitted `qwen36-35b-a3b:nothink` chat variant. §6 now
   registers all of them.
2. **Operator-run OB1 recipes** with hardcoded `http://llama-cpp:8080/v1`
   defaults that bypass compose env. §16.1 / §17.11 added.
3. **little-coder's schema.json is generated** from `src/littlecoder/config.py`.
   Editing the JSON without the Python source means the next regeneration
   reverts your change. §16.1 row added.
4. `scripts/status_check.py` probe lines (139, 153, 175, 189, 326, 338)
   — Category E, no action required, but listed for completeness.

To prevent further surprises, the agent runs assertions A1–A7 below as a
Phase 0.0 pre-flight before any backups or edits. Each is a runnable
command with a pass/fail expectation; any failure stops the cutover and
surfaces the discrepancy.

### 18.1 Pre-flight assertions (run before Phase 0)

The full set is implemented as Phase 0.0 tasks in
[integration-tasks-LiteLLM-Proxy.md](integration-tasks-LiteLLM-Proxy.md)
(T-0.0.1 through T-0.0.7). Summary:

| # | Assertion | Pass criterion | Failure means |
|---|---|---|---|
| A1 | Every model ID llama-swap exposes is registered in §6 | `curl -s http://127.0.0.1:8081/v1/models \| jq -r '.data[].id' \| sort` returns exactly `qwen36-27b`, `qwen36-27b:nothink`, `qwen36-35b-a3b`, `qwen36-35b-a3b:nothink` | A new model was added since the guide was written → add to §6 before cutover |
| A2 | llama-cpp-embed exposes exactly one model | `curl -s http://127.0.0.1:8082/v1/models \| jq -r '.data \| length'` returns `1` | Multi-model embedding setup not anticipated → revise §6 |
| A3 | No undiscovered hardcoded llama-cpp URLs | `Grep -r "http://llama-cpp(-embed)?:8080" --include='*.{py,ts,mjs,json,yaml,yml}' .` returns only the file list in §16.1 + the audit-only references in §16.7 / §18.2 | New caller introduced since audit → audit it before cutover |
| A4 | OWUI current settings match §17.1 expectations | Operator confirms (T0.7) that embedding model id is `bge-m3` and dimension is `1024` | OWUI was re-configured between audit and cutover → re-snapshot, revise §17.4 if needed |
| A5 | No NEW hardcoded llama-cpp URLs in OB1 recipes | `Grep -r "http://llama-cpp" OB1/recipes/` returns only `email-history-import/pull-gmail.ts` and `google-activity-import/import-google-activity.mjs` | A new recipe was added → audit before cutover |
| A6 | little-coder schema is in sync with config.py | `python -m littlecoder.config --schema \| diff - little-coder/config/little-coder.schema.json` is empty | Schema is stale; regenerate before cutover so the diff doesn't tangle with our edits |
| A7 | No surprising off-host tailnet sessions | `docker exec tailscale tailscale --socket=/tmp/tailscaled.sock status` is reviewed by operator | An undocumented off-host caller exists → document in §16.4 |

### 18.2 Files confirmed NOT to be callers (verified during re-audit)

These appeared in the broad `llama-cpp` grep but inspection confirmed
they don't make inference requests. Listed so a future maintainer
doesn't re-audit them:

- `filters/context-window-filter.py` — sliding-window message-trim
  filter; operates on the body, no outbound calls.
- `OB1/dashboards/open-brain-dashboard-next/app/api/**/*` — Next.js API
  routes; DB CRUD against PostgREST. Grep for `llama|qwen|bge|embed`
  returns zero files.
- `OB1/dashboards/open-brain-dashboard/src/**/*` — Svelte dashboard;
  Supabase client only.
- `OB1/recipes/local-ollama-embeddings/` — targets Ollama at
  `localhost:11434`, not llama-cpp.
- `OB1/recipes/wiki-synthesis/`, operator-run `OB1/recipes/entity-wiki/`,
  `OB1/recipes/x-twitter-import/`, `OB1/recipes/grok-export-import/`,
  `OB1/recipes/journals-blogger-import/`, `OB1/recipes/instagram-import/`
  — all default to **cloud** providers (OpenRouter / OpenAI). The
  container-run `openbrain-wiki` overrides them via compose env (§16.1
  E4 / C4 covers it).
- `OB1/integrations/entity-extraction-worker/_shared/config.ts` and
  `_shared/helpers.ts` — read `CHAT_API_BASE` / `EMBEDDING_API_BASE`
  env vars; covered via compose env edits in §16.1.

## 19. Supply-chain & security posture

**Why this section exists.** Putting LiteLLM in front of llama-swap means
adding a third-party gateway onto the **credential-richest seam in the stack**:
it holds `LITELLM_MASTER_KEY`, every per-caller virtual key, and the
`llm-gateway-db` password, and it sits on `llm-net` with reach to every
inference caller. LiteLLM is also a *proven* supply-chain target, so this
component warrants explicit hardening rather than the default "pull the latest
image and go."

### 19.1 The incident that motivated this (CVE-2025-55182)

On **2026-03-24**, attackers compromised a Trivy dependency in LiteLLM's CI,
used the leaked PyPI publishing token to push malicious **PyPI** packages
`litellm==1.82.7` and `1.82.8` (live ~3 hours before PyPI quarantine). The
payload was an auto-executing `litellm_init.pth` that ran on Python startup
and delivered three stages: a credential harvester (50+ secret categories), a
Kubernetes lateral-movement toolkit, and a persistent RCE backdoor. Safe
versions: ≤ `1.82.6` and anything after the cleanup. The maintainer reported
**"limited impact on proxy docker."**

**Why it does not directly hit this deployment as designed:**

1. **We deploy the prebuilt Docker image, not the pip package.** The poisoning
   lived in the PyPI distribution channel (a stolen PyPI token), not the ghcr
   build pipeline. This stack never `pip install litellm==1.82.7/8`.
2. **The dangerous payload stages are largely inert here.** Cloud-credential
   harvesting (AWS/GCP metadata endpoints) and Kubernetes cluster lateral
   movement do not map to a single Windows + WSL Docker Desktop host with no
   cloud IAM and no cluster. Only generic secret-scraping would partially apply.
3. **The blast radius is contained by design** (see §19.2).

The standing risk is therefore not *this* CVE — it is the **next** compromised
image. §19.2 is built around that threat model.

### 19.2 Layered mitigations (all enforced in §6 / §8.3)

| # | Control | What it stops | Where |
|---|---|---|---|
| M1 | **Digest-pin** `ghcr.io/berriai/litellm@sha256:…` (never `:main-stable`) | A future poisoned image being adopted by a `docker compose pull` / recreate. Converts "trust whatever ghcr serves" into "trust exactly the image I reviewed." | D9, §8.3 image line, task T1.3.5 |
| M2 | **Watchtower-excluded** (`com.centurylinklabs.watchtower.enable=false`) | The stack's auto-updater silently pulling a new `:main-stable`. (Already the universal convention on every service in this compose.) | §8.3 labels |
| M3 | **Internal-only `llm-net`** (`internal: true` — no internet route) | A compromised image phoning home / exfiltrating the master key, virtual keys, or DB password. This is the strongest control: even full RCE in the container has no egress path. | §5, compose `networks:` block |
| M4 | **No outbound calls** — `telemetry: false` + `LITELLM_LOCAL_MODEL_COST_MAP=True` | LiteLLM's startup GitHub cost-map fetch and anonymous telemetry beacon — so the no-egress property holds even if M3 were ever relaxed, and the gateway boots cleanly offline. | D10, §6, §8.3 env |
| M5 | **Per-caller virtual keys** (not one shared key) | A single key leak forcing a stack-wide rotation. Any one key is revocable via `/key/regenerate` without touching the others. | §7, §12 |
| M6 | **Secrets gated behind G1/G2** + `.env` (gitignored), `no-new-privileges:true`, host port bound to `127.0.0.1:4000` only | Key material entering the agent's reading context, leaking via git, privilege escalation, or LAN exposure of the admin API. | plan §3, §8.3 |

The net posture: a hypothetical future compromised `llm-gateway` image lands on
an island — no internet egress (M3/M4), no auto-deployment (M1/M2), and the
keys it could read are independently revocable (M5). That is the difference
between "telemetry incident" and "stack-wide credential breach."

### 19.3 Operator runbook — keeping the pin current

The digest pin is not "set and forget" — security fixes ship in new releases,
so the pin must be **bumped deliberately**, not auto-followed:

1. `docker pull ghcr.io/berriai/litellm:main-stable`
2. `docker inspect ghcr.io/berriai/litellm:main-stable --format '{{index .RepoDigests 0}}'`
3. **Before pinning,** check
   [github.com/BerriAI/litellm security advisories](https://github.com/BerriAI/litellm/security/advisories)
   and confirm the resolved release is outside any known compromise window.
4. Paste the new `sha256` into the §8.3 `image:` line; recreate `llm-gateway`;
   re-run the §17.2 upstream-connectivity + model-list checks.
5. Subscribe to the LiteLLM GitHub security advisories feed so a future
   incident is a notification, not a surprise.

`scripts/update-stack.bat` is updated (task T5.5) to follow this digest-bump
procedure rather than blindly pulling `:main-stable`.

### 19.4 If a LiteLLM compromise is announced while this is deployed

1. **Do not pull.** Leave the pinned digest in place (M1 means you are not on
   the bad image unless you explicitly bumped to it).
2. Confirm the deployed digest: `docker inspect llm-gateway --format '{{.Image}}'`
   and compare against the announced bad digests/versions.
3. If the deployed digest is implicated: stop `llm-gateway`, rotate
   `LITELLM_MASTER_KEY` + `LITELLM_DB_PASSWORD` + **every** virtual key
   (`/key/regenerate` per caller, then re-inject), and restore
   `llm-gateway-db` from the nightly backup if integrity is in doubt. M3 means
   exfiltration was blocked at the network layer, but rotate regardless.
4. Re-pin to a known-good digest per §19.3 and bring the gateway back up.
- `OB1/integrations/kubernetes-deployment/index.ts` — defaults to cloud;
  overridden by compose env (this is the source for `openbrain-mcp`).
- `scripts/ai_pipes/fileshed.py` — `http://localhost:8080` is OWUI's
  own port, not llama-cpp.
- `smolcrawl/deep_research/{models.py, rag_research.py}`,
  `smolcrawl/deep_research_tool.py` — `owui_base_url` defaults to
  `http://openwebui:8080`; calls into OWUI, not llama-cpp.
- `search-gateway/gateway/src/gateway/config.py` — port 8080 is the
  gateway's own port, not llama-cpp.

### 18.3 Honest limits — what this audit cannot guarantee

100% certainty against a live system is hard. Things this audit cannot
verify with full confidence (and the mitigations):

| # | Unknowable | Mitigation |
|---|---|---|
| L1 | OWUI's database contents — custom model entries in Admin → Models may hold their own Base URL overrides invisible to grep | §17.4.4 operator audit + §17.9 dark-traffic acceptance test catches any missed entry empirically |
| L2 | `open_notebook`'s database contents | §17.5 is operator-driven; no automated audit |
| L3 | Off-host clients on the tailnet (Claude Code on other machines, etc.) | Pre-flight A7; existing `/llama-cpp` tailnet paths remain functional during phase 1 so unknown clients keep working (just stay unattributed) |
| L4 | OWUI Functions/Tools the operator developed locally (not in `filters/` or `scripts/ai_pipes/`) — live only in OWUI's data volume | §17.4.5 / §17.4.6 operator audit + dark-traffic check |
| L5 | Watchtower-triggered image updates between audit time and cutover time | Pre-flight A3 grep is run at cutover time, not audit time, so catches drift |
| L6 | Future-added callers during the cutover window itself | Cutover is short (~2 hours active work); operator must not merge other branches during the window |

The agent surfaces any of these uncertainties to the operator at the G0
gate so the operator can decide whether to proceed or pause for a manual
sweep.

### 18.4 What guarantees the cutover is complete

Despite the limits above, three converging signals together give very
high confidence (close to 100%) that nothing was missed:

1. **Pre-flight A1–A7 pass** — no new state since the audit.
2. **Every caller listed in §16.1 has a spend-log row tagged with its
   virtual key** — checked at each substep, not just at the end.
3. **§17.9 dark-traffic acceptance test returns ONLY the gateway IP** —
   the empirical answer to "is anything else still talking to llama-cpp
   directly." This is the ground-truth check no audit can substitute for.

If all three converge, residual risk is bounded to a future caller
appearing AFTER cutover — which would show up in the dark-traffic
query the next time it runs.

---

**Next document** (to be generated from this guide):
`documentation/LiteLLM-Proxy/integration-plan-LiteLLM-Proxy.md` — a phased,
checklist-form plan that expands §11 into discrete tasks with owners and
acceptance criteria, plus
`documentation/LiteLLM-Proxy/integration-tasks-LiteLLM-Proxy.md` mirroring the
little-coder / search-gateway pattern.
