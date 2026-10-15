#requires -Version 5
<#
.SYNOPSIS
  On an OB1 gitlink bump, refuse the commit if an UPSTREAM merge brought into the new pin
  touches what the fork owns for intake (OB1/integrations/intake/FORK-OWNED.md).

.DESCRIPTION
  Operator decision D23 (2026-10-15, security-hardening intake I1): intake lives in OB1 on the
  condition that upstream OB1 can never overwrite or collide with it. The fork lists what it
  owns (paths, the `intake` schema and role, the screen_verdict / intake_item_id columns) in
  FORK-OWNED.md; OB1/integrations/intake/tools/check_fork_owned.py reads that list.

  This gate sits beside 5b/5c/5d, the other checks that run OB1 fork code on a gitlink bump.
  In order:
    1. No OB1 gitlink staged -> exit 0 (the only skip path).
    2. The OB1 working tree must BE the staged pin (the checker and its list are read from
       disk), same clause as 5b/5c.
    3. If the staged pin has no FORK-OWNED.md: refuse when the OLD pin had one (something
       removed the guard), otherwise exit 0 (a pin from before intake I1).
    4. Python 3 must be present - REFUSE, not skip, when it is not (5b's call for node).
    5. Run the checker's unit tests, then
       check_fork_owned.py --range <old pin>..<staged pin>: for every merge commit in the
       range, what each upstream parent brought in since its merge-base. Exit 1 or 2 refuses.

  DELIBERATE LIMITS: an upstream change imported WITHOUT a merge commit (rebase, cherry-pick)
  looks like fork work to --range; run `check_fork_owned.py --upstream upstream/main` before
  such an import. A reviewed upstream merge is accepted by committing its sha as
  `reviewed-upstream:` in FORK-OWNED.md - there is no switch that skips this gate.

  Exit code 0 = clean or not applicable, 1 = refuse.
#>
[CmdletBinding()]
param([string]$Root)

$ErrorActionPreference = 'Stop'
$Tag = '[check-ob1-fork-owned]'
$ManifestRel = 'integrations/intake/FORK-OWNED.md'

function Fail([string]$msg) {
    Write-Host "$Tag FAIL: $msg" -ForegroundColor Red
    exit 1
}

# Under a hook git exports GIT_DIR / GIT_INDEX_FILE for the PARENT repo, and they override -C.
# Clear them for queries aimed at the submodule (same helper as 5c).
function Git-InOB1([string[]]$GitArgs) {
    $saved = @{}
    foreach ($k in 'GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE') {
        $saved[$k] = [Environment]::GetEnvironmentVariable($k)
        [Environment]::SetEnvironmentVariable($k, $null)
    }
    try {
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
    Write-Host "$Tag no OB1 gitlink staged - skipped."
    exit 0
}

# --- 2. Disk must be the staged pin ----------------------------------------
$lsLine = (& git -C $Root ls-files -s OB1) | Select-Object -First 1
if (-not $lsLine -or $lsLine -notmatch '^160000\s+([0-9a-f]{40})\s') {
    Fail "could not read the staged OB1 gitlink from the index (got '$lsLine')"
}
$newSha = $Matches[1]
$oldLine = (& git -C $Root ls-tree HEAD OB1 2>$null) | Select-Object -First 1
$oldSha = $null
if ($oldLine -match '^160000\s+commit\s+([0-9a-f]{40})\s') { $oldSha = $Matches[1] }
if (-not (Test-Path (Join-Path $Root 'OB1\.git'))) {
    Fail "OB1/ has no .git - the submodule is not initialized. Run: git submodule update --init OB1"
}
$diskSha = (Git-InOB1 @('rev-parse', 'HEAD'))
if ($diskSha -ne $newSha) {
    Fail ("staged OB1 gitlink is $($newSha.Substring(0,7)) but the OB1 working tree is at " +
          "$diskSha. The fork-owned list and checker are read from DISK; align them first.")
}

# --- 3. Does the new pin carry the guard? ----------------------------------
$hasNew = [bool](Git-InOB1 @('ls-tree', '--name-only', $newSha, '--', $ManifestRel))
$hasOld = $false
if ($oldSha) { $hasOld = [bool](Git-InOB1 @('ls-tree', '--name-only', $oldSha, '--', $ManifestRel)) }
if (-not $hasNew) {
    if ($hasOld) {
        Fail ("the staged OB1 pin $($newSha.Substring(0,7)) no longer has $ManifestRel, which the " +
              "current pin has. Something removed the fork-ownership guard; that is exactly what " +
              "it exists to catch.")
    }
    Write-Host "$Tag staged OB1 pin predates intake I1 ($ManifestRel absent in both pins) - skipped."
    exit 0
}

# --- 4. Python -------------------------------------------------------------
$py = $null
foreach ($n in 'python', 'python3', 'py') {
    $c = Get-Command $n -ErrorAction SilentlyContinue
    if ($c) { $py = $c.Source; break }
}
if (-not $py) {
    Fail ("Python 3 is not on PATH, so the fork-ownership guard cannot run. This gate REFUSES " +
          "rather than skipping (a skipped guard is no guard). Install Python 3.8+.")
}

# --- 5. Unit tests, then the range check -----------------------------------
$intakeDir = Join-Path $Root 'OB1\integrations\intake'
$prevEap = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
$savedEnv = @{}
foreach ($k in 'GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE') {
    $savedEnv[$k] = [Environment]::GetEnvironmentVariable($k)
    [Environment]::SetEnvironmentVariable($k, $null)
}
try {
    Push-Location $intakeDir
    $tOut = & $py -m unittest discover -s tests -t . -p 'test_check_fork_owned.py' 2>&1
    $tCode = $LASTEXITCODE
    $base = if ($oldSha) { $oldSha } else { (Git-InOB1 @('rev-list', '--max-parents=0', $newSha) | Select-Object -First 1) }
    $cOut = & $py tools/check_fork_owned.py --range "$base..$newSha" 2>&1
    $cCode = $LASTEXITCODE
}
finally {
    Pop-Location
    foreach ($k in $savedEnv.Keys) { [Environment]::SetEnvironmentVariable($k, $savedEnv[$k]) }
    $ErrorActionPreference = $prevEap
}
function Show($out) {
    foreach ($o in $out) {
        if ($o -is [System.Management.Automation.ErrorRecord]) { Write-Host "  $($o.Exception.Message)" }
        else { Write-Host "  $o" }
    }
}
if ($tCode -ne 0) {
    Show $tOut
    Fail "the fork-ownership checker's own unit tests fail in staged OB1 $($newSha.Substring(0,7))."
}
if ($cCode -ne 0) {
    Show $cOut
    Fail ("an upstream merge in OB1 $base..$($newSha.Substring(0,7)) touches what the fork owns " +
          "(exit $cCode). Review it against $ManifestRel; once reviewed, add its upstream sha as " +
          "'reviewed-upstream:' there, commit in OB1, and re-stage the gitlink.")
}
Write-Host "$Tag OK - no upstream merge in the OB1 bump touches the fork-owned intake surface."
exit 0
