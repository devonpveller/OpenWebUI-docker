# Private Search Gateway — Build Specification

**Audience:** autonomous coding agent
**Goal:** Build a self-hosted, privacy-first web search API gateway that fronts SearXNG (routed over Tor), exposes multiple API surfaces (native REST, Tavily-compatible, MCP, OpenAPI), and is architected for future expansion to a tiered provider rotation (Kagi → Mojeek → Brave → SearXNG/Tor).

Treat this document as the source of truth. Where it leaves choices open, prefer the simpler, well-tested option.

---

## 1. Project Goals

1. **Single search backend** for the user's LLM stack (Open WebUI primary, plus other LLM tools).
2. **Privacy by default**: outbound SearXNG traffic is routed through Tor; no query logging in any component.
3. **Modular providers**: the gateway is built around a `SearchProvider` interface so additional providers (Kagi, Mojeek, Brave, Tavily, etc.) can be plugged in later without changes to clients.
4. **Multiple client surfaces**: native REST, Tavily-compatible shim (broad client compatibility), MCP server, and OpenAPI via `mcpo`.
5. **Operability**: Docker Compose stack, sensible defaults, structured logs (no query content logged), health checks.

### Out of scope for v1

- Kagi / Mojeek / Brave / Tavily provider implementations (define the interface only, add stubs).
- User auth / multi-tenant API keys (a single shared API key via env var is enough for v1).
- A web UI for the gateway itself (SearXNG already has one).

---

## 2. High-Level Architecture

```
                      ┌────────────────────────────────────┐
                      │            Clients                  │
                      │  Open WebUI │ Other LLM tools │ MCP │
                      └──────┬──────────┬──────────┬────────┘
                             │          │          │
                  REST/JSON  │     Tavily-shim     │ MCP/OpenAPI
                             │          │          │
                      ┌──────▼──────────▼──────────▼──────┐
                      │     gateway  (FastAPI)            │
                      │  ┌─────────────────────────────┐  │
                      │  │ Provider rotation engine    │  │
                      │  │ Quota / circuit breaker     │  │
                      │  │ Result normalizer + cache   │  │
                      │  └────────────┬────────────────┘  │
                      └───────────────┼───────────────────┘
                                      │
                      ┌───────────────▼──────────┐
                      │  SearXNG (JSON enabled)  │
                      │  outbound via Tor SOCKS5 │
                      └────┬────────────────┬────┘
                           │                │
                  ┌────────▼────┐    ┌──────▼──────┐
                  │   Tor       │    │   Redis     │
                  │  SOCKS5     │    │ (SearXNG +  │
                  │  :9050      │    │  gateway    │
                  └─────────────┘    │  cache)     │
                                     └─────────────┘
```

**Services (Docker Compose):**

| Service   | Image / build                      | Purpose                        |
| --------- | ---------------------------------- | ------------------------------ |
| `tor`     | `dperson/torproxy` (or equivalent) | SOCKS5 anonymizing proxy       |
| `redis`   | `redis:7-alpine`                   | Cache for SearXNG + gateway    |
| `searxng` | `searxng/searxng:latest`           | Metasearch engine              |
| `gateway` | local build (FastAPI, Python 3.12) | Unified API + rotation logic   |
| `mcpo`    | `ghcr.io/open-webui/mcpo:latest`   | MCP-to-OpenAPI shim (optional) |

All services on a private Docker network. Only `gateway` (and optionally `searxng` for direct access) exposed to host.

---

## 3. Repository Layout

