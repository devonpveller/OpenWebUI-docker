# portal - the internet-exposed compose plane

The only plane that faces the internet: Cloudflare Tunnel to Caddy to Authelia
to an ai-stack app. Its own compose project (`name` comes from the `-p portal`
the lifecycle scripts pass) since 2026-08-21, because the portal is meant to
front more than ai-stack one day and a separate project is that seam.

**It is `manual` in [`../stack.manifest.toml`](../stack.manifest.toml), and that
is a safety property, not an oversight.** `stack.py up`/`down`/`restart` skip
it and say so; exposing the stack to the internet stays a deliberate human
action, run through
[`../scripts/portal/portal-on.ps1`](../scripts/portal/portal-on.ps1) and
[`portal-off.ps1`](../scripts/portal/portal-off.ps1).

Everything below is checked against
[`docker-compose.yml`](docker-compose.yml) and the four scripts under
`scripts/portal/`; where a doc elsewhere disagrees, those files win.

```
        internet                              tailnet (separate path)
           |                                          |
   [ cloudflared ]  edge-net   profile `internet`     |
           |                                          |
        [ caddy ] --- auth-net ---> [ authelia ]      |  caddy:8446
           |  app-net (ai-stack_app-net, external)    |  <----------
           v                                          |  (the frontend's
   openwebui:8080, open_notebook, openbrain-*         |   QUARTZ route)
```

## What starts

Twelve services. **Only two are profiled**, so a bare `docker compose -f
portal/docker-compose.yml up -d` would start ten of them and leave the tunnel
off - which is why the lifecycle script, not a bare `up`, is the documented
path.

| Service / container | Profile | What it is |
|---|---|---|
| `portal-init` | *(none)* | One-shot `alpine:3.21` with `network_mode: none`. Chowns the four volumes to the UIDs the services run as (authelia 10001, caddy 10000, tripwire 10003) and creates the two log files world-readable so the alerter can read them. Everything else waits on it with `condition: service_completed_successfully`. |
| `caddy` | *(none)* | `caddy:2.8.4-alpine`. The reverse proxy and `forward_auth` client, and the sole ingress. **Binds no host port.** Config is [`config/caddy/Caddyfile`](config/caddy/Caddyfile), read-only. |
| `authelia` | *(none)* | `authelia/authelia:4.39`. SSO and 2FA, on `:9091` inside `auth-net` only. |
| `portal-alerter` | *(none)* | Digest-pinned Deno service on `:8080`. Receives `/alert` from the watchers and mails through Gmail. **It is the only container in this plane with internet egress**, and it gets it from `notify-net`. |
| `authelia-watcher` | *(none)* | Tails Authelia and Caddy logs, alerts on new source IPs, and appends each one to [`config/watcher/known-ips.txt`](config/watcher/known-ips.txt) (mounted read-write so it can, and so you can prune it). |
| `authelia-notif-bridge` | *(none)* | Tails Authelia's filesystem notifier and forwards each user-facing record - the 2FA enrolment OTP, password resets - to the alerter. Without it the OTP exists only inside the container, which defeats a portal you use from away. |
| `integrity-tripwire` | *(none)* | Hashes the Caddyfile and the two Authelia configs on a supercronic schedule (`TRIPWIRE_CRON`, default `0 4 * * *`) and alerts on drift. |
| `portal-cron` | *(none)* | supercronic that triggers the daily digest (`PORTAL_DIGEST_CRON`, default `0 7 * * *`). |
| `caddy-backup` | *(none)* | supercronic (`CADDY_BACKUP_CRON`, default `0 3 * * *`) tarring `caddy-data` into `backups/caddy`. Built from the shared `backup/` Dockerfile with `BACKUP_UID=10000` so it can read Caddy's mode-0600 state. |
| `authelia-backup` | *(none)* | Same shape, `BACKUP_UID=10001`, `authelia-data` into `backups/authelia`. |
| `cloudflared` | `internet` | `cloudflare/cloudflared:2024.10.0`. The tunnel - **the only internet ingress**. Metrics on `:2000` inside `edge-net`, no host port. |
| `tunnel-watcher` | `internet` | Probes `cloudflared:2000/ready` every `TUNNEL_WATCHER_POLL_SEC` (30) and alerts HIGH after `TUNNEL_WATCHER_FAILURES_BEFORE_ALERT` (3) consecutive misses. |

**The hardening floor, read out of the render rather than the file** (`config
--format json`, all twelve services, 2026-09-19). **Each row's count plus its
exceptions must come to twelve** - that arithmetic is the cheapest check on
this table, and it is what caught the first version of the last row:

