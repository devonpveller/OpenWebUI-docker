# Search plane - findings

Things that are true about the `search` plane and its documentation but do not
belong in [`search/README.md`](../../search/README.md), which is an operator
document, not a defect log. Recorded here so they are neither pasted into the
README nor lost.

Each entry says what was checked and when. **Where an entry says what a script
or config does, it was checked against that code path, not against the comment
above it** - a claim in this file is held to the same standard as one in the
README, because the next item reads this file and acts on it. Where something
is an observation rather than a reading of the source, the entry says so.

Nothing here has been fixed - fixing any of it is a separate item.

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
`SEARXNG_SECRET_KEY` therefore does **not** fail the `up`: compose interpolates
it to an empty string and starts the container with `SEARXNG_SECRET=`.
`.env.example` ships a placeholder value, so a fresh checkout that never edits
it starts in that state silently.

Scope of what was checked (the compose file, `.env.example` and
`search-gateway/searxng/settings.yml`, 2026-08-28): the absence of the guard,
and that `settings.yml` carries
`secret_key: "placeholder-overridden-by-SEARXNG_SECRET-env"`. What SearXNG
itself does with an empty `SEARXNG_SECRET` - fall back to that placeholder, or
run with no secret at all - was **not** verified: that behaviour lives inside
the image, not in this tree. Verify it before acting on this entry.

Symptom if it bites: SearXNG behaving oddly right after an `.env` edit, with no
compose error.

## 4. `SEARCH_NET_SUBNET` / `FIREWALL_OUTBOUND_SUBNETS` does not govern in-plane traffic

gluetun auto-detects the subnets of the networks it is attached to and keeps
them reachable; its own startup log says so (`[routing] local ipnet found: ...`).
`searxng` and `gateway` share `search-net` with `vpn`, so they reach `vpn:8888`
whatever this variable says. What the variable actually does is add a route plus
a firewall allowance for subnets gluetun is **not** attached to - a client
elsewhere on the LAN or on another docker network.

Provenance, so the next reader knows what this rests on:

- **Read from the compose file (2026-08-28):** `vpn` sets
  `FIREWALL_OUTBOUND_SUBNETS=${SEARCH_NET_SUBNET:-172.16.0.0/12}`, and `vpn`,
  `searxng` and `gateway` all sit on `search-net`.
- **Observed live on 2026-08-28**, during the previous pass over this README and
  not re-run since: `search_search-net` was `192.168.192.0/20` while
  `FIREWALL_OUTBOUND_SUBNETS` carried the `172.16.0.0/12` default, and the plane
  served queries normally - so the shipped default is inert for this plane.
- **Not verified from source:** gluetun's auto-detection is a third-party binary,
  not code in this tree. The `[routing] local ipnet found` line is quoted from
  its startup log, not read from its source.

Consequence worth remembering: it is a tempting variable to chase during an
incident and it is almost never the cause. Change it only when adding an
off-network client.

## 5. `/readyz` does not check the tunnel directly

Read end to end, the readiness chain is:

- `routes/health.py` L26-28: `ready = redis_ok and provider_ok`.
- `rotation.py` L157-158: `redis_ok()` is `self._cache.ping()`.
- `rotation.py` L160-164: `any_provider_healthy()` returns True on the first
  provider whose `health()` returns True.
- `providers/searxng.py` L69-77: that provider's `health()` issues a live
  `GET {base}/search?q=healthcheck&format=json` and returns `status_code == 200`.

So `/readyz` proves redis answers and SearXNG answered one query with HTTP 200.
It does **not** probe the tunnel, and the 200 is a transport-level check: what a
200 implies about engines actually having answered is SearXNG's behaviour, inside
the image, and was not verified here. Treat `/readyz` as "the gateway can talk to
redis and to SearXNG", not as "the egress path is healthy".

This matters because documentation has described `/readyz` as walking
"vpn + searxng + redis" - stronger than what the code does. `search/README.md`
now states the chain instead of the inference.

Checked 2026-08-28 by reading each of the four functions above in full.

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

## 7. Where the anchor precondition is described loosely

Found 2026-08-28 while testing the README rewrite - an earlier draft of
`search/README.md` said "any `stack.ps1 up`" creates `ai-stack_default`, which
is false and would have sent a first-time operator into
`network ai-stack_default declared as external, but could not be found`.
`Resolve-Planes` in `scripts/stack/stack.ps1` returns only the named plane's
registry row and the `up` branch runs that one project; only `up` with no plane
(the whole ordered registry, anchor first) or `up anchor` creates the networks.
The README now says so. One other place still describes it loosely:

- `docker-compose.yml` (the root anchor), header comment: "run it once before
  any plane on a cold host (scripts/stack/stack.ps1 does this for you)". True
  only for `stack.ps1 up` with no plane, or `up anchor`.
*(An earlier version of this section carried a second bullet, about whether
`scripts/recovery/emergency-recovery.ps1` creates the anchor networks. It was
written twice and wrong twice - each time from part of that script rather than
all of it. It is dropped rather than restated a third time: describing another
script's control flow is outside what this item can responsibly verify, and a
confident wrong entry costs whoever acts on it more than no entry at all. If you
need to know what recovery does about the networks, read `emergency-recovery.ps1`
end to end - do not rely on a summary in a search-plane note.)*

## 8. The stack-map's cold-start list draws the search plane as a chain

`.claude/skills/stack-map/references/workspace-stacks.md` renders the plane's
internal order as "Search: `vpn` -> `redis` -> `searxng` -> `gateway`", which
reads as a chain. `redis` actually depends on nothing; `searxng` waits on `vpn`
and `redis` (both healthy) and `gateway` waits on `searxng` (started) and
`redis` (healthy). `search/README.md` now draws the real shape, so the two
documents no longer match - if you correct one, correct the other. Checked
2026-08-28 against `search/docker-compose.yml`.
