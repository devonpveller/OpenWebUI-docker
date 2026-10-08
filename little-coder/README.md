# little-coder — control plane

Containerized deployment of [little-coder](https://github.com/itayinbarr/little-coder)
that accumulates expertise from its own work. This directory holds the
**control-plane wrapper**: journals, config, sanitization, git-proxy, the CLI
operator surface, and the MCP edge.

> **Source of truth:** [`../documentation/little-coder/Self-improving-little-coder-design.md`](../../documentation-plans-ai-stack/implementation-guide/little-coder/Self-improving-little-coder-design.md).
> Build sequencing: [`integration-plan.md`](../../documentation-plans-ai-stack/journal/archive/implementation-guide/little-coder/integration-plan.md).
> Status: [`integration-tasks.md`](../../documentation-plans-ai-stack/journal/archive/implementation-guide/little-coder/integration-tasks.md).

## Current chapter: 1 — Tool

A working little-coder driven from the CLI. No OWUI surface, no `meta` outer
loop. Journals record quietly with the full envelope; the sanitization filter
runs in shadow mode; named volumes are the persistence boundary.

## Two planes

| Plane     | Container      | Role                                                        |
| --------- | -------------- | ----------------------------------------------------------- |
| Control   | `little-coder` | Decides. Runs the agent, owns journals + the FIFO queue.    |
| Workspace | `open-terminal`| Executes. Own network, egress-allowlisted. Repo lives here. |

The two share the `little-coder-workspace` named volume: the agent edits files
on it directly; build/test/git commands run inside `open-terminal`, the
network-isolated plane. `git` inside `open-terminal` **is** the git-proxy.

## Layout

```
little-coder/
├── config/little-coder.config.yaml   # centralized typed config (mounted ro)
├── config/little-coder.schema.json   # JSON schema for the config
├── src/littlecoder/                  # Python control-plane package
├── git-proxy/                        # git wrapper (whitelist/blocklist)
├── pi-extension/                     # routes the agent's exec into open-terminal
├── docker/                           # Dockerfiles
└── tests/                            # pytest suite
```

## Task API: the model a task runs on

`POST /tasks` takes an optional `model`. A task without it runs `agent.model` from
`config/little-coder.config.yaml`, as every caller did before (Open WebUI and the CLI never
send it). A task with it runs that model for that task only. The config is never changed, so
the next task without `model` runs `agent.model` again.

The value must be `agent.model` itself or a key of `agent.allowed_models`. The match is
exact: no prefix, no pattern, no case folding, no trimming. Each key is a gateway model ROLE
(`local-small`), mapped to the `--model` value the agent gets (`llamacpp/local-small`). The
config is refused at boot unless every key is a gateway role and every value is
`llamacpp/<gateway role>`. A gateway role is letters, digits and `. _ : -`, starting with a
letter or digit. Each value must also be registered in `config/models.json`, where the
`llamacpp` provider points at the LiteLLM alias, so a per-task model stays a role behind the
gateway. Anything else is refused
before a task exists, with `422 model refused: ...`. That includes an empty string and
anything starting with `-`. With no `allowed_models`, only `agent.model` may be named.

`GET /tasks/<id>` returns `model`: the `--model` value the task ran. The journal's
`task_started` record carries the same value. The agent-org bridge sends its `worker-default`
profile's model with every worker turn. See
[`../agent-org/agent-bridge/profiles/README.md`](../agent-org/agent-bridge/profiles/README.md).
The pooled workers read generated copies of this config, so after editing `allowed_models`,
run `python agent-org/scripts/gen-worker-configs.py`.

## Daemon API access: `LC_DAEMON_TOKEN`

The control daemon (`:8090`) runs agent tasks, clears the agent's stop-gates
(`/tasks/{id}/confirm` is journalled as an operator action), approves skills,
retargets the workspace and shuts itself down. So every route **except
`GET /health`** requires `Authorization: Bearer <LC_DAEMON_TOKEN>` (ao-dauth,
2026-10-07; [`src/littlecoder/daemon_auth.py`](src/littlecoder/daemon_auth.py)).

- **One route table**, `ROUTE_ACCESS` in `daemon_auth.py`. A route served but
  missing from it (or a row naming no route) stops the daemon from starting.
  `/docs`, `/redoc` and `/openapi.json` are off.
- **Checked before anything runs**: a pure ASGI middleware, before the request
  body is read; constant-time compare; the value is never logged or returned.
  Refusals carry `x-lc-auth: refused`. Websocket connections are refused.
- **Fail closed**: unset or blank, every route but `/health` answers **503**
  and the daemon logs `LC_DAEMON_TOKEN is not set` at startup. Missing or wrong:
  **401**.
- **Not readable by the agent**: at start the daemon takes the variable out of
  its environment and re-execs itself (same PID) with the value on a pipe that it
  reads once and closes (`LC_DAEMON_TOKEN_FD` names the fd), then marks itself
  non-dumpable. So the agent, `ot-exec`, git and acceptance checks - which run as
  the same uid - find it in neither their own env nor `/proc/<daemon>/environ`,
  `cmdline` or `fd` (pi's read tool included). It does remain in the daemon's
  memory (ptrace-attach needed: refused) and in the environment of `docker exec`
  processes, which run as root.
  `docker exec <container> lc ...` still works: `docker exec` carries the
  container's configured environment, and the `lc` CLI (and the dormant
  `lc-mcp`) send it.
- **Who holds it**: the coder-plane `little-coder` (from `coder/.env`) and Open
  WebUI's `little_coder` pipe (its `daemon_token` valve); `ao-worker-1/2` and
  `agent-bridge` (from `agent-org/docker/.env`, a different value). Never
  `open-terminal`, `lc-egress` or `ao-ot-1/2`.
- **Bind scope, `LC_DAEMON_HIDE_FROM`** (comma-separated host names): the daemon
  (`:8090`) and its metrics (`:9090`) do not listen on any interface whose subnet
  holds one of them. The agent-org
  workers set it to their own executor (`ao-ot-N`), so a worker's shell commands
  cannot even connect to its daemon; nothing in the executor calls the daemon.
  A name that does not resolve, or a scope that leaves only loopback, stops the
  daemon (fail closed). Unset: the configured `daemon.host` as before. The coder
  plane leaves it unset: `open-terminal` shares both of that daemon's networks.
- `scripts/agent-harness/dispatch.ps1` (docker-exec transport) prints the header
  from the container's own environment inside the container; the host never
  holds the value. Its `http` transport sends `$env:LC_DAEMON_TOKEN`. The
  quadrant comparison's transport (`scripts/agent-harness/quadrant/lc_docker.py`)
  does the same with `bash -c` and a process-substitution header file.

## Language note

Upstream little-coder is a **Node.js** CLI built on the `pi` agent framework —
not Python. The agent container is Node-based; this control-plane wrapper is
Python, mirroring the repo's `search-mcpo` / `openbrain-gateway` pattern. The
`agent.py` filename in design §6 is a Chapter-5 illustration only.

## Operator action items (cannot be automated)

Before deploying with self-improvement chapters, the operator must:

1. Create the **private** self-improvement git remote (design §10.6) and set
   `LC_SELF_REMOTE_URL` in **`coder/.env`** (per-plane since sl-env-split,
   2026-09-19 - the root `.env` no longer carries it).
2. Provision a fine-grained PAT scoped to `contents:write` on that remote only
   and set `LC_SELF_REMOTE_PAT` in **`coder/.env`**.

Unused until Chapter 4+, and to be exact: **nothing reads either name anywhere
in the repo today** (swept 2026-09-19). They are declared in
`coder/.env.example`, labelled `NO READER TODAY`, so the credential chain is
documented in the plane that will consume it rather than retrofitted later.
