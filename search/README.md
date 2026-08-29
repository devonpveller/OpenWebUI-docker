# search - the Private Search Gateway plane

Compose project **`search`** (`name: search` in [`docker-compose.yml`](docker-compose.yml)).
Four containers give the rest of the stack a web-search and page-fetch surface
whose egress leaves the host only through a Mullvad WireGuard tunnel. It was
split out of the root `ai-stack` project on 2026-08-21 (CLEANUP-PLAN Part K.3).

Drive it with the workspace script:

```powershell
.\scripts\stack\stack.ps1 up anchor    # once per host - creates the shared networks
.\scripts\stack\stack.ps1 up search
```

## Which document owns what

| Question | Read |
|---|---|
| **How do I run, stop, wire or debug the four containers?** networks, ports, volumes, dependency order | **this file** |
| What the gateway *application* is: HTTP endpoints, auth model, provider interface, privacy invariants, Python dev + tests | [`../search-gateway/README.md`](../search-gateway/README.md) |
| Why the plane is built this way | [`guide-Private-Search-Gateway.md`](../documentation/implementation-guide/web-search/guide-Private-Search-Gateway.md) |
| Where this plane sits among all the other planes | [stack-map](../.claude/skills/stack-map/references/workspace-stacks.md) |
| Adding, removing or moving a container here | [`SERVICE-LIFECYCLE.md`](../documentation/runbooks/SERVICE-LIFECYCLE.md) |

`search-gateway/` owns the **code**; this file owns the **plane**. Endpoint
tables and provider config belong there - link to them rather than copying them
here.

## The four services

| Service | Container | What it does |
|---|---|---|
| `vpn` | `search-vpn` | gluetun (`${VPN_IMAGE:-qmcgaw/gluetun:latest}`) holding a Mullvad **WireGuard** tunnel with a kill-switch, and exposing an **HTTP forward proxy on `:8888`**. This is the plane's only route to the internet, and it carries both engine queries (SearXNG) and page fetches (OB1). Needs `NET_ADMIN` and `/dev/net/tun`. |
| `redis` | `search-redis` | Cache for SearXNG (`db0`, including its engine suspensions) and for the gateway (`db1` - response cache and circuit-breaker state). Runs with `--save "" --appendonly no`: nothing it holds survives a restart, by design. |
| `searxng` | `searxng` | The metasearch engine. Its config comes from the repo tree, not the image default: [`../search-gateway/searxng`](../search-gateway/searxng) is bind-mounted at `/etc/searxng`, and that `settings.yml` is what points every engine request at `http://vpn:8888`. |
| `gateway` | `search-gateway` | The plane's front door: the Python app in `../search-gateway/gateway`, built here as `private-search-gateway:local`. Normalises engines behind one API and adds auth, caching and a circuit breaker. |

The compose file also carries comment tombstones for two services that no
longer exist - `tor` (retired 2026-08-21, superseded by the Mullvad tunnel) and
`mcpo` (retired 2026-08-20, no consumer). Leave the comments in place; they are
the reason nobody re-adds them.

## Networks and ports

```
  ai-stack_default (external, internet-capable)
    |-- vpn ......... the tunnel out
    +-- gateway ..... consumers resolve it by this name
              |
  search-net (internal: true, native to this project)
    |-- vpn    |-- redis    |-- searxng    +-- gateway
```

- **`search-net` is `internal: true`.** Nothing on it has a default route, so
  `searxng` and `redis` cannot reach the internet even if misconfigured. The
  only way out is the kill-switched `vpn` proxy. That is what makes the privacy
  property structural rather than a matter of configuration.
- **`default` is `external: true`, `name: ai-stack_default`** - the root
  anchor's shared bridge. `vpn` and `gateway` attach to it so their names keep
  resolving for consumers in other compose projects. **The anchor must exist
  before this plane starts**, or compose fails with `network ai-stack_default
  declared as external, but could not be found`. Create it with `docker compose
  up -d` at the repo root, or `.\scripts\stack\stack.ps1 up anchor`, or
  `.\scripts\stack\stack.ps1 up` with no plane (which walks the whole
  workspace in order, anchor first). Naming a single plane -
  `stack.ps1 up search` - starts only that plane and creates nothing.

One host port is published, and the omissions are deliberate:

| Service | Host port | Why |
|---|---|---|
| `gateway` | `127.0.0.1:8085:8080` - loopback only | The host-tools surface. On `0.0.0.0` it would put an API-keyed search proxy on the LAN. In-stack consumers use `http://gateway:8080` instead. |
| `vpn` proxy `:8888` | none | An open HTTP forward proxy into a VPN tunnel should not be reachable from the host or the LAN. Consumers use `http://vpn:8888` on `ai-stack_default`. |
| `searxng` `:8080` | none | Only `gateway` should talk to it. It is not on `ai-stack_default` either, which keeps the gateway's unauthenticated SearXNG-compat route the only keyless path in. |
| `redis` `:6379` | none | Unauthenticated redis. Internal-only is the whole of its security model. |

