# frontend - the frontend compose plane

Open WebUI and its tailnet companion. One compose project (`name: frontend`,
[`docker-compose.yml`](docker-compose.yml)) with **five service definitions**
behind **three profiles**, of which at most four ever run at once. Its own
project since 2026-08-21 (CLEANUP-PLAN Part K.5); profile-gated since
2026-09-19 (stack-layers 2.5 / DECISIONS D8), which is what lets a fresh clone
run Open WebUI on a machine with no GPU.

This is the plane a newcomer meets first: `stack.py init` with no arguments
enables **this plane and nothing else**
(`DEFAULT_ENABLED = ("frontend",)` in `scripts/stack/stack.py`).

Everything below is checked against
[`docker-compose.yml`](docker-compose.yml) and
[`entrypoint.sh`](entrypoint.sh); where a doc elsewhere disagrees, the compose
file wins.

## What starts

| Service | Container | Profile | What it is |
|---|---|---|---|
| `openwebui-stock` | `openwebui` | `stock` | Open WebUI on the pinned upstream image `ghcr.io/open-webui/open-webui:v0.11.0` (overridable with `OWUI_IMAGE`). No build, no GPU, no anchor network, no other plane. **The fresh-clone deployment.** |
| `openwebui` | `openwebui` | `gpu` | The same Open WebUI release built locally with CUDA torch ([`Dockerfile.openwebui-gpu`](Dockerfile.openwebui-gpu), image `openwebui:local`), plus the NVIDIA device reservation, the `llm-net` / `app-net` seams and the three narrow read-only code mounts. **This host's deployment.** |
| `tailscale` | `tailscale` | `tailscale` | The tailnet node, built from [`dockerfile.tailscale`](dockerfile.tailscale) as `tailscale:local`. It runs `network_mode: service:openwebui`, i.e. inside the GPU container's network namespace, and publishes eight serve routes from the table in [`entrypoint.sh`](entrypoint.sh). |
| `openwebui-backup` | `openwebui-backup` | *(none)* | `alpine:3.21` sleep-loop, daily by default (`OPENWEBUI_BACKUP_INTERVAL`, 86400), tarring the data volume into `backups/openwebui` and keeping `OPENWEBUI_BACKUP_RETAIN_COUNT` (2). Memory-capped at 1g so the nightly tar cannot balloon the WSL VM with page cache. |
| `tailscale-backup` | `tailscale-backup` | `tailscale` | `alpine:3.21` sleep-loop, daily (`TAILSCALE_BACKUP_INTERVAL`), tarring the `data/tailscale` bind into `backups/tailscale`. `HEALTH_TCP` is deliberately empty - a non-empty directory is its only precheck. |

What each profile set renders (`config --services`):

| `COMPOSE_PROFILES` | Services | For |
|---|---|---|
| *(empty)* | `openwebui-backup` alone | nothing useful - a misconfigured `.env` looks like this |
| `stock` | `openwebui-stock`, `openwebui-backup` | a fresh clone |
| `gpu` | `openwebui`, `openwebui-backup` | a GPU host without the tailnet |
| `gpu,tailscale` | `openwebui`, `tailscale`, `openwebui-backup`, `tailscale-backup` | **this host** |

`openwebui-stock` and `openwebui` share `container_name: openwebui`, the
`frontend_openwebui-data` volume and the `:3000` port, so **exactly one may be
active**: turning on `stock` and `gpu` together is refused by `docker compose
config` (`container name "openwebui" is already in use`) before anything
starts. Two definitions exist because compose can gate a SERVICE on a profile
but cannot gate a FIELD - neither `build:` nor
`deploy.resources.reservations.devices` can be made to vanish when a variable
is unset, and those two are exactly what breaks a fresh clone.

`tailscale` **requires** `gpu`: its `network_mode` names the `gpu` definition,
so `--profile tailscale` alone is refused outright by compose with
`service "tailscale" depends on undefined service "openwebui"`. The manifest
encodes that as `requires = ["gpu"]` under
`[planes.frontend.profiles.tailscale]`.

