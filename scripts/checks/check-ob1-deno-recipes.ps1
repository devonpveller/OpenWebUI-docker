#requires -Version 5
<#
.SYNOPSIS
  Type-check the Deno recipe OB1/recipes/daily-digest whenever an OB1 gitlink
  bump is staged. Refuse the commit if `deno check` fails - or if the gate
  cannot honestly run.

.DESCRIPTION
  Why this exists (2026-09-04): the hook chain had a LANGUAGE-shaped blind
  spot. Check 5b (check-ob1-recipe-tests.ps1) runs `node --test` over
  `*.test.mjs` under OB1/recipes. OB1/recipes/daily-digest is Deno TypeScript
  - its sources are `.ts` and its one test file is `.test.ts` - so NOTHING in
  the hook chain compiled, type-checked or ran a single line of it. The parent
  repo deploys that recipe by committing a gitlink bump (the live containers
  bind-mount OB1/recipes from this checkout), so every line of it was
  reachable at commit time and none of it was catchable there.

  Five defects shipped through that hole in the week of 2026-08-29..09-03
  (podcast delivery). The sharpest: link-enrich.ts called `retryUntil` without
  importing it - a ReferenceError that would have aborted EVERY production run
  of the podcast pipeline. `deno check` finds it in about two seconds and
  names the file and line.

  What it does, in order:
    1. If no OB1 gitlink is staged, exit 0 without invoking deno - ordinary
       commits stay cheap. This is the only skip path.
    2. If the staged gitlink SHA differs from the OB1 working tree's HEAD,
       FAIL: `deno check` reads the DISK, so a green run would prove the wrong
       tree. (Same clause, same reason, as 5b.)
    3. `deno check` every non-test *.ts under OB1/recipes/daily-digest,
       enumerated FROM DISK (see DELIBERATE LIMITS below - that is a choice,
       not an oversight). Any type error - or a missing deno - fails the
       commit. On the FAILURE path only, the gate additionally names any
       enumerated file that is UNTRACKED in OB1, because the standard "re-stage
       the gitlink" advice is wrong for those and loops forever.

  DELIBERATE LIMITS, stated here so a green is not read as more than it is:

  * TYPE CHECK ONLY, NOT THE TEST SUITE. `deno test` for this recipe is slow
    and needs --allow-net, so it is deliberately not run here. `deno check` is
    the cheap high-value half: it catches missing imports, renamed exports and
    wrong signatures - the class most of those five defects fell into. A pass
    here means "it compiles", NOT "it works".

  * *.test.ts IS EXCLUDED. src/podcast/script-renderer.test.ts is the only
    file in the recipe with a REMOTE import (`jsr:@std/assert@1`). Including
    it would make the gate depend on a warm deno cache and a working network,
    so an offline commit or a cold cache would turn it red on innocent work -
    the false-positive-disables-the-guard failure .githooks/pre-commit warns
    about twice. Every non-test file resolves entirely from local relative
    imports, so the check below is fully offline.

  * daily-digest ONLY. OB1/recipes/vercel-neon-telegram also carries .test.ts
    files; it is not deployed by this stack's bind mounts and is deliberately
    outside this gate's scope. If a SECOND Deno recipe is ever deployed here,
    add it to $RecipeRels below - this gate will not find it on its own.

  * ENUMERATION IS FROM DISK, NOT FROM THE STAGED TREE - deliberately, and
    NOT for symmetry with 5b (recorded 2026-09-04, after 5b had to fix a
    quotepath silent-drop that this gate would inherit wholesale). 5b reads
    git objects because it COMPARES TWO TREES and disk cannot be trusted to be
    either of them; this gate compares nothing, it compiles one tree, and
    step 2 above has already refused the commit unless disk IS the staged
    tree. Switching to `git ls-tree` here would change almost nothing and cost
    something real:
      - `deno check` follows the IMPORT GRAPH out from the roots it is handed,
        so enumeration only picks ROOTS. Any file that a checked file imports
        is checked whether or not git can see it. The only difference ls-tree
        would make is whether an ORPHAN untracked .ts - one that nothing
        imports - is also a root.
      - Reading names out of git re-opens the exact hole 5b closed on
        2026-09-04: core.quotepath is on by default, a non-ASCII path comes
        back C-quoted, the `*.ts` filter stops matching it, and the gate
        type-checks less than it reports while still printing a count.
    The two failure modes are NOT symmetric, and that is the whole argument.
    Disk enumeration can only over-refuse: a stray untracked .ts with a type
    error refuses a commit that would in fact have deployed fine. That is
    LOUD, it lands on one operator, and the failure path below names the
    untracked files so it cannot masquerade as a real defect. ls-tree
    enumeration fails the other way - it under-checks, silently, and ships.
    A loud over-refusal beats a silent under-check.

  * NO "subtree unchanged" SKIP. Comparing the daily-digest subtree between
    the old pin and the staged one and skipping when equal would be cheaper,
    but every skip path is another way for a gate not to run, and two seconds
    on a rare gitlink-bump commit does not buy one.

  WHEN deno IS ABSENT this gate REFUSES the commit; it does not warn and pass.
  The reason this file exists at all is that nothing was checking this code,
  and a gate that switches itself off when its tool is missing reproduces
  exactly that state - quietly. Its sibling 5b makes the same call for node.
  Host has deno 2.8.1: install it, or commit from a shell that has it on PATH.

  Exit code 0 = clean or not applicable, 1 = refuse.

