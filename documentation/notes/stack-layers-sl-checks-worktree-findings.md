# sl-checks-worktree: every check, and whether a worktree changes what it looks at (2026-09-20)

Findings sink for harness item `sl-checks-worktree` (stack-layers). The item exists because
`scripts/checks/check-llm-gateway-routing.ps1` pruned `*\.claude\*` by ABSOLUTE-path
substring, and a harness session worktree lives at `<repo>\.claude\worktrees\<id>\` - so
from a worktree the guard filtered every candidate away, read nothing, and exited 0. A
check that scans nothing from a worktree lets a worktree commit anything.
Symptom note: `documentation/notes/routing-check-worktree-blindspot-2026-09-19.md`.

**Scope of the sweep:** every executable file tracked under `scripts/checks/` and
`.githooks/` - 50 of them (47 scripts + 3 hooks; the other 6 tracked files are a README, a
data file, 2 fixtures and 2 .sql).

**Result: 2 fixed, 1 defect documented and deliberately not fixed, 47 safe with a reason.**

**How "MEASURED" was obtained.** Two trees of the same commit: this worktree
(`D:\Open WebUI\ai-stack\.claude\worktrees\wt-sl-checks-worktree`) and a scratch `git clone`
at a repo-root path outside `.claude`
(`...\scratchpad\meas\base`), with the OB1 submodule tree copied in so the two are
content-identical. Each check was run in BOTH and its own output compared; where a check
prints no count, the same violation was PLANTED in both and the verdicts compared. A row
marked READ was not run (it needs a staged OB1 gitlink, a live container or a database) and
says what was read instead - reading is weaker evidence and is labelled as such.

---

## 1. Fixed: check-llm-gateway-routing.ps1

Three edits, all about WHERE it looks; `$badPattern` and `$queueUpstreamAllow` - what it
flags - are untouched.

* `Test-Allowed` matches every `$allowPathLike` glob against the path RELATIVE to `$Root`
  (prefixed with `\`, so each existing glob still matches - `-like` lets `*` match the
  empty string). This closes the class rather than one instance: `*\data\*`,
  `*\documentation\*`, `*\backups\*` could each have swallowed a whole tree whose ABSOLUTE
  path happened to carry that segment.
* `'*\.claude\*'` leaves the allow list and `.claude` becomes a TRAVERSAL prune in
  `$pruneDirNames`. The walk starts AT `$Root`, so a name can only be pruned inside the
  tree being scanned: from the main checkout that is `<repo>\.claude\` (which also stops it
  walking other sessions' worktrees); from a worktree it is that worktree's own `.claude\`,
  and the `.claude` the worktree hangs off is above the root and unreachable.
* The scan set is counted. Both verdicts print `N file(s) scanned under <root>`, and `N = 0`
  now FAILS. The defect's whole shape was that nothing distinguished a clean tree from an
  unexamined one, because the number was never printed.

MEASURED: 1009 files from the worktree, 1009 from the clone once the eight gitignored `.env`
files a provisioned worktree carries are created there (raw clone: 1001). Planted
`OPENAI_API_BASE: http://llama-cpp-upstream:8080` in a staged
`inference/compose/zz-planted-bypass.yml`: base `a2d3644` = GREEN exit 0 from the worktree,
branch = `FAIL - 1 gateway bypass(es) found in 1010 file(s) scanned`, exit 1. The same plant
in the clone is caught by the BASE script too, which is what makes the defect the path and
not the pattern. Full table in the routing note.

## 2. Fixed: plan-store.ps1