**Four blocks these five services used to repeat are declared once** at the top
of the compose file as YAML extension fields and merged in with `<<: *name`
(`sl-compose-anchors`, 2026-09-19): `x-hardening` (the `security_opt` the four
services that carry it share - `tailscale` never did and does not gain it),
`x-hardening-owui` (that plus `read_only: false` and the `/tmp` tmpfs both
openwebui definitions share, because they are one application under two
profiles), `x-healthcheck-http` (the probe TIMINGS; `test:` stays per-service),
and `x-backup-sidecar` (the `alpine:3.21` sleep-loop shape both backup sidecars
share, so bumping the alpine tag moves both). `docker compose config` echoes
the `x-` keys back at the top level - they are declarations, not services, and
no rendered definition changed. **The trap when you edit: a merge key merges
MAPS, and a LIST written on a service REPLACES the anchored list rather than
appending to it**, so a service needing one more `security_opt` or `tmpfs`
entry must spell out the whole list. Extra hardening under a DIFFERENT key
(`tailscale`'s `cap_add`, `tailscale-backup`'s `cap_drop`) is just that key and
merges cleanly.

## Requires

| Needs | What makes it so |
|---|---|
| **anchor**, under `gpu` only | `default`, `llm-net` and `app-net` are `external: true` here, named `ai-stack_default` / `ai-stack_llm-net` / `ai-stack_app-net`, and only the `gpu` definition attaches to them. Under `stock` the plane uses only its own project-local `owui-net` and needs no anchor network at all - compose does not require an UNUSED external network to exist (measured against compose v5.3, 2026-09-19). |
| nothing else, hard | `[planes.frontend]` declares `requires = ["anchor"]`. Every other plane it touches is `optional`. |

**Five soft edges, and every one of them defaults ON.** The manifest records
them as `optional = ["inference", "search", "ob1", "portal", "agent-org"]`.
They are `HOST=${VAR:-<service>}` plus a `..._ENABLED` toggle that defaults
`true`, so a default boot reaches into four other planes and degrades - a dead
route or a 502 - when they are off. Open WebUI itself still serves.