## Bring it up and down

Every command needs the root `.env`: the plane interpolates from it, and
`MULLVAD_WG_PRIVATE_KEY` carries a `${...:?}` guard that aborts the `up` if it
is missing. `stack.ps1` sets its own working directory and adds
`--env-file .env` for every plane project, this one included, so it can be run
from anywhere; **by hand, run from the repo root** so the relative
`--env-file .env` resolves.

```powershell
.\scripts\stack\stack.ps1 up search        # this plane only - see the anchor note above
.\scripts\stack\stack.ps1 down search      # compose down for this project
.\scripts\stack\stack.ps1 restart search   # compose restart - see the caveat below
.\scripts\stack\stack.ps1 health           # whole-workspace probe sweep, not plane-scoped;
                                           # includes GET 127.0.0.1:8085/healthz
```

Each of those is a thin wrapper: `up search` runs
`docker compose -f search\docker-compose.yml --env-file .env up -d` and nothing
else. `restart` maps to `docker compose restart`, which restarts the existing
containers **without recreating them** - a changed `.env` value or compose
setting needs an `up -d`, not a restart. `health` takes no plane argument, runs
every probe in the workspace, and exits with the number of failures.

By hand:

```powershell
docker compose -f search/docker-compose.yml --env-file .env up -d
docker compose -f search/docker-compose.yml --env-file .env down
docker compose -f search/docker-compose.yml --env-file .env config   # render/validate
```

`gateway` is the only image built here (`private-search-gateway:local`); the
other three are pulled. **`up -d` alone does not pick up source changes** under
`../search-gateway/gateway` - build it explicitly:

```powershell
docker compose -f search/docker-compose.yml --env-file .env build gateway
docker compose -f search/docker-compose.yml --env-file .env up -d gateway
```

Retagging `private-search-gateway:local` is a deploy, not a test. Under the
[merge protocol](../documentation/implementation-guide/multi-agent-concurrency/MERGE-PROTOCOL.md)
that is a gated step: test builds tag `:wt-<id>` and leave `:local` alone.

Relative paths inside the compose file (`../.env`, `../search-gateway/gateway`,
`../search-gateway/searxng`) resolve against the **file**, not your shell's
working directory - so `-f search/docker-compose.yml` works from anywhere in the
repo, but a copy of the file somewhere else will not.

### Is it up, and is it working?

Two different questions, two endpoints:

```bash
curl -fsS http://127.0.0.1:8085/healthz   # liveness: the gateway process is serving
curl -fsS http://127.0.0.1:8085/readyz    # readiness: redis answers AND a provider answers
```

`/healthz` returns 200 whenever the event loop is alive - it is the compose
healthcheck, and what the watchdog restarts on. `/readyz` returns 503 until
redis responds and at least one provider is healthy, which means a real SearXNG
query got through and therefore that the tunnel is up. Expect a gap between the
two after a cold start: SearXNG has a 90 s `start_period` because the WireGuard
handshake has to complete first, and `gateway` waits on it with
`condition: service_started`, not `service_healthy`.

### Environment

Read straight from the root `.env` - see the "Private Search Gateway" and
"Search plane" blocks in [`../.env.example`](../.env.example):

| Key | Notes |
|---|---|
| `MULLVAD_WG_PRIVATE_KEY` | Required; guarded, so a missing value fails the `up` loudly. |
| `MULLVAD_WG_ADDRESSES` | The tunnel's own addresses. **IPv4-only** unless the Docker host has IPv6 - gluetun rejects v6 addresses. |
| `MULLVAD_COUNTRIES` | Exit-country selection; defaults to `Netherlands`. |
| `SEARXNG_SECRET_KEY` | SearXNG's session secret. Required in practice - set it before starting the plane. |
| `SEARCH_NET_SUBNET` | Passed to gluetun as `FIREWALL_OUTBOUND_SUBNETS`. It opens the kill-switch firewall to subnets gluetun is *not* attached to; the in-plane services never need it, because they share `search-net` with `vpn`. |
| `VPN_IMAGE`, `SEARXNG_IMAGE`, `SEARCH_REDIS_IMAGE` | Pin the three pulled images. |

`gateway` additionally inherits the whole root `.env` through
`env_file: ../.env` - notably `GATEWAY_API_KEY`, `PROVIDER_PRIORITY`,
`CACHE_TTL_SECONDS`, the `CIRCUIT_*` values and `LOG_QUERIES`. Those are the
application's knobs, and
[`../search-gateway/README.md`](../search-gateway/README.md) explains them.

None of these values belong in a paste. `docker compose config` renders them
interpolated in plaintext, so grep the section you need rather than printing the
whole render.

