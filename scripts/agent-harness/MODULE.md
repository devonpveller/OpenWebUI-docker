# MODULE — the agent harness

This directory is a **module**: a self-contained way to run several agents on one
repository without them treading on each other, and to land their work through a
pipeline with separated duties. It can be configured, switched off, lifted out, or
dropped into another distribution.

It was called `scripts/worktree/` until 2026-08-28. The name described one of the
five things in here; the rename was to stop it mislabelling the rest.

## What it is for

Several agents work at once, each in its own `git worktree`. Work reaches the
shared branch through a queue with roles: the developer never tests and never
merges their own work. Two human gates bracket the machine work — the operator
agrees **what the work is for** before it starts (the anchor), and releases it
for review after the tests pass.

## The public surface

Everything else in here is internal and may change without notice.

| entry point | what it does |
|---|---|
| `new-worktree.ps1 -Id <id>` | provision an agent's worktree (branch, env files, registry row) |
| `remove-worktree.ps1 -Id <id>` | retire it; refuses while it holds unmerged work |
| `sync-worktree-env.ps1 -Id <id>` | re-copy runtime env files when the main checkout's are newer |
| `queue.ps1` | the pipeline: propose → confirm → submit → test → release → review → merge |
| `lease.ps1` | named leases for SHARED RUNTIME only (Docker, GPU, ports, live DBs) |
| `verify-merge-protocol.ps1` | the executable drill over the whole protocol |
| `check-runner-endpoints.ps1` | does the runner registry tell the truth about reachability? (needs the stack up) |
| `verify-runner-endpoint-check.ps1` | the drill that proves the check above can FAIL — six mutations, six expected exit codes |
| `harness.config.json` | the configuration (see below) |
| `config.py` | the reader other Python code imports (`bridge.py` does) |

