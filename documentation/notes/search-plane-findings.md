# Search plane - findings

Things that are true about the `search` plane and its documentation but do not
belong in [`search/README.md`](../../search/README.md), which is an operator
document, not a defect log. Recorded here so they are neither pasted into the
README nor lost.

Each entry says what was checked and when. Nothing here has been fixed - fixing
any of it is a separate item.

## 1. `search-gateway/README.md` is partly stale

Checked 2026-08-28 against `search/docker-compose.yml` and the files it names.
Where the two disagree, the compose file is the authority.

| Claim in `search-gateway/README.md` | Reality |
|---|---|
| "The gateway is part of the **main ai-stack compose** - no separate stack" (line 13) | It has been compose project `search` since 2026-08-21 (CLEANUP-PLAN K.3). |
| "From the ai-stack root: `docker compose up -d --build`" (line 20) | The root project is a pure network anchor with zero services since K.5b; that command builds nothing. The plane needs `-f search/docker-compose.yml --env-file .env`. |
| A `tor` service, "Tor image" (`osminogin/tor-simple`, `TOR_IMAGE`), "Tor latency / budgets" (lines 75-85) | `tor` was retired 2026-08-21. All egress is the Mullvad tunnel. |
| "MCP -> OpenAPI \| `:8001` (mcpo)" in the surfaces table (line 37), and "Ports: gateway `127.0.0.1:8085`, mcpo `127.0.0.1:8001`" (line 89) | `search-mcpo` was retired 2026-08-20. The only published port in the plane is `127.0.0.1:8085`. |
| "Default-deny Tor-hostile engines - `searxng/settings.yml` disables Google/Bing/Yandex" (line 66) | Inverted on 2026-06-14 with the Mullvad swap. `search-gateway/searxng/settings.yml` now enables google, bing and mojeek and disables duckduckgo, brave, startpage, qwant and yandex, because the privacy trio captchas datacenter IP ranges. |
| Link: "Integration decisions -> `../documentation/implementation-guide/web-search/integration-plan-private-search-gateway.md`" (line 9) | Dead. The file was moved to `documentation/archive/implementation-guide/web-search/integration-plan-private-search-gateway.md`. |

## 2. Stale Tor references in files that are otherwise current

- `search-gateway/searxng/settings.yml` still carries Tor-era comments that
  contradict the configuration below them: the header says "Captcha/Tor-hostile
  engines (Google/Bing/Yandex) are disabled by default" while the `engines:`
  block enables google and bing; the `outgoing.request_timeout` comment explains
  itself in terms of "Tor circuit setup"; and the proxy comment ends with
  "Page-fetch still uses Tor (see compose)", which has not been true since
  2026-08-21. The values themselves are correct - only the prose is stale.
- `OB1/docker/docker-compose.yml` (at the pinned submodule SHA `0502f8e`)
  describes `search-gw-net` as reaching "the private SearXNG gateway
  (search-over-Tor)" (line 520) and repeats "the gateway still routes every
  query via Tor" in the network definition (lines 1139-1143). The wiring is
  right; the description is not.

Checked 2026-08-28.

## 3. `SEARXNG_SECRET` has no fail-loud guard

`search/docker-compose.yml` guards the Mullvad key
(`${MULLVAD_WG_PRIVATE_KEY:?...}`) but sets
`SEARXNG_SECRET=${SEARXNG_SECRET_KEY}` unguarded. A missing or empty
`SEARXNG_SECRET_KEY` therefore starts SearXNG with an *empty* session secret
instead of failing the `up`. `.env.example` ships a placeholder, so a fresh
checkout that never edits it starts in that state silently.

Symptom if it bites: SearXNG behaving oddly right after an `.env` edit, with no
compose error. Checked 2026-08-28.

## 4. `SEARCH_NET_SUBNET` / `FIREWALL_OUTBOUND_SUBNETS` does not govern in-plane traffic

gluetun auto-detects the subnets of the networks it is attached to and keeps
them reachable; its own startup log says so (`[routing] local ipnet found: ...`).
`searxng` and `gateway` share `search-net` with `vpn`, so they reach `vpn:8888`
whatever this variable says. What the variable actually does is add a route plus
a firewall allowance for subnets gluetun is **not** attached to - a client
elsewhere on the LAN or on another docker network.

Observed 2026-08-28 during the previous pass over this README: the live
`search_search-net` was `192.168.192.0/20` while `FIREWALL_OUTBOUND_SUBNETS`
carried the `172.16.0.0/12` default, and the plane served queries normally. So
the shipped default is effectively inert for this plane.

Consequence worth remembering: it is a tempting variable to chase during an
incident and it is almost never the cause. Change it only when adding an
off-network client.

## 5. `/readyz` does not check the tunnel directly

`search-gateway/gateway/src/gateway/routes/health.py` computes readiness as
`redis_ok and any_provider_healthy()`. The tunnel is covered only transitively -
a provider is healthy because a SearXNG query succeeded, and SearXNG can only
reach an engine through `vpn:8888`. A failure mode where redis and SearXNG are
both fine but the tunnel is degraded in some way that still returns *some*
result would read as ready. Nothing observed; noted because documentation
(including the previous version of `search/README.md`) has described `/readyz`
as walking "vpn + searxng + redis", which is stronger than what the code does.

Checked 2026-08-28.

## 6. In flight elsewhere: `gateway` may stop inheriting the whole root `.env`

Not a defect - a heads-up for whoever reads this next. On 2026-08-28 the
operator's main checkout carried an **uncommitted** edit to
`search/docker-compose.yml` removing `env_file: ../.env` from the `gateway`
service (with a comment explaining that it injected all 111 root variables -
Cloudflare, Authelia, Mullvad, Tailscale, Mattermost tokens - into a service
that uses none of them, and a new `scripts/checks/check-env-file-scope.ps1`).

It is not on the work line, so `search/README.md` documents the committed state:
`gateway` inherits the root `.env` wholesale. When that edit lands, the
"Environment" section of the README needs one paragraph updated - the
application's own keys (`GATEWAY_API_KEY`, `PROVIDER_PRIORITY`, the `CIRCUIT_*`
values, `LOG_QUERIES`) will then have to be named explicitly in the compose file
rather than arriving through `env_file`.
