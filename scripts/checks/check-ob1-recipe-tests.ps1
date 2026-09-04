#requires -Version 5
<#
.SYNOPSIS
  Run the OB1 recipe unit tests whenever an OB1 gitlink bump is staged.
  Refuse the commit if any test fails - or if the gate cannot honestly run.

.DESCRIPTION
  Why this exists (2026-09-03): OB1 commit dfc6228 (2026-08-28) shipped a
  one-line binding bug in recipes/_shared/wiki-pages.mjs that starved the
  wiki_pages mirror for 5 days under clean "compile ok" logs - while a unit
  test that caught it (5/10 failing) sat in the tree, run by nothing. Fixed
  in OB1 09f70f4 / parent merge 443c5d8. This gate closes the "run by
  nothing" half: the parent repo deploys OB1 code exclusively by committing
  a gitlink bump (the live containers bind-mount OB1/recipes from this
  checkout), so the staged-gitlink moment IS the deploy chokepoint.

  What it does, in order:
    1. If no OB1 gitlink is staged, exit 0 without invoking node - ordinary
       commits stay cheap.
    2. If the staged gitlink SHA differs from the OB1 working tree's HEAD,
       FAIL: the files on disk are not the tree being pinned, so a green run
       would prove the wrong code. (Stage the intended OB1 state first.)
    3. Run `node --test` over every *.test.mjs under OB1/recipes. Any
       failure, a missing node, or an empty test set fails the commit -
       a gate that cannot run is a gate that is off, and silence is how
       the original bug survived.

  The tests are Node-builtins-only by OB1 convention (no npm install), so
  host node is sufficient. Exit code 0 = clean or not applicable, 1 = refuse.

.EXAMPLE
  powershell -File scripts/checks/check-ob1-recipe-tests.ps1
#>
[CmdletBinding()]
param(
    [string]$Root
)

$ErrorActionPreference = 'Stop'

function Fail([string]$msg) {
    Write-Host "[check-ob1-recipe-tests] FAIL: $msg" -ForegroundColor Red
    exit 1
}

# Query the OB1 SUBMODULE repo with a clean git environment. Under a hook, git
# exports GIT_DIR (absolute, the PARENT repo - in a linked worktree its admin
# dir) and GIT_INDEX_FILE; those override `-C`, so `git -C OB1 rev-parse HEAD`
# silently answered with the PARENT's HEAD. Found by this gate's own first
# live run (2026-09-03): it refused a correct commit claiming the OB1 tree was
# at the parent's SHA. Interactive runs never see this - only hook runs do.
function Git-InOB1([string[]]$GitArgs) {
    $saved = @{}
    foreach ($k in 'GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE') {
        $saved[$k] = [Environment]::GetEnvironmentVariable($k)
        [Environment]::SetEnvironmentVariable($k, $null)
    }
    try { & git -C (Join-Path $Root 'OB1') @GitArgs 2>$null }
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
    Write-Host "[check-ob1-recipe-tests] no OB1 gitlink staged - skipped."
    exit 0
}

# --- 2. The tree on disk must BE the tree being pinned ---------------------
# `git ls-files -s OB1` reads the INDEX: "160000 <sha> 0\tOB1".
$lsLine = (& git -C $Root ls-files -s OB1) | Select-Object -First 1
if (-not $lsLine -or $lsLine -notmatch '^160000\s+([0-9a-f]{40})\s') {
    Fail "could not read the staged OB1 gitlink from the index (got '$lsLine')"
}
$stagedSha = $Matches[1]
$diskSha = (Git-InOB1 @('rev-parse', 'HEAD'))
if (-not $diskSha) { Fail "OB1/ has no readable git HEAD - is the submodule initialized?" }
if ($stagedSha -ne $diskSha) {
    Fail ("staged OB1 gitlink is $($stagedSha.Substring(0,7)) but the OB1 working tree is at " +
          "$($diskSha.Substring(0,7)). The tests below would run against the DISK tree and prove " +
          "nothing about the tree this commit deploys. Align them first (git -C OB1 checkout " +
          "$($stagedSha.Substring(0,7)), or re-stage: git add OB1).")
}
# Same clause, stricter reading: dirty TRACKED files inside OB1 mean the disk
# tree (what the live bind mounts serve, and what the tests below read) is not
# the commit being pinned - the "deployed from an uncommitted tree" trap.
# Untracked/ignored files (OB1/docker/.env etc.) are fine and not checked.
$dirty = @(Git-InOB1 @('status', '--porcelain', '--untracked-files=no'))
if ($dirty.Count -gt 0) {
    $dirty | Select-Object -First 5 | ForEach-Object { Write-Host "  $_" -ForegroundColor Yellow }
    Fail ("OB1 has $($dirty.Count) uncommitted tracked change(s) - the disk tree the tests (and " +
          "the live bind mounts) see is not the commit $($stagedSha.Substring(0,7)) this gitlink " +
          "pins. Commit or stash them in OB1 first.")
}

# --- 3. Run every recipe test ----------------------------------------------
$nodeCmd = Get-Command node -ErrorAction SilentlyContinue
if (-not $nodeCmd) {
    Fail ("node is not on PATH, so the recipe tests cannot run. A gate that silently skips is " +
          "how the 08-28 wiki-pages bug shipped - install node (or commit from a shell that has " +
          "it) rather than bypassing this.")
}

$testFiles = @(Get-ChildItem -Path (Join-Path $Root 'OB1\recipes') -Recurse -Filter '*.test.mjs' |
    ForEach-Object { $_.FullName })
if ($testFiles.Count -eq 0) {
    Fail "found ZERO *.test.mjs under OB1/recipes - an empty test set passing is vacuous, refusing."
}

$out = & $nodeCmd.Source --test @testFiles 2>&1
$code = $LASTEXITCODE
$summary = ($out | Where-Object { $_ -match '^# (tests|pass|fail) ' }) -join ' | '

if ($code -ne 0) {
    # Show the failing tests, not the whole TAP stream.
    $out | Where-Object { $_ -match '^not ok|^# Subtest.*fail|AssertionError|ReferenceError|Error \[' } |
        Select-Object -First 12 | ForEach-Object { Write-Host "  $_" -ForegroundColor Red }
    Fail ("recipe tests FAILED ($summary) for staged OB1 $($stagedSha.Substring(0,7)). " +
          "Fix the tree (or the test) in OB1, push, re-stage the gitlink, and commit again.")
}

Write-Host "[check-ob1-recipe-tests] OK - $($testFiles.Count) test file(s), $summary (staged OB1 $($stagedSha.Substring(0,7)))."
exit 0
