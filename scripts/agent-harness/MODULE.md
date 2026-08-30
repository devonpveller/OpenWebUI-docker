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
| `verify-oracle-on-stall.ps1` | the executable drill for frontier-oracle-on-stall |
| `oracle_on_stall.py` | the stall detector, the escalation, and its ledger (U4) |
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
do not read config support as a working feature. To be precise about how
little is wired: `resolve_role` maps a role and a profile to a runner, and
**nothing in this module then dispatches to one**. There is no code path that
submits a task to little-coder's API — and that API port is not published to
the host in any case (the coder plane publishes only `127.0.0.1:9091`, the metrics
port).

## Frontier-oracle-on-stall

ORCHESTRATION-DESIGN sec 7 splits the work roughly 95% small-model search / 5%
frontier unstick, and is specific that the frontier is "an oracle invoked on a
stall signal — not a better worker": it injects the one constraint that only
knowledge provides, then hands back.

`oracle_on_stall.py` is that stall signal, and the durable record of the
escalation. It runs on every `queue.ps1 -Fail`, because a failed test round is the
only moment the line learns something new about whether an item is converging.

- **A stall** is agent-org's definition, ported rather than re-invented: a round
  whose failure signature is not novel against every signature seen on this item,
  or whose branch head did not move, is not progress; two such rounds in a row is
  a stall. `failure_signature` is `Orchestrator._failure_sig` byte for byte, and a
  test extracts that function from `orchestrator.py` and compares behaviour, so
  the two cannot drift apart quietly.
- **Firing** appends to `oracle-escalations.jsonl` in the shared state dir: what
  stalled, the round-by-round trail the detector saw, the runner that stalled, the
  oracle above it, and `hand_back_to`. Read it with `queue.ps1 -Oracle`.
- **When the worker is already the frontier runner** — which is what the default
  `all-cloud` profile means — the outcome is `no-oracle-above`: recorded, not
  escalated. Escalating claude-code to claude-code would fill the audit trail
  while changing nothing.
- **Nothing dispatches the oracle round yet.** `pending()` returns the target a
  dispatcher would use; the dispatcher is the unbuilt half of U4.

`queue.ps1 -Submit -RunnerProfile <name>` records which profile an item is worked
under, so the detector can name the runner that stalled. Not `-Profile`: `$Profile`
is a PowerShell automatic variable, and a parameter of that name shadows it.

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
shared by every worktree. `AI_STACK_WORKTREE_STATE` redirects it, which
is how a drill runs against a scratch namespace instead of the live queue and
ledger.

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
