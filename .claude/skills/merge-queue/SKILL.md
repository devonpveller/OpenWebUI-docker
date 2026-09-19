---
name: merge-queue
description: |
  Drive the ai-stack agent harness: see what parallel agents are working on,
  what is waiting on the operator, and move work through the develop -> test ->
  review -> merge pipeline. Use when the user asks about the merge queue, the
  work queue, what agents are doing, what needs their approval, an anchor, a
  worktree, or a plane lease - and when they say "release", "approve", "confirm
  the anchor", or "what is blocked".
author: ai-stack
version: 1.0.0
---

# merge-queue — driving the agent harness

The harness lives in `scripts/agent-harness/` (module boundary and configuration:
`scripts/agent-harness/MODULE.md`). This skill is the operator-facing view of it.
Agents follow
`documentation/implementation-guide/multi-agent-concurrency/MERGE-PROTOCOL.md`;
you follow this.

## First: is it even on?

```powershell
.\scripts\agent-harness\queue.ps1 -List
```

Exit 2 with a sentence means the harness is disabled — that is a setting, not a
fault (`enabled: false` in `harness.config.json`, or `AI_STACK_HARNESS_ENABLED=0`).
Say so plainly rather than trying to work around it.

## The board

`-List` shows every work item, its state, its developer, and who holds it. Two
flags matter because they mean **nothing is moving until a person acts**:

| flag | what the operator has to do |
|---|---|
| `[waiting: operator to confirm the anchor]` | agree what the work is for, before it is built |
| `[waiting: operator to release for review]` | tests are green; release it, or change course |
| `[needs hand-off]` | the work line is checked out in the main checkout, so no agent can merge it — the reviewer will hand the exact command back |

When the user asks "what is blocked" or "what needs me", those three are the
answer. Lead with them; do not make the user read a full board to find them.

## The two human gates

**Gate 1 — the anchor, before any work.**

```powershell
.\scripts\agent-harness\queue.ps1 -Show -Id <id>          # read the proposed anchor
.\scripts\agent-harness\queue.ps1 -ConfirmAnchor -Id <id> -By <operator>
.\scripts\agent-harness\queue.ps1 -ConfirmAnchor -Id <id> -By <operator> -Anchor <amended.json>
```

Read `goal`, `artifact`, `audience` and `out_of_scope` back to the user in plain
words before confirming — that is the whole point of the gate, and the agent
cannot proceed until it closes (`-Submit` exits 5).

**Amending is normal, not a correction.** The operator is allowed to change what
the work is for; the record then shows what was actually agreed rather than what
was asked. If an agent flags a judgement call in its proposal, resolve it here —
that is the cheapest moment in the pipeline.

**Gate 2 — release for review, after the tests pass.**

```powershell
.\scripts\agent-harness\queue.ps1 -Approve -Id <id> -By <operator>
```

Green tests say the work does what the plan said. They do not say the operator
still wants it, or wants it this way. Once review starts the next step is a
merge, so this is the last cheap moment to change course.

From a Mattermost thread, the same gate is `release: <id>`.

## Reading a verdict

`-Show <id>` carries the anchor, the history, and per-attempt results. Three
fields answer most questions:

- `attempt` — cycles are the tests **doing their job**. A case failed because it
  found something real. An involved change that finds nothing on the first pass
  is a reason to doubt the plan, not to celebrate.
- `plan_adequate` — the tester's judgement of the developer's plan, stated on
  both verdicts.
- `fits_codebase` — the reviewer's fitness judgement (`-FitsCodebase`/`-Misfits`).
  A merge cannot happen without it. It asks whether the work BELONGS in this
  codebase — right module, house patterns, coherent tree — **not** whether it was
  the right thing to build: intent is settled at the anchor gate and again at the
  release gate, so an intent objection goes back there, not into this verdict.
  Items merged before 2026-08-29 carry `fits_anchor` instead, which answered that
  older question; the two are deliberately not migrated into one field.

## What the operator does NOT do

- Do not test or merge on an agent's behalf to speed things along. The separation
  of duties is the mechanism; short-circuiting it removes the only thing that
  makes the pipeline worth its overhead.
- Do not confirm an anchor you have not read.
- Do not run `queue.ps1` verbs on someone else's claim.

## Worktrees

```powershell
.\scripts\agent-harness\new-worktree.ps1 -Id <short-id>
.\scripts\agent-harness\remove-worktree.ps1 -Id <short-id>
git worktree list
```

`remove-worktree.ps1` **refuses (exit 2)** while a worktree holds uncommitted or
unmerged work. That refusal is the feature — override it only when the user has
seen what would be lost.

## Leases — shared runtime only

```powershell
.\scripts\agent-harness\lease.ps1 -Status
.\scripts\agent-harness\lease.ps1 -Acquire -Name <plane> -Owner <id>
.\scripts\agent-harness\lease.ps1 -Release -Name <plane> -Owner <id>
```

Names are in `lease-names.conf`. Leases cover what a worktree cannot isolate: the
Docker daemon, the GPU, host ports, live databases. **They are not for files and
not for merging** — a worktree already isolates files, and git refuses two
worktrees on one branch.

## Profiles — which models the roles run on

```powershell
. .\scripts\agent-harness\config.ps1
Get-HarnessProfileNames
Resolve-RoleTarget -Role reviewer -Surface mattermost
```

`all-cloud` (opus for worker, tester and reviewer) is the default. Extension
sessions are **locked** to it. Mattermost threads switch with `profile: <name>`.
The `little-coder` (local) runner is wired but unproven — its `status` field says
so, and that is not decoration.

## Verifying the harness itself

```powershell
.\scripts\agent-harness\verify-merge-protocol.ps1
```

An end-to-end drill: two agents, a conflict, a failed case, both gates, a stale
pass, two merges, and cleanup. Run it after changing `queue.ps1`, `lease.ps1`,
the worktree scripts, or the protocol. It leaves nothing behind and never touches
`development`.
