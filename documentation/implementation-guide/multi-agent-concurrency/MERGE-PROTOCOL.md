# MERGE-PROTOCOL — how parallel agents land work without wrecking each other

**Status:** LIVE (2026-08-28). The tooling this references is built and verified:
`scripts/worktree/{new-worktree,sync-worktree-env,remove-worktree,lease}.ps1`.

**Audience:** any Claude session — VS Code extension or Mattermost bridge — that is
about to change this repo. Read this before your first `git add`, not after your
first conflict.

---

## 0. The one-paragraph version

Work in your own worktree. When your work is ready, take the `merge` lease, rebase onto
your work line, re-run your gates, merge `--no-ff` with your evidence in the commit
message, release it, and say what you landed in your Mattermost thread. (If the line is
checked out in the operator's checkout, you hand off instead of merging - Step 4.) If
your rebase conflicts with work someone else landed first, **you adapt** - you do not
re-litigate what has already landed. If adapting would break the
other agent's goal, talk to them in their thread before resolving. If you two cannot
agree, stop and ask the operator.

## 1. Before you touch anything

```powershell
.\scripts\worktree\new-worktree.ps1 -Id <short-id> -OwnerKind extension -OwnerRef <session>
# then EITHER:  EnterWorktree path: <the path it prints>   (moves your session there)
# OR simply:    git -C <the path it prints> ...          (works from anywhere)
```

`EnterWorktree` is a convenience, not the mechanism - it is unavailable to some session
kinds (a subagent with a pinned cwd was refused outright). If it does not work, use
`git -C <worktree>` and absolute paths; what must hold is that your commits land in the
worktree and the main checkout is never mutated, and neither depends on your cwd.

The script prints your **work line** - the branch you branched from and will land on. It
defaults to whatever the main checkout currently has loaded, so agents inherit the tooling
and docs on that branch; override with `-Base` or `AI_STACK_WORK_LINE`.

This is not optional politeness — a bare `git worktree add` is broken here in three
verified ways (no `.env`, empty `OB1/`, wrong base branch), and a shared checkout is
how one session's `git add` swept another's staged gitlink on 2026-08-23.

Rules while you work:

- **Stage by path, never `git add -A`.** Other agents' files are dirty in *their*
  trees, but your worktree can still see stray files; sweeping is how accidents
  happen.
- **Never `cd` into the main checkout to mutate it.** It is the operator's.
- **Testing runs under plane leases, not cloned environments.** Before any test that
  *mutates* a plane or *needs it stable to trust the result*, hold that plane's
  lease; a read-only probe (curl a health endpoint) needs none:

  ```powershell
  .\scripts\worktree\lease.ps1 -Acquire -Name open-brain -Owner <your-wt-id> -Thread <mm-thread>
  #   ... test ...
  .\scripts\worktree\lease.ps1 -Release -Name open-brain -Owner <your-wt-id>
  ```

  Lease names = the compose planes (`lease-names.conf`): `inference`, `memory`,
  `search`, `coder`, `frontend`, `open-brain`, `agent-org`, `portal`. Rules:
  1. A multi-plane test requests all names in **one call**
     (`-Name "frontend,open-brain"`) — sorted, all-or-nothing, so two agents
     cannot deadlock.
  2. **When unsure which planes you touch, widen.** Your announce post (step 5)
     names the leases you held; under-declaring is visible in the post-mortem.
  3. Exit 3 = held: wait, poll ≤1/min. Long build? `-Refresh` keeps it alive.
  4. **Editing files that belong to a plane needs no plane lease** - only touching the
     RUNNING plane does. A docs, config or source change you do not deploy is the
     read-only case: take `merge` to land it, and nothing else.
  5. Leave the plane the way you found it *before releasing* — a lease serializes
     interference, it does not clean up after you. Test data stays test-prefixed
     (`testing-*`), test images tag `:wt-<id>` and never touch `:local` (retagging
     IS the deploy, which stays gated).
- **Never mutate prod containers, `:local` tags, or prod volumes** — that is a
  *deploy*, a separate gated step. Where a container-level sandbox is cheap, use
  the proven sidecar shape (`docker run --entrypoint ... ` on a private/no
  network) — but never attach a test container to the `ai-stack_*` anchor
  networks (it would hijack service aliases and block emergency recovery), and
  never reuse a prod `container_name` (the watchdog inspects by name).
- If your run spans hours, `.\scripts\worktree\sync-worktree-env.ps1 -Id <id>` picks
  up any `.env` change the operator made meanwhile.

## 2. Landing it

**Step 1 — validate in your own worktree, first.** Your task's own RED→GREEN
evidence, plus `ruff check .` and a compose `config` render for any plane you
touched. A merge is not the place to discover your branch is red.

**Step 2 — take the `merge` lease.**

```powershell
.\scripts\worktree\lease.ps1 -Acquire -Name merge -Owner <your-wt-id> -Thread <your-mm-thread>
```

Exit 3 means someone else is merging: **wait**, poll at most once a minute, and do
something useful meanwhile. Do not force. Refresh (`-Refresh`) if your rebase runs
long; the TTL is 30 minutes and exists only so a dead agent cannot block the queue
forever.

**Step 3 — rebase onto the current tip and re-run your gates.**

```powershell
git fetch origin
git -C <your-worktree> rebase <your work line>       # the branch new-worktree.ps1 reported
```

This is where cross-agent breakage surfaces, which is the entire point of doing it
under the lease. **Re-run the gates after the rebase** — green before the rebase says
nothing about green after it.

**Step 4 — merge with evidence, in a dedicated merge worktree.**

```powershell
# Write the evidence to a FILE first: multi-line -m is impractical in PS5.1, and unlike
# `git commit`, `git merge -F -` does NOT read stdin ("could not read file '-'").
git -C <main-checkout> worktree add .claude/worktrees/merge-line <work line>
git -C <main-checkout>/.claude/worktrees/merge-line merge --no-ff work/<your-id> -F <msg-file>
git -C <main-checkout> worktree remove .claude/worktrees/merge-line
```

**If the work line is checked out in the main checkout, you cannot merge - hand off
instead.** Git refuses a second checkout of one branch, and force-moving the ref would
leave that working tree's index lying about its contents. This is the normal case when
the line is the operator's active branch, so it is a supported outcome, not a failure:
leave your branch rebased and green, release the lease, and report the exact command for
them to run. `new-worktree.ps1` warns about this at PROVISIONING time so it is never a
surprise at landing time.

**Never `git checkout <work line>`** to do this - it switches your own worktree off its
branch, and the Verify-D drill showed the original wording only worked by luck of where
the operator's checkout was parked. The merge worktree is owned by whoever holds the
`merge` lease, so it can never collide. Never merge inside the operator's checkout: it
is their working copy and a bridge turn could be running in it.

`--no-ff` keeps your branch visible in history. The merge message carries the
validation evidence — that is the operator's branch policy made mechanical, and it
is what makes a later bisect readable. If your merge bumps the `OB1` gitlink, verify
the SHA is reachable on the OB1 remote **first**; an unreachable gitlink breaks every
fresh `--recurse-submodules` clone.

**Step 5 — release and announce.**

```powershell
.\scripts\worktree\lease.ps1 -Release -Name merge -Owner <your-wt-id>
```

Then post in your own #claude-sessions thread: what landed, which files, the
evidence, and whether a deploy is still pending. Other agents read this to know
whether they need to rebase.

**If you have no thread** (extension sessions, subagents), your merge commit message IS
the announcement - it carries the same content and is what the next agent sees when they
rebase. Say exactly that in your report rather than claiming you announced, and never
drop the evidence just because there is no thread to put it in.

**Step 6 — clean up.**

```powershell
.\scripts\worktree\remove-worktree.ps1 -Id <your-id>
```

It refuses if you still hold uncommitted or unmerged work, and tells you what. That
refusal is a feature: it is the difference between "cleaned up" and "deleted the only
copy".

## 3. Conflicts: who talks to whom

**Tier 1 — mechanical** (adjacent edits, both-added files, import ordering). The
merging agent resolves them during the rebase. **The later merger adapts.** This rule
exists so merge order cannot change correctness, and so nobody has an incentive to
race for the lease.

**Tier 2 — semantic overlap** (you both changed the same subsystem with competing
intents; adapting mechanically would defeat the other agent's goal). Do not resolve
it silently:

1. Post into the **other session's Mattermost thread** — the worktree registry
   (`scripts/worktree/state/worktrees.json`) carries each worktree's owner thread.
   Describe the conflict, your proposed resolution, and what it costs them.
2. The bridge's follow/auto-wake machinery wakes that session with your post as its
   next prompt. It answers in-thread. This is existing, working machinery — no new
   code, and it leaves an auditable trail the operator can read later.
3. Implement what you agree on, then finish the merge.

**Use Mattermost, not `SendMessage`.** A `ListAgents` peer may be a headless
subprocess mid-turn, and injecting into one derails it — that happened on 2026-08-23
and produced confidently-wrong answers. The thread is the bus for both entry points.

**Tier 3 — no convergence, or the resolution is destructive** (one of you wants to
delete what the other depends on). Stop. Ask the operator — `AskUserQuestion` in the
extension, a plain question in-thread from the bridge. Never resolve a disagreement
by force-push; `development` history is append-only.

## 4. What stays human

| Action | Who |
|---|---|
| Merge into `development` with evidence | **Agents** (this protocol) |
| Promotion of `development` → `main` | Operator, deliberately |
| Deploying to prod containers / retagging `:local` | Gated step; operator-approved |
| `git push` | Only when the operator asks |
| Resolving a Tier-3 disagreement | Operator |

## 5. Failure modes and what to do

| Symptom | What it means | Do |
|---|---|---|
| `-Acquire` exits 3, lease HELD | Another agent is mid-merge | Wait; poll ≤1/min |
| `-Acquire` exits 3, lease EXPIRED | Previous owner probably died | `-Takeover`, **and** post a note in their thread |
| Rebase conflict you cannot judge | Semantic overlap | Tier 2 — talk to them |
| `remove-worktree` refuses | Your only copy of something | Land it or `-Force` deliberately |
| Worktree exists but registry row is gone | Manual `git worktree add`, or a crash | `remove-worktree.ps1 -PruneRegistry` |
| `.env` in your worktree looks stale | Operator changed it after you branched | `sync-worktree-env.ps1 -Id <id>` |

## 6. Deliberate limits

- **A merge is serialized; the work is not.** Only the land step takes the `merge` lease, and
  it is short. Everything before it runs fully parallel.
- **`development` is never left mid-merge.** The rebase happens on your branch; the
  merge itself is one fast operation. A dead agent leaves a stale lease and a
  half-rebased branch in *its own* worktree — never a broken shared line.
- **This protocol does not cover deploy verification.** Anything that must be proven
  through the real caddy/tailnet chain happens after the merge, serially, by nature.
