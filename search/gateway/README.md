# Search Gateway (the search plane's gate)

Privacy-first web search and page fetching for the ai-stack. Fronts today's engine
(**SearXNG, routed over Mullvad WireGuard**; tor retired 2026-08-21) behind one
engine-adapter interface, and is **the gate**: the one path by which a model-facing
service searches the web or fetches a page, with every result and page screened by
intake before a caller sees it (security plan step 3; see [The gate](#the-gate)).
Surfaces: native REST, a Tavily-compatible shim, a SearXNG-compatible endpoint, the
gate's fetch / web-loader / Open WebUI search routes, and an MCP server.

> Build spec: [`../../../documentation-plans-ai-stack/implementation-guide/web-search/guide-Private-Search-Gateway.md`](../../../documentation-plans-ai-stack/implementation-guide/web-search/guide-Private-Search-Gateway.md)
> Integration decisions: [`../documentation-plans-ai-stack/journal/archive/implementation-guide/web-search/integration-plan-private-search-gateway.md`](../../../documentation-plans-ai-stack/journal/archive/implementation-guide/web-search/integration-plan-private-search-gateway.md)

## Quickstart

The gateway is part of the **main ai-stack compose** — no separate stack.

1. Add secrets to the stack `.env` (see `.env.example` "Private Search Gateway"):
   ```env
   GATEWAY_API_KEY=<openssl rand -hex 32>
   SEARXNG_SECRET_KEY=<openssl rand -hex 32>
   ```
2. From the ai-stack root: `docker compose up -d --build`
3. Verify (the WireGuard tunnel takes a few seconds):
   ```bash
   curl -fsS http://localhost:8085/readyz
   curl -fsS -H "Authorization: Bearer $GATEWAY_API_KEY" \
        -H "Content-Type: application/json" \
        -d '{"query":"privacy search engines"}' \
        http://localhost:8085/v1/search
   ```

## Surfaces

| Surface | Endpoint | Auth | Answer |
| --- | --- | --- | --- |
| Native REST | `POST /v1/search` | Bearer | per-caller key: screened; `GATEWAY_API_KEY`: today's |
| Tavily shim | `POST /tavily/search` | Bearer or `api_key` body | as above |
| SearXNG-compat | `GET /search?q=&format=json` | Bearer; keyless only while `SEARCH_ANONYMOUS_LEGACY=true` | as above (keyless = today's) |
| Gate: fetch | `POST /v1/fetch` `{url}` | per-caller key only | screened page, or `refused` / `withheld` / `error` |
| Gate: OWUI web loader | `POST /v1/web-loader` `{urls}` | per-caller key only | `[{page_content, metadata}]`, clean pages only |
| Gate: OWUI web search | `POST /v1/owui/search` `{query, count}` | per-caller key only | `[{link, title, snippet}]`, clean results only |
| MCP | `python -m gateway.mcp_server` (stdio) | per the MCP edge | today's (no edge is wired) |
| Health | `GET /healthz`, `/readyz`, `/health` | none | no query text |

**Callers.** A per-caller key (`GATE_CALLER_KEYS_FILE`) is SCREENED: it gets only
what intake labelled clean. `GATEWAY_API_KEY` is the legacy lane (today's host tools
and Tavily clients, unscreened) until step 5 moves them. No key is refused, except
`GET /search` while `SEARCH_ANONYMOUS_LEGACY=true`: the plane's compose sets it for
Open WebUI's keyless `searxng` engine and the research engine until step 5 moves
both (Open WebUI to `WEB_SEARCH_ENGINE=external` -> `/v1/owui/search` and
`WEB_LOADER_ENGINE=external` -> `/v1/web-loader`, each with its own key).

## Why these design choices (ai-stack specifics)

- **OWUI → gateway via the SearXNG engine (Approach B)**, not the Tavily shim.
  OWUI **v0.8.10** hardcodes `https://api.tavily.com/search` with no base-URL
  override, so `WEB_SEARCH_ENGINE=tavily` would bypass the gateway and the VPN.
  Approach B uses OWUI's unmodified `searxng` engine → upgrade-safe, no OWUI
  patch. The Tavily shim remains for *external* Tavily clients. **Re-verify
  on every OWUI version bump** that the searxng engine contract still matches
  `routes/searxng_compat.py`.
- **One wiring point.** Setting OWUI's web-search engine is the whole
  OWUI-side wiring. (It used to feed `smolcrawl` deep-research too, which
  called OWUI's `search_web()`; that service was retired 2026-08-21 and its
  source is in `scripts/archive/smolcrawl/`.)

## Privacy enforcement (spec §8)

| Invariant | Enforcement here |
| --- | --- |
| SearXNG outbound via the VPN | **Network-layer, not best-effort.** `search-net` is `internal: true`; SearXNG has no internet route except the kill-switched `vpn` proxy. A misconfig cannot leak — §8 is *over*-enforced. |
| No query logging unless `LOG_QUERIES=true` (hash only) | structlog config logs a 16-char sha256 *fingerprint* only when explicitly enabled; default false. uvicorn runs with `--no-access-log` (image CMD and compose), so no request line carries `?q=` (audit A3). The gate logs counts and outcomes, never a fetched URL. |
| Result URLs not logged at INFO | URLs never logged; only counts/provider/latency. |
| No telemetry | SearXNG `enable_metrics: false`; no metrics endpoint. |
| Default-deny Tor-hostile engines | `../searxng/settings.yml` disables Google/Bing/Yandex. |
| Privacy infra not silently swapped | All search services `watchtower.enable=false`; images pinnable via `.env`. |

**Scope caveat:** until step 5 moves the callers onto `/v1/fetch` and
`/v1/web-loader`, whatever fetches result *pages* does so on its own route unless it
is pointed at the `vpn` proxy itself (`openbrain-research` is, via `FETCH_PROXY_URL`;
Open WebUI's default loader is not). "Private search" is not yet end-to-end private
browsing.

## The gate

Security plan step 3 (PLAN-connectors.md, "Web connector (the gate)"; audit
2026-10-15 A1/A2/A3/A4, B3/B4/B5). Code: `egress/` (the fetch leg), `screen/` (the
intake hand-off), `callers.py`, `routes/gate.py`.

**The fetch leg (`egress/`).** One deadline (`GATE_FETCH_TIMEOUT_SECONDS`, 20) over
resolution, every hop and the whole body:

1. `guard.check_url` - a Python port of OB1 `ingest-egress.ts` rule 2: http(s) only, no
   userinfo, allowed ports only (`GATE_FETCH_PORTS`, 80/443), no localhost / single-label
   / internal-suffix names (`.local`, `.localdomain`, `.internal`, `.docker`, ...), no
   wildcard-DNS names (`nip.io`, `sslip.io`, ...), numeric hosts decoded the WHATWG way
   (`2130706433`, `0x7f.1`) and judged, every IPv4/IPv6 literal judged (private,
   loopback, link-local, CGNAT, mapped, 6to4, Teredo, NAT64, ULA, site-local, multicast,
   zone ids, and anything not `is_global`).
2. A name is RESOLVED and every answer judged; one is PINNED. In proxy mode (the
   default, `GATE_FETCH_PROXY_URL=http://vpn:8888`) resolution is DNS-over-HTTPS
   (`GATE_DOH_URL`) sent THROUGH the proxy, so nothing leaks outside the tunnel;
   `GATE_FETCH_PROXY_URL=""` is direct mode (system resolver), chosen explicitly only.
3. The connection goes to the pinned IP only (`transport.PinnedBackend` refuses any
   other address): `CONNECT <ip>:<port>` through the proxy, the name kept for the Host
   header and the TLS certificate check.
4. Redirects are never followed by the client: each `Location` goes back to step 1, so
   every hop, the final one included, is judged and pinned (`GATE_FETCH_MAX_REDIRECTS`, 5).
5. Caps: the content type must be on the allow-list (`GATE_FETCH_CONTENT_TYPES`:
   html, xhtml, plain, markdown, json; an empty type is refused); the body is streamed
   and cut at `GATE_FETCH_MAX_BYTES` (2 MiB), counted on the wire and after
   gzip/deflate decoding (a decompression bomb is cut too).

The refusal corpus is `tests/fixtures/guard_corpus.json` (229 URLs + 14 answer sets);
`tests/test_guard_parity.py` runs it through the TypeScript original under deno and
asserts the port refuses everything the original refuses.

**The intake hand-off (`screen/`) - a connector on the connector kit.** The gate is the
connector `search-gate` (security plan step 2's contract): `screen/gate_connector.py` holds two
connector classes that only COLLECT what the gate already has - every search result (title +
snippet, source `web-search`) and every fetched page (raw bytes, source `web-page`, origin = the
requested URL, redirect_chain = every hop after it). The kit builds each envelope (intake's stage-1
schema exactly, plus `metadata.provenance`) and its `IntakeClient` sends it to `INTAKE_URL` with the
gate's key read from `INTAKE_KEY_FILE` (bounded send, no proxy, no redirect; 2 attempts on
408/503/no answer). The query is never sent. The gate opts in to `return_clean_text`, so a `clean`
answer carries intake's text and labels. Read policy: only `clean` is returned (`suspicious`,
`pending`, `injected`, `rejected` and every failure are withheld and counted); the text is intake's,
wrapped in the section-6 nonce fence `<<UNTRUSTED-{nonce} src=... verdict=clean>> ... <<END-{nonce}>>`
with intake's labels beside it. No intake key file: every screened route is 503, never unscreened.

The kit is VENDORED at `src/connector_kit/` (a byte copy of OB1 `integrations/connectors/connector_kit`
at the gitlink; `VENDORED.md` names the sha and how to re-copy), so the image is built from
`search/gateway/` alone and never needs an OB1 checkout. `tests/test_kit_vendored.py` fails on any
drift from OB1 or a sha that is not the gitlink; `tests/test_kit_conformance.py` runs the kit's
conformance runner (its 9 contract tests) against both gate connectors. `screen/fence.py` is a byte
copy of intake's `fence.py` (a test asserts it).

**The cache (A2).** Keys and values are HMAC'd with a key only the gateway holds
(`GATE_CACHE_KEY_FILE`, else random per process): a value anyone else wrote is a miss
and is deleted. redis also gets `requirepass` from `REDIS_PASSWORD_FILE`.

**The engine (artifact 7).** `SEARCH_ENGINE` names the adapter (`searxng`, `fake`),
`SEARCH_ENGINE_URL` its address (`PROVIDER_PRIORITY` / `SEARXNG_URL` still read).
`tests/adapter_contract.py` is the one contract every adapter passes; the docker round
trip runs it against the fake engine and the real SearXNG image.

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
pip install -e ".[dev]"
pytest                       # unit + gate tests (in-process fakes; no network beyond 127.0.0.1)
pytest -m integration        # requires the stack up (GATEWAY_E2E_URL/API_KEY)
ruff check src tests
# the docker round trip (internal test networks in 10.243.70.0/24; never ai-stack_*):
python tests/docker/run_gate_e2e.py --gate-image private-search-gateway:wt-<id> \
    --intake-image openbrain-intake:wt-<id> --model-dir <verified classifier dir> --out <dir>
```

The gate's test doubles are in `tests/fakes.py` (the internet behind a forward proxy,
a DoH resolver, a simplified intake) and `tests/fake_engine.py` (the fake engine).

## Roadmap (documented, not built)

1. **Paid-tier providers** (Kagi, Brave, Mojeek): implement `SearchProvider`
   (stubs in `providers/_stubs.py`), add Redis monthly quota buckets, place
   above `searxng` in `PROVIDER_PRIORITY`.
2. Rate limits per caller key (the keys themselves exist: the gate).
3. Cross-provider result re-ranking (currently first-success-wins).
4. Optional Whoogle / LibreY fallbacks.
5. Opt-in Prometheus metrics + Grafana (localhost-bound only).