```
private-search-gateway/
├── docker-compose.yml
├── .env.example
├── README.md
├── tor/
│   └── torrc
├── searxng/
│   ├── settings.yml
│   └── limiter.toml
├── gateway/
│   ├── Dockerfile
│   ├── pyproject.toml
│   ├── README.md
│   └── src/
│       └── gateway/
│           ├── __init__.py
│           ├── main.py                # FastAPI app entry
│           ├── config.py              # env-driven settings (pydantic-settings)
│           ├── models.py              # Pydantic request/response models
│           ├── cache.py               # Redis-backed cache
│           ├── normalizer.py          # provider-agnostic result schema
│           ├── rotation.py            # provider rotation + circuit breaker
│           ├── providers/
│           │   ├── __init__.py
│           │   ├── base.py            # SearchProvider ABC
│           │   ├── searxng.py         # SearXNG implementation
│           │   ├── kagi.py            # stub
│           │   ├── mojeek.py          # stub
│           │   ├── brave.py           # stub
│           │   └── tavily.py          # stub
│           ├── routes/
│           │   ├── __init__.py
│           │   ├── native.py          # POST /v1/search
│           │   ├── tavily_shim.py     # POST /tavily/search
│           │   └── health.py          # GET /healthz, /readyz
│           ├── mcp_server.py          # MCP server entry (stdio)
│           └── logging.py             # structlog config (NO query content logged)
└── tests/
    ├── test_normalizer.py
    ├── test_rotation.py
    ├── test_searxng_provider.py        # uses fake httpx transport
    ├── test_tavily_shim.py
    └── test_end_to_end.py              # docker-compose-based smoke test
```

---

## 4. Component Specifications

### 4.1 Tor

Use a lightweight Tor container. Bind SOCKS5 to `0.0.0.0:9050` **inside the docker network only** — do not publish to host.

`tor/torrc` (minimal):

```
SOCKSPort 0.0.0.0:9050
SOCKSPolicy accept 172.16.0.0/12
SOCKSPolicy accept 10.0.0.0/8
SOCKSPolicy reject *
Log notice stdout
ExitRelay 0
ClientOnly 1
```

Healthcheck: `nc -z localhost 9050`.

### 4.2 Redis

`redis:7-alpine` with no persistence (`--save "" --appendonly no`). Used purely as a cache. Single network-internal port `6379`. No password needed (network-isolated), but support `REDIS_PASSWORD` env var if set.

### 4.3 SearXNG

Use the official `searxng/searxng` image.

Required configuration in `searxng/settings.yml`:

- `server.secret_key`: read from env (do not hardcode).
- `server.limiter`: `true`.
- `search.formats`: must include both `html` and `json` — JSON is required for the gateway to consume results. SearXNG returns 403 if JSON is not enabled.
- `outgoing.request_timeout`: `15.0` (Tor adds latency).
- `outgoing.proxies`:
  ```yaml
  outgoing:
    proxies:
      all://:
        - socks5h://tor:9050
  ```
  (`socks5h` ensures DNS goes through Tor too.)
- `redis.url`: `redis://redis:6379/0`.
- Enabled engines (Tor-tolerant set — disable Google/Bing as they captcha Tor exits aggressively):
  - duckduckgo, brave, mojeek, qwant, startpage, wikipedia, wikidata, github, stackoverflow, arxiv
  - Disable: google, bing, yandex (all hostile to Tor)

The gateway calls SearXNG at `http://searxng:8080/search?q=<query>&format=json`.

### 4.4 Gateway (FastAPI)

#### 4.4.1 Configuration (`config.py`)

Use `pydantic-settings`. All env-driven. Defaults shown:

| Env var                     | Default                | Purpose                                |
| --------------------------- | ---------------------- | -------------------------------------- |
| `GATEWAY_API_KEY`           | _(required)_           | Shared bearer token for clients        |
| `SEARXNG_URL`               | `http://searxng:8080`  | Base URL for SearXNG                   |
| `REDIS_URL`                 | `redis://redis:6379/1` | Cache (DB 1, separate from SearXNG)    |
| `CACHE_TTL_SECONDS`         | `900`                  | Result cache TTL                       |
| `PROVIDER_PRIORITY`         | `searxng`              | Comma-separated, highest privacy first |
| `REQUEST_TIMEOUT_SECONDS`   | `20`                   | Per-provider HTTP timeout              |
| `CIRCUIT_FAILURE_THRESHOLD` | `3`                    | Failures before opening circuit        |
| `CIRCUIT_COOLDOWN_SECONDS`  | `120`                  | Cooldown when circuit opens            |
| `LOG_LEVEL`                 | `INFO`                 | structlog level                        |
| `LOG_QUERIES`               | `false`                | Must default to false — privacy        |

