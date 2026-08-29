# coder - the little-coder control plane

Compose project **`coder`** (`coder/docker-compose.yml`), split out of the root
`ai-stack` project on 2026-08-21 by CLEANUP-PLAN Part **K.4**. Four services run
the self-improving coding agent: a control plane that **decides**, a workspace
plane that **executes**, an allowlisted egress proxy, and a backup sidecar.
Keeping the deciding and the executing in separate containers is this plane's
safety surface, and most of what follows - the network shape, the single
published port, what you may rebuild - falls out of that.

## Which document owns what

| Question | Read |
|---|---|
| **How do I run, stop, wire or debug these four containers?** | **this file** |
| What the agent *is*: inner loop, journals, cohorts, skill library, chapters | [`Self-improving-little-coder-design.md`](../documentation/implementation-guide/little-coder/Self-improving-little-coder-design.md) |
| The agent source, its operator CLI and tests | [`../little-coder/README.md`](../little-coder/README.md) |
| Where this plane sits among all the others | [stack-map reference](../.claude/skills/stack-map/references/workspace-stacks.md) |
| Adding, removing or moving a container here | [`SERVICE-LIFECYCLE.md`](../documentation/runbooks/SERVICE-LIFECYCLE.md) |
| Restoring a volume from a snapshot | [`restore-from-snapshot.md`](../documentation/runbooks/restore-from-snapshot.md) |

## Services

| Service / container | Image | Role |
|---|---|---|
| `little-coder` | `little-coder:local` | **Control plane - DECIDES.** The daemon on `:8090` that takes triggers, plans, and runs the inner loop, and owns all accumulated expertise. Prometheus metrics on `:9090`. |
| `open-terminal` | `little-coder-open-terminal:local` | **Workspace plane - EXECUTES.** `POST /execute` on `:8000`, API-keyed. Every build, test and git command the agent issues runs here. |
| `lc-egress` | `little-coder-egress:local` | tinyproxy forward proxy on `:8888`, default-deny by host. The only route from this plane to the internet. |
| `little-coder-backup` | `alpine:3.21` | Sleep-loop sidecar; tars the state volumes into `../backups/little-coder`. |

Service name and container name are identical for all four, so `docker logs
little-coder` and `docker exec open-terminal ...` work as written.

### Decide vs execute, and what that constrains

- The agent's only execution path is `bash -> open-terminal -> git-proxy`, and the
  switch is `LC_ROUTE_EXEC` (default `1`). **Leave it at `1`.** Setting it to `0` moves
  execution into `little-coder`, which has no internet on either of its networks and
  does not go through git-proxy - you lose the egress allowlist and the git chokepoint
  together, and it shows up as "the clone hangs", not as a policy change.
- Inside `open-terminal`, `git` **is** the git-proxy wrapper; the real binary is
  relocated to `/usr/bin/git.real`, and `core.hooksPath` is baked at image build to an
  empty, read-only directory outside `.git/`, so hooks a cloned repo brings with it are
  never executed. Both are image-level - only a rebuild changes them.
- **This bounds accidents, not a hostile repo.** `open-terminal` runs as root and the
  workspace volume is read-write in both containers; git-proxy blocks `git config`
  writes at the command level and the workspace shell filters the obvious direct-write
  bypasses, but a root process that writes `.git/config` another way still gets there.
  Point this plane at repos you trust.
- Both containers mount `little-coder-workspace`, so the agent edits files directly
  while execution stays in the isolated container. The volume crosses container uids,
  hence `git safe.directory = *` in both images.
- `../little-coder/config` is mounted read-only at `/app/config` and read at boot, so
  **restart `little-coder`** after editing `little-coder.config.yaml` or `models.json`.

## Networks and ports

| Network | Kind | Attached | Carries |
|---|---|---|---|
| `ai-stack_llm-net` | **external**, `internal: true`, owned by the root anchor | `little-coder`, `open-terminal` | inference via the `llama-cpp` alias, and the inbound seam for OWUI's pipe |
| `coder_lc-net` | native to this project, `internal: true` | `little-coder`, `open-terminal`, `lc-egress` | control plane to executor to egress. No internet. |
| `coder_default` | project-local bridge | `lc-egress`, `little-coder-backup` | `lc-egress`'s internet leg. The backup sidecar declares no networks, so compose attaches it here. |

