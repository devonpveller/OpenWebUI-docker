# memory — the memory compose plane

The stack's **personal-memory layer**: `mnemory` (the unified memory service),
`mnemory-cloud-gateway` (the privacy-enforcing door cloud clients use), and
`mnemory-backup`. Its own compose project since 2026-08-21 (CLEANUP-PLAN Part
K.2), split out of the root `ai-stack` project. Definition-identical to the
former `compose/memory.yml` plus the `mnemory-backup` block from
`compose/backups.yml` — only relative paths and network/volume ownership
changed.

Everything below is checked against `memory/docker-compose.yml` and the live
containers; where a doc elsewhere disagrees, the compose file wins.

```
                        host loopback :8060
                               |
                     [ mnemory-cloud-gateway ]  <- the ONLY published door
                       memory_default + llm-net
                               |  http://mnemory:8050 (+ real key, X-User-Id)
   OWUI / OB1 / -------->  [ mnemory ]  --------> http://llama-cpp:8080/v1
   local llm-net callers    llm-net only          (LiteLLM alias, inference plane)
                               |
                          memory_mnemory-data
                               |  :ro
                      [ mnemory-backup ] -> ../backups/mnemory
```

## The three services

| Service | Container | What it is / what it is for |
|---|---|---|
| `mnemory` | `mnemory` | The memory layer itself. Serves the MCP/REST API on **8050** and a management/health endpoint on **8051** (`MGMT_PORT`). Stores to SQLite + embedded Qdrant under `/data`. Calls the LLM plane for synthesis (`qwen36-27b:nothink`) and embeddings (`qllama/bge-m3:latest`, 1024 dims) through the `llama-cpp` / `llama-cpp-embed` **aliases on LiteLLM** — never a direct upstream. Auth by `MCP_API_KEY` (+ additional `MCP_API_KEYS`). |
| `mnemory-cloud-gateway` | `mnemory-cloud-gateway` | Privacy-enforcing reverse proxy in front of mnemory's MCP endpoint, **for cloud clients only** (Claude Code and friends). Source lives in this repo: [`memory/mnemory-gateway/`](mnemory-gateway). Default-deny: reads are force-filtered to `labels.share == "cloud"`, writes are stamped `origin=cloud, share=cloud` with the `personal` category stripped, `user_id`/`agent_id` arguments are stripped, and everything outside a small allow-list (notably `ask_memories`, `get_core_memories`, `get_recent_memories`, and all update/delete/artifact tools) is blocked — including from `tools/list`, so the model never even sees them. Cloud clients hold `MNEMORY_GATEWAY_KEY` and **never** the real mnemory key; the gateway injects the real key plus a fixed `X-User-Id` (`BOUND_USER_ID`) upstream. |
| `mnemory-backup` | `mnemory-backup` | `alpine:3.21` sleep-loop (default 24 h) running [`../backup/mnemory-backup.sh`](../backup/mnemory-backup.sh): tars `/data` (mounted `:ro`) to `../backups/mnemory/mnemory-backup-<ts>.tar.gz` + `.sha256`, count-based retention (default keep 2). Not crond — crond misses fire-windows on Docker Desktop VM clock jumps. |

## Network and port posture

| Network | Ownership | Who is on it | Why |
|---|---|---|---|
| `llm-net` (`ai-stack_llm-net`) | **external** — owned by the root anchor project | `mnemory`, `mnemory-cloud-gateway` | The shared caller seam. It is `internal: true` on the anchor, so **mnemory has no internet access at all** and reaches inference only through the LiteLLM aliases. |
| `default` (`memory_default`) | project-local bridge | `mnemory-cloud-gateway`, `mnemory-backup` | Two jobs only: an `internal` network cannot publish a host port, so the gateway needs a non-internal net to be reachable on :8060; and it carries the backup sidecar's `HEALTH_TCP` precheck to the gateway. No cross-project consumers. |

**The only published port in this plane is `127.0.0.1:8060` → the cloud
gateway.** Loopback-bound, not `0.0.0.0`.

Deliberately **not** exposed:

- **`mnemory:8050`** (the API) — publishing it would hand any host-local process
  the unfiltered memory store. Trusted callers already reach it by DNS on
  `llm-net` (`http://mnemory:8050` — see `owui/tools/mnemory.py` and
  `owui/filters/mnemory_persistent_memory.py`). Cloud clients are *not* trusted
  with it; the gateway exists precisely so they cannot have it.