.EXAMPLE
  powershell -File scripts/checks/check-ob1-deno-recipes.ps1
#>
[CmdletBinding()]
param(
    [string]$Root
)

$ErrorActionPreference = 'Stop'

# Deno recipe roots, relative to the OB1 submodule root. See the scope note above.
$RecipeRels = @('recipes/daily-digest')

function Fail([string]$msg) {
    Write-Host "[check-ob1-deno-recipes] FAIL: $msg" -ForegroundColor Red
    exit 1
}

# Query the OB1 SUBMODULE repo with a clean git environment. Under a hook, git
# exports GIT_DIR (absolute, the PARENT repo - in a linked worktree its admin
# dir) and GIT_INDEX_FILE, and those OVERRIDE `-C`: `git -C OB1 rev-parse HEAD`
# then answers with the PARENT's HEAD. That cost the sibling gate a false
# refusal on its first live run (2026-09-03). Interactive runs never see it -
# only hook runs do, which is why this gate's verdicts have to be proven with a
# real `git commit`, not by invoking this file by hand.
function Git-InOB1([string[]]$GitArgs) {
    $saved = @{}
    foreach ($k in 'GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE') {
        $saved[$k] = [Environment]::GetEnvironmentVariable($k)
        [Environment]::SetEnvironmentVariable($k, $null)
    }
    try {
        # PS 5.1 under $ErrorActionPreference='Stop' THROWS a NativeCommandError the
        # moment a redirected native command writes to stderr, which would fire before
        # the friendly Fail below. Relax it for the native call only; function-scoped.
        $ErrorActionPreference = 'Continue'
        & git -C (Join-Path $Root 'OB1') @GitArgs 2>$null
    }
    finally {
        foreach ($k in $saved.Keys) { [Environment]::SetEnvironmentVariable($k, $saved[$k]) }
    }
}

if (-not $Root) {
    $Root = (& git rev-parse --show-toplevel 2>$null)
    if (-not $Root) { Fail "not inside a git repository and no -Root given" }
}
$Root = (Resolve-Path $Root).Path

# --- 1. Is an OB1 gitlink bump staged? -------------------------------------
$staged = @(& git -C $Root diff --cached --name-only)
if ($staged -notcontains 'OB1') {
    Write-Host "[check-ob1-deno-recipes] no OB1 gitlink staged - skipped."
    exit 0
}

# --- 2. The tree on disk must BE the tree being pinned ---------------------
$lsLine = (& git -C $Root ls-files -s OB1) | Select-Object -First 1
if (-not $lsLine -or $lsLine -notmatch '^160000\s+([0-9a-f]{40})\s') {
    Fail "could not read the staged OB1 gitlink from the index (got '$lsLine')"
}
$stagedSha = $Matches[1]
$shortSha = $stagedSha.Substring(0, 7)
# An UNINITIALIZED submodule is an empty dir with no .git - and `git -C` on it
# does not fail, it walks UP and answers from the PARENT repo. So the uninit
# case must be caught explicitly, before any git query.
if (-not (Test-Path (Join-Path $Root 'OB1\.git'))) {
    Fail ("OB1/ has no .git - the submodule is not initialized, so there is no disk tree to " +
          "type-check against the staged gitlink. Run: git submodule update --init OB1")
}
$diskSha = (Git-InOB1 @('rev-parse', 'HEAD'))
if (-not $diskSha) { Fail "OB1/ has no readable git HEAD - is the submodule initialized?" }
if ($stagedSha -ne $diskSha) {
    Fail ("staged OB1 gitlink is $shortSha but the OB1 working tree is at " +
          "$($diskSha.Substring(0,7)). deno check reads the DISK, so it would type-check code " +
          "this commit does not deploy. Align them first (git -C OB1 checkout $shortSha, or " +
          "re-stage: git add OB1).")
}

# --- 3. deno must be present ----------------------------------------------
$denoCmd = Get-Command deno -ErrorAction SilentlyContinue
if (-not $denoCmd) {
    Fail ("deno is not on PATH, so OB1/recipes/daily-digest cannot be type-checked. This gate " +
          "REFUSES rather than skipping: 'nothing checks this Deno code' is the exact state it " +
          "was written to end, and a silent skip restores it. Install deno (host has 2.8.1) or " +
          "commit from a shell that has it.")
}

