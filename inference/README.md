# inference - the inference compose plane

The stack's **LLM front door**. One compose project (`name: inference`,
[`docker-compose.yml`](docker-compose.yml)) holding LiteLLM, its ledger DB, the
Admin-UI sidecar, the admission queue, the two real llama.cpp servers and two
backup sidecars. Its own project since 2026-08-21 (CLEANUP-PLAN Part K.1);
split by service group into four `include:`d files on 2026-09-19
(stack-layers `sl-inference-split`), which changed no project name, no
container name and no rendered definition.

Everything below is checked against the four files under
[`compose/`](compose) and the spine; where a doc elsewhere disagrees, the
compose files win.

```
   every caller on ai-stack_llm-net
   (OWUI, mnemory, little-coder, OB1, agent-bridge)
              |  http://llama-cpp:8080  /  http://llama-cpp-embed:8080
              |  (network ALIASES, not hostnames of a model server)
              v
       [ llm-gateway ]  LiteLLM - the only member of both networks
              |                \
              |  llm-backend-net `-- [ llm-gateway-db ]  (llm-net)
              v                              ^
        [ llm-queue ]  hold-and-dispatch     | reads the same ledger
              |                       [ llm-gateway-ui ]  (llm-net + app-net)
              v
   [ llama-cpp-upstream ]   [ llama-cpp-embed-upstream ]   <- the real servers
        127.0.0.1:8081           127.0.0.1:8082            (probes only)
```

## What starts

`docker compose -f inference/docker-compose.yml config --services` renders
**4** services with no profile and **8** with `local`. Service name and
container name are identical for all eight.

| Service / container | Profile | What it is | Defined in |
|---|---|---|---|
| `llm-gateway` | - | LiteLLM, the caller-facing front door. Carries the `llama-cpp` and `llama-cpp-embed` **aliases** on `llm-net`, so every caller's `http://llama-cpp:8080` lands here and is routed by model name. The image is digest-pinned; `master_key` plus per-caller virtual keys are enforced since J.1, and the pre-call hook turns a key's `metadata.lane` into the `x-ai-stack-caller` header the queue attributes on. | `compose/gateway.yml` |
| `llm-gateway-db` | - | `postgres:16-alpine` holding the LiteLLM spend ledger (volume `inference_llm-gateway-db-data`). | `compose/gateway.yml` |
| `llm-gateway-ui` | - | A SECOND LiteLLM instance, run only for the Admin UI at `/ui`. Shares `llm-gateway-db` so the dashboard reads the same ledger. It carries **no alias** and serves no inference. | `compose/gateway.yml` |
| `llm-gateway-backup` | - | `postgres:16-alpine` sleep-loop (86400 s, hard-coded in the entrypoint - not a variable) running [`../backup/llm-gateway-backup.sh`](../backup/llm-gateway-backup.sh) into `backups/llm-gateway`; `RETAIN_DAYS` comes from `LITELLM_BACKUP_RETAIN_DAYS`, default 7. | `compose/backups.yml` |
| `llama-cpp-upstream` | `local` | llama-swap (`ghcr.io/mostlygeek/llama-swap:cuda`) serving the chat GGUFs, one model resident at a time. Its healthcheck is liveness **plus** a slot watchdog: a slot that has been processing for more than 900 s gets `llama-server` killed so Docker restarts the container. | `compose/upstreams.yml` |
| `llama-cpp-embed-upstream` | `local` | `ghcr.io/ggml-org/llama.cpp:server-cuda` serving `bge-m3-f16.gguf` (`LLAMA_ARG_CTX_SIZE=20480`, `N_PARALLEL=2`, embeddings on). | `compose/upstreams.yml` |
| `llm-queue` | `local` | The B2 admission controller, built from [`llm-queue/`](llm-queue) as `llm-queue:local`. Sits BETWEEN LiteLLM and the upstreams, on `llm-backend-net` only, holding and dispatching instead of letting llama-swap drop the overflow with a flat 429. Keeps its own SQLite event store on `inference_llm-queue-data`. | `compose/queue.yml` |
| `lm-models-backup` | `local` | `alpine:3.21` sleep-loop, weekly by default (`LM_MODELS_BACKUP_INTERVAL`, 604800), tarring `${LM_MODELS_DIR}` into `backups/lm-models`; it skips the very large tar when an artifact younger than `MIN_AGE_SECS` (43200) already exists. Its `HEALTH_TCP` probes `llama-cpp-upstream:8080` **by upstream name**, never the alias - the alias now resolves to the gateway and would report "reachable" with the model server down. | `compose/backups.yml` |

