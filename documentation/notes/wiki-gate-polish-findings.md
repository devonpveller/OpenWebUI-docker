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

## 4. Out-of-scope observations

- `queue.ps1`'s `-Resubmit` path copies a revised plan over the OLD plan
  file (`$item.test_plan`) rather than a per-attempt name, so a failed
  attempt's plan is overwritten by the retry's. History of plans across
  attempts is lost. Read from the code (`queue.ps1` -Resubmit handler);
  not probed, not fixed here.
- PSScriptAnalyzer warns `Git-InOB1` uses an unapproved verb. Cosmetic;
  the repo's lint gate is ruff (Python) and does not run analyzer rules.
  Renaming would churn the landed gate for no behavior change.