# --- 4. Type-check each Deno recipe ---------------------------------------
foreach ($rel in $RecipeRels) {
    $recipeDir = Join-Path $Root ('OB1\' + ($rel -replace '/', '\'))
    if (-not (Test-Path $recipeDir)) {
        Write-Host ("[check-ob1-deno-recipes] WARNING: OB1/$rel is not in the staged tree - " +
            "nothing type-checked for it. If the recipe moved, this gate is now checking " +
            'NOTHING: update $RecipeRels in this script.') -ForegroundColor Yellow
        continue
    }

    # *.test.ts excluded (remote jsr import - see the header). node_modules is
    # gitignored vendor code: one `npm install` inside a recipe would otherwise
    # feed foreign sources to this gate and turn it red on innocent commits.
    $tsFiles = @(Get-ChildItem -Path $recipeDir -Recurse -Filter '*.ts' |
        Where-Object { $_.FullName -notmatch '\\node_modules\\' -and $_.Name -notlike '*.test.ts' } |
        ForEach-Object { $_.FullName })
    if ($tsFiles.Count -eq 0) {
        Fail "found ZERO non-test *.ts under OB1/$rel - an empty check passing is vacuous, refusing."
    }

    # --no-lock: the recipe carries no deno.json/deno.lock today, and a lockfile
    # materialising inside OB1 would leave the submodule DIRTY - which gate 5b
    # then refuses on the next commit. Pin the behaviour instead of relying on it.
    # NO_COLOR: deno emits ANSI escapes even through a pipe, and they make the
    # refusal below unreadable in the hook's output.
    $prevNoColor = $env:NO_COLOR
    $prevEap = $ErrorActionPreference
    $env:NO_COLOR = '1'
    $ErrorActionPreference = 'Continue'
    try {
        $out = & $denoCmd.Source check --quiet --no-lock @tsFiles 2>&1
        $code = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $prevEap
        $env:NO_COLOR = $prevNoColor
    }

    if ($code -ne 0) {
        # deno writes its diagnostics to STDERR, so `2>&1` hands PowerShell
        # ErrorRecord objects, not strings - and "$_" on one renders the blank
        # ones as the literal text "System.Management.Automation.RemoteException",
        # which is what the first live run of this gate printed between every
        # real TS error (dev verification, 2026-09-04). Unwrap the message.
        # (`Out-String -Stream` is worse: it decorates the first stderr line
        # with the whole PowerShell NativeCommandError banner.)
        $lines = foreach ($o in $out) {
            if ($o -is [System.Management.Automation.ErrorRecord]) { $o.Exception.Message }
            else { [string]$o }
        }
        $lines | Select-Object -First 40 | ForEach-Object { Write-Host "  $_" -ForegroundColor Red }

        # UNTRACKED CULPRITS (added 2026-09-04). Enumeration is from DISK by
        # design (see DELIBERATE LIMITS in the header), so a .ts that exists
        # only in the working tree IS type-checked and CAN be the thing that
        # just failed. For that file every word of the advice below is wrong:
        # `git add OB1` re-stages the identical gitlink, the gate re-enumerates
        # the identical untracked file, and the operator goes round again with
        # no state changing. Name them, and say plainly that the loop is a loop.
        # Diagnosis only - it never changes the verdict, and never runs on the
        # green path, which is byte-identical to what it was before.
        $tracked = @(Git-InOB1 @('-c', 'core.quotepath=false', 'ls-files', '--', $rel))
        if ($LASTEXITCODE -ne 0) {
            Write-Host ("  NOTE: could not list OB1's tracked files, so this gate cannot say " +
                "whether an UNTRACKED file is the culprit. If re-staging the gitlink does not " +
                "change this failure, look for .ts files that exist only on disk.") -ForegroundColor Yellow
        }
        else {
            $trackedSet = @{}
            foreach ($tr in $tracked) { $trackedSet[$tr] = $true }
            $ob1Root = (Join-Path $Root 'OB1')
            $untracked = @()
            foreach ($tsf in $tsFiles) {
                if (-not $tsf.StartsWith($ob1Root, [StringComparison]::OrdinalIgnoreCase)) { continue }
                $relPath = $tsf.Substring($ob1Root.Length).TrimStart('\', '/') -replace '\\', '/'
                if (-not $trackedSet.ContainsKey($relPath)) { $untracked += $relPath }
            }
            if ($untracked.Count -gt 0) {
                Write-Host ("  NOTE: $($untracked.Count) of the $($tsFiles.Count) type-checked " +
                    "file(s) are UNTRACKED in OB1 - present on disk, absent from commit " +
                    "$shortSha :") -ForegroundColor Yellow
                $untracked | Select-Object -First 10 | ForEach-Object {
                    Write-Host "    $_" -ForegroundColor Yellow
                }
                Write-Host ("  RE-STAGING THE GITLINK WILL NOT HELP if one of those is the " +
                    "culprit: this gate enumerates from DISK, so 'git add OB1' pins the same " +
                    "commit and the same untracked file gets checked again. Commit them inside " +
                    "OB1 and bump the gitlink to that commit, or delete them.") -ForegroundColor Yellow
            }
        }

        Fail ("deno check FAILED for OB1/$rel in staged OB1 $shortSha. This is a COMPILE error, " +
              "not a test failure - the code above would not have run at all. Fix it in OB1, " +
              "push, re-stage the gitlink, and commit again.")
    }

    Write-Host ("[check-ob1-deno-recipes] OK - deno check clean over $($tsFiles.Count) non-test " +
        "*.ts in OB1/$rel (staged OB1 $shortSha). Type check only - the test suite is NOT run.")
}

exit 0