**Each of the four group files declares its own `x-hardening` anchor**
(`sl-compose-anchors`, 2026-09-19): `security_opt: [ no-new-privileges:true ]`,
the one key every service in this plane shares, written once per file and merged
in with `<<: *hardening`. It is per FILE and not per plane because a YAML anchor
does not cross an `include:` boundary - four files, four identical declarations,
and that is the correct shape rather than duplication to remove. **The trap when
you edit: a merge key merges MAPS, and a LIST written on a service REPLACES the
anchored list rather than appending to it**, so a service wanting a second
`security_opt` entry must spell out the whole list. Hardening under a DIFFERENT
key is just that key and merges cleanly - which is what `lm-models-backup`'s
`cap_drop: [ALL]` is. No rendered service definition changed.

**The `local` profile is the whole GPU half of the plane.** With it off the
project is a LiteLLM gateway + ledger + Admin UI + ledger backup: a front door
that can serve CLOUD models from a machine with no GPU (stack-layers D11).
`llm-gateway`'s `depends_on` entries for the three profiled services carry
`required: false` for exactly that reason - without it compose refuses to
render at all when the profile is off.

## Requires

| Needs | What makes it so |
|---|---|
| **anchor** (the root `docker-compose.yml`) | `llm-net` and `app-net` are `external: true` here, named `ai-stack_llm-net` and `ai-stack_app-net`. Without the anchor, compose fails with `network ai-stack_llm-net declared as external, but could not be found`. Create them with `docker compose up -d` at the repo root, or `python scripts/stack/stack.py up anchor`. |
| nothing else | `[planes.inference]` in [`../stack.manifest.toml`](../stack.manifest.toml) declares `requires = ["anchor"]` and `optional = []`. This is a FOUNDATION plane: it needs only the host. |

Who needs **it** is the long list: `memory`, `coder`, `ob1` and `agent-org` all
declare `inference` in their `requires`, and the frontend lists it as
`optional`. Every one of them reaches it by the two aliases and nothing else.

## Surfaces

| Surface | Where | Notes |
|---|---|---|
| `http://llama-cpp:8080/v1` | `ai-stack_llm-net`, in-cluster only | The chat API. An alias on `llm-gateway`, not a machine. |
| `http://llama-cpp-embed:8080/v1` | same | The embeddings API, same container. |
| LiteLLM Admin UI `/ui` | `llm-gateway-ui`, **no host port** | Reached over the tailnet on `:8445` (the serve route in [`../frontend/entrypoint.sh`](../frontend/entrypoint.sh)), and through the portal's Caddy, which is why this service also joins `app-net`. |
| `127.0.0.1:8081` and `127.0.0.1:8082` | the two upstreams, loopback | **Probes only**, and the only published host ports in the plane. They exist for health, GPU and recovery checks. |

`llm-gateway` publishes **no** host port on purpose: it sits on `llm-net`
(`internal: true`) and `llm-backend-net` (`internal: true`), so a `ports:`
mapping here would show in `docker inspect` and never bind. Admin and ledger
access is in-network - `docker exec llm-gateway ...`, `docker exec
llm-gateway-db psql -U litellm -d litellm -c '...'`.

**Never GET LiteLLM `/health` through the alias.** It makes the gateway load
every model it advertises. Use `/health/liveliness`, which is what both compose
healthchecks and `stack.py health` do.

**Never route inference around LiteLLM.** Only health, GPU and recovery probes
may target `*-upstream` directly; `scripts/checks/check-llm-gateway-routing.ps1`
enforces that at commit time, and the topology backs it: the upstreams sit on
`llm-backend-net`, native to this project and `internal: true`, so nothing
outside the project can reach them at all.

## Host requirements and keys

From `[planes.inference]` in [`../stack.manifest.toml`](../stack.manifest.toml),
which is the manifest of record:

- **An NVIDIA GPU and the NVIDIA Container Toolkit** - `local` profile only
  (the two `deploy.resources.reservations.devices` blocks in
  `compose/upstreams.yml`).
- **Chat GGUFs under `${LM_MODELS_DIR}`** (the read-only `/models` bind on
  `llama-cpp-upstream`, and the same directory again on `lm-models-backup`).
  The compose default is the in-repo `data/models/gguf`; the operator's real
  path lives in `inference/.env`.