#### 4.4.2 Auth

Every endpoint except `/healthz` and `/readyz` requires `Authorization: Bearer <GATEWAY_API_KEY>`. Return `401` otherwise.

#### 4.4.3 Normalized result schema (`models.py`)

```python
class SearchResult(BaseModel):
    title: str
    url: HttpUrl
    snippet: str | None = None
    published_at: datetime | None = None
    score: float | None = None
    source_engine: str | None = None      # which upstream engine inside SearXNG
    provider: str                          # which top-level provider returned it

class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    max_results: int = Field(default=10, ge=1, le=50)
    safe_search: int = Field(default=0, ge=0, le=2)
    language: str | None = None            # e.g. "en"
    time_range: Literal["day","week","month","year"] | None = None

class SearchResponse(BaseModel):
    query: str
    provider_used: str
    results: list[SearchResult]
    cached: bool = False
```

#### 4.4.4 `SearchProvider` interface (`providers/base.py`)

```python
class SearchProvider(ABC):
    name: str                              # e.g. "searxng", "kagi"
    privacy_rank: int                      # lower = more private; used for sorting

    @abstractmethod
    async def search(self, req: SearchRequest) -> list[SearchResult]: ...

    @abstractmethod
    async def health(self) -> bool: ...

    # Optional quota hooks; default to no-op
    async def remaining_quota(self) -> int | None: return None
```

#### 4.4.5 SearXNG provider (`providers/searxng.py`)

- HTTP client: `httpx.AsyncClient` with `REQUEST_TIMEOUT_SECONDS`.
- Calls `GET {SEARXNG_URL}/search` with `q`, `format=json`, plus `time_range`, `language`, `safesearch` when provided.
- Maps each result: `title`, `url`, `content → snippet`, `engine → source_engine`, `publishedDate → published_at` if present.
- Returns at most `max_results`.
- Treat HTTP 4xx (except 429) as configuration errors (raise). 5xx and 429 as transient (raise a specific `TransientProviderError` so the circuit breaker can react).

#### 4.4.6 Rotation engine (`rotation.py`)

- Maintain registry of provider instances loaded from `PROVIDER_PRIORITY`.
- For each request:
  1. Check cache (key = sha256 of normalized request JSON). If hit, return with `cached=true`.
  2. Walk providers in priority order. Skip any whose circuit is open.
  3. On `TransientProviderError`, increment failure counter; if `>= CIRCUIT_FAILURE_THRESHOLD`, open circuit for `CIRCUIT_COOLDOWN_SECONDS`. Move to next provider.
  4. On success, reset circuit, cache result, return.
  5. If all providers fail: return `503` with structured error.
- Circuit state stored in Redis with TTL (so it survives restarts and is consistent across workers).

#### 4.4.7 Endpoints

**Native:**

```
POST /v1/search
Authorization: Bearer <key>
Content-Type: application/json
Body: SearchRequest
Response: SearchResponse
```

**Tavily-compatible shim** (for tools that already speak Tavily):

```
POST /tavily/search
Authorization: Bearer <key>            # also accept "api_key" field in body
Body: { "query": str, "max_results": int, "search_depth": "basic"|"advanced", ... }
Response: {
  "query": str,
  "results": [
    { "title": str, "url": str, "content": str, "score": float, "published_date": str|null }
  ],
  "answer": null,
  "response_time": float
}
```

Map `search_depth=advanced` to a higher `max_results` (e.g. 20) but do not attempt to synthesize an `answer` — return `null`. This keeps the shim honest.

**Health:**

- `GET /healthz` — process liveness, returns 200 always.
- `GET /readyz` — checks SearXNG reachability and Redis ping; returns 200 only if both healthy.

#### 4.4.8 MCP server (`mcp_server.py`)

Use the official Python `mcp` SDK. Expose one tool:

```
name: web_search
description: Privacy-respecting web search via SearXNG over Tor.
input_schema: SearchRequest (subset: query, max_results, time_range)
```

