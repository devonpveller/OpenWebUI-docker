# Incident — the merge-protocol drill left the operator's checkout mid-rebase on the live work line

2026-08-30, found and repaired by the orchestrator. **Repaired: the branch is intact.** Read
the "what is proven" split before acting on the cause.

## What happened

While committing a note, a `git commit` timed out. Investigating, `git branch --show-current`
returned **empty** — the main checkout at `D:\Open WebUI\ai-stack` was in **detached HEAD,
mid-rebase**:

```
.git/rebase-merge/head-name  ->  refs/heads/refactor/ai-stack-cleanup
.git/rebase-merge/onto       ->  104d8f0   (a development-line commit)
HEAD                         ->  335d46f   "Part M build 2: development branch live, ..."
```

That is the **live work line being rebased onto the development line, in the operator's own
checkout**. The reflog shows the same shape at least three times:

```
rebase (pick):  Part M build 2: ...
rebase (start): checkout drill/verify-d
rebase (abort): returning to refs/heads/refactor/ai-stack-cleanup
```

The earlier cycles self-aborted within seconds. This one did not: its files were **8 minutes
stale** (13:11 vs 13:19) with no `index.lock` and no progress, i.e. the process that started it
had died mid-drill — consistent with the known trap that background work is killed when a turn
ends.

`refactor/ai-stack-cleanup` still pointed at `98cf02e` throughout, so **no commit was lost**.
`git rebase --abort` restored the checkout to the branch — the same operation the prior cycles
performed, so this was the restorative action, not a destructive one.

Side effect while it was detached: the working tree held a *different* line's content, which is
why `ls scripts/agent-harness/*.ps1` briefly reported no such file.

## What is PROVEN

1. The state above, read directly from `.git/rebase-merge/*` and the reflog.
2. `drill/verify-d` is created by `scripts/agent-harness/verify-merge-protocol.ps1:102`
   (`branch drill/verify-d development`), so the rebase came from a drill run.
3. **`Invoke-DrillGit` swallows every git error.** Its whole body is
   `$ErrorActionPreference = "Continue"; & git.exe @args | Out-Null`. No exit-code check, no
   stderr surfaced. Any git step in the drill can fail completely invisibly — and the drill
   reports checks green around it.
4. **`git -C "" <cmd>` silently runs in the current directory and exits 0.** Verified in a
   scratch repo: `git -C "" rev-parse --show-toplevel` printed the scratch repo's path, exit 0.
   An empty path makes `-C` a no-op rather than an error.
5. The drill's own header (line 16) claims: "Runs against a SCRATCH line (drill/verify-d),
   never `development`. The operator's checkout ..." — a protection claim that did not hold.

## What is NOT proven

**Which line fired.** The leading hypothesis is that a `-C <worktree>` argument resolved empty,
so `Invoke-DrillGit -C $wtA rebase drill/verify-d` (line 456, and the same shape at 488)
degraded to `git rebase drill/verify-d` **in the current directory** — the main checkout, whose
branch is the work line. That fits every observation, and finding 4 shows the degradation is
silent. But `$wtA` is assigned at :107 as `Join-Path $repo ".claude\worktrees\wt-drilla"`, which
is not obviously empty, so the chain is inferred, not demonstrated.

It is equally possible an agent ran a bare `git rebase` in the main checkout while following
the reviewer's rebase step of MERGE-PROTOCOL.

I am recording the hypothesis as a hypothesis. Asserting a mechanism I did not execute is the
exact error this effort keeps catching in others, and it would be worse coming from the
orchestrator, whose notes are read instead of the diffs.

## Why this matters more than a one-off repair

The drill is what agents run to prove the merge protocol works. Two of its properties combine
badly:

- a git failure inside it is **invisible** (finding 3), and
- a mis-scoped `-C` degrades to **operating on the current repo** rather than erroring
  (finding 4).

So the safety property "the drill never touches the operator's checkout" is not enforced by
anything; it holds only as long as every path variable happens to be right. That is the same
class this effort has now found ten times — **a guard that silently degrades to no guard** —
and here it sits inside the tool that certifies the merge protocol.

It also lands on a policy already in CLAUDE.md: worktree-per-session exists precisely so that
concurrent sessions do not collide in one checkout. The drill's own header claims compliance.

## What I did NOT do, deliberately

I did not fix the drill in this commit. Four fan-outs are in flight and their agents each carry
their own copy of this script in their worktrees; editing the work-line copy now neither
protects them nor is it safe to reason about mid-run. The fix belongs in a scoped item with a
red-proof, and in-flight worktrees keep the vulnerable copy until they rebase.

## Recommended fix, for whoever takes it

1. Make `Invoke-DrillGit` check the exit code and surface stderr. A drill that cannot see its
   own git failures cannot certify anything. Red-prove it with a deliberately failing step.
2. Assert every worktree path is non-empty AND exists before any `-C` use — or drop `-C` and
   use an explicit `--git-dir`/`--work-tree` pair — so a bad path is an error, never a silent
   retarget onto the current repo.
3. Add a guard that refuses to run any drill git operation when the resolved target is the main
   checkout, and prove it RED by pointing it there on purpose.
4. Consider running the drill against a throwaway clone rather than the live object store.

## DECISIONS entries to append

- **2026-08-30, incident:** the merge-protocol drill left the operator's main checkout in
  detached HEAD mid-rebase, rebasing `refactor/ai-stack-cleanup` onto a development-line commit;
  the process had died 8 minutes earlier. No commit was lost (the branch ref held at `98cf02e`)
  and `git rebase --abort` restored it. Proven contributing defects: `Invoke-DrillGit` swallows
  every git error, and `git -C ""` silently operates on the current directory instead of
  failing. The specific line that fired is NOT proven and is recorded as a hypothesis.
  Revert path: none needed — the repair was an abort, and no code changed.
- **2026-08-30, method:** a safety property asserted in a script header ("never the operator's
  checkout") is worth nothing unless something refuses when it is violated. Two silent
  degradations — an unchecked git exit code and an empty `-C` — were enough to turn the drill
  that certifies the merge protocol into the thing that rebased the live work line.