- **`bge-m3-f16.gguf` in `data/models/embeddings`** (`LLAMA_ARG_MODEL` on
  `llama-cpp-embed-upstream`).

Keys that must be non-blank before `stack.py enable inference` will pass:

| Key | Guard |
|---|---|
| `LITELLM_DB_PASSWORD` | `${LITELLM_DB_PASSWORD:?...}` on `llm-gateway-db`, so a bare `up` without `inference/.env` fails loudly instead of starting Postgres with empty credentials. |
| `LITELLM_MASTER_KEY` | No `:?` guard. Blank means every caller's bearer is rejected at runtime, which is why the manifest lists it: `stack.py doctor` and `stack.py enable` refuse on it. |

Note the asymmetry: `LITELLM_UI_MASTER_KEY`, `LITELLM_UI_USERNAME` and
`LITELLM_UI_PASSWORD` have no guard either, so a half-filled file gives you an
Admin UI you cannot log into rather than a refusal.

## First run

```powershell
Copy-Item inference\.env.example inference\.env   # then fill it in
```

Migrating an existing host away from the single root `.env`:
[`../documentation/runbooks/env-split-migration.md`](../documentation/runbooks/env-split-migration.md).

`inference/.env.example` ships `COMPOSE_PROFILES` **commented out**
(`#COMPOSE_PROFILES=local`), so a straight copy of the example renders the
cloud-only four. To run the local backends, uncomment it:

```
COMPOSE_PROFILES=local
```

and nothing else - `COMPOSE_PROFILES` is per-plane since `sl-env-split` (D17),
so the frontend's `gpu,tailscale` belongs in `frontend/.env` and naming it here
would do nothing. **Set it in the file; do not pass `--profile local`:**
`llm-gateway` reads `COMPOSE_PROFILES` itself to decide which model groups to
register, so the flag alone starts the backends and registers none of their
models.

Then the driver, which knows the plane order and starts the anchor first:

```powershell
python scripts\stack\stack.py enable inference        # writes .stack/state.json
python scripts\stack\stack.py up inference            # this plane only
python scripts\stack\stack.py up                      # everything this machine enables
python scripts\stack\stack.py up inference --dry-run  # print the docker line, run nothing
python scripts\stack\stack.py health                  # 15 probes; exit code = failures
```

`enable inference` refuses while `anchor` is off and while either key above is
blank, naming the key and the file. `up inference` starts ONLY this plane and
prints a note saying what it assumes is already running.
`.\scripts\stack\stack.ps1 up inference` is a shim over the same driver.

By hand, **from the repo root** (the `-f` path below is written relative to it;
`inference/.env` is found wherever you stand, because compose loads it from the
PROJECT DIRECTORY):

```powershell
docker compose -f inference/docker-compose.yml up -d
docker compose -f inference/docker-compose.yml ps
docker compose -f inference/docker-compose.yml config --services   # 4, or 8 with `local`
docker compose -f inference/docker-compose.yml down
```

There is no `--env-file` flag anywhere in that list, from any working
directory; that mechanism was retired on 2026-09-19.

After a crash or a wedged GPU, use the ordered path instead:
`.\scripts\recovery\emergency-recovery.ps1 recover`, or `gpu-reset`.

**Order inside the plane** is expressed with `depends_on: service_healthy`:
`llm-gateway-db` and - under `local` - `llama-cpp-upstream`,
`llama-cpp-embed-upstream` and `llm-queue` all gate `llm-gateway`, and
`llm-queue` additionally waits on `llama-cpp-upstream`. Let compose sequence it;
`down` reverses it.

## Where the live state is

| Mount | Kind | Live state? |
|---|---|---|
| `inference_llm-gateway-db-data` to `llm-gateway-db:/var/lib/postgresql/data` | named volume | **Yes.** The LiteLLM spend ledger. Migrated from `ai-stack_llm-gateway-db-data` on 2026-08-21 (data copied). |
| `inference_llm-queue-data` to `llm-queue:/data` | named volume | **Yes**, but derived: the queue's own admit/finish/reject analytics in SQLite, NOT LiteLLM's schema. Migrated from `ai-stack_llm-queue-data` the same day. |
| `${LM_MODELS_DIR}` to the upstream `:/models:ro` and the backup `:/data:ro` | host bind | The GGUF store. Large, read-only to the containers, tarred weekly. |
| `config/litellm.config.yaml`, `config/litellm/model_list/`, `config/litellm/assemble-config.py`, `config/litellm/custom_callbacks.py`, `config/litellm.ui.config.yaml`, `config/llama-swap.config.yaml`, `config/chat-template.jinja` | host binds, `:ro` | Plane-internal since `sl-colo-inference` (2026-09-19). Editing one of these and restarting the container is how the gateway's behaviour changes. |
| `backups/llm-gateway`, `backups/lm-models` | host binds | Backup output, not a source of truth. |

