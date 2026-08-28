# search — the Private Search Gateway compose plane

Compose project **`search`** (`name: search`), split out of the root `ai-stack`
project on 2026-08-21 by CLEANUP-PLAN Part **K.3**. Four services give the rest
of the stack a web-search and page-fetch surface whose egress leaves the host
only through a Mullvad WireGuard tunnel.

## Which document owns what

| Question | Read |
|---|---|
| **How do I run, stop, wire or debug the four containers?** networks, ports, volumes, dependency order, compose gotchas | **this file** |
| What the gateway *application* is: HTTP surfaces (`/v1/search`, the Tavily shim, the SearXNG-compat route), auth model, provider interface, privacy invariants, Python dev + tests, roadmap | [`../search-gateway/README.md`](../search-gateway/README.md) |
| Build spec | [`guide-Private-Search-Gateway.md`](../documentation/implementation-guide/web-search/guide-Private-Search-Gateway.md) |
| Where this plane sits among all the other planes | [stack-map §1d](../.claude/skills/stack-map/references/workspace-stacks.md) |
| Adding/removing/moving a container here | [`SERVICE-LIFECYCLE.md`](../documentation/runbooks/SERVICE-LIFECYCLE.md) |

Roughly: `search-gateway/` owns the **code**, this file owns the **plane**. Do
not duplicate endpoint tables or provider config here — link instead.

> **`search-gateway/README.md` is partly stale, and where the two disagree
> `search/docker-compose.yml` is the authority.** The file says: (a) the gateway
> is **not** "part of the main ai-stack compose" any more, and `docker compose
> up -d --build` from the repo root builds nothing — the root project is a pure
> network anchor with zero services; (b) there is **no `tor` service** (retired
> 2026-08-21) and no `mcpo` / `:8001` surface (`search-mcpo` retired
> 2026-08-20), so that README's "Tor image", "Tor latency" and "MCP → OpenAPI"
> notes describe containers that do not exist; (c) its privacy table claims
> `settings.yml` "disables Google/Bing/Yandex", but
> `../search-gateway/searxng/settings.yml` inverted that on 2026-06-14 — behind
> a Mullvad exit it *enables* google/bing/mojeek and disables
> duckduckgo/brave/startpage, which captcha datacenter IPs. Its
> "Integration decisions" link is also dead —
> `integration-plan-private-search-gateway.md` is not in the tree, so it is not
> linked from here.

## Services (all four defined in `docker-compose.yml`)

| Service | Container | What it is for |
|---|---|---|
| `vpn` | `search-vpn` | gluetun (`${VPN_IMAGE:-qmcgaw/gluetun:latest}`) holding a Mullvad **WireGuard** tunnel with a kill-switch, and exposing an **HTTP forward proxy on `:8888`**. This is the plane's *only* route to the internet, and since tor was retired it carries **both** engine queries (SearXNG) **and** page fetches (OB1's `FETCH_PROXY_URL=http://vpn:8888`). `NET_ADMIN` + `/dev/net/tun` are required for the tunnel. |
| `redis` | `search-redis` | Cache for SearXNG (`db0` — including *its* engine suspensions) and for the gateway (`db1`, via `REDIS_URL=redis://redis:6379/1` — response cache + circuit-breaker state; the gateway has no suspension logic of its own). Started with `--save "" --appendonly no`: **deliberately non-persistent**. |
| `searxng` | `searxng` | The metasearch engine. Its config comes from the repo tree, not an image default: `../search-gateway/searxng` is bind-mounted at `/etc/searxng`. All outbound engine traffic is proxied to `http://vpn:8888` by that `settings.yml`. |
| `gateway` | `search-gateway` | The plane's front door — the Python app in `../search-gateway/gateway`, built here as `private-search-gateway:local`. Normalises engines behind one API and adds auth, caching and a circuit breaker. Endpoints: see [`../search-gateway/README.md`](../search-gateway/README.md). |

Two more services appear in the file only as tombstones — **`tor`** (retired
2026-08-21: its exits are blocked by most large sites and it carried no traffic
after the Mullvad flip) and **`mcpo`** (retired 2026-08-20: keyless and
unreachable, it sat only on the internal net with no consumer). Leave those
comments in place; they are the reason nobody re-adds them.

## Networks and ports

```
                ai-stack_default  (external, internet-capable)
                ├── vpn ───────── the tunnel out
                └── gateway ───── consumers resolve it here
                         │
   search-net (internal: true, native to this project)
   ├── vpn     ├── redis     ├── searxng     └── gateway
```

- **`search-net` is `internal: true`** and native to this project. Nothing on it
  has a default route. That is what makes the privacy claim structural rather
  than best-effort: `searxng` and `redis` *cannot* reach the internet even if
  misconfigured — the only way out is the kill-switched `vpn` proxy.
- **`default` is `external: true`, `name: ai-stack_default`** — the root anchor's
  shared bridge. `vpn` and `gateway` attach to it so their bare DNS names keep
  resolving for consumers in *other* compose projects. The anchor must therefore
  exist before this plane comes up (`docker compose up -d` at the repo root, or
  any `stack.ps1 up`); otherwise compose fails to find the network.

