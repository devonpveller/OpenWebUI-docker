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
| `dispatch.ps1` | RUN the work: role+profile → runner → submit, follow, one outcome + exit code |
| `verify-merge-protocol.ps1` | the executable drill over the whole protocol |
| `verify-dispatch.ps1` | the executable drill over the dispatch layer (probes the REAL daemon by default; `-Offline` skips that and says so) |
| `harness.config.json` | the configuration (see below) |
| `config.py` | the reader other Python code imports (`bridge.py` does) |
| `quadrant/` | the runner x target comparison - its own submodule with its own boundary, see [quadrant/MODULE.md](quadrant/MODULE.md). The first executable CONSUMER of the runner axis: everything else resolves a runner and nothing runs one. |

Internal: `common.ps1` (composition root), `git-io.ps1` (git facts), `resolve.ps1`
(policy), `config.ps1` (settings), `anchor.ps1` (the anchor's shape and validation).

The dependency direction is one-way and deliberate:

```
queue.ps1 / lease.ps1 / new-worktree.ps1 / dispatch.ps1
        |
     common.ps1 ──> resolve.ps1 ──> git-io.ps1   (facts about this repository)
                          └──────> config.ps1    (files + environment -> settings)
                    anchor.ps1                   (the shape of an anchor; owns no state)
```

## Runners: resolving one and running one are two different things

`config.ps1`'s `Resolve-RoleTarget` answers *which* runner and model a role uses.
`dispatch.ps1` is what CALLS it. Until 2026-08-30 only the first half existed, which
is why PLAN.md's A11 ("the little-coder runner is wired, status: unproven") read as
optimistic: the wiring was a config entry naming a door — `http://127.0.0.1:8090` —
that `coder/docker-compose.yml` never published. See
`harness.config.json`'s `_why_docker_exec` for the transport decision and its revert path.

A runner record therefore carries topology (`transport`, `container`, `base_url`, the API
paths, and the `lease` a caller must hold) as well as policy, readable through
`Get-HarnessRunner` / `config.runner()`.

**DECLARED is not the same as EXISTS, and two different checks cover the two claims.**
`test_harness_config.py`'s door check is a substring match over the TEXT of the compose
files: it proves the door is **declared** — a `127.0.0.1:<port>` publish for `http`, a
`container_name:` for `docker-exec` — and it proves nothing about the running stack. Those
two genuinely differ here: on 2026-08-30 `coder/docker-compose.yml` declared
`127.0.0.1:9091->9090` for little-coder while `docker port little-coder` printed nothing and
`docker inspect little-coder --format '{{json .NetworkSettings.Ports}}'` gave `{"9090/tcp":[]}`.
**The cause of that disagreement is NOT established.** The obvious guess — that the running
container predates the declaration — is wrong: `docker inspect little-coder --format
'{{.Created}}'` is `2026-08-23T17:00:48Z` and the ports line has been in
`coder/docker-compose.yml` since `56af93a` (2026-08-21), so the container POSTDATES the
declaration by two days. The runtime claim is `verify-dispatch.ps1`'s live section, which by
DEFAULT inspects that the container is really running and reaches the daemon over the
declared transport; its summary line states the real transport's coverage on every run that
reaches the end — `COVERED`, `NOT COVERED`, or `ATTEMPTED AND FAILED`.

`dispatch.ps1` returns ONE outcome shape whatever the runner, and its exit code
distinguishes "the dispatch worked" from "the work is right": `0` acceptance passed,
`3` acceptance failed, `4` completed with no checkable signal, `1` no usable outcome at all
(no runner, no lease, unreachable transport, no focus, timeout, or a task that ended
`abandoned`/`rejected`). The little-coder daemon runs the acceptance command itself, so the
agent never grades its own work.

A runner that declares a `lease` cannot be dispatched unless the caller HOLDS that lease
(`-LeaseOwner`, or `AI_STACK_LEASE_OWNER`): focusing little-coder wipes its workspace and a
task runs arbitrary commands in it. Someone else's lease refuses the dispatch just as a free
one does.

**Run the drill as `powershell.exe -File`, not with `&` inside a session you are reusing.**
Under `powershell.exe -NoProfile -File .\scripts\agent-harness\verify-dispatch.ps1` it is
deterministic here. UNVERIFIED, reported by a U4 verifier and NOT reproduced: invoked with
the call operator (`& $drill -Offline`) repeatedly inside one long-lived session they saw
three different counts (50/51, 46/51, 48/51, differing checks) on an unmodified copy. Nine
such runs in one session on 2026-08-30 gave `51/51 checks passed` every time with zero
`[FAIL]` lines, so the cause is unknown and it may not be reproducible. The reported
failures were spurious REDs only — never a green that should have been red — so the risk is
a confusing verdict, not a false pass.

**No pipeline consumes `dispatch.ps1` yet.** `queue.ps1` does not call it and nothing else
does either — today it is driven by hand or by a session, and the only shipped consumer of
the runner layer is `bridge.py`'s `profile: list`, which renders `describe_runner()`.
Wiring dispatch into the queue is U4's remaining half, not something this file already does.

`config.ps1` knows nothing about git, worktrees, queues or leases — which is what
lets another distribution retarget the toolkit by editing JSON instead of code.

## Configuration

One file, two readers. `config.ps1` serves the scripts, `config.py` serves the
bridge, and `test_harness_config.py` asks them the same questions and compares the
answers so they cannot drift apart unnoticed.

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
| `AI_STACK_LEASE_OWNER` | default `-LeaseOwner` for `dispatch.ps1`'s lease assertion |
| `AI_STACK_LEASE_DIR` | the lock namespace (drills point it at a temp dir to stay hermetic) |

Two more are named *by* the configuration rather than hardcoded, so a distribution
can rename them: `worktree.work_line_env` (default `AI_STACK_WORK_LINE`, which
branch agents work against) and `worktree.state_dir_env` (default
`AI_STACK_WORKTREE_STATE`, where coordination state lives).

## Profiles: which models the roles run on

A profile assigns each role a **runner** and a model. The runner matters because
"cloud" and "local" are different execution substrates, not two values of one
setting: `claude-code` is a Claude Code agent, `little-coder` is the local control
plane reaching `llama-cpp` through LiteLLM.

`all-cloud` — opus for all three — is the default everywhere. `all-local`,
`local-work-cloud-review` and `cloud-work-local-test` are also defined.

- **Extension sessions are locked** to `all-cloud` (`profile_locked: true`). A
  requested profile is ignored there. Operator decision, 2026-08-28: the surface
  the operator drives interactively should never silently degrade.
- **Mattermost sessions** switch with `profile: <name>` in a thread. The bridge
  passes the choice to the session as `AI_STACK_HARNESS_PROFILE`, which is simply
  the configuration's own top layer — no separate mechanism.

The `little-coder` runner is wired and callable but **unproven**: no work item has
completed through it yet. Its `status` field says so, and that is not decoration —
do not read config support as a working feature.

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

State under `.git/agent-worktrees/` is disposable once no worktree holds unmerged
work — check with `git worktree list` before deleting it.

## Related

- [PLAN.md](../../documentation/implementation-guide/multi-agent-concurrency/PLAN.md) — why the pipeline replaced a merge lock
- [HARNESS-V2-PLAN.md](../../documentation/implementation-guide/multi-agent-concurrency/HARNESS-V2-PLAN.md) — anchors, runners, profiles, this boundary
- [MERGE-PROTOCOL.md](../../documentation/implementation-guide/multi-agent-concurrency/MERGE-PROTOCOL.md) — the agent-facing protocol
- [README.md](README.md) — day-to-day usage and the gotchas found while building it
- [quadrant/MODULE.md](quadrant/MODULE.md) - the runner x target quadrant comparison (dark-factory U4)