## State

**The plane holds no persistent state, deliberately.** There is no top-level
`volumes:` block, no named volume, and no backup sidecar under `backups/`.

- Everything in `redis` is cache plus circuit-breaker and suspension
  bookkeeping. Losing it on restart is the intended behaviour.
- The one bind mount is `../search-gateway/searxng:/etc/searxng:rw`. SearXNG
  config is served straight out of your git working tree, and the mount is
  **`rw`**, so the container can write into your checkout: unexplained
  `git status` noise under `search-gateway/searxng/` is the container, not you.
- Consequently `docker compose -f search/docker-compose.yml --env-file .env
  down -v` destroys nothing that matters here. Do not carry that habit over to
  `memory` or `open-brain`, where the same command is destructive.

Images are not auto-updated: every service carries
`com.centurylinklabs.watchtower.enable=false`, kept as recorded intent after
watchtower itself was retired. Update deliberately, per
[`UPDATE-MANAGEMENT.md`](../documentation/runbooks/UPDATE-MANAGEMENT.md).

## Startup order

Inside the plane one `up -d` is enough; it is health-gated and self-ordering.
`vpn` and `redis` come up in parallel, and the gates are:

```
  vpn (healthy) ---+
                   +--> searxng (started) --> gateway
  redis (healthy) -+---------------------------^
```

`searxng` waits for `vpn` **and** `redis` to be healthy; `gateway` waits for
`searxng` to have *started* and for `redis` to be healthy. `redis` itself waits
for nothing.

Across the workspace it is step 7 of the cold-start order in the
[stack-map](../.claude/skills/stack-map/references/workspace-stacks.md):

- **After the root anchor**, which owns `ai-stack_default`.
- **Independent of `inference`.** Nothing here calls an LLM; `stack.ps1` starts
  the plane after `memory` for a stable order, not because of a dependency.
- **Before OB1**, which depends on it in the other direction - and therefore
  torn down after OB1.
- **Underneath OWUI web search.** `openwebui` starts fine without this plane,
  but every web search fails until `gateway` resolves.
- Outside the portal lifecycle. `scripts/recovery/emergency-recovery.ps1` drives
  it as project `search`, waiting up to 150 s for `search-gateway`.

## Who depends on this plane

`vpn` and `gateway` are short, generic DNS names on a shared bridge, and two
other compose projects are wired to them. Renaming either service breaks those
consumers as a DNS failure rather than a config error, so change them only
together with their consumers:

| Consumer | Wiring |
|---|---|
| `openwebui` (frontend plane) | `SEARXNG_QUERY_URL` - defaults to `http://gateway:8080/search?q=<query>` |
| `openbrain-research` (OB1) | `SEARCH_API_BASE` -> `http://gateway:8080`, `FETCH_PROXY_URL` -> `http://vpn:8888` (both compose defaults) |
| `openbrain-grounding-backfiller`, `openbrain-podcast` (OB1) | `FETCH_PROXY_URL` -> `http://vpn:8888` for page fetches |
| Host tools, probes, `stack.ps1 health` | `http://127.0.0.1:8085` |

OB1's compose calls this network `search-gw-net`; that is `ai-stack_default`
under another name.

## When something looks wrong

| Symptom | Where to look |
|---|---|
| `up` aborts complaining about `MULLVAD_WG_PRIVATE_KEY` | The guard doing its job: you ran without `--env-file .env`, or the key is unset. |
| `/healthz` green but `/readyz` 503 | The chain behind the gateway. Either the tunnel is still building (give SearXNG its 90 s), or redis or SearXNG is unhappy. The watchdog only restarts on `/healthz`, so a plane that is up but not working will not self-heal - somebody has to look. |
| Searches return fewer engines than usual, without errors | Everything leaves through one exit IP, so a heavy fan-out can get the whole plane captcha'd at once. SearXNG keeps its state, engine suspensions included, in redis `db0` (`SEARXNG_REDIS_URL`); clear it with `docker exec search-redis redis-cli -n 0 FLUSHDB`. |
| One engine in particular returns nothing | Engine enable/disable policy lives in [`../search-gateway/searxng/settings.yml`](../search-gateway/searxng/settings.yml), tuned for what actually answers a VPN exit IP. |
| Bursts are not being throttled | SearXNG's own limiter is off on purpose (`server.limiter: false`): it is public-instance bot protection and would throttle our own research fan-out. Burst control lives in the gateway's cache and circuit breaker. |
| Something outside the plane cannot reach `vpn:8888` | The kill-switch firewall. `SEARCH_NET_SUBNET` is the knob for clients on other networks; in-plane services never need it. |
| The whole plane needs bringing back after a crash | `scripts/recovery/emergency-recovery.ps1` restarts it in order along with the rest of the workspace. |