| Property | Who has it |
|---|---|
| `cap_drop: ALL` + `security_opt: no-new-privileges` | **all twelve**, `portal-init` included |
| `read_only: true` | **eleven** - every service except `portal-init`, which exists to chown the volumes and so needs a writable root FS |
| a non-root `user:` | **ten**. `portal-init` is deliberately `0:0` (it chowns), and `portal-cron` sets no `user:` at all |
| `cpus` + `memory` + `pids` limits | **nine**. `caddy-backup` and `authelia-backup` carry `pids` only, and `portal-init` has no `deploy` block at all - 9 + 2 + 1 = 12 |

Since `sl-compose-anchors` (2026-09-19) that floor is declared **once** at the
top of the file as YAML extension fields and merged into each service with
`<<: *name` - `x-hardening` (the two keys all twelve carry), `x-hardening-ro`
(that plus `read_only`), and `x-healthcheck-http` (the interval/timeout/retries
the three probed services share; `test:` and `start_period` stay per-service).
No rendered service definition changed. **The trap when you edit: a merge key
merges MAPS, and a LIST written on a service REPLACES the anchored list rather
than appending to it** - a service needing one more `security_opt` or `cap_drop`
entry must spell out the whole list. That is why `portal-init`'s and `caddy`'s
extra capabilities use `cap_add`, a different key, and survive the merge.

## Requires

| Needs | What makes it so |
|---|---|
| **anchor** | `app-net` is `external: true`, named `ai-stack_app-net`. Without it compose refuses the project. |
| **frontend** (hard) | `caddy` joins `ai-stack_app-net` to reach `openwebui:8080`, and the Caddyfile proxies it for the primary vhost. Without the frontend the portal's main site 502s. `[planes.portal] requires = ["anchor", "frontend"]`. |
| **ob1** (soft) | The Caddyfile also proxies `openbrain-wiki-viewer:8080`, `openbrain-workbench:8000` and `open_notebook`. Those vhosts 502 while the rest of the portal serves. |
| **inference** (soft) | The Caddyfile proxies `llm-gateway-ui:8080`, which joins `ai-stack_app-net` for exactly that. |

And in the other direction: the **frontend's tailnet wiki route needs this
plane**. `QUARTZ_HOST` defaults to `caddy` and `QUARTZ_PORT` to `8446`, so the
tailnet `:8444` wiki route goes through Caddy to get the same key-injected
`/workbench` routing the external vhost gets. Caddy must be up before that
serve configures, or the frontend's monitor loop does deferred setup once it
appears.

## Surfaces

| Surface | Where |
|---|---|
| `https://${PUBLIC_DOMAIN}/` and its vhosts | through `cloudflared`, in production mode. **The plane publishes no host port at all** - all ingress arrives through the tunnel. |
| `http://127.0.0.1:8443/` | test mode ONLY, and only because [`local-test.override.yml`](local-test.override.yml) adds `127.0.0.1:8443:80` to `caddy`. |
| `caddy:8446` | in-cluster, on `ai-stack_app-net`: the tailnet wiki route from the frontend. |
| `portal-alerter:8080/alert` | in-cluster, on `auth-net`: where the three watchers POST. |

In test mode a browser on `http://127.0.0.1:8443/` hits Caddy's catch-all
`:80 { respond 421 }`, because the Host header does not match any site block.
Add `127.0.0.1 <your domain>` to the Windows hosts file and visit
`http://<your domain>:8443/` instead - and expect cookies NOT to persist, since
the URL is neither HTTPS nor the cookie domain. **Test mode validates
reverse-proxy routing and service health, not the login UX.**

## Host requirements and keys

From `[planes.portal]` in [`../stack.manifest.toml`](../stack.manifest.toml):

- **A Cloudflare tunnel token and a public domain.** The plane publishes no host
  ports; without the tunnel there is no ingress.

Five keys must be non-blank, and
[`portal-on.ps1`](../scripts/portal/portal-on.ps1) reads that exact list **out
of the manifest** before it starts anything:

| Key | Why it is on the list |
|---|---|
| `CLOUDFLARE_TUNNEL_TOKEN` | no tunnel, no portal. Blank fails safe - nothing gets exposed. |
| `AUTHELIA_JWT_SECRET` | the one with a `${...:?}` guard in the compose file, because a silently empty identity-signing secret on an internet-exposed auth gate is worse than a refusal. |
| `AUTHELIA_SESSION_SECRET` | no guard in compose - it renders fine blank, which is why the script checks it. |
| `AUTHELIA_STORAGE_ENCRYPTION_KEY` | same. |
| `PUBLIC_DOMAIN` | same; it is also what the alerter puts in every alert link. |