| Plane | The alias or URL | What goes dead without it |
|---|---|---|
| inference | `LLAMA_CPP_HOST` defaults to `llama-cpp`, `LLAMA_CPP_EMBED_HOST` to `llama-cpp-embed` - the LiteLLM aliases on `ai-stack_llm-net`. Also `LITELLM_UI_HOST` defaults to `llm-gateway-ui`. | chat model calls, and the `/llama-cpp`, `/llama-cpp-embed` and `:8445` tailnet routes |
| search | `SEARXNG_QUERY_URL` defaults to `http://gateway:8080/search?q=<query>` | web search; chat is unaffected |
| ob1 | `OPEN_NOTEBOOK_HOST` defaults to `open_notebook`, an ob1-plane service | the `:8443` and `:5055` tailnet routes |
| portal | `QUARTZ_HOST` defaults to `caddy` **in this compose file** - the tailnet wiki route goes Tailnet > Caddy > wiki app on `caddy:8446`, which resolves because both share `ai-stack_app-net`. (The image's own fallback, in `entrypoint.sh`, is `openbrain-wiki-viewer`; the compose file overrides it.) | the `:8444` tailnet wiki route |
| agent-org | `MATTERMOST_HOST` defaults to `mattermost`. Declared in the IMAGE (`entrypoint.sh`), not in this compose file - a reader of the compose file alone does not see this edge. | the `:8446` tailnet Mattermost route |

Note the portal edge is the reverse of the portal's own: the portal plane HARD
`requires` the frontend, because Caddy's primary vhost proxies `openwebui:8080`.

## Surfaces

| Surface | Where |
|---|---|
| **Open WebUI** | `http://127.0.0.1:3000` - `127.0.0.1:3000:8080`, loopback only, in BOTH openwebui definitions. The one published host port in the plane. |
| Tailnet root | `https://<tailnet-host>/` to `127.0.0.1:8080`, configured by `entrypoint.sh` (`tailscale` profile only) |
| Tailnet sub-routes | seven more from the route table in `entrypoint.sh`: `/llama-cpp` and `/llama-cpp-embed` on `:443`, and root serves on `:8443` (open-notebook), `:5055` (open-notebook-api), `:8444` (quartz/wiki), `:8445` (litellm-ui) and `:8446` (mattermost). Eight `proxy http` mappings in total - which is exactly what `stack.py health` counts. |

Nothing else is published. External reach is the tailnet or the portal, never a
LAN bind.

## Host requirements and keys

From `[planes.frontend]` in [`../stack.manifest.toml`](../stack.manifest.toml):

- **Under `gpu` only:** the local CUDA build (`openwebui:local`) and an NVIDIA
  container runtime for the device reservation. **The default `stock` profile
  needs neither** - it pulls the pinned upstream image and runs on any host
  with Docker.
- **Under `tailscale` only:** a Tailscale auth key (`TAILSCALE_AUTH_KEY`). That
  profile requires `gpu`, because the pair is hard-wired.

| Key | Guard |
|---|---|
| `WEBUI_SECRET_KEY` | `${WEBUI_SECRET_KEY:?...}` on BOTH openwebui definitions, so a bare `up` without `frontend/.env` fails loudly. It encrypts values at rest in `webui.db`: **a recreate without it rotates the key and breaks every encrypted value and every session.** Pin it once; never rotate casually. |
| `TAILSCALE_AUTH_KEY` | No guard. A blank one gives you a tailscale container that cannot authenticate, not a refusal - and a dead auth key has crash-looped this container before. |
| `OWUI_CHAT_LLM_API_KEY` | No guard. OWUI's own LOW-PRIVILEGE LiteLLM virtual key, used by the status-pipe `llm-traffic` module for the gateway's read-only queue board. **Never the master key.** |

## First run

```powershell
Copy-Item frontend\.env.example frontend\.env   # then set WEBUI_SECRET_KEY
```

Migrating an existing host away from the single root `.env`:
[`../documentation/runbooks/env-split-migration.md`](../documentation/runbooks/env-split-migration.md).

`frontend/.env.example` ships `COMPOSE_PROFILES=stock`, which is the fresh-clone
value. This host's value is:

```
COMPOSE_PROFILES=gpu,tailscale
```

and only that - `COMPOSE_PROFILES` is per-plane since `sl-env-split` (D17), so
the inference plane's `local` lives in `inference/.env`. A duplicate assignment
in one env file is last-wins and silent, so keep exactly one.

Then the driver:

```powershell
python scripts\stack\stack.py init      # writes .stack/state.json enabling frontend alone
python scripts\stack\stack.py up        # anchor, then frontend
python scripts\stack\stack.py up frontend --dry-run   # print the docker line, run nothing
python scripts\stack\stack.py status
python scripts\stack\stack.py health    # 15 probes; exit code = failures
```

`init` refuses while `WEBUI_SECRET_KEY` is missing or blank, naming the key and
the file. `.\scripts\stack\stack.ps1 up frontend` is a shim over the same
driver.

By hand, **from the repo root** (the `-f` path is written relative to it;
`frontend/.env` is found wherever you stand, because compose loads it from the
PROJECT DIRECTORY, and there is no `--env-file` flag any more):

```powershell
docker compose -f frontend/docker-compose.yml up -d
docker compose -f frontend/docker-compose.yml config --services
docker compose -f frontend/docker-compose.yml down
```

**The netns rule.** `tailscale` shares `openwebui`'s network namespace, so
**never restart `openwebui` alone**: restart `openwebui`, wait for healthy, then
restart `tailscale`. Inside the project `depends_on: service_healthy` encodes
that order for whole-project operations; it cannot help a hand-typed
single-service restart. After a crash or a netns break use
`.\scripts\recovery\emergency-recovery.ps1 recover`.

**Rebuild deliberately, never as a side effect.** `openwebui:local` and
`tailscale:local` are pinned tags; the CUDA Dockerfile reinstalls torch, and
upgrades follow `documentation/runbooks/UPDATE-MANAGEMENT.md`. A plain `up -d`
does not rebuild an existing image - keep it that way.

## Where the live state is

| Mount | Kind | Live state? |
|---|---|---|
| `frontend_openwebui-data` to `/app/backend/data` | named volume | **Yes - the plane's real state.** `webui.db` (chats, users, plugins, connection secrets) plus uploads. Shared by BOTH openwebui definitions on purpose, so a host can move between `stock` and `gpu` and keep it. Migrated from `ai-stack_openwebui-data` on 2026-08-21 (data copied, ~10 GB). |
| `../data/tailscale` to `tailscale:/var/lib/tailscale` | host bind | **Yes.** The node's identity and state. Also mounted read-only into `openwebui` at `/host_project/data/tailscale` for the status pipe, and read-only into `tailscale-backup`. |
| `../status-pipe`, `../system-prompts` to `/host_project/...:ro` | host binds | Code, not state - and the ONLY code mounts into the container. They replaced an old whole-repo mount that shipped every on-disk secret into the internet-facing frontend. |
| `backups/openwebui`, `backups/tailscale` | host binds | Backup output, not a source of truth. |

There is **no `/app/config` mount** on either definition (removed 2026-09-19):
no OWUI code path read it, verified on the deployed image four ways.

Restore: `documentation/runbooks/restore-from-snapshot.md` (its frontend recipe
passes `--profile gpu --profile tailscale` itself), or
`scripts/backup/restore-from-snapshot.ps1 -SnapshotRoot .\backups -Date
<yyyy-MM-dd> [-Services <name|all>] [-Apply]`. `-SnapshotRoot` and `-Date` are
MANDATORY, and without `-Apply` it only plans.

## Gotchas

- **Without this plane's profiles in `frontend/.env`, every lifecycle path
  breaks quietly.** Measured 2026-09-19: `up -d` starts `openwebui-backup` and
  nothing else; `down` removes the backup and LEAVES `openwebui` and `tailscale`
  running, because compose only tears down services whose profile is active; and
  **any verb naming `tailscale`** - `up -d`, `stop`, `start`, `restart`, `rm`,
  `ps`, `config`, even `up -d --force-recreate --no-deps tailscale` - exits 1
  with `no such service: openwebui`. Naming a service activates only that
  service's own profile, and `--no-deps` does not help because the reference
  resolves at project load. `openwebui` is the one exception: the gpu
  definition names nothing outside its own profile.
  `scripts/checks/check-watchdog-repair-targets.ps1` is the check that tells you
  whether this host's value is right. Run it after editing `frontend/.env`.
- **A `--profile` flag REPLACES `COMPOSE_PROFILES` rather than adding to it.**
  That is why no frontend profile is `default` in the manifest: a default would
  hand a `stock` host `gpu`. It is also what lets a caller render this host's
  deployment from a `stock` env file by passing
  `--profile gpu --profile tailscale`, which `restore-from-snapshot.ps1` and the
  restore runbook both do.
- **`emergency-recovery.ps1` does not pass the profiles - it CHECKS them**
  (`Confirm-FrontendProfiles`) and logs an error naming the fix. A generic
  driver that hard-coded `gpu,tailscale` would start a CUDA build and reserve a
  GPU on a `stock` host.
- **Observers must fail open.** `stack.py health` prints
  `[skip] frontend: 8 tailnet serve routes` instead of a FAIL when the render
  says there is no tailscale container, and `stack-watchdog.ps1` skips its whole
  container-tailscale section the same way - both deciding from the RENDERED
  project, and both still complaining loudly if a container named `tailscale` is
  running while the render denies it.
- **`tailscale-backup` does get a network.** It declares none, so compose
  attaches it to the project default, which here is the external
  `ai-stack_default`. An older comment claimed no attachment; the render has
  never agreed.
- **The tailnet probe for LiteLLM-fronted routes is `/health/liveliness`.**
  Plain `/health` 401s since J.1 and is the model-load-thrash endpoint anyway.

## Changing this plane

Adding, removing or moving a container here is never a one-file change. The
full checklist is
[`../documentation/runbooks/SERVICE-LIFECYCLE.md`](../documentation/runbooks/SERVICE-LIFECYCLE.md);
the surfaces this plane appears on today are:

- [`docker-compose.yml`](docker-compose.yml) here, and the plane-internal build
  inputs beside it ([`Dockerfile.openwebui-gpu`](Dockerfile.openwebui-gpu),
  [`dockerfile.tailscale`](dockerfile.tailscale), [`entrypoint.sh`](entrypoint.sh),
  `.dockerignore`) - both `build.context` values are `.`
- [`../stack.manifest.toml`](../stack.manifest.toml) - the `[planes.frontend]`
  table, its three `profiles` sub-tables, and the `chat` product
- `scripts/stack/stack.py` - the two frontend probes in `HealthSweep.run()`, the
  `tailscale_deployed()` guard, and the labels pinned in `PS1_PROBES`
  (`scripts/stack/test_stack.py`). `scripts/stack/stack.ps1` is a shim over that
  driver since 2026-09-19 and holds no plane list of its own
- `scripts/recovery/emergency-recovery.ps1` - `$Script:FrontendServices` and
  `Confirm-FrontendProfiles`
- `scripts/checks/stack-watchdog.ps1` (tailnet serve repair) and
  `scripts/checks/check-watchdog-repair-targets.ps1`
- `scripts/backup/restore-from-snapshot.ps1` - the **`openwebui`** and
  `tailscale` catalog entries, which pass
  `--profile gpu --profile tailscale` themselves. The catalog is keyed by what
  you restore, not by the plane: there is no `frontend` key, and
  `-Services openwebui` is what restores `frontend_openwebui-data`
- `scripts/lib/stack-services.curated.json` - edit the CURATED sidecar, then run
  `python scripts/stack/stack.py inventory --write`
- `.claude/skills/stack-map/references/workspace-stacks.md` section 1a, and the
  plane table in [`../CLAUDE.md`](../CLAUDE.md)
- `documentation/CONTAINER-REGISTRY.md` and the backup / restore runbooks
- `scripts/agent-harness/lease-names.conf` - the `frontend` lease name

Run `/stack-map` afterwards; it checks for drift between these.
