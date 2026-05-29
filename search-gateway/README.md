# Private Search Gateway

Privacy-first web search for the ai-stack. Fronts **SearXNG routed over Tor**,
exposes native REST + a Tavily-compatible shim + a SearXNG-compatible endpoint
+ an MCP/OpenAPI surface, and is built around a pluggable `SearchProvider`
interface for future paid-provider rotation.

> Build spec: [`../documentation/web-search/guide-Private-Search-Gateway.md`](../documentation/web-search/guide-Private-Search-Gateway.md)
> Integration decisions: [`../documentation/web-search/integration-plan-private-search-gateway.md`](../documentation/web-search/integration-plan-private-search-gateway.md)

## Quickstart

The gateway is part of the **main ai-stack compose** — no separate stack.

1. Add secrets to the stack `.env` (see `.env.example` "Private Search Gateway"):
   ```env
   GATEWAY_API_KEY=<openssl rand -hex 32>
   SEARXNG_SECRET_KEY=<openssl rand -hex 32>
   ```
2. From the ai-stack root: `docker compose up -d --build`
3. Verify (Tor's first circuit build is slow — allow ~30–90s):
   ```bash
   curl -fsS http://localhost:8085/readyz
   curl -fsS -H "Authorization: Bearer $GATEWAY_API_KEY" \
        -H "Content-Type: application/json" \
        -d '{"query":"privacy search engines"}' \
        http://localhost:8085/v1/search
   ```

## Surfaces

| Surface | Endpoint | Auth | Consumer |
| --- | --- | --- | --- |
| Native REST | `POST /v1/search` | Bearer | host tools (`127.0.0.1:8085`) |
| Tavily shim | `POST /tavily/search` | Bearer or `api_key` body | Tavily-speaking clients |
| SearXNG-compat | `GET /search?q=&format=json` | none* | **Open WebUI** (stock `searxng` engine) |
| MCP → OpenAPI | `:8001` (mcpo) | per mcpo | MCP/OpenAPI clients |
| Health | `GET /healthz`, `/readyz` | none | compose healthcheck |

\* The SearXNG-compat endpoint is unauthenticated **by necessity** — OWUI's
`searxng` engine cannot send an `Authorization` header. It is safe because it
is reachable only on the internal `search-net`/`default` Docker networks
(never host-published) and called solely by OWUI — the same internal-trust
model the stack already uses for `mnemory`.

## Why these design choices (ai-stack specifics)

- **OWUI → gateway via the SearXNG engine (Approach B)**, not the Tavily shim.
  OWUI **v0.8.10** hardcodes `https://api.tavily.com/search` with no base-URL
  override, so `WEB_SEARCH_ENGINE=tavily` would bypass the gateway and Tor.
  Approach B uses OWUI's unmodified `searxng` engine → upgrade-safe, no OWUI
  patch. The Tavily shim remains for *external* Tavily clients. **Re-verify
  on every OWUI version bump** that the searxng engine contract still matches
  `routes/searxng_compat.py`.
- **One wiring point.** Setting OWUI's web-search engine also feeds
  `smolcrawl` deep-research, which calls OWUI's `search_web()`.

## Privacy enforcement (spec §8)

| Invariant | Enforcement here |
| --- | --- |
| SearXNG outbound via Tor | **Network-layer, not best-effort.** `search-net` is `internal: true`; SearXNG has no internet route except `tor:9050`. A misconfig cannot leak — §8 is *over*-enforced. |
| No query logging unless `LOG_QUERIES=true` (hash only) | structlog config logs a 16-char sha256 *fingerprint* only when explicitly enabled; default false. |
| Result URLs not logged at INFO | URLs never logged; only counts/provider/latency. |
| No telemetry | SearXNG `enable_metrics: false`; no metrics endpoint. |
| Default-deny Tor-hostile engines | `searxng/settings.yml` disables Google/Bing/Yandex. |
| Privacy infra not silently swapped | All search services `watchtower.enable=false`; images pinnable via `.env`. |

**Scope caveat:** the gateway privatises the **search query** step. `smolcrawl`
then fetches result *pages* directly (not via Tor). "Private search" ≠
end-to-end private browsing.

## Operational notes

- **Tor latency / budgets.** No daily quota (vs. the old Google PSE 100/day
  wall). The ceiling is upstream-engine tolerance over shared Tor exit IPs:
  heavy `deep_research` fan-out is slower and can hit captchas — graceful
  degradation (fewer engines answer; circuit breaker routes around), not a
  hard stop. Cache + circuit breaker + the deep_research fan-out cap govern
  burst volume. **SearXNG's own limiter is intentionally disabled**
  (`server.limiter: false`) — it is public-instance bot protection and would
  rate-limit our own fan-out, re-creating a budget. See plan §3.5.
- **Tor image.** `osminogin/tor-simple` (override via `TOR_IMAGE`). The guide's
  `dperson/torproxy` is stale. If you swap images, re-check that
  `tor/torrc` mounts at the image's expected path and the `nc -z 9050`
  healthcheck has `nc` available.
- **403 from SearXNG** → JSON format not enabled; `search.formats` must include
  `json` (it does in the shipped `settings.yml`).
- **Ports:** gateway `127.0.0.1:8085`, mcpo `127.0.0.1:8001`. SearXNG/Redis/Tor
  have no host ports (network-internal).

## Development

```bash
python -m venv .venv && .venv/Scripts/activate    # (Windows)
pip install -e "gateway[dev]"
pytest                       # 27 unit tests; integration deselected
pytest -m integration        # requires the stack up (GATEWAY_E2E_URL/API_KEY)
ruff check gateway/src gateway/tests
```

## Roadmap (documented, not built)

1. **Paid-tier providers** (Kagi, Brave, Mojeek): implement `SearchProvider`
   (stubs in `providers/_stubs.py`), add Redis monthly quota buckets, place
   above `searxng` in `PROVIDER_PRIORITY`.
2. Per-client API keys with rate limits.
3. Cross-provider result re-ranking (currently first-success-wins).
4. Optional Whoogle / LibreY fallbacks.
5. Opt-in Prometheus metrics + Grafana (localhost-bound only).