`WORKBENCH_KEY` is not on that list but matters: Caddy injects it as
`X-Brain-Key` when proxying `/workbench/*`, and it must equal OB1's
`MCP_ACCESS_KEY`. Empty means the workbench rejects every `/workbench` call
(fail closed) - and an unset `${WORKBENCH_KEY}` has crashed a health check
before.

**Why this plane is not in `stack.py doctor`.** It is `manual`, so it is never
in the driver's enabled set and the driver's blank-key check never reaches it.
That is the whole reason the pre-flight lives in the operator's own entrypoint.

## First run

```powershell
Copy-Item portal\.env.example portal\.env   # then fill in the five keys above
```

Migrating an existing host away from the single root `.env`:
[`../documentation/runbooks/env-split-migration.md`](../documentation/runbooks/env-split-migration.md).

**`portal/.env` carries no `COMPOSE_PROFILES` line, deliberately** (D17). The
`internet` profile is passed on the command line by `portal-on.ps1`; exposing
the stack must never be a standing setting.

Then, from the repo root:

```powershell
.\scripts\portal\portal-on.ps1 -WhatIf   # print every docker command, run nothing
.\scripts\portal\portal-on.ps1 -Test     # local test mode: NO tunnel, caddy on 127.0.0.1:8443
.\scripts\portal\portal-on.ps1           # PRODUCTION: --profile internet, tunnel up, publicly reachable
.\scripts\portal\portal-status.ps1       # read-only liveness, one line per check
.\scripts\portal\portal-off.ps1          # stop the portal, preserve volumes and config
```

What `portal-on.ps1` actually does, read to the end:

1. Refuses if `portal/.env` is absent, or if any key in
   `[planes.portal] keys` is missing or blank - naming them. If it cannot read
   the manifest it says so and falls back to a built-in list of the same five,
   so it never silently checks nothing.
2. Chooses ONE of two argument sets: production adds `--profile internet`; test
   adds `-f portal/local-test.override.yml` and **no profile**.
3. Brings the services up in five ordered groups - `portal-alerter`, then
   `authelia`, then `caddy`, then the four watchers, then the two backups -
   with a 2 s pause between them so healthchecks can flip. Production adds a
   sixth group, `cloudflared` + `tunnel-watcher`. (`portal-init` is in no group;
   it is pulled in by the `depends_on` of the services that need it.)
4. Runs `portal-status.ps1`.

It passes **no `--env-file`**: `-f portal\docker-compose.yml` makes `portal/`
the project directory, so compose loads `portal/.env` itself.

By hand, from the repo root, if you must:

```powershell
docker compose -p portal -f portal/docker-compose.yml --profile internet up -d
docker compose -p portal -f portal/docker-compose.yml config --services   # 10, or 12 with `internet`
```

The `-p portal` is not optional by hand: this compose file declares no `name:`,
so without it the project is named after the directory and you get a second,
parallel set of volumes.

`stack.py up portal` starts nothing and prints
`# portal is not driven by stack.py - start it with scripts/portal/portal-on.ps1
/ scripts/portal/portal-off.ps1`; `stack.py restart portal` refuses outright
with the same pointer. That is the manifest's `manual` key doing its job.

**In an incident, reach for the killswitch, not `portal-off`.**
[`breach-killswitch.ps1`](../scripts/portal/breach-killswitch.ps1) mails first
(so the record exists before the alerter goes down), stops the exposed
services, snapshots the Caddy and Authelia logs under `./incident/<UTC ts>/`,
rotates the two Authelia secrets in `portal/.env` keeping the old values
commented out, then prints recovery steps and exits without restarting
anything. `-DryRun` prints the intended actions.
[`access-query.ps1`](../scripts/portal/access-query.ps1) is the read-only "who
hit the portal" review.

## Where the live state is

| Mount | Kind | Live state? |
|---|---|---|
| `portal_authelia-data` to `authelia:/data` | named volume | **Yes.** `db.sqlite3` (users, 2FA devices, sessions), `authelia.log`, `notification.txt`. Backed up nightly. Read-only into the alerter, the watcher, the notif bridge and `authelia-backup`. |
| `portal_caddy-data` to `caddy:/data` | named volume | **Yes.** ACME state, the instance UUID and `caddy-access.log`. Backed up nightly. |
| `portal_caddy-config` to `caddy:/config` | named volume | Caddy's runtime config. Not backed up - it is regenerated from the Caddyfile. |
| `portal_tripwire-data` to `integrity-tripwire:/state` | named volume | The accepted `baseline.sha256`. After a deliberate config change, re-accept it (`docker exec integrity-tripwire sh /scripts/tripwire.sh accept`) or the next check alerts on your own edit. |
| [`config/`](config) | host binds, mostly `:ro` | The Caddyfile, the Authelia config and user database, the four sidecar source trees, and `config/watcher/known-ips.txt` (read-write). Plane-internal since `sl-colo-portal` (2026-09-19) - it used to live in a shared repo-root `config/`. |
| `../secrets/google/portal-alerter/` | host binds | The alerter's Gmail OAuth credentials and token. `token.json` is NOT `:ro` - Docker cannot create the mount target inside a read-only parent. |
| `../reports/portal-digest` | host bind | Daily digest markdown, so the operator can read it without entering the container. |

