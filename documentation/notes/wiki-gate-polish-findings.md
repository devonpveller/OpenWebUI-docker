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
## 7. `gate2` (2026-09-04): four residuals closed, and what the probes turned up

Findings sink for harness item `gate2` (the four residuals the `gatesee`
tester and reviewer left on the table). Everything below was measured in
`.claude/worktrees/wt-gate2` with `git -c core.hooksPath=.githooks commit`.

### 7.1 Baselines, so a future reader knows SILENCE is correct

Both new behaviours are expected to say NOTHING on today's corpus. Written
down so nobody reads correct silence as a broken warning:

- **Lexical case count == node's `# tests` == 48** at OB1 `48c0363` AND at
  `a18e2ca` (8 test files at both). Measured twice: by hand
  (`git ls-tree` + the same regex), and hook-real - the 5b run for a bump
  `48c0363 -> a18e2ca` printed `test CASES 48 -> 48` and `# tests 48` with no
  step-3c warning between them. The 3c warning firing today would be NEWS.
- **Zero untracked `.ts` under `OB1/recipes/daily-digest`** -
  `git ls-files --others --exclude-standard -- recipes/daily-digest` returns
  0 lines, including ignored files. 36 non-test `.ts` on disk, all tracked.
  So 5c's new untracked NOTE cannot fire today either, and its green path is
  byte-identical to the pre-change script (same md5 over captured stdout).

### 7.2 `git show` with an EMPTY argument shows HEAD and exits 0

Found by this item's own quotepath proof, in this item's own first patch.
The rewritten counter read `if ($e -match '<mode> blob <sha>\t<path>')` and
then `if ($Matches[2] -match '\.test\.mjs$') { $blobs += $Matches[1] }`.
`$Matches` is ONE automatic variable that every `-match` overwrites, so the
inner match destroyed the outer captures and `$blobs` filled with `$null`.
`git show $null` then printed the HEAD COMMIT, exited 0, and contributed zero
`test(` lines - so the gate reported `test CASES 0 -> 0` while the file count
was right. Two general lessons, both cheap to reuse:

- Copy every capture group out of `$Matches` before the next `-match` runs.
- A git plumbing call given a blank ref does not fail loudly; it answers about
  something else and exits 0. Any lookup whose argument is computed should
  validate the argument before shelling out. The fixed counter refuses to run
  `git show` on anything that is not 40 hex characters.

### 7.3 The quotepath drop and the 3c warning are two detectors for one defect

In the scratch-tree proof, the PRE-fix counter under-counted the non-ASCII
test file (`FILES 1 -> 1, CASES 2 -> 2`) while node ran 3 tests. That is the
3c disagreement signature. So had 3c existed alone, it would have flagged the
quotepath bug as "lexical 2, node 3" without naming the cause; had the
quotepath fix landed alone, the count would simply have been right. They are
independent, which is the useful property: a future silent-drop in the LEXICAL
half is now visible as a disagreement with node even before anyone knows why.

### 7.4 Residual limits of the pair, stated so nobody over-reads a green

- **3c cannot see an equal-count swap.** Delete a real case, add a different
  real case: lexical 48, node 48, no warning, and that is correct behaviour -
  the counts agree because both are true. The floor detects SHRINK; 3c detects
  DISAGREEMENT; neither detects substitution. Reading the OB1 diff is still
  the only thing that does.
- **An untracked `*.test.mjs` inside OB1 now surfaces as a 3c disagreement.**
  5b's dirty check is `--untracked-files=no` (deliberate, documented in its
  header), so an untracked test file is invisible to it - but node RUNS it off
  disk while the lexical floor reads git objects and cannot see it. The two
  counts then disagree and 3c warns. That is a happy accident, not a designed
  guard: it fires only when the untracked file actually contains cases.
- **5c's untracked NOTE compares against OB1's INDEX**, not against the staged
  commit (`git ls-files`). A file `git add`ed inside OB1 but not committed
  therefore reads as "tracked" and is not named - but 5b has already refused
  that commit by then (a staged-in-OB1 change is a tracked dirty change), so
  the case is unreachable in the hook chain. It would matter to anyone
  invoking 5c standalone.

### 7.5 Out of scope, left alone deliberately

- **`core.hooksPath` is still an absolute path to the main checkout** (section
  5 above). Unchanged by this item, and every verdict here was therefore taken
  with the explicit `git -c core.hooksPath=.githooks` override.
- **`AI_STACK_OB1_TESTS_ALLOW_SHRINK`.** The `gatesee` reviewer ruled the
  smuggle non-blocking BECAUSE this override is a louder, cheaper bypass. That
  ruling now has a second leg (3c warns on the smuggle's signature), but the
  first leg still holds and must: make ALLOW_SHRINK quiet and both arguments
  weaken at once.
- **The line's current OB1 pin `b69cdbf`** (as of parent `08c4ae1`) measures
  the same: 8 test files, 48 lexical cases, `# tests 48`, 0 untracked `.ts`
  under `recipes/daily-digest`. So silence is also correct one pin ahead of
  this branch's base. Recorded because the first measurement pass could not
  see `b69cdbf` at all - the worktree's submodule object store did not have it
  until the worktree was re-provisioned. "Not in my object store" is not
  "does not exist", and a count you could not take is not a count of 48.
