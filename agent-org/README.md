# agent-org - governed multi-agent chat orchestration

A self-hosted Mattermost chat that doubles as the coordination fabric for a
governed fleet of coding agents (human operator, product owner, project manager,
`little-coder` workers). **Safety leads:** the escalation gate, bus-only
communication and the role charters are the spine; capability rides on top.
[`IMPLEMENTATION-NOTES.md`](IMPLEMENTATION-NOTES.md) records what is built and
what waits on an operator, and [`docs/ORCHESTRATION-DESIGN.md`](docs/ORCHESTRATION-DESIGN.md)
is the design.

One compose project (`name: agent-org`,
[`docker/docker-compose.yml`](docker/docker-compose.yml)). A default `up` starts
Mattermost, `agent-bridge`, their two databases and two dump sidecars; the
worker pool and the cloud lane are profiles you start by hand.

Commands below run from the repository root. They are written `python`; use
`python3` if that is what your system calls Python 3.11+. Blocks marked `bash`
need a POSIX shell (Linux, macOS, or Git Bash on Windows).

## Layout

```
agent-org/
  docker/docker-compose.yml     # the `agent-org` compose project
  docker/.env.example           # the env template (no secrets in git)
  config/litellm-cloud.config.yaml   # the cloud lane's LiteLLM (profile `cloud`)
  agent-bridge/                 # the orchestration + governance-gate service (Python/FastAPI)
    app/
      modules/governance_gate.py  #   the escalation gate (the safety spine)
      modules/scheduler.py        #   worker pool + idle-wait state machine
      modules/{event_gateway,router,scope_ledger,model_router,audit_sink,...}.py
      orchestrator.py             #   concern posting, decision parsing, monitor
      main.py                     #   FastAPI control surface + lifespan
    profiles/                   # role -> model profile registry
    charters/                   # role charters (the profiles' system prompts)
    floor/                      # hard-rules.md (the immutable floor) + stop-gate enforcement
    hooks/pretooluse_floor.py   # the deterministic floor hook
    tests/
```

The charters are also delivered to workers as Agent Skills under
[`.claude/skills/`](../.claude/skills/) (`agent-org-floor`, `agent-org-worker`,
`agent-org-reviewer`).

```
  Mattermost  --WS events-->  agent-bridge  --spawn/resume-->  little-coder workers
  (chat+app)  <--REST post--   router . GATE (freeze/CONCERN/clear) . scheduler
      ^ the human operator joins any channel (mobile)   | audit + learning
      +------------------------------------------  Open Brain (audit mirror, optional)
```

## What starts

| Service | Profile | What it is |
|---|---|---|
| `mattermost`, `mattermost-db` | *(none)* | Mattermost Team Edition on `127.0.0.1:8065`, and its PostgreSQL. |
| `agent-bridge`, `agent-bridge-db` | *(none)* | The orchestration and governance service, built here as `agent-bridge:local`, with a control API on `127.0.0.1:8830`; and its PostgreSQL. |
| `mattermost-db-backup`, `agent-bridge-db-backup` | *(none)* | The **dump sidecars**: a `pg_dump --format=custom` of each database into `backups/mattermost-db` and `backups/agent-bridge-db` at start and every 24 h, keeping `AO_BACKUP_RETAIN_COUNT` (7). |
| `ao-worker-1`, `ao-worker-2`, `ao-ot-1`, `ao-ot-2`, `ao-git-egress`, two journal backups | `workers` | The worker pool: little-coder daemons, their executors, and the egress proxy that confines them. |
| `llm-gateway-cloud`, `llm-gateway-cloud-db`, `ao-egress` | `cloud` | A second LiteLLM for cloud judgment roles, and its egress proxy. |

## Requirements

- **Docker Engine with the compose plugin, and Python 3.11+** for the driver.
- **The inference plane, enabled and serving.** `enable --plane agent-org` refuses while
  it is not enabled; every local model call goes through the `llama-cpp` alias.