**Deliberately not exposed:**

| Thing | Posture | Why |
|---|---|---|
| `gateway` | `127.0.0.1:8085:8080` — **loopback only**, never `0.0.0.0` | It is the host-tools surface. Publishing it on the LAN would put an API-keyed search proxy on the network; in-stack consumers reach it by DNS (`http://gateway:8080`) and never need the host port. |
| `vpn` proxy `:8888` | **no host port at all** | An open HTTP forward proxy into a VPN tunnel is exactly what you do not want reachable from the host or LAN. Consumers reach it as `http://vpn:8888` on `ai-stack_default`. |
| `searxng` (`:8080`) | **no host port**, `search-net` only | Only `gateway` should talk to it. It is not on `ai-stack_default` either, which is what keeps the gateway's unauthenticated SearXNG-compat route the only keyless path in. |
| `redis` (`:6379`) | **no host port**, `search-net` only | Unauthenticated redis. Internal-only is the whole of its security model. |

Consumers, verified in their own compose files:

- `openwebui` (frontend plane) — `SEARXNG_QUERY_URL=http://gateway:8080/search?q=<query>`, resolved on `ai-stack_default`.
- `openbrain-research` (OB1) — `SEARCH_API_BASE=http://gateway:8080` and `FETCH_PROXY_URL=http://vpn:8888`.
- `openbrain-grounding-backfiller`, `openbrain-podcast` (OB1) — `http://vpn:8888` for page fetches.
- OB1 calls this network `search-gw-net` in its own compose; same network, different alias.
- Host tools, probes and `stack.ps1 health` — `http://127.0.0.1:8085`.

## Volumes and state

**This plane holds no live state, and that is on purpose.** There is no
top-level `volumes:` block in the compose file, no named volume, and no backup
sidecar (`backups/` has no `search*` directory — every other stateful plane has
one).

- `redis` runs `--save "" --appendonly no`: everything it holds is cache and
  circuit-breaker/suspension bookkeeping. Losing it on restart is the intended
  behaviour, not data loss — `FLUSHDB` on `db0` is a documented way to clear
  engine suspensions.
- The one bind mount is **`../search-gateway/searxng:/etc/searxng:rw`** — SearXNG
  config served straight out of the git working tree. Note the **`rw`**: the
  container can write into your checkout, so unexplained `git status` noise under
  `search-gateway/searxng/` is the container, not you.
- Consequence for recovery: `docker compose -f search/docker-compose.yml down -v`
  destroys nothing that matters here. The same command on `memory` or
  `open-brain` would be catastrophic — do not generalise this plane's safety to
  those.

## Bring it up and down

Always **from the repo root**, and always with `--env-file .env` — the plane
interpolates from the single root `.env` and carries a fail-loud guard
(`${MULLVAD_WG_PRIVATE_KEY:?}`) that aborts a bare `up`:

```powershell
# preferred: the workspace driver, which knows the plane order
.\scripts\stack\stack.ps1 up search
.\scripts\stack\stack.ps1 down search
.\scripts\stack\stack.ps1 restart search
.\scripts\stack\stack.ps1 health            # includes GET 127.0.0.1:8085/healthz

# equivalent by hand
docker compose -f search/docker-compose.yml --env-file .env up -d
docker compose -f search/docker-compose.yml --env-file .env down
docker compose -f search/docker-compose.yml --env-file .env config    # render/validate
docker compose -f search/docker-compose.yml --env-file .env build gateway
```

`gateway` is the only built image (`private-search-gateway:local`). A plain
`up -d` will **not** pick up source changes in `../search-gateway/gateway` — pass
`--build`, or build it explicitly. The other three images are pulled and pinnable
from `.env` (`VPN_IMAGE`, `SEARXNG_IMAGE`, `SEARCH_REDIS_IMAGE`).

Health by hand — the plane takes a minute to be *ready*, not just *up*, because
the WireGuard handshake gates SearXNG's 90 s `start_period`:

```bash
curl -fsS http://127.0.0.1:8085/healthz    # process liveness only
curl -fsS http://127.0.0.1:8085/readyz     # the whole chain: vpn + searxng + redis
```

`.env` keys this plane reads: `MULLVAD_WG_PRIVATE_KEY` (required),
`MULLVAD_WG_ADDRESSES`, `MULLVAD_COUNTRIES`, `SEARCH_NET_SUBNET`,
`SEARXNG_SECRET_KEY`, `VPN_IMAGE`, `SEARXNG_IMAGE`, `SEARCH_REDIS_IMAGE` — plus
everything `gateway` inherits wholesale through `env_file: ../.env` (notably
`GATEWAY_API_KEY`, `PROVIDER_PRIORITY`, `CACHE_TTL_SECONDS`, `LOG_QUERIES`). See
the "Private Search Gateway" and "Search plane" blocks in `../.env.example`.