Same class, different shape, and it had never been reported: `$Store` was
`Join-Path (Split-Path $Root -Parent) 'documentation-plans-ai-stack'`, and `$Root` from a
worktree is `<repo>\.claude\worktrees\<id>`, whose parent is `...\worktrees\`.

MEASURED from this worktree before the fix:
`plan store not found at D:\Open WebUI\ai-stack\.claude\worktrees\documentation-plans-ai-stack`,
exit 1. CLAUDE.md asks every planning session to run this at its start and again before it
stops, and a planning session that will commit is a worktree session - so the check every
plan depends on could not run where plans are written.

It now also tries the directory beside the MAIN checkout, found through
`git rev-parse --git-common-dir` (absolute from a worktree; a bare `.git` from the main
checkout, so it is resolved against `$Root` first). The checkout's own parent is still tried
FIRST, so an ordinary clone with the store beside it is unaffected, and `-Store` still wins.
MEASURED after: from the worktree it resolves `D:\Open WebUI\documentation-plans-ai-stack`
and audits `plan store: clean (versioned, pushed, indexed)`, exit 0; from a scratch clone
with no store anywhere near it, the same refusal and exit 1 as before.

## 3. Documented, NOT fixed: dev-helper.ps1

`scripts/checks/dev-helper.ps1:24` and `:45`:
`Get-ChildItem -Path $PROJECT_DIR -Filter "*.sh" -Recurse | Where-Object { $_.FullName -notmatch "\.git" }`
- an absolute-path substring filter, the same shape as the defect above.

MEASURED (the selector expression, run verbatim in three places): 33 files from this
worktree, 33 from the scratch clone, **168 from the main checkout**. So it does not
UNDER-scan from a worktree - the acceptance test for this item - it OVER-reaches from the
main checkout, into the three trees under `.claude/worktrees/` and the gitignored trees,
because it has no
prune at all beyond `\.git`.

Left alone deliberately, for three reasons:
1. Its `fix-lineendings` action WRITES (`Set-Content`, :51). Changing the selector changes
   which files a mutating action rewrites - that is not "where a check looks", and from the
   main checkout it would be rewriting other sessions' in-progress work either way. The
   fix is a prune list (`.claude`, `node_modules`, ...), which is a behaviour change to a
   mutating helper and belongs in its own item with its own test.
2. `-notmatch "\.git"` as a regex on the full path also excludes `.github/`; any rewrite
   ALTERS the set it lints rather than merely relocating it.
3. It is not a gate: nothing in `.githooks/` or CI runs it.

**Follow-up (open):** give `dev-helper.ps1` the routing check's prune list and root-relative
match, and decide deliberately whether `.github/**/*.sh` should be linted.

## 4. The sweep table

Position column = where it runs (`pre-commit` = the hook chain, in order).

### 4.1 The commit-time chain

| # | script | how it selects files | worktree vs clone | verdict |
|---|---|---|---|---|
| 1 | `check-staged-secrets.ps1` (pre-commit 1) | `git diff --cached --name-only --diff-filter=ACM` (:38) minus gitlinks from `git ls-files --stage` (:35); reads the staged BLOB (`git show :<path>`) | MEASURED identical: the same planted `zz-plant.env` staged in each produced the same `COMMIT BLOCKED / ENV FILE STAGED` block | safe - git answers with repo-relative paths in any checkout |
| 2 | `validate-lineendings.ps1` (pre-commit 2) | `git ls-files '*.sh'` (:16) from `Split-Path -Parent (Split-Path -Parent $PSScriptRoot)` (:8) | MEASURED identical: 24 tracked `*.sh` in both; SUCCESS in both | safe. Its separate known vacuity - exit 0 on a `git archive` export with no `.git` - is unchanged and still open (routing note, section 2) |
| 3 | `check-doc-placement.ps1` (pre-commit 2b) | `git rev-parse --show-toplevel` (:59), then `git diff --cached --diff-filter=A` (:121) or, under `-All`, `git status --porcelain --untracked-files=all` | MEASURED identical `-All` output: 3 tracked feature directories, no untracked paths | safe |
| 4 | `check-llm-gateway-routing.ps1` (pre-commit 3) | own directory walk from `$PSScriptRoot`'s grandparent; prune by directory NAME, then `$allowPathLike` globs | WAS 0 from the worktree vs 1001 from the clone; NOW 1009 vs 1009 (MEASURED) | **FIXED** - section 1 |
| 5 | `check-corpus-exposure-producers.ps1` (pre-commit 3b) | the same walk shape; prunes `worktrees` by name (:421); carries NO `.claude` glob and says at :435 why | MEASURED identical: `all 13 RECOGNISED corpus insert site(s) ... (773 file(s) scanned)` in both | safe, and already the model for the fix - it also asserts a non-empty universe |
| 6 | `check-project-configs.ps1` (pre-commit 4) | `git diff --cached --name-only --diff-filter=ACM` (:28); staged `.ps1` are tokenized, staged `.yml` triggers `docker compose config -q` over fixed project paths | MEASURED identical: the same planted broken `.ps1` gave the same `PS1 PARSE ERROR` line in both | safe |
| 7 | `check-env-file-scope.ps1` (pre-commit 5) | staged compose files (:491); `-All` walks from `$RootFull` and prunes on the ROOT-RELATIVE path, `(^|/)(\.git\|\.claude\|node_modules\|OB1)/` (:477) | MEASURED identical: `-All` clean in both, and the same planted `env_file: ../../.env` flagged identically | safe - fixed 2026-09-19/20 by sl-ao-envfile after the same worktree symptom; its comment at :469 is the precedent this item generalised |
| 8 | `check-ob1-recipe-tests.ps1` (pre-commit 5b) | toplevel (:84) + `git diff --cached` for an `OB1` gitlink; then `Get-ChildItem (Join-Path $Root 'OB1\recipes') -Recurse -Filter '*.test.mjs'` minus `node_modules` (:149) | READ: every path is `$Root`-relative and `$Root` is the worktree's own toplevel; it refuses when OB1 is uninitialised (:107) and when the test set is empty (:152). Not run - needs a staged gitlink | safe, with anti-vacuity already present |
| 9 | `check-ob1-deno-recipes.ps1` (pre-commit 5c) | toplevel (:140); fixed `$RecipeRels`, then `Get-ChildItem $recipeDir -Recurse -Filter '*.ts'` minus `node_modules` (:197) | READ: same shape; refuses on zero non-test `.ts` (:201) and WARNS when a listed recipe path is absent (:188) | safe |
| 10 | `check-ob1-integration-images.ps1` (pre-commit 5d) | staged gitlink from the index; `git archive` of the service directory AT THE PIN into a temp dir | READ: the content comes from git objects, not from the working tree, so the checkout's location cannot change it | safe |
| 11 | `.githooks/pre-commit` | invokes each check as `./scripts/checks/<name>.ps1` - cwd-relative, and git runs hooks with cwd at the top of the working tree being committed | MEASURED indirectly: committing from this worktree ran all ten against the worktree | safe. Note `core.hooksPath` is absolute to the main checkout, so the HOOK FILE is always the main checkout's copy - deliberate, and the attestation records which hook blob ran |
| 12 | `.githooks/pre-merge-commit` | `exec "$(dirname "$0")/pre-commit"` | READ: delegates | safe |
| 13 | `.githooks/commit-msg` | `git diff --cached --raw` for gitlinks; `git -C <sub> cat-file` with GIT_DIR unset | READ: index-derived, no path selection | safe |

### 4.2 Other scripts that read the repo or git

| # | script | how it selects files | worktree vs clone | verdict |
|---|---|---|---|---|
| 14 | `check-hook-attestation.ps1` | the ledger at `git rev-parse --git-common-dir`, resolved against the repo root when relative (:82-96) | READ: deliberately SHARED across worktrees - a reviewer in another worktree must read the developer's attestations | safe, documented in-script |
| 15 | `plan-store.ps1` | toplevel; the store beside the checkout | MEASURED: threw from a worktree, clean after the fix | **FIXED** - section 2 |
| 16 | `dfu-done.ps1` | `rev-parse --show-toplevel` with `-WorkDir $PSScriptRoot` (:491), then fixed document paths under it; makes its own CLONE for the audited run and says why a worktree would be wrong (:2016-2025) | READ | safe |
| 17 | `verify-dfu-done.ps1` | builds throwaway fixture repos in `%TEMP%` (:89, :110); its `Get-ChildItem -Recurse` is over the fixture | READ | safe - selects nothing from this repo |
| 18 | `check_quadrant_evidence_reproduces.py` | `git ls-files --full-name` and ASSERTS that the toplevel IS the directory being checked (:279-296) | READ: a worktree's toplevel is the worktree itself, so the assertion holds | safe, documented in-script |
| 19 | `check-owui-drift.ps1` | `owui\manifest.csv` from `$PSScriptRoot`'s grandparent (:103) + the LIVE webui.db | READ: the manifest is tracked and present in a worktree; it REFUSES rather than passing when nothing could be compared (:95) | safe |
| 20 | `check-watchdog-repair-targets.ps1` | fixed paths: `$ScriptDir\stack-watchdog.ps1` and `$RepoRoot\scripts\lib\stack-services.json` (:41-42); AST parse | READ + presence MEASURED (both present in this worktree) | safe |
| 21 | `check-bridge-freshness.ps1` | resolves the DEPLOYED tree from the running process or the Scheduled Task and explicitly refuses `$PSScriptRoot` (:49-58, :99) | READ: worktree-aware by design, for exactly this reason | safe, documented in-script |
| 22 | `dev-helper.ps1` | `Get-ChildItem $PROJECT_DIR -Filter '*.sh' -Recurse` filtered by `FullName -notmatch "\.git"` (:24, :45) | MEASURED: 33 worktree / 33 clone / 168 main checkout | **documented, not fixed** - section 3 |

### 4.3 Scripts that select no file set from the tree (25)

These read FIXED, `$PSScriptRoot`-relative inputs and/or talk to live containers, databases
and HTTP endpoints. There is no pattern-selected file set for a worktree to change, and
every fixed input they name was MEASURED present in this worktree (`OB1/docker/.env`,
`agent-org/docker/.env`, `scripts/lib/stack-services.json`, `owui/manifest.csv`,
`OB1/docker/docker-compose.yml`, `scripts/agent-harness/harness.config.json`). The one
absentee is `logs/`, which is gitignored and created on demand.

* Live-state probes (read a running stack, not the tree): `check-agent-org-health.ps1`,
  `check-openbrain-health.ps1`, `check-backup-coverage.ps1`,
  `census-db-connection-roles.ps1`, `stack-watchdog.ps1`, `queue-eta-notify.ps1`,
  `wiki-latency-probe.ps1`, `recall-sibling-class.ps1`, `seed-defect-classes.ps1`,
  `smoke-agent-memory-live.ps1`, `live_recall_probe.py`, `recall-falsifiability-drill.py`.
* Drills and red-proofs that build throwaway containers/databases from fixed inputs:
  `drill-app-role-not-superuser.ps1`, `drill-mcp-door-not-superuser.ps1`,
  `drill-personal-plane-exclusion.ps1`, `drill-rls-boot-assertion.ps1`,
  `drill-u6-dark-gate.ps1`, `prove-agent-memory-rls.ps1`,
  `redprove-census-cannot-measure.ps1`, `redprove-fixture-cleanup.ps1`,
  `smoke-agent-memory.ps1`, `assert-rls-force.sh`, `test-quartz4-offline.ps1`.
  (`drill-u6-dark-gate.ps1:541`'s `Get-ChildItem -Recurse` is over its own `%TEMP%` scratch
  root, not the repo.)
* Dot-sourced helpers, no selection of their own: `lib/harness-owner.ps1`,
  `lib/ob-initdb.ps1`.
* Operator installers, not gates: `install-service.ps1`, `simple-monitor.ps1`,
  `setup-prereqs.ps1`. NOTE `install-service.ps1` registers the Scheduled Task against
  `$ScriptDir\stack-watchdog.ps1`, so running it FROM a worktree would install a disposable
  tree as the deployment. That is an operator-action hazard, not a vacuous check; it is the
  same error `check-bridge-freshness.ps1` documents from the other side.

## 5. The two rules this leaves behind

1. **A prune is a statement about a tree, so match it against a path relative to that
   tree's root.** Anything matched against an absolute path is a statement about the
   MACHINE, and a checkout can be moved under any directory name at all - `.claude`
   today, `data` or `backups` tomorrow.
2. **Print what was examined, and refuse zero.** The routing guard was green for months
   while reading nothing; the number that would have exposed it existed inside the script
   and was never shown. Every check whose scope is a discovered file set should print the
   count on every verdict.

## 6. Also noticed, not acted on

* `validate-lineendings.ps1` still exits 0 with "No tracked shell scripts to check" outside
  a checkout (a `git archive` export). Pre-existing, reported 2026-09-19, unchanged here.
  Test plans that say "run the checks on the export" record a vacuous green for it.
* Scanning checks run from the MAIN checkout descend into `.claude/worktrees/<id>/` unless
  they prune it. `check-corpus-exposure-producers.ps1` prunes `worktrees` (:416-421) and
  the routing check now prunes `.claude`; `dev-helper.ps1` prunes neither (section 3). The
  hazard is another session's in-progress edits being reported as this tree's - or, for a
  mutating helper, rewritten.