- **`mnemory:8051`** (management/health) — an internal probe surface only. A
  2026-05 portal audit chased `mnemory:8051` as an address it could not reach;
  it is on `llm-net` only, and that is intentional.
- The `*-upstream` inference servers are on the inference project's private
  `llm-backend-net`, so nothing here can route around LiteLLM even by accident.

**The doors rule, stated once:** local callers on `llm-net` talk to `mnemory`
directly and get everything; cloud callers get the gateway and only what the
policy allows. Do not add a host port to `mnemory`, and do not widen the
gateway allow-list, without treating it as a privacy change.

## Volumes and where the live state is

| Mount | Kind | Live state? |
|---|---|---|
| `memory_mnemory-data` → `mnemory:/data` | named volume | **Yes — the only live state in this plane.** SQLite (WAL) + embedded Qdrant. Migrated from `ai-stack_mnemory-data` on 2026-08-21 (data copied). |
| `memory_mnemory-data` → `mnemory-backup:/data:ro` | same volume, read-only | read-only view for the tar |
| `../backups/mnemory` → `mnemory-backup:/backups` | host bind | backup artifacts (output, not source of truth) |
| `../backup/mnemory-backup.sh` → `/scripts/backup.sh:ro` | host **file** bind | the backup script. If that file ever goes missing, Docker silently creates an empty *directory* in its place and the sidecar fails quietly — a known stack-wide failure shape. |

The gateway and the backup sidecar hold no state of their own.