## Where it sits in the dependency order

Internally the plane is health-gated and self-ordering, so one `up -d` is enough:
**`vpn` (healthy) → `redis` (healthy) → `searxng` → `gateway`**. Note that
`gateway` waits on `searxng` with `condition: service_started`, not
`service_healthy` — it comes up while SearXNG is still warming, which is exactly
why `/healthz` can be green while `/readyz` is not.

Across the workspace it is **step 7** of the cold-start order
([stack-map](../.claude/skills/stack-map/references/workspace-stacks.md)):

- **After** the root network anchor — it needs `ai-stack_default` to exist.
- **Independent of `inference`.** Nothing here calls an LLM; `stack.ps1` starts it
  after `memory` for a stable order, not because of a dependency.
- **Before OB1**, which *is* a hard dependency in the other direction:
  `openbrain-research`, `-podcast` and `-grounding-backfiller` resolve `gateway`
  and `vpn` by name. Bring this plane up before OB1, and tear it down after OB1.
- **Underneath OWUI web search** — `openwebui` starts fine without it, but every
  web search fails until `gateway` resolves.
- Outside the portal lifecycle. `scripts/recovery/emergency-recovery.ps1` drives
  it as project `search` with a 150 s gate on `search-gateway`.

## Gotchas (each verified against the compose file or a config it names)

1. **A bare `docker compose -f search/docker-compose.yml up -d` fails**, by
   design: `MULLVAD_WG_PRIVATE_KEY` carries a `${...:?}` guard and there is no
   `--env-file`. That is the guard working, not a broken file. Add
   `--env-file .env`.
2. **`SEARXNG_SECRET` has no such guard.** `SEARXNG_SECRET=${SEARXNG_SECRET_KEY}`
   is unguarded, so a missing key starts SearXNG with an *empty* secret instead
   of failing loudly. If SearXNG behaves oddly right after an `.env` edit, check
   that first.
3. **Relative paths resolve against the compose file, not your cwd.** `../.env`,
   `../search-gateway/gateway` and `../search-gateway/searxng` all mean the repo
   root. Running from elsewhere with `-f` is fine; copying the file elsewhere is
   not.
4. **`WIREGUARD_ADDRESSES` must be IPv4-only** unless the Docker host has IPv6 —
   gluetun rejects v6 addresses. The `.env.example` default is empty.
5. **`SEARCH_NET_SUBNET` / `FIREWALL_OUTBOUND_SUBNETS` does *not* govern this
   plane's own traffic — do not chase it during an incident.** gluetun
   auto-detects the subnets of the networks it is *attached to* and keeps them
   reachable; its own startup log says so
   (`[routing] local ipnet found: 192.168.192.0/20`). `searxng` and `gateway`
   share `search-net` with `vpn`, so they reach `vpn:8888` whatever this variable
   says. What it actually does is add a route plus a firewall allowance for
   subnets gluetun is **not** attached to — a client elsewhere on the LAN or on
   another docker network. Live proof the two are unrelated (2026-08-28):
   `search_search-net` is `192.168.192.0/20` while `FIREWALL_OUTBOUND_SUBNETS`
   is `172.16.0.0/12`, and the plane serves queries normally. The
   `172.16.0.0/12` default is effectively inert here; change it only if you add
   an off-network client, not to fix a broken query path.
6. **`/healthz` is not `/readyz`.** `/healthz` is the compose healthcheck and only
   proves the event loop is alive; `/readyz` walks vpn + searxng + redis. The
   watchdog treats `/readyz` as informational and never restarts on it — so a
   plane that is up but not *working* will not self-heal, and somebody has to
   look.
7. **All four services carry `com.centurylinklabs.watchtower.enable=false`.**
   Privacy infrastructure is not silently auto-updated. Watchtower itself was
   retired on 2026-08-20; the labels stay as recorded intent, and updates are
   manual per [`UPDATE-MANAGEMENT.md`](../documentation/runbooks/UPDATE-MANAGEMENT.md).
8. **`vpn` and `gateway` are very generic DNS names on a shared bridge.** They are
   the contract OB1 and OWUI are wired to. Renaming either service breaks
   consumers in two other compose projects as a DNS failure, not a config error —
   change them only together with those consumers.
9. **SearXNG's own rate limiter is off** (`server.limiter: false` in
   `settings.yml`). It is public-instance bot protection and would throttle our
   own deep-research fan-out. Burst control lives in the gateway (cache + circuit
   breaker) and in the research fan-out cap instead.
10. **Everything shares one exit IP.** Engine queries and page fetches leave via
    the same Mullvad tunnel, so a heavy fan-out can get the whole plane captcha'd
    at once. The symptom is SearXNG returning fewer engines rather than erroring.
    Clearing redis `db0` (`FLUSHDB`) resets engine suspensions.
11. **Rebuilding `gateway` retags `private-search-gateway:local`, which is a
    deploy.** Under the multi-agent merge protocol that is a gated step — test
    builds tag `:wt-<id>` and leave `:local` alone.
