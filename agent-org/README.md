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
| `AO_LOCAL_API_KEY` | Not in `.env.example`, not checked. `agent-bridge` reads it for the local model lane and otherwise sends `agent-org`, which the gateway refuses. Put a virtual key here ([issue one](../inference/README.md#issue-a-key-for-each-caller)). |
| `LC_LLAMA_API_KEY` | The workers' key for the gateway; the shipped `llama` is refused. A virtual key, as above. |
| `AO_OPEN_TERMINAL_KEY` | The worker executors' key. Still the shipped `change-me-...` while `workers` is enabled: `enable --product agent-org`, `doctor` and `up` refuse. |
| `LC_DEPLOY_TOKEN` | Optional: a deploy token for private work repositories. |

`agent-bridge` reads the whole `agent-org/docker/.env` (it is the one service
with `env_file:`); every other service names the variables it takes.

## Enable and start

**On a fresh clone the frontend is already enabled.** With no
`.stack/state.json`, the driver treats the frontend as enabled, and `enable`
keeps it. `up` starts the planes in dependency order - inference, then the
frontend, then this plane - so until `frontend/.env` holds a real
`WEBUI_SECRET_KEY`, the `up` below starts the inference plane (the
`llm-gateway*` containers), stops at the frontend with
`# up stopped: frontend exited 1`, and leaves those running with none of this
plane's containers started. Either give
the frontend its key - the `cp` and `WEBUI_SECRET_KEY` steps of
[its README](../frontend/README.md#enable-and-start); skip its `init`, which
refuses once `.stack/state.json` exists, and the frontend is enabled already -
or run this plane without it:

```bash
python scripts/stack/stack.py disable --plane frontend
```

(`python scripts/stack/stack.py enable --plane frontend` turns it back on
later; it refuses with `WEBUI_SECRET_KEY is missing in frontend/.env` until
that key is set.)

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

`enable --plane agent-org` leaves the pool off. Start it by hand:

```bash
docker compose -f agent-org/docker/docker-compose.yml --profile workers up -d
```

or enable the *product*, which records the `workers` profile so that every later
`up` starts the pool too:

```bash
python scripts/stack/stack.py enable --product agent-org
```

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
   `curl -X POST http://127.0.0.1:8830/profiles/lane -H "Content-Type: application/json" -d '{"name":"pm","lane":"cloud"}'`
   (repeat for the other judgment roles; without the header the bridge answers
   422). Workers stay local.

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
plane on `127.0.0.1:8830` for tooling and the floor hook:

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