- **For `workers`:** the images `little-coder:local` and
  `little-coder-open-terminal:local`, which the coder plane builds
  (`python scripts/stack/stack.py up coder` once is enough; the coder plane need
  not stay up).
- **For `cloud`:** an OpenRouter key, and the egress work described under
  [Security notes](#security-notes).

Keys, in `agent-org/docker/.env`:

| Key | What happens if it is wrong |
|---|---|
| `MM_DB_PASSWORD`, `AO_DB_PASSWORD` | Blank, or still the shipped `change-me-...`: `enable`, `doctor` and `up` refuse, naming them. |
| `AO_MATTERMOST_BOT_TOKEN` | Blank on a first start: `agent-bridge` crash-loops with `Illegal header value b'Bearer '` until you create the bot (below). |
| `AO_LOCAL_API_KEY` | Shipped blank in `.env.example`; the driver does not check it. `agent-bridge` reads it for the local model lane (left out of the file it sends `agent-org`), and the gateway refuses anything but a key it issued. Put a virtual key here ([issue one](../inference/README.md#issue-a-key-for-each-caller)). |
| `LC_LLAMA_API_KEY` | The workers' key for the gateway; the shipped `llama` is refused. A virtual key, as above. |
| `AO_OPEN_TERMINAL_KEY` | The worker executors' key. Still the shipped `change-me-...` while `workers` is enabled: `enable --product agent-org`, `doctor` and `up` refuse. |
| `LC_DEPLOY_TOKEN` | Optional: a deploy token for private work repositories. |

`agent-bridge` reads the whole `agent-org/docker/.env` (it is the one service
with `env_file:`); every other service names the variables it takes.

## Enable and start

**On a fresh clone the frontend is already enabled.** With no
`.stack/state.json`, the driver treats the frontend as enabled, and `enable`
keeps it. `up` checks the keys of every plane it will start before it starts
any of them (and then starts them in dependency order, ties broken by the order
the manifest declares them). So until `frontend/.env` holds a real
`WEBUI_SECRET_KEY`, the `up` below starts nothing: it prints
``refused: fix these before `up` starts anything:``, names `WEBUI_SECRET_KEY`
in `frontend/.env`, and exits 1. Either give
the frontend its key - the `cp` and `WEBUI_SECRET_KEY` steps of
[its README](../frontend/README.md#enable-and-start); skip its `init`, which
refuses once `.stack/state.json` exists, and the frontend is enabled already -
or run this plane without it:

```bash
python scripts/stack/stack.py disable --plane frontend
```

(`python scripts/stack/stack.py enable --plane frontend` turns it back on
later; it refuses, naming `WEBUI_SECRET_KEY` in `frontend/.env`, until that
key is set.)

```bash
cp agent-org/docker/.env.example agent-org/docker/.env
python -c "import secrets; print(secrets.token_hex(32))"
```

Put a printed value in each of `MM_DB_PASSWORD` and `AO_DB_PASSWORD`, and a
virtual key in `AO_LOCAL_API_KEY` and `LC_LLAMA_API_KEY`. Then:

```bash
python scripts/stack/stack.py enable --plane agent-org
python scripts/stack/stack.py up
```

`agent-bridge` crash-loops until it has a bot token. Create one:

1. Open `http://127.0.0.1:8065`, create the admin account, a team and an
   `#mgmt` channel.
2. System Console, Integrations, **Bot Accounts**: create `@pm`, copy its access
   token, and set `AO_MATTERMOST_BOT_TOKEN=` in `agent-org/docker/.env`.
3. Recreate the bridge so it reads the new value, and check it:

   ```bash
   docker compose -f agent-org/docker/docker-compose.yml up -d agent-bridge
   curl -fsS http://127.0.0.1:8830/health
   ```

**Is the local model a good enough judge?** Before relying on the org, measure
the local model on instruction following, structured output and coordination
with bounded real completions (never a health probe) -
[`IMPLEMENTATION-NOTES.md`](IMPLEMENTATION-NOTES.md) has the procedure. If it is,
stay all-local (the profiles ship `lane: local`); if not, build the cloud lane
below and move the judgment roles to it.

**The worker pool.** Set `AO_WORKER_INSTANCE_URLS`,
`AO_MAX_CONCURRENT_WORKERS` and `AO_OPEN_TERMINAL_KEY` first; concurrency is a
static semaphore, and the GPU is the budget. Each worker needs its own copy of
the little-coder config pointing at its own executor; generate them (again
whenever `little-coder/config/` changes):

```bash
python agent-org/scripts/gen-worker-configs.py
```

Each worker turn runs on the `worker-default` profile's model, which the bridge sends with
the task. That model must be listed in the config's `agent.allowed_models`, or the worker
refuses the turn. See
[`agent-bridge/profiles/README.md`](agent-bridge/profiles/README.md) and
[`../little-coder/README.md`](../little-coder/README.md#task-api-the-model-a-task-runs-on).

`enable --plane agent-org` leaves the pool off. Start it by hand:

```bash
docker compose -f agent-org/docker/docker-compose.yml --profile workers up -d
```

or enable the *product*, which records the `workers` profile so that every later
`up` starts the pool too:

```bash
python scripts/stack/stack.py enable --product agent-org
```

While `workers` is active for the plane, `enable`, `doctor` and `up` check that the
generated configs exist (`agent-org/agent-bridge/worker-configs/worker-1` and `-2`) and
refuse, naming `python agent-org/scripts/gen-worker-configs.py`, until they do. The
hand-typed `docker compose ... --profile workers` line above is not checked.

A running worker keeps the environment it started with. After changing
`LC_DEPLOY_TOKEN` or `LC_LLAMA_API_KEY`, recreate the pool:

```bash
docker compose -f agent-org/docker/docker-compose.yml --profile workers up -d --force-recreate ao-worker-1 ao-worker-2
```

**The cloud lane** (only if the judge measurement calls for it):

1. Set an OpenRouter spend ceiling, fill `OPENROUTER_API_KEY` and the
   `AO_CLOUD_*` values in `agent-org/docker/.env`, and pin no-log providers in
   [`config/litellm-cloud.config.yaml`](config/litellm-cloud.config.yaml).
2. Start it: `docker compose -f agent-org/docker/docker-compose.yml --profile cloud up -d`.
3. Issue one virtual key with a budget per judgment role on `llm-gateway-cloud`.
4. Set `AO_CLOUD_ENABLED=true` and move each judgment role:
   `curl -X POST http://127.0.0.1:8830/profiles/lane -H "Authorization: Bearer $AO_OPERATOR_TOKEN" -H "Content-Type: application/json" -d '{"name":"pm","lane":"cloud"}'`
   (repeat for the other judgment roles; without the bearer the bridge answers
   401, without the content type 422). Workers stay local.

**Mobile.** Install the Mattermost app and expose the server on your tailnet
only; there is no public exposure and no end-to-end encryption on agent
channels, because observability is the safety control. See
[`docs/P7-mobile-and-exposure.md`](docs/P7-mobile-and-exposure.md).

## Operate

```bash
python scripts/stack/stack.py status
python scripts/stack/stack.py health
python scripts/stack/stack.py stats
python scripts/stack/stack.py recover agent-org
python scripts/stack/stack.py down agent-org
```

`health` pings Mattermost. `agent-bridge`'s own health is
`curl -fsS http://127.0.0.1:8830/health`.

The chat is the primary control surface; the bridge also serves an HTTP control
plane on `127.0.0.1:8830` for tooling and the floor hook. Every route except
`GET /health` needs `Authorization: Bearer <AO_OPERATOR_TOKEN>` (from
`agent-org/docker/.env`); the floor hook's route also takes `AO_WORKER_TOKEN`.
See "HTTP control-plane auth" under Security notes.

| Action | From chat (`#mgmt`) | From HTTP |
|--------|-------------------|-----------|
| Decide a concern | `approve\|modify\|abort <effort_id> [note]` | `POST /decision` |
| Global kill switch | `kill` / `unkill` | `POST /kill-switch {on}` |
| Create an effort/channel | - | `POST /effort {name}` |
| Inspect gate state | - | `GET /state/{effort_id}` |
| Audit replay | - | `GET /audit?effort_id=` |
| Move a profile's lane | - | `POST /profiles/lane {name,lane}` |
| Suggestion pool | post to `#suggestions` | `GET /suggestions` |

**Backup and restore.** Both databases are dumped by their sidecars into
`backups/mattermost-db/` and `backups/agent-bridge-db/` as `*.dump` files.
`python scripts/stack/stack.py backup agent-org` archives the Mattermost file
volumes and names the dump sidecar for each running database instead of tarring
it. To restore Mattermost's database from a dump:

```bash
docker compose -f agent-org/docker/docker-compose.yml stop agent-bridge mattermost
docker exec -i mattermost-db pg_restore -U mmuser -d mattermost --clean --if-exists --no-owner --no-acl < backups/mattermost-db/mattermost-db-<stamp>.dump
python scripts/stack/stack.py up
```

The bridge's database is the same with `agent-bridge-db`, user `bridge` and
database `agent_bridge`.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `refused: agent-org requires inference, which is not enabled` | Enable the inference plane first: `python scripts/stack/stack.py enable --plane inference`. |
| `refused: agent-org cannot be enabled yet:` naming `MM_DB_PASSWORD` / `AO_DB_PASSWORD` | Set them in `agent-org/docker/.env`. |
| `agent-bridge` restarting, `Illegal header value b'Bearer '` in its log | `AO_MATTERMOST_BOT_TOKEN` is blank. Create the bot (above). |
| 401 from the gateway in `agent-bridge` or worker logs | `AO_LOCAL_API_KEY` or `LC_LLAMA_API_KEY` is not a virtual key the gateway issued. |
| The worker containers cannot find `little-coder:local` or `little-coder-open-terminal:local` | Those images are built by the coder plane; run `python scripts/stack/stack.py up coder` once. |
| `network ai-stack_llm-net declared as external, but could not be found` from a hand-typed `docker compose` | The anchor's networks are missing; `python scripts/stack/stack.py up` creates them. |
| `[DIFFERS] ai-stack_llm-net: internal is false, declared true` and `up` stops | An existing network does not match the anchor. Stop what is attached, `docker network rm ai-stack_llm-net`, run `up` again. |
| `ao-worker-1`/`-2` restarting with `config file not found: /app/config/little-coder.config.yaml` | The per-worker configs were not generated. Run `python agent-org/scripts/gen-worker-configs.py`, then the `--force-recreate` line above. |
| Workers keep an old token after you changed `.env` | Recreate the pool (the `--force-recreate` line above). |

## Security notes

The stack is private and local-first; every cloud-capable part ships off. This
plane holds four such parts, and a default `up` starts none of them:

| Component | Off by default because | What turns it on | Where it egresses |
|---|---|---|---|
| **`llm-gateway-cloud` + `llm-gateway-cloud-db`** | profile `cloud`; and `AO_CLOUD_ENABLED` defaults to `false`, so every role stays on the local lane | the `cloud` profile, `OPENROUTER_API_KEY`, `AO_CLOUD_DB_PASSWORD`, `AO_CLOUD_MASTER_KEY`; then `AO_CLOUD_ENABLED=true` and a lane move per role | no internet leg of its own: `ao-net` plus `ao-cloud-egress-net` (internal), with `HTTP_PROXY`/`HTTPS_PROXY` at `http://ao-egress:8888` |
| **`ao-egress`** | profile `cloud` | the same | the one dual-homed container in the cloud lane - **but read the allowlist note below** |
| **`ao-git-egress`** and the worker pool | profile `workers` | the `workers` profile | a default-deny tinyproxy whose allowlist lives on the shared `ao-egress-config` volume; [`docker/egress/egress-reload.sh`](docker/egress/egress-reload.sh) seeds it with `github.com` and `githubusercontent.com` and reloads tinyproxy whenever `agent-bridge` rewrites it (`/project add`, `/egress allow`). Only `ao-ot-1`/`ao-ot-2` are proxied through it; `ao-worker-1`/`-2` carry no proxy and are confined by `ao-worker-net` (internal) having no route out. |
| **The GitHub App** | off unless `AO_GITHUB_APP_ID` is set **and** `agent-bridge/secrets/github-app-key.pem` is readable ([`agent-bridge/app/config.py`](agent-bridge/app/config.py)) | `AO_GITHUB_APP_ID`, `AO_GITHUB_APP_OWNER` and the key file (gitignored, mounted read-only) | `https://api.github.com` directly from `ao-net`, not through `ao-egress` |

- **`ao-egress` would deny OpenRouter.** Its `AO_EGRESS_ALLOWLIST` (default
  `openrouter.ai`) is set in the compose file, but the image it runs
  (built from `little-coder/docker/Dockerfile.egress`) reads its allowlist from a
  file baked in at build time - `github.com` and `githubusercontent.com` - and
  nothing reads the variable. Turning the cloud lane on needs the allowlist
  wired the way `ao-git-egress` wires it, or the host added to the image.
- **Two gateways, two lanes.** Local inference goes through the inference
  plane's `llm-gateway`, which has no route off the machine; its own cloud
  model group stays listed but unreachable, and
  [`config/litellm-cloud.config.yaml`](config/litellm-cloud.config.yaml) says not
  to add OpenRouter to it. Never route around LiteLLM, and never probe model
  health with a completion.
- **`ao-net` is an ordinary bridge**, so host port publishing works; "no cloud"
  there is enforced by no default service holding a cloud credential.
  `mattermost` sits on it, so it has a route out; whether Team Edition reports
  diagnostics on its own defaults is upstream behaviour this repository does not
  settle (`MM_LOGSETTINGS_ENABLEDIAGNOSTICS` is not set here).
- Published ports are `127.0.0.1:8065` and `127.0.0.1:8830`, loopback only.
- **HTTP control-plane auth (ao-auth, 2026-10-07).** Loopback is not a boundary
  inside Docker: before this, anything that could reach `agent-bridge:8000` -
  including `ao-ot-1/2`, where worker commands run - could POST any operator
  verb to `/nl` (approve a gate, move a model lane, abort an effort, retire a
  check). Now:
  - **`AO_OPERATOR_TOKEN`** is required on every route except `GET /health`
    (the one classification table is `ROUTE_ACCESS` in
    [`agent-bridge/app/main.py`](agent-bridge/app/main.py); a route missing from
    it stops the app from starting). Unset, those routes answer **503** and the
    bridge logs `AO_OPERATOR_TOKEN is not set` at startup - fail closed, never
    open. Missing or wrong: **401**; the worker token: **403**. `/docs` and
    `/openapi.json` are off.
  - **`AO_WORKER_TOKEN`** is accepted only on `POST /hook/floor-check` (the
    one route the worker side calls - `hooks/pretooluse_floor.py`; nothing on
    the worker side calls `/lateral-concern`, `/handoff` or `/suggestion`,
    which are operator routes). Compose sets it in `ao-worker-1/2` only. The
    hook sends it and blocks an irreversible action if it is missing or the
    bridge refuses.
  - **`ao-ot-1/2` are on `ao-worker-net` only** (no longer on
    `ai-stack_llm-net`), so a worker command cannot connect to the bridge; they
    keep their worker and `ao-git-egress`. The agent and its model calls run in
    `ao-worker-N`.
  - **Callers.** Host tooling sends `Authorization: Bearer <AO_OPERATOR_TOKEN>`:
    `scripts/issue-ops/issue_ops.py` and `scripts/maintenance/disk-guard.ps1`
    read it from `agent-org/docker/.env`; `gym-watch-effort.py` runs inside the
    bridge and uses its env. The **gym runner** (`ai-orchestration-gym`,
    `runner/gym_runner.py`) must send that same header on every bridge call
    (`/health` excepted) - it already reads this `.env` (`--env-file`), so it
    takes `AO_OPERATOR_TOKEN` from there. Mattermost intake is unaffected (the
    bridge's outbound websocket).
  - **Rotation:** two different random values; on change, `--force-recreate`
    `agent-bridge` and `ao-worker-1/2`. Both are on the key-rotation list in
    [`SECURITY.md`](../SECURITY.md).
- **The workers' little-coder daemons (ao-dauth, 2026-10-07).** `ao-worker-N:8090`
  takes tasks, `/tasks/{id}/confirm` (an operator action), `/admin/approve`,
  `/project` and `/admin/shutdown`; before this, a worker command running in
  `ao-ot-N` could call all of them on its own worker. Now:
  - **`LC_DAEMON_TOKEN`** (in `agent-org/docker/.env`) is required on every
    daemon route except `GET /health`; unset = **503** (fail closed), missing or
    wrong = **401**. The table and the details are in
    [`../little-coder/README.md`](../little-coder/README.md#daemon-api-access-lc_daemon_token).
    `agent-bridge` sends it on every call (`LittleCoderHarness`; the setting is
    `lc_daemon_token`, env name `LC_DAEMON_TOKEN` with no `AO_` prefix);
    `ao-worker-1/2` check it; no `ao-ot` sandbox holds it. The daemon re-execs
    itself without it (pipe hand-off) and goes non-dumpable, so the agent - same
    uid - cannot read it from its env or from `/proc/<daemon>`.
  - **`ao-ot-N` cannot connect to `:8090` at all**: the workers set
    `LC_DAEMON_HIDE_FROM=ao-ot-N`, so the daemon (and its metrics `:9090`) does not listen on
    `ao-worker-net` (the network it shares with its executor and
    `ao-git-egress`). The daemon still reaches `ao-ot-N` (outbound), and the
    bridge reaches the daemon over `ai-stack_llm-net`. Nothing in `ao-ot` calls
    the daemon (`git-proxy` and `ot-exec` only write locally / are called by the
    worker).
  - **Rotation:** a value different from `coder/.env`'s; on change,
    `--force-recreate` `ao-worker-1/2` and `agent-bridge`. On the key-rotation
    list in [`SECURITY.md`](../SECURITY.md).
- No secret belongs in a file under git: bot tokens, database passwords and
  model keys come from `agent-org/docker/.env` only.

## Tests

In a virtual environment (a system Python that follows PEP 668 refuses a bare
`pip install`):

```bash
python -m venv agent-org/agent-bridge/.venv
agent-org/agent-bridge/.venv/bin/pip install -e "agent-org/agent-bridge[test]"
agent-org/agent-bridge/.venv/bin/pip install -r agent-org/agent-bridge/requirements.txt
cd agent-org/agent-bridge && .venv/bin/pytest -q
```

On Windows the environment's programs are under `.venv\Scripts\` instead of
`.venv/bin/`. The `[test]` extra alone is not enough: without
`requirements.txt`, collection fails on `No module named 'jwt'`.

## Conventions

- Never commit, push or merge to `main` without an explicit ask.
- Every container is in the compose file, `scripts/recovery/emergency-recovery.ps1`
  and `.claude/skills/stack-map/references/workspace-stacks.md` together (run
  `/stack-map`); the full list is
  [`SERVICE-LIFECYCLE.md`](../documentation/runbooks/SERVICE-LIFECYCLE.md).
- Reuse, do not reinvent: little-coder for workers, its git-proxy and egress
  proxy for enforcement, Open Brain for audit and learning, the existing
  `llm-gateway` for local inference.
