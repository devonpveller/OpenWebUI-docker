# Private Search Gateway — ai-stack Integration Plan

**Status:** plan for review (no code written yet)
**Source spec:** [guide-Private-Search-Gateway.md](guide-Private-Search-Gateway.md)
**Decisions locked with user (2026-05-18):**

1. Merge into the **main `docker-compose.yml`** (single stack), conformed to stack conventions.
2. Open WebUI routes web search **through the gateway** (unified Tor/logging/quota).
3. **Tor is mandatory** — strict adherence to spec §8 privacy invariants.
4. This document is **plan-only**; build proceeds after approval.
5. OWUI↔gateway uses **Approach B** (SearXNG-compat endpoint on the gateway; OWUI's
   unmodified `searxng` engine). Resolved 2026-05-18 — see §2 / §9.

---

## 1. Why the guide fits this stack

The guide's architecture (SearchProvider interface, normalizer, rotation/circuit-breaker,
cache, multi-surface API) is sound and adopted as-is. The decisive integration fact the
guide doesn't know about:

- **You already have a single web-search integration point.** `smolcrawl`'s deep-research
  pipeline calls **Open WebUI's own `search_web()`**, driven by OWUI's admin setting
  `WEB_SEARCH_ENGINE` — see
  [smolcrawl/deep_research/research.py:401-414](../../smolcrawl/deep_research/research.py#L401-L414)
  and [domain_discovery.py:69](../../smolcrawl/deep_research/domain_discovery.py#L69).
  Today it's unconfigured, so domain discovery logs *"No WEB_SEARCH_ENGINE configured —
  skipping"*. Wire the engine once and **both** native chat search **and** the
  deep-research tool light up together. Single wiring point, high leverage.

---

## 2. Blocking constraint: OWUI v0.8.10 Tavily endpoint is hardcoded

Open WebUI is pinned to **v0.8.10** ([Dockerfile.openwebui-gpu:1](../../Dockerfile.openwebui-gpu#L1)).
In that version, `backend/open_webui/retrieval/web/tavily.py` hardcodes:

```python
url = "https://api.tavily.com/search"
```

There is **no base-URL override**. Therefore `WEB_SEARCH_ENGINE=tavily` would send
queries to the real Tavily, **bypassing the gateway and Tor** — a direct violation of
decision (3). The user's intent is preserved via one of two approaches:

| Approach | Mechanism | Pros | Cons |
| --- | --- | --- | --- |
| **B (recommended)** | Gateway also exposes a **SearXNG-compatible** `/search?q=&format=json`. OWUI uses its unmodified `searxng` engine pointed at the gateway. | No OWUI patching; survives OWUI upgrades; gateway still owns rotation/Tor/cache/logging; Tavily shim still serves external clients. | One extra small endpoint + response mapper in the gateway. |
| **A** | Build-time patch to the custom OWUI image adding a `TAVILY_API_BASE_URL` override; OWUI uses `tavily` engine → gateway `/tavily`. | Uses the literal Tavily shim path. | Patches OWUI internals; must re-verify on every manual OWUI version bump; brittle. |

**This plan assumes Approach B** unless the user selects A. Either way the gateway is the
single egress; only the OWUI-facing endpoint shape differs.

---

## 3. Required adjustments to the guide

### 3.1 Single compose, stack conventions (vs. guide's standalone repo)

The guide ships a standalone `private-search-gateway/` repo with its own compose. This
stack is one compose project with strict, uniform conventions. Adjustments to **every**
new service:

- `restart: unless-stopped`
- `security_opt: [ "no-new-privileges:true" ]`
- `labels: [ "com.centurylinklabs.watchtower.enable=false" ]` — pin images; do not let
  Watchtower auto-update search infra (consistent with all non-core services here).
- Host port publishes bound to `127.0.0.1` only, and **only where a host/Tailscale
  client needs them** (OWUI reaches the gateway over the Docker network — no host port
  required for that path).
- Real `healthcheck` blocks (Tor, Redis, SearXNG, gateway, mcpo).
- Pin image tags (`searxng/searxng:<digest-or-tag>`, not `:latest`) so privacy-relevant
  infra is reproducible.

**Source layout** (new top-level directory, lives in the ai-stack repo — *not* OB1):

```
ai-stack/
├── docker-compose.yml          # + tor, redis, searxng, gateway, mcpo services
├── .env.example                # + gateway/searxng/OWUI search vars
└── search-gateway/
    ├── tor/torrc
    ├── searxng/{settings.yml,limiter.toml}
    └── gateway/                # FastAPI build context (guide §3 layout)
        ├── Dockerfile
        ├── pyproject.toml
        └── src/gateway/...     # + routes/searxng_compat.py for Approach B
```

### 3.2 Network design — enforce §8 at the network layer

The stack splits `llm-net` (`internal: true`, no internet) from `default` (bridge, has
internet). The guide's own network (confusingly named `internal`, actually a plain
internet-capable bridge) is **not** reused. Instead, a new tier that makes the privacy
invariant structural rather than best-effort:

| Network | Type | Members | Rationale |
| --- | --- | --- | --- |
| `search-net` | `internal: true` (no gateway) | `redis`, `searxng`, `gateway`, `mcpo`, `tor` (internal side) | **SearXNG physically cannot reach the internet** except through `tor:9050`. This enforces spec §8 better than the guide's startup check — a misconfig can't leak. |
| `default` | existing bridge | `tor` (egress side), `gateway` | `tor` is the **only** internet bridge. `gateway` joins `default` solely so OWUI (already on `default`) can resolve `http://gateway:8080`. |

Notes:

- `gateway` on `default` has internet *reachability* but makes **no** outbound internet
  calls (it only talks to `searxng` on `search-net`). Acceptable. Tighter alternative:
  keep `gateway` off `default` and instead attach `openwebui` to `search-net` — heavier
  change to the core service; **not** recommended for v1. Documented tradeoff.
- The guide's startup "is SearXNG bypassing Tor?" check (spec §8) is still implemented as
  defence-in-depth, but the `internal: true` topology is the primary guarantee. Note this
  explicitly in the gateway README per spec §8 ("call out anything that couldn't be
  enforced") — here it is *over*-enforced.

### 3.3 Redis is net-new and isolated

The stack has no Redis today. Add `redis:7-alpine` (no persistence, per guide §4.2) on
`search-net` only. Do **not** reuse OB1's separate Postgres/infra — OB1 stays a separate
project per the git/infra boundary.

### 3.4 Tor mandatory — operational consequences to accept

Strict §8: `socks5h://tor:9050` for all SearXNG outbound; Google/Bing/Yandex disabled;
Tor-tolerant engine set only (DuckDuckGo, Brave, Mojeek, Qwant, Startpage, Wikipedia,
Wikidata, GitHub, StackOverflow, arXiv).

`deep_research` fans out **many concurrent `search_web()` calls** (`asyncio.gather`
batches in [research.py:198-203](../../smolcrawl/deep_research/research.py#L198-L203)).
Through Tor + free engines this means higher per-query latency and occasional exit-node
captchas. Mitigations (config/doc only — **no `deep_research` code changes in v1**):

- Gateway result cache (guide §4.4.6) — generous `CACHE_TTL_SECONDS` (default 900) damps
  repeated/overlapping fan-out terms.
- `REQUEST_TIMEOUT_SECONDS` raised for the Tor reality (guide default 20 is reasonable;
  SearXNG-internal `outgoing.request_timeout: 15.0` per §4.3).
- Circuit breaker prevents a captcha-storming engine from stalling every batch.
- Documented expectation: research runs are slower under mandatory Tor — this is the
  accepted cost of decision (3). Cross-reference existing fan-out cap / llama-swap
  lane-budget tuning so research latency isn't misattributed to inference.
- Privacy scope caveat to document: the gateway privatises the **search** step;
  `smolcrawl` then **fetches result pages directly** (not via Tor). "Private search" ≠
  end-to-end private browsing. Out of scope for this task; stated so expectations are
  correct.

---

### 3.5 Result budgets / rate limits (vs. prior Google PSE 100/day)

The prior backend (Google PSE) had a hard **100 queries/day** free wall (10k/day even
paid), which deep-research fan-out exhausted in 2–5 runs. SearXNG has **no daily quota,
no API key, no per-query cost** — it is metasearch, not an API product. The constraint
changes shape rather than disappearing; three *soft* ceilings replace the hard cap:

1. **Upstream engine tolerance — the real ceiling.** Per-engine throttling/captcha when
   hit hard from one IP; worse over Tor (shared, flagged exit IPs). Degrades *quality*
   (fewer engines answer a given query), not a hard stop; circuit breaker routes around
   persistently-failing engines. Managed by pacing, not rationing.
2. **SearXNG inbound `limiter` — build decision.** Guide §4.3 sets
   `server.limiter: true`. That limiter is **bot protection for *public* instances**: it
   403s automated-looking requests (`format=json`, no cookies, bursts). Here SearXNG is
   private, `internal:true`, called **only** by the gateway — so an untuned limiter would
   block *our own gateway* and re-create a self-inflicted budget.
   **Decision: keep SearXNG's limiter effectively off / permissive for the sole gateway
   client** (private instance, single trusted caller; no public exposure to protect).
   Implement via `limiter.toml` / `settings.yml` so the gateway's JSON fan-out is never
   rate-limited by SearXNG itself. Document in README.
3. **Gateway cache + existing fan-out cap.** Redis cache (`CACHE_TTL_SECONDS`, default
   900) collapses overlapping/repeat fan-out terms; deep-research's per-chat fan-out cap
   + llama-swap lane budget bound burst volume per run. These — not a daily counter — are
   the volume governor going forward.

Net: the daily-budget problem is removed. New discipline = **pace bursts so Tor exits
aren't captcha'd**, handled by cache + circuit breaker + fan-out cap. No gateway-side
daily quota is added for SearXNG (quota tracking in guide roadmap applies only to future
paid providers).

## 4. Services added to `docker-compose.yml` (shape)

All conformed to §3.1 conventions. Illustrative — not final YAML.

- **`tor`** — pinned Tor proxy image, `torrc` from guide §4.1, networks `[search-net,
  default]`, healthcheck `nc -z localhost 9050`. (Evaluate a maintained image; guide's
  `dperson/torproxy` is stale — call out final choice in README.)
- **`redis`** — `redis:7-alpine`, `--save "" --appendonly no`, network `[search-net]`,
  healthcheck `redis-cli ping`.
- **`searxng`** — pinned `searxng/searxng`, `./search-gateway/searxng:/etc/searxng:rw`,
  `SEARXNG_SECRET` from env, network `[search-net]` **only** (no internet except via
  tor), depends_on tor+redis healthy. `settings.yml`: JSON format enabled (else 403),
  `outgoing.proxies: all:// → socks5h://tor:9050`, `redis.url: redis://redis:6379/0`,
  Tor-tolerant engines, Google/Bing/Yandex disabled. **Limiter permissive/off for the
  sole gateway client** per §3.5 (private instance — do not let SearXNG rate-limit our
  own JSON fan-out and re-create a budget).
- **`gateway`** — `build: ./search-gateway/gateway`, networks `[search-net, default]`,
  `env_file: .env`, depends_on searxng (started) + redis (healthy), healthcheck
  `/healthz`. Host publish `127.0.0.1:8080:8080` **optional** (only if a host/Tailscale
  tool needs native REST; OWUI does not). Endpoints: `/v1/search`, `/tavily/search`,
  `/healthz`, `/readyz`, **plus `/search` SearXNG-compat (Approach B)**.
- **`mcpo`** — pinned `ghcr.io/open-webui/mcpo:latest` (→ pin a digest), exposes the
  gateway MCP server as OpenAPI on `127.0.0.1:8001:8001`, network `[search-net]`. Build
  approach (shared image vs venv mount) decided at implementation, documented in README
  per guide §5 note.

`volumes:` adds none with host persistence for search (Redis is cache-only; SearXNG
config is a bind mount under `search-gateway/`).

---

## 5. `.env.example` additions

```env
# --- Private Search Gateway ---
GATEWAY_API_KEY=change-me-long-random            # bearer for native/MCP/Tavily clients
SEARXNG_SECRET_KEY=change-me-another-long-random
PROVIDER_PRIORITY=searxng
CACHE_TTL_SECONDS=900
REQUEST_TIMEOUT_SECONDS=20
LOG_LEVEL=INFO
LOG_QUERIES=false                                # privacy: must stay false by default

# --- Open WebUI web search wiring (Approach B: searxng engine -> gateway) ---
ENABLE_WEB_SEARCH=True
WEB_SEARCH_ENGINE=searxng
SEARXNG_QUERY_URL=http://gateway:8080/search?q=<query>
# (Approach A instead would set WEB_SEARCH_ENGINE=tavily + a patched TAVILY base URL)
```

OWUI consumes these via its existing config; `deep_research` inherits automatically
through `search_web()`. No `deep_research` code change.

---

## 6. Privacy invariants (§8) — enforcement map

| Invariant | How enforced here |
| --- | --- |
| SearXNG outbound via Tor | `search-net` is `internal: true`; SearXNG has **no** route to internet except `tor:9050`. Structural, not best-effort. Plus startup check as defence-in-depth. |
| No query logging unless `LOG_QUERIES=true` (then hash only) | Gateway structlog config per guide §4.4.9; default false in `.env.example`. |
| Result URLs not logged at INFO | Gateway logging config. |
| No telemetry/external metrics | None added; Prometheus stays opt-in + localhost if ever added. |
| Default-deny Tor-hostile engines | `searxng/settings.yml` ships with Google/Bing/Yandex disabled. |
| Watchtower must not silently swap privacy infra | All new services `watchtower.enable=false` + pinned tags. |

---

## 7. Testing & acceptance (guide §7, adapted)

Unit (pytest, in `search-gateway/gateway/tests/`): `normalizer`, `rotation` (failover,
circuit open/cooldown, cache short-circuit), `searxng_provider` (httpx MockTransport),
`tavily_shim`, **`searxng_compat` (Approach B response shape OWUI expects)**.

Integration (marked slow, opt-in): compose up, `/v1/search` for `"anthropic claude"` →
≥1 result, `provider_used=searxng`.

Stack-specific acceptance:

1. `docker compose up -d` brings the whole ai-stack (existing + new) up clean.
2. `curl 127.0.0.1:8080/readyz` (if published) → 200 within ~30s after Tor bootstrap
   (allow longer `start_period` — Tor circuit build is slow).
3. From OWUI chat, run a web search → results return (proves OWUI→gateway→SearXNG→Tor).
4. Trigger a `deep_research` run → domain discovery no longer logs "No
   WEB_SEARCH_ENGINE configured"; fan-out completes (slower under Tor — expected).
5. (If MCP client available) call `web_search` via mcpo OpenAPI on `:8001`.

---

## 8. Proposed build phases (post-approval)

1. **Scaffold** `search-gateway/` (gateway FastAPI per guide §3, tor/searxng config).
2. **Gateway core**: config, models, normalizer, SearXNG provider, rotation+cache,
   native + Tavily + health routes, **SearXNG-compat route (B)**, MCP server, logging.
3. **Compose merge**: add 5 services + `search-net` to `docker-compose.yml`, conformed.
4. **Env**: extend `.env.example`; document required secrets.
5. **Tests**: unit suite green; mark integration slow.
6. **OWUI wiring**: set search env; verify chat search + deep_research path.
7. **README**: quickstart, the v0.8.10 Tavily constraint + chosen approach, privacy
   enforcement (note §8 is over-enforced via `internal: true`), Tor latency expectations,
   roadmap (paid providers, per-client keys, re-ranking — document only).

---

## 9. Decisions — resolved

**Section 2 fork: RESOLVED → Approach B** (SearXNG-compat endpoint on the gateway;
OWUI's unmodified `searxng` engine pointed at `http://gateway:8080/search`). No OWUI
image patch. The Tavily shim remains for external Tavily-speaking clients only. All
plan sections are now settled; the plan is ready to execute on approval.