Internally call the same rotation engine used by the HTTP routes (do not re-implement). Output is the normalized `SearchResponse` serialized as JSON.

Run as a stdio MCP server (standard for local MCP clients). Provide a separate Compose service `mcpo` that mounts the MCP server config and exposes it as OpenAPI on `:8001` for clients that want REST.

#### 4.4.9 Logging

Use `structlog`. **Never log query strings or result URLs by default.** When `LOG_QUERIES=true`, log a sha256 of the query (still not the query itself). Log: provider used, latency, result count, cache hit/miss, circuit state changes.

---

## 5. Configuration Files

### 5.1 `.env.example`

```env
# Required
GATEWAY_API_KEY=change-me-to-a-long-random-string
SEARXNG_SECRET_KEY=change-me-to-another-long-random-string

# Optional overrides
PROVIDER_PRIORITY=searxng
CACHE_TTL_SECONDS=900
LOG_LEVEL=INFO
LOG_QUERIES=false
```

### 5.2 `docker-compose.yml` skeleton

```yaml
services:
  tor:
    image: dperson/torproxy
    restart: unless-stopped
    networks: [internal]
    healthcheck:
      test: ["CMD", "nc", "-z", "localhost", "9050"]
      interval: 30s

  redis:
    image: redis:7-alpine
    command: ["redis-server", "--save", "", "--appendonly", "no"]
    restart: unless-stopped
    networks: [internal]
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 15s

  searxng:
    image: searxng/searxng:latest
    restart: unless-stopped
    depends_on:
      tor: { condition: service_healthy }
      redis: { condition: service_healthy }
    environment:
      SEARXNG_SECRET: ${SEARXNG_SECRET_KEY}
      SEARXNG_REDIS_URL: redis://redis:6379/0
    volumes:
      - ./searxng:/etc/searxng:rw
    networks: [internal]
    cap_drop: [ALL]
    cap_add: [CHOWN, SETGID, SETUID, DAC_OVERRIDE]

  gateway:
    build: ./gateway
    restart: unless-stopped
    depends_on:
      searxng: { condition: service_started }
      redis: { condition: service_healthy }
    env_file: .env
    environment:
      SEARXNG_URL: http://searxng:8080
      REDIS_URL: redis://redis:6379/1
    ports:
      - "127.0.0.1:8080:8080"
    networks: [internal]
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://localhost:8080/healthz"]
      interval: 30s

  mcpo:
    image: ghcr.io/open-webui/mcpo:latest
    restart: unless-stopped
    depends_on: [gateway]
    command: ["--port", "8001", "--", "python", "-m", "gateway.mcp_server"]
    ports:
      - "127.0.0.1:8001:8001"
    networks: [internal]

networks:
  internal:
    driver: bridge
```

Note: the `mcpo` service is illustrative — adjust to mount the gateway's MCP entrypoint correctly (either build a shared image with the gateway code, or mount a venv volume). The agent should pick whichever is cleaner and document it in the README.

---

## 6. Client Integration

### 6.1 Open WebUI

In Open WebUI's environment:

```env
ENABLE_WEB_SEARCH=True
WEB_SEARCH_ENGINE=searxng
SEARXNG_QUERY_URL=http://searxng:8080/search?q=<query>
```

Open WebUI talks to SearXNG directly. This is fine — it benefits from the same Tor routing because SearXNG's outbound is already proxied. The gateway is for _other_ LLM tools.

If the user later wants Open WebUI to go through the gateway too (for unified logging/quota), switch to `WEB_SEARCH_ENGINE=tavily` and point `TAVILY_API_BASE_URL` (or equivalent) at `http://gateway:8080/tavily`. Mention this in the README as an optional alternative.

### 6.2 Other LLM tools

- **Tavily-speaking clients** → point base URL at `http://gateway:8080/tavily`, use `GATEWAY_API_KEY` as the Tavily API key.
- **MCP clients (Claude Desktop, etc.)** → add the gateway's MCP server to `mcpServers` config.
- **OpenAPI clients** → point at `http://gateway:8001` (mcpo).
- **Native** → `POST http://gateway:8080/v1/search`.