Internal: `common.ps1` (composition root), `git-io.ps1` (git facts), `resolve.ps1`
(policy), `config.ps1` (settings), `anchor.ps1` (the anchor's shape and validation).

The dependency direction is one-way and deliberate:

```
queue.ps1 / lease.ps1 / new-worktree.ps1
        |
     common.ps1 ──> resolve.ps1 ──> git-io.ps1   (facts about this repository)
                          └──────> config.ps1    (files + environment -> settings)
                    anchor.ps1                   (the shape of an anchor; owns no state)
```

`config.ps1` knows nothing about git, worktrees, queues or leases — which is what
lets another distribution retarget the toolkit by editing JSON instead of code.

## Configuration

One file, three readers. `config.ps1` serves the scripts, `config.py` serves the
bridge, and `test_harness_config.py` asks them the same questions and compares the
answers so they cannot drift apart unnoticed. The third reader is **outside this
module**: agent-org's `agent-bridge/app/modules/runners.py` reads the `runners`
block out of this same file (bind-mounted read-only into the container), and its
`tests/test_runner_registry.py` pins itself to `config.py` from the other side.

Layers, lowest to highest:

```
built-in defaults < harness.config.json < harness.local.json (gitignored) < environment
```

Environment overrides are a short explicit list, not a naming convention:

| variable | effect |
|---|---|
| `AI_STACK_HARNESS_CONFIG` | path to an alternate `harness.config.json` |
| `AI_STACK_HARNESS_ENABLED` | `0` — kill switch, beats both files |
| `AI_STACK_HARNESS_PROFILE` | profile name applied to every surface |

Two more are named *by* the configuration rather than hardcoded, so a distribution
can rename them: `worktree.work_line_env` (default `AI_STACK_WORK_LINE`, which
branch agents work against) and `worktree.state_dir_env` (default
`AI_STACK_WORKTREE_STATE`, where coordination state lives).

## Profiles: which models the roles run on

A profile assigns each role a **runner** and a model. The runner matters because
"cloud" and "local" are different execution substrates, not two values of one
setting: `claude-code` is a Claude Code agent, `little-coder` is a local control
plane reaching `llama-cpp` through LiteLLM.

`all-cloud` — opus for all three — is the default everywhere. `all-local`,
`local-work-cloud-review` and `cloud-work-local-test` are also defined.

- **Extension sessions are locked** to `all-cloud` (`profile_locked: true`). A
  requested profile is ignored there. Operator decision, 2026-08-28: the surface
  the operator drives interactively should never silently degrade.
- **Mattermost sessions** switch with `profile: <name>` in a thread. The bridge
  passes the choice to the session as `AI_STACK_HARNESS_PROFILE`, which is simply
  the configuration's own top layer — no separate mechanism.

The `little-coder` runner **resolves but does not dispatch**. `Resolve-RoleTarget`
validates the profile, the role and the runner, and returns the runner's `status` —
and nothing in this module submits a task to anything. So selecting `all-local`,
`local-work-cloud-review` or `cloud-work-local-test` resolves cleanly and then the run
proceeds on whatever the surface actually invokes.

Say it precisely, because the earlier phrasing here ("wired and callable but unproven")
read as *tried and not yet demonstrated* and the truth is *never attempted, because the
code path stops one layer above*: the resolution is wired; the dispatch was never built
([`u4-profile-mechanism-deadcode.md`](../../documentation/notes/u4-profile-mechanism-deadcode.md)).
`all-cloud` being both the default and locked for extension sessions is what has kept
this invisible. Building that dispatch is a separate piece of work; until it lands, the
three local-bearing profiles are a choice this module cannot honour.

## Runners: the shared registry

`runners{}` is the one part of this configuration that is **not only ours**.
agent-org's bridge reads the same block out of the same file, because both systems
need the same answer to "what execution substrates exist, of what kind, at what
address" — agent-org used to hold it as a bare CSV of URLs
(`AO_WORKER_INSTANCE_URLS`, kind implicit and always little-coder). Their *profile*
tables were deliberately **not** merged: agent-org's profile binds a role to a model
**lane** for the bridge's own inference calls; ours binds a role to a **runner**.
The full argument, with the evidence for it, is in
[`documentation/notes/u4bidir-findings.md`](../../documentation/notes/u4bidir-findings.md).

Readers: `Get-HarnessRunner` / `Get-HarnessRunnerAddresses` / `Get-HarnessRunnerPool`
in `config.ps1`, `runner()` / `runner_addresses()` / `runner_pool()` in `config.py`.
Dispatch is *not* here — this module answers questions about configuration and does
not submit tasks to anything.

### The two directions are not equally true

U4's phrasing ("agent-org workers as harness runners **and vice versa**") invites one
sentence covering two things of unequal status. They are:

| direction | status | what backs the word |
|---|---|---|
| **agent-org reads this registry** | **DISPATCHING** | `RunnerDispatch` is on the live wake path; changing only this file changes which `WorkerHarness` implementation executes (`agent-org/agent-bridge/tests/test_runner_registry.py`) |
| **the harness runs work on a runner** | **DECLARED, NOT DISPATCHING — parked** | there is no dispatcher here at all: nothing submits a task, no profile names `agent-org-worker`, and `Get-HarnessRunnerPool` has no executable caller |

The park is asserted, not described:
`test_the_harness_side_of_u4_is_declared_not_dispatching` fails the moment a harness
script reads the pool, and `test_no_profile_routes_a_role_to_a_pooled_runner` fails if a
profile ever aims a role at a daemon agent-org's scheduler owns. Whoever builds the
dispatcher will be told by those two tests exactly which claims to re-state.

Why parked rather than built here: a harness item lives in a git worktree **on the
host**, and little-coder-kind daemons only ever work on a `/workspace` they cloned
themselves — so handing one an item means handing over a pushed **branch**, never a
path. That is a design question, not a wiring question, and it is the subject of a
separate work item.

Each row states three things that are claims, not decoration:

| field | means | checked by |
|---|---|---|
| `status` | `proven` only once a work item has completed through it | a human, honestly |
| `reachable_from` | the vantage points that can actually open the address (`host`, or a docker network name) | `check-runner-endpoints.ps1`, in both directions |
| `pooled` | an orchestrator may ACQUIRE these addresses as work capacity | `test_harness_config.py` + agent-org's suite |

`pooled` is not a synonym for addressable. The coder plane's `little-coder` is
addressable from inside `llm-net` and is deliberately **not** pooled: it is the
operator's interactive daemon on one shared `/workspace`, and an orchestrator that
acquired it would collide with a human mid-task — the collision little-coder's own
design already rejected as deterministic
([`Self-improving-little-coder-design.md:656`](../../documentation/implementation-guide/little-coder/Self-improving-little-coder-design.md)).

`reachable_from` exists because this file claimed `little-coder` lived at
`http://127.0.0.1:8090` from the day the block was written, while the only port the
coder plane even declares on the host is `127.0.0.1:9091` (metrics). Every host-side
probe of `8090` was refused — and none was ever made, because nothing dispatched. A
runner nobody calls is a runner nobody corrects.

"Falsifiable by a script" is itself a claim, and the first version of that script could
not back it: it probed only from the host, so for a container-DNS row the *failing*
probe was the branch that recorded a pass, and a wrong port passed. It now probes from a
container on each declared network (`docker exec <probe> curl <url>`, which exercises the
name **and** the port), and "could not look" is exit 2 rather than a silent zero.
`verify-runner-endpoint-check.ps1` executes both failure directions on every run — start
with its `wrong-port` case.

`claude-code` contributes no pooled address, and that is the honest statement rather
than an omission: a Claude Code agent is a host process with no task endpoint, so
agent-org cannot acquire one as a worker. Routing to that kind raises
`RunnerNotProvisionedError` naming what is missing.

## Turning it off

| setting | effect |
|---|---|
| `enabled: false`, or `AI_STACK_HARNESS_ENABLED=0` | off everywhere |
| `surfaces.mattermost.enabled: false` | off for bridge sessions |
| `surfaces.extension.enabled: false` | off for editor-driven sessions |

Off means **inert, and it says why**. Every script entry point exits 2 with a
sentence naming the setting; the bridge's `profile:`, `worktree: on` and `release:`
directives answer with the same sentence instead of running anything. Nothing in
the ordinary workflow changes behaviour.

## Where the state lives

`<git-common-dir>/agent-worktrees/` — the `.git` directory of the MAIN checkout,
shared by every worktree.

This is load-bearing. It was originally anchored on the script's own location,
which is correct only while exactly one copy of the toolkit exists — and the whole
point is that copies exist inside worktrees. Each copy resolved its own gitignored
state directory, so two agents were each told they held a claim while excluding
nobody. A lock that reports success and protects nothing is worse than no lock,
because people trust it. `resolve.ps1` now throws rather than fall back.

## Removing the module

1. Delete `scripts/agent-harness/`.
2. Delete `documentation/implementation-guide/multi-agent-concurrency/` and
   `.claude/skills/merge-queue/`.
3. In `scripts/claude-sessions-bridge/bridge.py`, remove the `harness_config`
   import block below `WORKTREE_SCRIPTS`, `harness_off_reason()`, and the
   `profile:` / `worktree:` / `release:` directive branches.
4. Remove the worktree/profile columns from `scripts/claude-sessions-bridge/sessions.py`.
5. Drop the harness rows from `CLAUDE.md`.
6. In `agent-org/docker/docker-compose.yml`, remove the `runner-registry.json` bind
   mount, `AO_RUNNER_REGISTRY_FILE` and `AO_WORKER_POOL_SOURCE`. agent-org keeps
   working on `AO_WORKER_INSTANCE_URLS` alone — an absent file leaves every address
   at the documented default kind, and `AO_WORKER_POOL_SOURCE` already defaults to
   `env`, which IS the pre-U4 pool behaviour.

State under `.git/agent-worktrees/` is disposable once no worktree holds unmerged
work — check with `git worktree list` before deleting it.

## Related

- [PLAN.md](../../documentation/implementation-guide/multi-agent-concurrency/PLAN.md) — why the pipeline replaced a merge lock
- [HARNESS-V2-PLAN.md](../../documentation/implementation-guide/multi-agent-concurrency/HARNESS-V2-PLAN.md) — anchors, runners, profiles, this boundary
- [MERGE-PROTOCOL.md](../../documentation/implementation-guide/multi-agent-concurrency/MERGE-PROTOCOL.md) — the agent-facing protocol
- [README.md](README.md) — day-to-day usage and the gotchas found while building it