Restore: `documentation/runbooks/restore-from-snapshot.md`, or
`scripts/backup/restore-from-snapshot.ps1 -SnapshotRoot .\backups -Date
<yyyy-MM-dd> -Services caddy,authelia -Apply`. `-SnapshotRoot` and `-Date` are
MANDATORY, and without `-Apply` it only plans.

## Gotchas

- **`portal-off.ps1` names its services explicitly, and its list is not the
  whole plane.** It stops ten of the twelve; `tunnel-watcher` and
  `authelia-notif-bridge` are not named and keep running. Check with
  `docker compose -p portal -f portal/docker-compose.yml ps` afterwards.
  (Naming services at all is deliberate: a bare `docker compose stop` is
  project-scoped, and `--profile` does not narrow `stop`, `down` or `rm`.)
- **There is no `local-test` compose PROFILE**, despite the name. Test mode is
  an overlay file ([`local-test.override.yml`](local-test.override.yml)) that
  adds one port to `caddy`; `portal-on.ps1 -Test` passes no `--profile` at all.
  Several comments in this tree still describe a `local-test` profile and an
  older filename - the code is what runs.
- **Exposing a new app is a recipe, not a proxy line.** It lives at the top of
  [`config/caddy/Caddyfile`](config/caddy/Caddyfile) next to the
  `(authelia_gate)` snippet: attach the backend to `app-net`, copy the litellm
  vhost, add the hub card, add the Cloudflare hostname in the dashboard,
  validate and reload - then re-accept the tripwire baseline.
- **`auth-net` is `internal: true`.** Nothing on it has a default route, which
  is why `portal-alerter` also sits on `notify-net`: that one network is the
  plane's single internet-egress chokepoint.
- **Bump a digest-pinned image deliberately.** `portal-alerter` and the Authelia
  and Caddy images are pinned; the alerter's own header carries the bump recipe,
  ending in `alerter.ts --selftest`.

## Changing this plane

Adding, removing or moving a container here is never a one-file change. The
full checklist is
[`../documentation/runbooks/SERVICE-LIFECYCLE.md`](../documentation/runbooks/SERVICE-LIFECYCLE.md);
the surfaces this plane appears on today are:

- [`docker-compose.yml`](docker-compose.yml) and the plane-internal
  [`config/`](config) tree. A new service joins the `x-hardening` /
  `x-hardening-ro` floor by MERGING it, never by re-typing it - and if it needs
  one more list entry it spells the whole list out, per the trap above
- [`../stack.manifest.toml`](../stack.manifest.toml) - the `[planes.portal]`
  table (including `manual` and the `keys` list `portal-on.ps1` reads), its
  `internet` profile, and the `portal` product
- `scripts/portal/portal-on.ps1`, `portal-off.ps1` (its explicit service list),
  `portal-status.ps1`, `breach-killswitch.ps1`
- `scripts/checks/check-project-configs.ps1` - the portal entry in its
  compose-validation list, which passes `--profile internet` so a staged
  compose change is rendered with the tunnel services present
- `scripts/backup/restore-from-snapshot.ps1` - the `caddy` and `authelia`
  catalog entries
- `.claude/skills/stack-map/references/workspace-stacks.md` section 1, and the
  plane table in [`../CLAUDE.md`](../CLAUDE.md)
- `documentation/CONTAINER-REGISTRY.md` and the backup / restore runbooks
- `scripts/agent-harness/lease-names.conf` - the `portal` lease name

Note what is NOT on that list, and why. `scripts/recovery/emergency-recovery.ps1`
and `stack.py`'s drive verbs do not manage this plane, on purpose. And the
generated inventory `scripts/lib/stack-services.json` carries **no portal rows
and no portal project** - `inventory --check`'s plane-coverage assertion exempts
a `manual` plane. So a container added here is invisible to the sysadmin
surfaces until somebody adds it deliberately; `portal-status.ps1` is what
watches this plane today.

Run `/stack-map` afterwards; it checks for drift between these.