> **Volume-name trap.** The host carries three similarly-named volumes:
> `memory_mnemory-data` (**live**, created 2026-08-21),
> `ai-stack_mnemory-data` (the pre-split rollback copy from 2026-04-19 — nothing
> reads it; it survives until the post-K soak cleanup), and
> `mnemory_mnemory-data` (from the sibling mnemory repo's own compose file).
> Restoring into the wrong one looks like a successful restore and changes
> nothing. Confirm with
> `docker inspect mnemory --format "{{range .Mounts}}{{.Name}}{{end}}"`.

Restore procedure: `documentation/runbooks/restore-from-snapshot.md` ("Other
tar-based services") and `documentation/runbooks/backup-restore-runbook.md` §7.

## Bringing it up and down

Preferred — the plane driver, which knows the ordering:

```powershell
.\scripts\stack\stack.ps1 up      memory
.\scripts\stack\stack.ps1 down    memory
.\scripts\stack\stack.ps1 restart memory
.\scripts\stack\stack.ps1 health          # probes http://127.0.0.1:8060/health
```

Manual, **from the repo root** (compose resolves `--env-file` against your cwd
but every `context:` and bind path against the *compose file*, so the root is
the only cwd where both are correct):

```powershell
docker compose -f memory/docker-compose.yml --env-file .env up -d
docker compose -f memory/docker-compose.yml --env-file .env ps
docker compose -f memory/docker-compose.yml --env-file .env down
```

A bare `up` without `--env-file` fails loudly on the `${MCP_API_KEY:?}` guard —
by design. Note the asymmetry: `MNEMORY_GATEWAY_KEY`, `MCP_API_KEYS` and
`MNEMORY_CLOUD_USER` have **no** `:?` guard, so a partially-populated `.env`
gives you a gateway with an empty key instead of a hard failure.

After a crash or a netns break, use the ordered driver instead:
`.\scripts\recovery\emergency-recovery.ps1 recover` — it starts this plane as a
unit and waits up to 90 s for `mnemory` before moving on.

**Order inside the plane** is expressed with `depends_on: service_healthy`:
`mnemory` → `mnemory-cloud-gateway`, and `mnemory-backup` also waits on
`mnemory`. Let compose sequence it; `down` reverses it. Restarting `mnemory`
alone is safe — the gateway retries — but the backup precheck will skip for as
long as the gateway is unreachable.

## Where this plane sits in the stack order

Anchor (networks) → **inference** → frontend → **memory** → search → coder →
open-brain → agent-org (declared by the order of the `[planes.*]` tables in
`stack.manifest.toml`, which `scripts/stack/stack.py` topologically sorts;
`emergency-recovery.ps1` uses the same relative position).

Upward: memory needs the anchor's `ai-stack_llm-net` to exist, and the inference
plane's `llm-gateway` to be answering on the `llama-cpp` / `llama-cpp-embed`
aliases. **`depends_on` cannot express that** — it is a different compose
project — so the ordering is enforced by the driver scripts, plus mnemory
retrying until the gateway answers (the same posture as OB1). Starting memory
before inference is not fatal, just noisy.

Downward: OWUI's mnemory tool and filter, and the cloud MCP clients, depend on
this plane. Tear it down *before* the anchor, and after its consumers if you
care about clean logs.

## Gotchas (each verified against the file, the script, or the running plane)

- **`mnemory` is pinned to `mnemory:local` with `pull_policy: never`.** A fresh
  `up --build` pulls an unpinned newer `mcp` package whose `fastmcp` module
  moved → `ModuleNotFoundError` crash loop (found 2026-08-21). Rebuild
  deliberately, after pinning deps in the mnemory source — never as a side
  effect of `up --build`.
- **The mnemory build context is outside this repository.** `context:
  ../../mnemory` resolves to the *sibling* `mnemory` checkout next to
  `ai-stack/`, not to anything inside this repo. A clean clone of ai-stack alone
  cannot build `mnemory:local`; it can only run a pre-built image. (The gateway
  is different — `context: ./mnemory-gateway` is in-repo.)
- **The compose healthcheck overrides the image's own.** The mnemory Dockerfile
  `HEALTHCHECK` hits `:8050/health`; compose replaces it with `:8051/health`
  (`MGMT_PORT`). Change `MGMT_PORT` and you must change the healthcheck with it,
  or the container never reports healthy and both dependents stay down.
- **`HEALTH_TCP` must be `mnemory-cloud-gateway:8060`, not 8080.** The old 8080
  default made the backup precheck exit 0 with no artifact and no alert, nightly
  from 2026-05-29 to 2026-07-05. That is why the port is called out inline in
  the compose file.
- **A down gateway silently stops backups.** The precheck is deliberate — tarring
  a live SQLite WAL mid-flush is not recoverable — but "skip" is exit 0. The
  safety net is `scripts/checks/stack-watchdog.ps1`, which alerts when
  `backups/mnemory` holds no artifact newer than 52 h. Trust that, not the
  sidecar's exit code.
- **`MNEMORY_BACKUP_INTERVAL` and `BACKUP_RETAIN_COUNT` are absent from `.env`**,
  so the plane runs on the compose defaults: daily, keep 2. Two nightly
  snapshots is the real local retention window — anything longer lives on the
  NAS sync.
- **`mnemory` has no internet.** `llm-net` is `internal: true`. Any feature that
  wants outbound HTTP has to be re-designed, not merely configured.
- **The watchdog restarts `mnemory` and `mnemory-backup` as auxiliaries** and
  probes the gateway separately. A container that appears to have "fixed itself"
  overnight may simply have been restarted for you — read the watchdog log
  before concluding it was a flap.

## Changing this plane

Adding, removing or moving a container here is never a one-file change. The full
checklist is `documentation/runbooks/SERVICE-LIFECYCLE.md`; the surfaces this
plane actually appears on today are:

- `memory/docker-compose.yml` (here)
- `stack.manifest.toml` — the `[planes.memory]` table (compose file, `env_file`,
  `requires`, `ports`, `keys`)
- `scripts/stack/stack.py` — the plane's probe in `HealthSweep.run()`, and its
  label in `PS1_PROBES` (`scripts/stack/test_stack.py`), which pins the probe set.
  `scripts/stack/stack.ps1` is a shim over that driver since 2026-09-19 and holds
  no plane list and no probe of its own
- `scripts/recovery/emergency-recovery.ps1` — `$Script:MemoryServices`, `Start-PlaneStack "memory"`
- `scripts/checks/stack-watchdog.ps1` — auxiliary restarts + `$ExpectedBackupRecency`
- `scripts/lib/stack-services.curated.json` — the `memory` section and the backup
  row. Edit the CURATED sidecar, then run
  `python scripts/stack/stack.py inventory --write`:
  `scripts/lib/stack-services.json` is generated and `inventory --check` (in the
  pre-commit hook and in CI) refuses a hand edit
- `.claude/skills/stack-map/references/workspace-stacks.md` §1c, and the plane table in `CLAUDE.md`
- `documentation/CONTAINER-REGISTRY.md`,
  `documentation/runbooks/restore-from-snapshot.md`,
  `documentation/runbooks/backup-restore-runbook.md`
- `scripts/worktree/lease-names.conf` — the `memory` lease name, if the plane's
  fault domain changes

Run `/stack-map` afterwards; it checks for drift between these.
