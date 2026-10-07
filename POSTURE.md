# Posture: local-first, cloud-capable

This is the egress inventory for the whole stack: what can reach the internet, what
turns each part on, and how to re-derive the list yourself. The [README](README.md) links
here; nothing in this file is needed to run the quickstart.

**No component in this stack sends a prompt, a document or a memory to a model
provider by default.** Every model call goes to llama.cpp on your own machine through
the LiteLLM gateway, and that gateway is attached to two networks that are both
`internal: true` - `llm-net`, declared that way by the anchor
[`docker-compose.yml`](docker-compose.yml), and `llm-backend-net`, declared that
way by [`inference/docker-compose.yml`](inference/docker-compose.yml). The
cloud-capable parts are present in the tree and inert: each is gated behind a
compose profile, a credential, or a script you run by hand. That is the
posture: keep the components, ship them off, and write down what turns each
one on.

That is not the same as "nothing reaches the internet". Three containers do,
from a fresh clone, and they are named at the end of this section.

**How this list was built, so it can be rebuilt and disagreed with.** Two
stages, because either alone gets it wrong:

1. **Render, then classify by network.** `docker compose config --format json`
   for every plane, every profile; a service is a *candidate* if any network it
   joins is non-internal. An `external: true` reference carries no internal
   flag in a plane's own render, so resolve those against the anchor:
   `ai-stack_llm-net` is `internal: true`, `ai-stack_app-net` and
   `ai-stack_default` are ordinary bridges.
2. **Read the candidate's source for an outbound call.** Being on a bridge is a
   *route*, not traffic. `llm-gateway-ui`, the search `gateway`, `openbrain-ext`
   and the portal's watchers are all on bridges and all make no call off this
   host; they are listed under "network-capable, no outbound call" below rather
   than in the table.

A grep for provider names finds stage 2 and misses stage 1 entirely - which is
how the first version of this section missed four services, every one of them
unprofiled. Where a sentence here and a compose file disagree, the compose file
wins.

## The table