Restore: `documentation/runbooks/restore-from-snapshot.md`, or the orchestrated
`scripts/backup/restore-from-snapshot.ps1 -SnapshotRoot .\backups -Date
<yyyy-MM-dd> [-Services <name|all>] [-Apply]`. `-SnapshotRoot` and `-Date` are
MANDATORY, and without `-Apply` the script only plans. **The catalog is keyed by
what you restore, not by the plane:** this plane's one key is `lm-models` (the
GGUF store). The LiteLLM ledger has no orchestrated entry - restore it from the
`pg_dump` in `backups/llm-gateway` by hand.

## Gotchas

- **A `--profile` flag REPLACES `COMPOSE_PROFILES`; it does not add to it.**
  Measured on compose v5.3.0 against this plane: `config --services` renders 8
  with `local` in `inference/.env`, and 4 the moment any unrelated `--profile`
  flag is passed. The driver's `effective_profiles()` re-passes the env's own
  list for exactly that reason.
- **The model list is assembled at container start** by
  `config/litellm/assemble-config.py`, from `config/litellm.config.yaml` plus
  `config/litellm/model_list/*.yaml`: a local group is kept only when `local` is
  in `COMPOSE_PROFILES`, a cloud provider only when its API key is non-empty.
  The rule is "never register a model whose backend is absent".
- **The gateway image is digest-pinned and `llm-gateway-ui` shares the digest.**
  Bump the two together, or the Postgres schema the pair shares diverges.
- **`background_health_checks` is false** in the LiteLLM config, for the same
  model-load reason as `/health`.
- **llama-swap runs with `--no-mmap`**: GGUF mmap over the Windows bind mount
  hangs.
- **`LLM_QUEUE_SLOTS` must track `LLAMA_SWAP_QWEN36_27B_N_PARALLEL`**, and
  llama-swap's `concurrencyLimit` must stay 0, so the ordered depth lives in the
  queue's heap rather than in llama.cpp's opaque FIFO. Both values live in
  `inference/.env`.
- **A queue connection leak looks like an idle GPU.** GPU at 0% with
  `queue_connections_exhausted` in the logs is `docker restart llm-queue`.

## Changing this plane

Adding, removing or moving a container here is never a one-file change. The
full checklist is
[`../documentation/runbooks/SERVICE-LIFECYCLE.md`](../documentation/runbooks/SERVICE-LIFECYCLE.md);
the surfaces this plane appears on today are:

- the compose files here (`docker-compose.yml` plus `compose/*.yml`)
- [`../stack.manifest.toml`](../stack.manifest.toml) - the `[planes.inference]`
  table with its `ports` and `profiles` sub-tables, and the `inference` product
- `scripts/stack/stack.py` - the plane's probe in `HealthSweep.run()` and its
  label in `PS1_PROBES` (`scripts/stack/test_stack.py`), which pins the probe
  set. `scripts/stack/stack.ps1` is a shim over that driver since 2026-09-19 and
  holds no plane list and no probe of its own
- `scripts/recovery/emergency-recovery.ps1` - `$Script:InferenceServices`
- `scripts/checks/stack-watchdog.ps1` and
  `scripts/checks/check-llm-gateway-routing.ps1`
- `scripts/lib/stack-services.curated.json` - edit the CURATED sidecar, then run
  `python scripts/stack/stack.py inventory --write`;
  `scripts/lib/stack-services.json` is generated and `inventory --check`
  refuses a hand edit
- `.claude/skills/stack-map/references/workspace-stacks.md` section 1b, and the
  plane table in [`../CLAUDE.md`](../CLAUDE.md)
- `documentation/CONTAINER-REGISTRY.md` and the backup / restore runbooks
- `scripts/agent-harness/lease-names.conf` - the `inference` lease name

Run `/stack-map` afterwards; it checks for drift between these.
