# Findings: wiki-gate-polish (2026-09-03)

Findings sink for harness item `wiki-gate-polish`. Each claim states how it
was checked.

## 1. The `git -C <submodule>` trap has a THIRD costume: the uninitialized dir

Known so far: (a) hook env — GIT_DIR/GIT_INDEX_FILE override `-C` (gate,
fixed `1dbc042`); (b) same, solved earlier in `.githooks/commit-msg:58`.
Found while probing this item: (c) an UNINITIALIZED submodule — an empty dir
with no `.git` — makes `git -C OB1 rev-parse HEAD` **succeed with the
parent's answer** (git walks up), it does not fail. Probed 2026-09-03 in a
scratch `git clone --no-checkout` with a staged gitlink: the gate's mismatch
message presented the parent's HEAD as OB1's. Consequence for the anchor's
own acceptance: the "Git-InOB1 returns null on stderr" criterion was built
on a wrong prediction — the uninit path produces NO stderr; the reachable
fix is an explicit `Test-Path OB1/.git` guard (landed in this item), while
the ErrorActionPreference fix remains right for genuinely-failing git
(corrupt repo, permission errors). Any script that shells `git -C` into a
possibly-uninitialized submodule needs the existence check FIRST.

## 2. The floor's failure mode was "silently vacuous" until caught in dev

First draft of the shrink floor treated a failed `ls-tree` as an empty array
→ count 0 → old side reads "no tests" → floor passes vacuously exactly when
it cannot see. Caught by re-reading before commit, fixed with an explicit
`-1` sentinel on `$LASTEXITCODE`. Recorded because it is the same shape as
the vacuous-check class this whole line exists to kill: an error that
looks like an empty result turns a guard into a green lamp.

## 3. `AI_STACK_WORKTREE_STATE` makes queue.ps1 fully hermetic for probes

The evidence-spill acceptance was proven on a scratch state dir via the
documented override: propose→confirm→submit→claim→pass ran end-to-end
without touching the real queue, and each PowerShell tool invocation gets a
fresh environment, so the override cannot leak between calls. Useful
pattern for any future queue.ps1 change; the anchor gate's shortness check
(a one-word acceptance criterion is refused) was also observed working.

## 3b. The Test-Path-on-prose throw: the governing variable is a COLON (attempt 2)

Attempt 1's tester proved inline evidence can throw at the path check and
named the illegal-path punctuation class (`| < > " * [`) as the trigger; the
reviewer's matrix showed that diagnosis wrong: **a colon anywhere in the
string suppresses the throw** (drive-qualified handling skips validation),
so colon-free multi-line prose with no special characters throws while
pipe-laden text with a timestamp sails through — and `-LiteralPath` changes
no outcome. Fixed in this item (single-line + length pre-filter before
Test-Path, try/catch around it); all four input classes proven on a scratch
queue. The meta-lesson stands on its own: a defect's REPRODUCTION is not its
DIAGNOSIS — the tester's probe pairs differed in two variables and the wrong
one got the credit. Isolate before you name a cause.

## 4. Out-of-scope observations (queue.ps1 mechanics, found across this item's cycles)

- **`-Requeue` does not bump `attempt`** and `-Resubmit` accepts only
  `test-failed` — so after a requeue there is no mechanism to revise the
  queued test plan, and the next `-Pass` OVERWRITES
  `<id>.attempt1.evidence.md` (attempt-1 tester evidence preserved by hand
  as `...attempt1.evidence.KEPT-tester1.md` this time). Harness follow-up:
  requeue should bump the attempt counter (fixing both), or -Resubmit
  should accept `ready-to-test`.

- `queue.ps1`'s `-Resubmit` path copies a revised plan over the OLD plan
  file (`$item.test_plan`) rather than a per-attempt name, so a failed
  attempt's plan is overwritten by the retry's. History of plans across
  attempts is lost. Read from the code (`queue.ps1` -Resubmit handler);
  not probed, not fixed here.
- PSScriptAnalyzer warns `Git-InOB1` uses an unapproved verb. Cosmetic;
  the repo's lint gate is ruff (Python) and does not run analyzer rules.
  Renaming would churn the landed gate for no behavior change.

## 5. `gatesee` (2026-09-04): a worktree's own commits do NOT run its own hook file

`core.hooksPath` is set in the SHARED `.git/config` to the ABSOLUTE path
`D:/Open WebUI/ai-stack/.githooks` (checked:
`git config --show-origin --get-all core.hooksPath` ->
`file:D:/Open WebUI/ai-stack/.git/config`). A linked worktree shares that
config, so a `git commit` made inside `.claude/worktrees/wt-*` executes the
MAIN checkout's `.githooks/pre-commit` - while the `./scripts/checks/*.ps1`
that file invokes resolve against the WORKTREE's cwd. Commits from a worktree
therefore run a HYBRID: the main checkout's hook script driving the worktree's
check scripts.

Consequences, both real:

- A developer who edits `.githooks/pre-commit` in a worktree cannot observe
  their change by committing there. It looks like the edit did nothing. The
  override that does work is `git -c core.hooksPath=.githooks commit ...`
  (relative resolves against the worktree root), which is what `gatesee`'s
  test plan mandates for every hook-real verdict.
- A check script DELETED or RENAMED on a work branch still gets invoked by the
  main checkout's hook, which then fails with a PowerShell "file not found"
  rather than a check verdict.

Not fixed here (out of `gatesee`'s scope, and the fix is a policy call:
`git config core.hooksPath .githooks` as a RELATIVE value would make every
worktree run its own hooks, which is either the right isolation or an
unreviewed-hook hazard depending on who you ask).

## 6. `gatesee`: what the new 5c gate still cannot see

- **`OB1/recipes/daily-digest/src/podcast/script-renderer.test.ts` is excluded
  from `deno check`.** It is the only file in the recipe with a remote import
  (`jsr:@std/assert@1`, confirmed by grep over every `.ts` in the recipe), so
  including it would make the gate depend on a warm deno cache and a network.
  A type error inside that test file is consequently still uncaught at commit
  time. Running it (`deno test`) is explicitly out of the item's scope.
- **`OB1/recipes/vercel-neon-telegram`** also carries `.test.ts` files and is
  covered by nothing in the hook chain. Out of scope by the anchor; recorded
  so the hole is on paper rather than in someone's head.
- **`deno check` on this recipe writes no lockfile** (probed: `deno check
  link-enrich.ts` with the lock removed created no `deno.lock`, because the
  recipe has no `deno.json`). The gate still passes `--no-lock` so that a
  future `deno.json` cannot start dirtying the OB1 submodule and tripping 5b.