**The whole plane publishes exactly one host port** -
`127.0.0.1:9091 -> little-coder:9090`, Prometheus metrics, loopback only.
Everything else is deliberately unpublished:

- **`little-coder:8090`** accepts work, so it stays off the host's TCP stack.
  In-stack callers use the DNS name over `llm-net` - OWUI's pipe defaults to
  `http://little-coder:8090` (`owui/pipes/little_coder.py`).
- **`open-terminal:8000`** is arbitrary command execution behind an API key.
- **`lc-egress:8888`** is reachable from `lc-net` only.
- `llm-net` is itself `internal: true`, so a port published on a service attached
  only to it would be inert anyway. See [Checking it](#checking-it) for the
  `docker exec` probes that stand in for a host port.

**Egress.** `open-terminal` carries `HTTP(S)_PROXY=http://lc-egress:8888` with
`NO_PROXY=llama-cpp,little-coder,lc-egress,localhost,127.0.0.1`; both of its
networks are internal, so that proxy is its only way out. `lc-egress` runs
`FilterDefaultDeny Yes` against `little-coder/docker/egress-allowlist.txt`
(`github.com` and `githubusercontent.com` enabled) with `CONNECT` limited to
ports 443 and 22. `little-coder` itself has no internet at all.

**To clone from another forge**, add its host pattern to that allowlist file and
**rebuild `lc-egress`** - the list is `COPY`d into the image, so a restart will
not pick it up. Until then the clone fails looking like network flakiness.

## Volumes and state

Six named volumes, all `coder_little-coder-*`.

| Volume | Holds | Backed up |
|---|---|---|
| `little-coder-journals` | the three journals + `audit.jsonl` | yes |
| `little-coder-skill` | the artifact / skill library | yes |
| `little-coder-cohorts` | derived counters + repro corpora | yes |
| `little-coder-polyglot` | canonical Polyglot clone | yes |
| `little-coder-sessions` | pi session files, one per OWUI chat / CLI channel | yes |
| `little-coder-workspace` | the focused project clone, shared with `open-terminal` | **no, by design** |

The first four are the **expertise volumes** - accumulated, append-only, and the reason
this plane has state worth protecting. `little-coder-workspace` is different in kind:
`/project` switching wipes and re-clones it, so it is excluded from backup deliberately,
an exemption recorded in `scripts/checks/check-backup-coverage.ps1`. `docker compose up
-d --build` recreates containers but **preserves volumes** - treating an expertise volume
as inside-container state wipes accumulated expertise on the first rebuild.

**Backups.** `little-coder-backup` mounts five volumes read-only under `/data`
and tars whatever is there every `LITTLE_CODER_BACKUP_INTERVAL` seconds (default
86400), keeping `LC_BACKUP_RETAIN_COUNT` archives (default 2); neither is set in
`.env`, so both defaults apply. **Check the newest tarball's timestamp, not the
sidecar's exit code** - `backup/little-coder-backup.sh` deliberately exits 0 when
`/data` is missing or empty, so a clean exit is not proof of an artifact.
`stack-watchdog.ps1` age-checks `./backups/little-coder` at 52 hours for exactly
that reason.

## Bringing it up and down

Preferred - the workspace driver knows the plane order and passes the root
`.env` for you (`up`, `down`, `restart`, `status` all take a plane name):

```powershell
.\scripts\stack\stack.ps1 up coder
.\scripts\stack\stack.ps1 down coder
```

By hand, **from the repository root**:

```powershell
docker compose -f coder/docker-compose.yml --env-file .env up -d
docker compose -f coder/docker-compose.yml --env-file .env down
```

**`--env-file .env` is not optional, and its path resolves against your current
directory** - which is why you run these from the repo root. Two mechanisms are in play
and only the first cares where you are standing: `--env-file` feeds the compose CLI's
`${VAR}` interpolation, including the `${OPEN_TERMINAL_API_KEY:?...}` guard that makes a
bare `up` fail loudly rather than start a keyless executor, while `env_file: ../.env` in
the service definitions injects the root `.env` into the containers and resolves relative
to the compose file, so it finds that same file whatever your cwd.

Two things to know before editing `.env` for this plane:

- **`OPEN_TERMINAL_API_KEY` is defined twice** and compose takes the last one, so
  rotating only the first occurrence yields a 401 that looks like a code bug. Run
  `grep -n "^OPEN_TERMINAL_API_KEY=" .env` before you touch it.
- **Keep `LC_LLAMA_API_KEY` set.** It is passed through bare as `LLAMACPP_API_KEY` while
  the sibling variable defaults to `llama`, so unsetting it boots the agent with an empty
  bearer for the gateway - a 401 - while the other still reads fine.

Crash recovery - health gates, GPU repair, ordered restart across every
project - is `scripts/recovery/emergency-recovery.ps1`, not this file.

## Where it sits in the dependency order

`scripts/stack/stack.ps1` runs planes top to bottom on `up`, reversed on `down`:

```
anchor -> inference -> frontend -> memory -> search -> CODER -> ob1 -> agent-org
```

- **Needs first:** the root **anchor**, which creates `ai-stack_llm-net` (without it
  the external network reference fails outright), and the **inference** plane answering
  on the `llama-cpp` alias. There is no cross-project `depends_on` - compose cannot
  express one - so order is enforced by `stack.ps1` and `emergency-recovery.ps1`, and
  the services retry until the gateway answers.
- Inside the plane `little-coder` waits on `open-terminal` being healthy. `lc-egress`
  has no `depends_on` and nothing waits on it; tinyproxy starts fast and requests
  retry, but a very early clone can race it.
- **Nothing else in the stack blocks on `coder`** - the watchdog classes it as
  auxiliary, and taking it down takes nothing else down.

## Checking it

```powershell
.\scripts\stack\stack.ps1 health     # functional probes, all planes
docker exec little-coder  curl -fsS http://localhost:8090/health
docker exec open-terminal curl -fsS http://localhost:8000/health
```

- **`stack.ps1 health` covers the daemon, not the executor.** Its coder probe curls
  `little-coder:8090/health`, which reports only the daemon's own state - status,
  version, focus, queue depth, in-flight count - so a dead `open-terminal` passes that
  sweep green. Probe it directly with the third command above.
- **Both container healthchecks curl their own localhost**, so they stay green while
  `llm-net` DNS, the gateway or the virtual key is broken; `stack.ps1 health` is the
  functional probe.
- **Restart one service with the full `-f` form**, from the repo root:
  `docker compose -f coder/docker-compose.yml --env-file .env up -d open-terminal`.
  A bare `docker compose up -d <service>` at the repo root silently does nothing - the
  root project is a network anchor with zero services.
- **Read the rendered config as YAML, not `--format json`.** PowerShell 5.1's
  `ConvertFrom-Json` is case-insensitive and dies with `DuplicateKeysInJsonString` on
  this plane's `HTTP_PROXY` / `http_proxy` pair.

## Changing this plane

- **`--build` here retags images another project runs.** agent-org's worker pool
  uses `little-coder:local` and `little-coder-open-terminal:local` with no build
  stanza of its own, and its egress services build the same
  `little-coder-egress:local` tag this plane runs. Neither pins a digest, so a
  rebuild on either side changes what the other gets on its next recreate. Plain
  `up -d` does not rebuild an existing image; keep it that way unless the retag
  is what you mean. Those three `:local` images also have no registry path - they
  exist nowhere but this tree, so losing one means a rebuild, not a pull.
- **The context window comes from `little-coder/config/models.json`, currently
  90000.** `LITTLE_CODER_NO_CTX_PROBE=1` is deliberate: the provider's startup
  probe asks llama.cpp for `/props`, which the LiteLLM gateway does not forward,
  so the declared value is what the agent actually uses. It must stay under the
  `qwen36-27b` per-request lane (`ctx-size / n_parallel`); above that lane it is
  a latent overflow. Restart `little-coder` after changing it.
- **Adding, removing or moving a container is never a one-file edit.** The full checklist
  is [`SERVICE-LIFECYCLE.md`](../documentation/runbooks/SERVICE-LIFECYCLE.md); at minimum
  it is this compose file plus `scripts/recovery/emergency-recovery.ps1`
  (`$Script:CoderServices`), `scripts/stack/stack.ps1`, `scripts/checks/stack-watchdog.ps1`,
  `scripts/checks/check-backup-coverage.ps1`, the backup and restore runbooks, and the
  stack-map reference. Run `/stack-map` to check for drift.
- **Never route inference around LiteLLM.** Both containers reach it through the
  `llama-cpp` alias on `llm-net`; `scripts/checks/check-llm-gateway-routing.ps1`
  enforces this pre-commit.