| Cloud-capable component | Where it is defined | What it does when off | What turns it on | Where it egresses |
|---|---|---|---|---|
| LiteLLM cloud model group (`cloud-large`, `cloud-small`) | `inference/config/litellm/model_list/cloud.openrouter.yaml` | `config/litellm/assemble-config.py` drops every model entry whose `os.environ/VAR` reference is unset or empty, and logs the drop with its reason. The two models are not registered, so `/v1/models` does not list them and nothing else about the gateway changes. | `OPENROUTER_API_KEY` in `inference/.env`. It is blank in `inference/.env.example`. | **Nowhere.** See "Why a cloud key does not open a route" below: the key makes these models LISTED, not reachable. |
| agent-org cloud lane: `llm-gateway-cloud`, `llm-gateway-cloud-db`, `ao-egress` | `agent-org/docker/docker-compose.yml`, `agent-org/config/litellm-cloud.config.yaml` | All three carry `profiles: ["cloud"]`, and `agent-org/docker/.env.example` sets no `COMPOSE_PROFILES` at all, so none of them renders. `AO_CLOUD_ENABLED=false` separately keeps `agent-bridge` on the local lane. | `cloud` in `COMPOSE_PROFILES` (or `--profile cloud`), plus `OPENROUTER_API_KEY`, `AO_CLOUD_DB_PASSWORD`, `AO_CLOUD_MASTER_KEY` and `AO_CLOUD_ENABLED=true` in `agent-org/docker/.env`. | `llm-gateway-cloud` has no internet leg of its own: it sits on `ao-net` and `ao-cloud-egress-net` (`internal: true`) with `HTTP_PROXY`/`HTTPS_PROXY` pointed at `ao-egress`, the one dual-homed container. Read the allowlist note under the table before turning this on. |
| `ao-git-egress`, and the worker pool behind it | `agent-org/docker/docker-compose.yml` | `profiles: ["workers"]`, so with no `COMPOSE_PROFILES` neither the proxy nor the pool renders. | The `workers` profile. | A default-deny tinyproxy on `ao-worker-net` (`internal: true`) + the project bridge. **The mechanism, since it is not uniform:** only `ao-ot-1`/`ao-ot-2` carry `HTTP_PROXY`; `ao-worker-1`/`-2` carry none and are confined by `ao-worker-net` having no route out at all. The filter file lives on the shared `ao-egress-config` volume - `agent-org/docker/egress/egress-reload.sh` seeds it with `github.com` + `githubusercontent.com` and SIGHUPs tinyproxy whenever `agent-bridge` rewrites it as projects and hosts are onboarded from Mattermost. |
| `agent-bridge`'s GitHub App (the capability plane) | `agent-org/agent-bridge/app/config.py`, `agent-org/docker/docker-compose.yml` | `Settings.github_app_enabled` is false unless `github_app_id` is set **and** the private-key file exists, so the plane stays offline and the bridge runs normally without it. | `AO_GITHUB_APP_ID` + `AO_GITHUB_APP_OWNER` in `agent-org/docker/.env` (absent from the example - this plane leaves `${VAR:-}` names out on purpose), and a readable key at `agent-org/agent-bridge/secrets/github-app-key.pem`. | `https://api.github.com`, directly from `ao-net`, which is a plain bridge. This one does **not** go through `ao-egress`. |
| `lc-egress`, and the `open-terminal` proxied through it | `coder/docker-compose.yml`, `little-coder/docker/Dockerfile.egress` | Nothing: it is unprofiled and **starts whenever the coder plane starts**. What is "off" is the destination set - tinyproxy runs `FilterDefaultDeny Yes`, so any host not matching the allowlist is refused, and the proxy initiates nothing on its own. | No variable. The allowlist is baked into the image from `little-coder/docker/egress-allowlist.txt`, which ships `github.com` and `githubusercontent.com`; widening it is an edit plus a rebuild. | `CONNECT` to 443 and 22 on allowlisted hosts, out of the coder project's own `default` bridge. `open-terminal` has no other route: it is on `lc-net` (`internal: true`) and `llm-net` (also internal). |
| `vpn` (Mullvad WireGuard, gluetun) | `search/docker-compose.yml` | **This is an egress that is meant to be on.** It is unprofiled, so it renders and starts with the search plane and dials Mullvad itself. From the examples it cannot connect: `search/.env.example` ships `MULLVAD_WG_PRIVATE_KEY=change-me-real-wg-private-key`, a placeholder rather than a blank because the `${...:?}` guard rejects empty. | Real values for `MULLVAD_WG_PRIVATE_KEY` and `MULLVAD_WG_ADDRESSES` in `search/.env`. | Mullvad, over WireGuard. It exists so search does **not** leak: `searxng` sits on `search-net` (`internal: true`) and `search/searxng/settings.yml` points `outgoing.proxies` at `http://vpn:8888`, so engine queries and page fetches have no other way out and HTTPS `CONNECT` resolves DNS at the far end of the tunnel. |
| `cloudflared` | `portal/docker-compose.yml` | `profiles: [internet]`, and `portal/.env.example` deliberately carries no `COMPOSE_PROFILES` line. The whole plane is `manual` in `stack.manifest.toml`, so the driver never starts it either. | `scripts/portal/portal-on.ps1`, which passes `--profile internet` on the command line, plus `CLOUDFLARE_TUNNEL_TOKEN` in `portal/.env` (blank in the example). | Cloudflare's edge, from `edge-net`. It is the portal's only ingress - `caddy` publishes no host port. |
| `portal-alerter` | `portal/docker-compose.yml` | Unprofiled, but it can only start when the portal does, which is by hand. Each channel is off while its settings are blank: Telegram without `PORTAL_ALERT_TELEGRAM_BOT_TOKEN` + `PORTAL_ALERT_TELEGRAM_CHAT_ID`, Mattermost without `PORTAL_ALERT_MM_URL` + `_TOKEN` + `_CHANNEL_ID`, email without `DIGEST_TO` and usable OAuth files (it logs `email disabled: OAuth not configured` once and makes no Google call). | The portal being up, plus, per channel: those `PORTAL_ALERT_*` values in `portal/.env` (blank in the example), and Google OAuth files at `secrets/google/portal-alerter/credentials.json` and `token.json`. | All on `notify-net`: `api.telegram.org` (Telegram Bot API `sendMessage`, added 2026-10-04 by pa-channels), the HOST's published Mattermost port via `PORTAL_ALERT_MM_URL` (e.g. `host.docker.internal:8065` - a host/LAN hop, not the internet, though Mattermost's own push notifications to a phone go out through its push proxy), and `oauth2.googleapis.com` + `gmail.googleapis.com`. Telegram and Mattermost get one minimal line per alert - severity, event name, a host label, the time - never source IPs, usernames or the log line (the Authelia notification bridge's log line carries one-time codes); only the email copy carries detail. No new network, no inbound path: `notify-net` already existed as the alerter's egress leg. **Do not read `auth-net`'s `internal: true` as "the portal has one way out":** the render has FOUR non-internal networks - `edge-net`, `notify-net`, the external `app-net`, and an implicit `default` that both backup sidecars join - and `caddy` itself is on `app-net` and `edge-net`. `notify-net` is the alerter's egress leg, not the plane's only one. |
| `tailscale` | `frontend/docker-compose.yml` | `profiles: [tailscale]`, and `frontend/.env.example` ships `COMPOSE_PROFILES=stock`, so a fresh clone renders neither it nor its backup. | `tailscale` in `COMPOSE_PROFILES` in `frontend/.env` - necessarily together with `gpu`, because `network_mode: service:openwebui` names the `gpu` definition - plus `TAILSCALE_AUTH_KEY`, which the example leaves as `putyourtskeyhere`. | Tailscale's coordination servers and DERP relays, from inside `openwebui`'s network namespace. |
| `openwebui` (either profile) | `frontend/docker-compose.yml` | Nothing - one of the two definitions is what this plane exists to run. The `stock` one is on the project-local `owui-net`; the `gpu` one additionally joins `ai-stack_default`, `app-net` and `llm-net`. | Whichever profile is active. | Both sit on an internet-capable bridge. Its web SEARCH does not use that - `SEARXNG_QUERY_URL` points at the search plane's gateway - but that setting covers the query only; what Open WebUI's own loaders fetch at runtime is upstream behaviour this repo does not pin (no `HF_HUB_OFFLINE` is set beside `HF_HOME`). Treat it as network-capable and unbounded rather than as bounded by `SEARXNG_QUERY_URL`. |
| `openwebui-backup` | `frontend/docker-compose.yml` | Nothing. It is **unprofiled and renders under `stock`**, i.e. in the quickstart deployment, and its `command:` begins `apk add --no-cache pigz` on every container start. | Nothing - this is on by default. | The Alpine package CDN, over `owui-net`. The compose comment beside the network list says so in as many words. It is the only runtime package install in any compose file here; the other backup sidecars run their script and nothing else. |
| `openbrain-gateway` (the only cloud door) and `openbrain-ops-gateway` | `OB1/docker/docker-compose.yml`; the image is built from `openbrain-gateway/` in this repo | Two instances of one image. The cloud door force-filters reads to `share == "cloud"` and blocks the aggregate tools; the OPS door (`GATEWAY_PROFILE: ops`, for host processes such as the claude-sessions bridge) filters on `exposure == "ops"` and allowlists only the agent-memory tools. | `OPENBRAIN_GATEWAY_KEY` / `OPS_GATEWAY_KEY` in `OB1/docker/.env` - and they must differ; the ops door's `${OPS_GATEWAY_KEY:?…never the cloud one}` guard says so. | The gateway process makes no outbound call, and both publish on loopback (`:8061`, `:8062`). **It is not simply "a door in", though:** `openbrain-gateway/app.py`'s default `WRITE_TOOLS` allowlist includes `ingest_url` and `ingest_urls`, so a remote client holding the cloud key can make `openbrain-mcp` fetch a URL of its choosing - through the tunnel. openbrain-mcp itself refuses internal targets it can recognise (literal private / loopback / link-local addresses, internal-looking names); a PUBLIC name that resolves to a private address is resolved by the proxy, not by openbrain-mcp, and is stopped only by gluetun's DNS rebinding protection (on by default; not tested from this stack) - see the next row. Each door keeps an append-only audit record (`GATEWAY_AUDIT_LOG`; `cloud.jsonl` / `ops.jsonl` under `backups/openbrain-gateway-audit/`): one line per request with the key id, tool names, allowed/refused, status and byte counts - never arguments or payloads - rotated at 5 MiB x 4. |
| `openbrain-mcp` | `OB1/docker/docker-compose.yml`, source `OB1/integrations/kubernetes-deployment/index.ts` + `ingest-egress.ts` | **Nothing gates it.** It carries no profile, sits on `obnet` (a bridge) plus `ai-stack_default` (to reach `vpn`) and is the core of the OB1 plane. The `ingest_url` / `ingest_urls` tools and `capture_with_thread`'s `url` fetch go through `guardedFetch` (`ingest-egress.ts`). | Nothing. It is live whenever Open Brain is. | Any public host the caller names, **through `FETCH_PROXY_URL`** (compose: `${OB_MCP_FETCH_PROXY_URL-http://vpn:8888}`), fail-closed the way `openbrain-research` is: a proxy client that cannot be built refuses the fetch rather than going direct, and direct happens only when `OB_MCP_FETCH_PROXY_URL` is set to empty on purpose. Loopback / private / link-local / CGNAT targets (including IPv4 embedded in IPv6 forms) and internal-looking hostnames are refused by literal address or name; in proxy mode openbrain-mcp does not resolve names (that would leak them outside the tunnel), so a public name that resolves to a private address is left to gluetun's DNS rebinding protection, while direct mode resolves and checks every answer. Redirects are followed by hand with every hop re-checked (capped at 5), and the fetch has a 20 s timeout. Fetched text is screened for prompt injection; a page classified as an attack is quarantined (not stored). Still the broadest destination set in the stack, reachable locally and, for the two ingest tools, through the cloud door above. |
| `openbrain-grounding-backfiller` | `OB1/docker/docker-compose.yml`, source `OB1/integrations/grounding-backfiller/index.ts` | **Nothing gates it** - unprofiled, on `obnet` + `llm-net` + `ai-stack_default`. `WIKI_BASE` defaults to `https://en.wikipedia.org`. | Nothing. | Wikipedia, and the source URLs it re-fetches, through `FETCH_PROXY_URL` (compose: `${BACKFILL_FETCH_PROXY_URL:-http://vpn:8888}`), fail-closed: a configured proxy whose client cannot be built refuses every fetch and every `/backfill` and `/refetch` run (`egress REFUSED` at start, `/health` reports `REFUSED`); it goes direct only when `FETCH_PROXY_URL` reaches the container empty - which compose's `:-` default does not allow from `.env`. Unlike `openbrain-mcp` it does not vet targets: a re-fetch follows the stored URL's redirects (`redirect: "follow"`), through the proxy. The refetch path's DIRECT fallback is **off**: `REFETCH_ALLOW_DIRECT` defaults to `false` in code and compose (`${BACKFILL_REFETCH_ALLOW_DIRECT:-false}`), and only the literal `true` turns it on - a source that comes back thin through the proxy stays thin. |
| `openbrain-wiki`'s `WIKI_GIT_REMOTE` | `OB1/docker/docker-compose.yml` | Set to `""`, which the compiler reads as local-commits-only: vault history is kept, nothing is pulled or pushed. The SSH URL sits beside it, commented out. | Restoring that URL (and a passphrase-less deploy key at `WIKI_GIT_SSH_KEY`). | `github.com` over SSH - the compiled wiki is force-pushed to a private repo. The present-but-inert shape this whole section is about. |
| OB1's scheduled chain: `openbrain-digest`, `openbrain-gmail-pull`, `openbrain-gmail-prune`, `openbrain-podcast` | `OB1/docker/docker-compose.scheduled.yml` | Unprofiled, but each mounts a Google OAuth client secret and token from `OB1/secrets/`, which is gitignored and absent from a fresh clone. Their two recipe env files (`OB1/recipes/daily-digest/.env`, `OB1/recipes/email-history-import/.env`) are gitignored too, and optional to compose: a fresh clone renders without them. | Putting those OAuth files in place - and the `open-brain` product being enabled at all. | Google's APIs (Gmail read and send, Calendar read), plus `wttr.in` for the digest's weather brief, out of `obnet`. |
| `openbrain-research` | `OB1/docker/docker-compose.yml` | `profiles: ["research"]`. | Any driver start of the ob1 plane: `idea-refinery` is a `default` profile of ob1 and `requires` `research`, so `stack.py up` passes `--profile research` whenever ob1 is enabled - by the `open-brain`, `research` or `digest` product or by `enable --plane ob1`. Outside the driver, `research` in `OB1/docker/.env`'s `COMPOSE_PROFILES` or `--profile research` on the command line. | Page fetches go through `FETCH_PROXY_URL`, defaulting to `http://vpn:8888` - the search plane's Mullvad tunnel - and searches to `http://gateway:8080`. **This one** connects TO the privacy boundary rather than around it and fails closed (a proxy client that cannot be built stops the service at start; with compose's `:-` default an empty `.env` value still means the tunnel). So, since eh-ingest (2026-10-07), do `openbrain-mcp`'s URL fetches and the backfiller three rows up - both refuse the fetch instead of exiting. Those are the three OB1 fetchers this table names; do not generalise "OB1 fetches through the tunnel" past them - the scheduled chain above talks to Google directly. |

## Network-capable, no outbound call

These are on ordinary bridges and so pass stage 1, but their source makes no
outbound call. They are listed so the criterion is visible and so a reader
who disagrees has something to argue with:

- **`llm-gateway-ui`** - on `app-net` so the portal's Caddy can front `/ui`. Its
  `litellm.ui.config.yaml` declares no `model_list`, sets `telemetry: false`,
  and the service sets `LITELLM_LOCAL_MODEL_COST_MAP=True`, so there is a route
  and no call. It carries no alias and serves no inference.
- **the search `gateway`** - on `ai-stack_default` for cross-project DNS; its
  only HTTP client targets `searxng`.
- **`openbrain-ext`** - its one outbound `fetch` is `WIKI_RECOMPILE_URL`,
  internal.
- **the portal's `caddy`, watchers, tripwire and `portal-cron`** - internal
  targets only. Caddy attempts no ACME: every site address in the `Caddyfile` is
  `http://` or a bare port, which disables auto-HTTPS (the `https://` strings in
  that file are redirect TARGETS and comments). `authelia`'s notifier is `filesystem`
  and it is on `auth-net` (`internal: true`).
- **`frontend/status-pipe/`** - every request target is an internal `host:port`.
- **the backup sidecars other than `openwebui-backup`** - on a project bridge,
  but each only runs its own script; the NAS sync (`scripts/backup/backup-to-nas.ps1`)
  is SMB to a LAN address, not the internet.
- **`mattermost`** - FLAGGED rather than cleared: it is on `ao-net`, a plain
  bridge, and nothing in this repo sets `MM_LOGSETTINGS_ENABLEDIAGNOSTICS`.
  Whether Team Edition phones home on its defaults is an upstream fact this
  repo cannot settle.

## Host-side, not compose

Three things that egress are **not containers**, so a compose render cannot see
them. They run on the host, from this repo:

- **The sysadmin Telegram channel** - `scripts/sysadmin-mcp/telegram_notify.py`
  POSTs to `api.telegram.org`, and `scripts/sysadmin-mcp/telegram_listener.py`
  POLLS it for operator commands. The listener is an **inbound control path**
  that no firewall rule sees, because it is the host reaching out. Off without
  the bot token and chat id.
- **The claude-sessions bridge** (`scripts/claude-sessions-bridge/bridge.py`) -
  runs the `claude` CLI headless with `BRIDGE_MODEL` defaulting to `opus`, so
  every bridge turn is a call to Anthropic from the host, and it also posts to
  Telegram. This is the one place the stack talks to a frontier provider at all.
  It is a scheduled host process, not part of any plane; it is off unless you
  run it.
- **The Open WebUI plugins in `frontend/owui/`** - deploy-by-paste, tracked in
  `frontend/owui/manifest.csv`. `frontend/owui/tools/github_chat_mcp_tools.py` targets
  `https://api.github.com`, and `frontend/owui/tools/fileshed.py` permits `curl`, `wget`
  and network `git` subcommands from inside the `openwebui` container, behind
  its own valves. They live in the OWUI database, not in a compose file.

## Why a cloud key does not open a route

Setting `OPENROUTER_API_KEY` in `inference/.env` makes `assemble-config.py` keep
the two cloud entries, so `llm-gateway` registers them and `/v1/models` lists
them. It does **not** make them work. `llm-gateway` is attached to `llm-net` and
`llm-backend-net` and to nothing else, and both are internal-only, so the
container has no route out of Docker's internal networks and a request for `cloud-large` fails where
LiteLLM tries to reach openrouter.ai. Making it real would mean giving that
container an egress path - attaching it to an internet-capable network, or
pointing it at a dual-homed allowlisted proxy the way `llm-gateway-cloud` points
at `ao-egress`. **Neither is done here, and neither is a casual change**:
keeping the gateway off every internet-capable bridge is the supply-chain
posture that also explains `LITELLM_LOCAL_MODEL_COST_MAP=True` (no cost-map
fetch at boot) and the digest-pinned image. agent-org took the other route
deliberately - its cloud lane is a *separate* LiteLLM behind its own egress
proxy, and `agent-org/config/litellm-cloud.config.yaml` says in as many words
not to add OpenRouter to the local gateway.

**Before you enable the agent-org cloud lane, read its allowlist.** `ao-egress`
builds from `little-coder/docker/Dockerfile.egress`, whose `CMD` runs tinyproxy
against the allowlist COPYd into the image at build time. The `EGRESS_ALLOWLIST`
environment variable the compose file sets on that service is read by nothing in
that image, so the effective allowlist is the baked `github.com` /
`githubusercontent.com` pair and `openrouter.ai` would be denied. That fails
closed, which is the safe direction, but it means the cloud lane does not work
as shipped. Fixing it is a compose or image change, not a documentation one.

## What a fresh clone actually does

Copy each plane's `.env.example` and:

- Every **provider** credential is blank (`OPENROUTER_API_KEY`,
  `CLOUDFLARE_TUNNEL_TOKEN`, `AO_CLOUD_API_KEY`, `LC_DEPLOY_TOKEN`,
  `LC_SELF_REMOTE_PAT`) or an unmistakable placeholder
  (`TAILSCALE_AUTH_KEY=putyourtskeyhere`,
  `MULLVAD_WG_PRIVATE_KEY=change-me-real-wg-private-key`). The cloud lane's own
  local secrets are placeholders too (`AO_CLOUD_DB_PASSWORD=change-me-cloud-db`,
  `AO_CLOUD_MASTER_KEY=change-me-cloud-master`) - they are not provider
  credentials, and the lane is profile-gated anyway.
- The only **active** `COMPOSE_PROFILES` assignment in any example is
  `frontend/.env.example`'s `COMPOSE_PROFILES=stock`. `inference`'s is commented
  out, and so is a `gpu,tailscale` line a few lines above the active one;
  `OB1/docker/.env.example` carries a commented
  `#COMPOSE_PROFILES=research,wiki,notebook,idea-refinery`; the other planes
  have no such line at all. **No active assignment names `cloud`,
  `internet`, `workers` or `tailscale`,** so no profile-gated component renders.
- **Three containers still reach the internet**, and none of them is a model
  provider. Measured from the renders above, not reasoned about:

| Container | Why | Plane |
|---|---|---|
| `vpn` | dials Mullvad itself at start; the whole point of the plane. Cannot connect on the example's placeholder key. | search |
| `openwebui-backup` | `apk add --no-cache pigz` on every start, against the Alpine CDN. **Renders under `stock`, so it is in the quickstart.** | frontend |
| `lc-egress` | up with the plane and dual-homed; it initiates nothing itself, but it is the route `open-terminal` uses, and the allowlist is the control. | coder |

The quickstart enables `frontend` alone: Open WebUI on `owui-net` plus that
backup sidecar. Bring up `coder` or `search` and the other two join it.