---

## 7. Testing & Acceptance Criteria

The agent must produce passing tests for:

1. **`normalizer`**: SearXNG JSON → `SearchResult` mapping, including missing-field handling.
2. **`rotation`**: with a fake provider lineup, verifies failover, circuit opening, circuit cooldown expiry, cache hit short-circuit.
3. **`searxng_provider`**: uses `httpx.MockTransport` to assert outbound URL, query params, and timeout behavior.
4. **`tavily_shim`**: a Tavily-shaped request produces a Tavily-shaped response; `search_depth` correctly maps to `max_results`.
5. **End-to-end smoke** (optional but preferred): spin up the compose stack, hit `/v1/search` with `query="anthropic claude"`, assert ≥1 result and `provider_used="searxng"`. Mark as slow/integration.

### Manual acceptance

The user should be able to:

1. `cp .env.example .env`, fill in two secrets, run `docker compose up -d`.
2. `curl -fsS http://localhost:8080/readyz` returns 200 within ~30s.
3. `curl -fsS -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" -d '{"query":"privacy search engines"}' http://localhost:8080/v1/search` returns a list of results.
4. Configure Open WebUI to point at SearXNG, run a web search from a chat, observe results.
5. (If MCP client available) connect to gateway MCP server, call `web_search`.

---

## 8. Privacy Invariants (Do Not Violate)

- SearXNG outbound **must** go through Tor (`socks5h://tor:9050`). The gateway must `readyz`-fail if it can detect SearXNG is bypassing Tor (best-effort: hit `https://check.torproject.org/api/ip` through the SearXNG proxy chain at startup if practical; otherwise document a manual verification step).
- Query strings **must not** be logged unless `LOG_QUERIES=true` is explicitly set, and even then only as a hash.
- Result URLs **must not** be logged at INFO level.
- No analytics, telemetry, or external metrics endpoints. Prometheus metrics are fine but must be opt-in via env var and bound to localhost only.
- Default deny on engines hostile to Tor (Google, Bing, Yandex) — easier to enable later than to discover privacy degradation in production.

---

## 9. Future Work (Document, Do Not Build)

In `README.md` under "Roadmap", list:

1. **Paid-tier providers** (Kagi, Brave Search API, Mojeek API): implement `SearchProvider` subclasses, add quota tracking in Redis with monthly buckets, place above `searxng` in `PROVIDER_PRIORITY`.
2. **Per-client API keys** with rate limits.
3. **Result re-ranking** across providers (currently first-success-wins).
4. **Optional Whoogle / LibreY** as additional fallbacks.
5. **Prometheus metrics + Grafana dashboard** for quota, latency, circuit state.

---

## 10. Deliverables Checklist

- [ ] Complete repo at the layout in §3.
- [ ] `docker compose up -d` brings everything up cleanly on a fresh host with Docker.
- [ ] `README.md` with: quickstart, env var reference, client integration snippets for Open WebUI / Tavily-clients / MCP, troubleshooting (403 from SearXNG → enable JSON, Tor slow → expected, etc.), roadmap.
- [ ] Unit tests passing (`pytest`).
- [ ] At least one integration test, even if skipped by default behind a marker.
- [ ] No secrets committed; `.env.example` only.
- [ ] Verify privacy invariants in §8 — call out anything that couldn't be enforced and explain why.

---

## 11. Style & Tooling

- Python 3.12, `pyproject.toml` (PEP 621), `ruff` for lint, `ruff format`, `mypy --strict` on the `gateway/src/gateway` package.
- `httpx` for HTTP, `redis.asyncio` for Redis, `pydantic` v2, `pydantic-settings`, `structlog`, `fastapi`, `uvicorn[standard]`, `mcp` SDK.
- No global mutable state outside of the rotation registry; everything else dependency-injected via FastAPI's `Depends`.

---

**End of spec.** If anything in here conflicts with a concrete operational reality the agent discovers during the build, prefer the privacy invariants in §8 over everything else, and document the deviation in the README.
