# verify-judge-flag-guard.ps1 - executable proof that check-judge-flag.ps1
# mechanically STOPS a commit that turns observer.judge_enabled on, and leaves
# an audit record when it does.
#
#   .\scripts\checks\verify-judge-flag-guard.ps1      # ~30s, cleans up after itself
#
# WHY A SEPARATE DRILL. verify-judge-dryrun.ps1 proves the INSTRUMENT can fail.
# This proves the GUARD can stop something - a different claim, and the one a
# verifier falsified: with only the instrument in place, setting judge_enabled
# true and re-running produced ALL CASES PASS, exit 0.
#
# EVERYTHING IS IN A SCRATCH REPOSITORY under the temp directory. This drill
# never stages anything in this repo, never writes this machine's audit log,
# and never sets judge_enabled anywhere outside its own throwaway git repo. The
# hook it drives is not a hand-written stand-in: the real .githooks/pre-commit's
# step-6 block is EXTRACTED BY MARKER, and the real check-judge-flag.ps1 and
# lib/judge_dryrun.py are copied in verbatim. A drill against a paraphrase of
# the mechanism proves nothing about the mechanism.
#
# Exit 0 = every case as expected. Exit 1 = at least one case wrong.

[CmdletBinding()]
param([switch]$KeepTemp)

$ErrorActionPreference = 'Continue'
$RepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$Guard = Join-Path $PSScriptRoot 'check-judge-flag.ps1'
$Lib = Join-Path $PSScriptRoot 'lib\judge_dryrun.py'
$RealHook = Join-Path $RepoRoot '.githooks\pre-commit'
$Tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("judge-flag-drill-" + [guid]::NewGuid().ToString('N').Substring(0, 8))

# EXPECTED_CASES exists so a case that silently stops running is a FAILURE and
# not a quieter green. The drill it is modelled on could lose a case and still
# print "ALL 12 CASES PASS" because nothing read the number.
$EXPECTED_CASES = 17

$results = @()
function Case($label, $expected, $actual, $detail = '') {
    $ok = ($expected -eq $actual)
    $script:results += [pscustomobject]@{ case = $label; expected = $expected; actual = $actual; pass = $ok; detail = $detail }
    Write-Host ("  [{0}] {1}  expected {2}, got {3}  {4}" -f $(if ($ok) { 'PASS' } else { 'FAIL' }), $label, $expected, $actual, $detail) -ForegroundColor $(if ($ok) { 'Green' } else { 'Red' })
}

function Invoke-ScratchGit {
    param([string[]]$GitArgs)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { $out = @(& git -C $script:Repo @GitArgs 2>&1); return $out } finally { $ErrorActionPreference = $prev }
}

function Get-AuditLines {
    # ALWAYS wrap the call site in @(). PowerShell UNROLLS a one-element array
    # on return from a function, so with a single audit line this returned a
    # String -- and `$audit[-1]` then indexed its last CHARACTER, which made an
    # assertion fail for a reason that had nothing to do with the guard. Caught
    # by running the drill, not by reading it.
    $log = Join-Path $script:Repo '.git\judge-flag-guard.log'
    if (Test-Path $log) { return @(Get-Content $log | Where-Object { $_ }) }
    return @()
}

function Set-Flag($value) {
    # The scratch config is the SHAPE the guard greps for, written at drill time
    # into a throwaway repo. Nothing with judge_enabled: true is committed to
    # this repository by this drill or shipped in its fixtures.
    $cfgText = @"
schema_version: 1
observer:
  enabled: true
  judge_enabled: $value
"@
    Set-Content -Path (Join-Path $script:Repo 'lc.config.yaml') -Value $cfgText -Encoding ascii
}

New-Item -ItemType Directory -Path $Tmp -Force | Out-Null
Write-Host "verify-judge-flag-guard - temp: $Tmp" -ForegroundColor Cyan
Write-Host ""

try {
    $script:Repo = Join-Path $Tmp 'repo'
    New-Item -ItemType Directory -Path $script:Repo -Force | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $script:Repo 'scripts\checks\lib') -Force | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $script:Repo 'little-coder\config') -Force | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $script:Repo '.githooks') -Force | Out-Null

    # The REAL guard and the REAL validator, verbatim.
    Copy-Item $Guard (Join-Path $script:Repo 'scripts\checks\check-judge-flag.ps1')
    Copy-Item $Lib (Join-Path $script:Repo 'scripts\checks\lib\judge_dryrun.py')
    $ScratchGuard = Join-Path $script:Repo 'scripts\checks\check-judge-flag.ps1'

    Invoke-ScratchGit @('init', '-q') | Out-Null
    Invoke-ScratchGit @('config', 'user.email', 'drill@example.invalid') | Out-Null
    Invoke-ScratchGit @('config', 'user.name', 'judge flag drill') | Out-Null
    Set-Content -Path (Join-Path $script:Repo 'seed.txt') -Value 'seed' -Encoding ascii
    Invoke-ScratchGit @('add', '-A') | Out-Null
    Invoke-ScratchGit @('-c', 'core.hooksPath=/nonexistent', 'commit', '-q', '-m', 'seed') | Out-Null

    # -----------------------------------------------------------------
    # 1. NEGATIVE CONTROL: an ordinary commit with no YAML at all.
    #    Without this the drill could not distinguish "the guard works"
    #    from "the guard denies everything".
    # -----------------------------------------------------------------
    Set-Content -Path (Join-Path $script:Repo 'a.txt') -Value 'hello' -Encoding ascii
    Invoke-ScratchGit @('add', 'a.txt') | Out-Null
    & powershell -NoProfile -ExecutionPolicy Bypass -File $ScratchGuard -RepoRoot $script:Repo | Out-Null
    Case 'no yaml staged -> allowed' 0 $LASTEXITCODE
    Case 'no yaml staged -> NO audit line' 0 @(Get-AuditLines).Count

    # -----------------------------------------------------------------
    # 2. NEGATIVE CONTROL: the flag staged OFF. Must not be denied - a
    #    guard that fires on the correct value gets switched off.
    # -----------------------------------------------------------------
    Set-Flag 'false'
    Invoke-ScratchGit @('add', 'lc.config.yaml') | Out-Null
    & powershell -NoProfile -ExecutionPolicy Bypass -File $ScratchGuard -RepoRoot $script:Repo | Out-Null
    Case 'judge_enabled false staged -> allowed' 0 $LASTEXITCODE
    Case 'judge_enabled false -> still NO audit line' 0 @(Get-AuditLines).Count

    # -----------------------------------------------------------------
    # 3. RED: the flag staged ON with no rating record.
    # -----------------------------------------------------------------
    Set-Flag 'true'
    Invoke-ScratchGit @('add', 'lc.config.yaml') | Out-Null
    & powershell -NoProfile -ExecutionPolicy Bypass -File $ScratchGuard -RepoRoot $script:Repo | Out-Null
    Case 'judge_enabled true, no rating -> DENIED' 1 $LASTEXITCODE
    $audit = @(Get-AuditLines)
    Case 'the denial is in the audit record' $true (($audit.Count -eq 1) -and ($audit[-1] -match 'DENY.*no-rating-record-staged')) ($audit -join ' | ')

    # -----------------------------------------------------------------
    # 4. THE LAUNDERING ATTEMPT: stage ON, then put the file back to OFF
    #    in the working tree. git commits the INDEX, so the guard must
    #    read the index too, or `git add` + edit-back defeats it.
    # -----------------------------------------------------------------
    Set-Flag 'false'   # working tree now says false; index still says true
    & powershell -NoProfile -ExecutionPolicy Bypass -File $ScratchGuard -RepoRoot $script:Repo | Out-Null
    Case 'staged-on + working-tree-off -> still DENIED' 1 $LASTEXITCODE
    Set-Flag 'true'
    Invoke-ScratchGit @('add', 'lc.config.yaml') | Out-Null

    # -----------------------------------------------------------------
    # 5. RED: an INVALID rating record does not buy the flip.
    # -----------------------------------------------------------------
    $ratingRel = 'little-coder/config/judge-enablement-rating.yaml'
    $ratingAbs = Join-Path $script:Repo 'little-coder\config\judge-enablement-rating.yaml'
    Set-Content -Path $ratingAbs -Value "rated_by: someone`nverdict: approve`n" -Encoding ascii
    Invoke-ScratchGit @('add', $ratingRel) | Out-Null
    & powershell -NoProfile -ExecutionPolicy Bypass -File $ScratchGuard -RepoRoot $script:Repo | Out-Null
    Case 'incomplete rating record -> DENIED' 1 $LASTEXITCODE
    $audit = @(Get-AuditLines)
    Case 'the invalid record is named in the audit record' $true ($audit[-1] -match 'rating-record-invalid.*missing required key') ($audit[-1])

    # -----------------------------------------------------------------
    # 6. RED: a record that is complete but does not APPROVE.
    # -----------------------------------------------------------------
    Set-Content -Path $ratingAbs -Value "rated_by: someone`nrated_at: 2026-08-30`nrated_report: dryrun.json`nverdict: reject`n" -Encoding ascii
    Invoke-ScratchGit @('add', $ratingRel) | Out-Null
    & powershell -NoProfile -ExecutionPolicy Bypass -File $ScratchGuard -RepoRoot $script:Repo | Out-Null
    Case 'rating record verdict=reject -> DENIED' 1 $LASTEXITCODE

    # -----------------------------------------------------------------
    # 7. GREEN: a complete, approving record allows the flip and says so
    #    in the audit record. Without this case the drill would prove
    #    only that the guard says no.
    # -----------------------------------------------------------------
    Set-Content -Path $ratingAbs -Value "rated_by: operator`nrated_at: 2026-08-30T12:00:00Z`nrated_report: dryrun/judge-dryrun-little-coder.json`nverdict: approve`n" -Encoding ascii
    Invoke-ScratchGit @('add', $ratingRel) | Out-Null
    & powershell -NoProfile -ExecutionPolicy Bypass -File $ScratchGuard -RepoRoot $script:Repo | Out-Null
    Case 'valid approving rating record -> ALLOWED' 0 $LASTEXITCODE
    $audit = @(Get-AuditLines)
    Case 'the allow is in the audit record too' $true ($audit[-1] -match 'ALLOW') ($audit[-1])

    # -----------------------------------------------------------------
    # 8. FAIL CLOSED: the validator is gone, so the rule cannot be
    #    evaluated. It must DENY, not wave the flip through.
    # -----------------------------------------------------------------
    $libInRepo = Join-Path $script:Repo 'scripts\checks\lib\judge_dryrun.py'
    Move-Item $libInRepo "$libInRepo.hidden"
    & powershell -NoProfile -ExecutionPolicy Bypass -File $ScratchGuard -RepoRoot $script:Repo | Out-Null
    Case 'validator missing -> DENIED (fail closed)' 1 $LASTEXITCODE
    $audit = @(Get-AuditLines)
    Case 'the cannot-tell is in the audit record' $true ($audit[-1] -match 'CANNOT-TELL') ($audit[-1])
    Move-Item "$libInRepo.hidden" $libInRepo

    # -----------------------------------------------------------------
    # 9. END TO END: does the exit code actually reach git?
    #    A guard that returns 1 into a hook that ignores it stops nothing.
    #    The hook body is the REAL step-6 block, extracted by marker.
    # -----------------------------------------------------------------
    $hookSrc = Get-Content -Raw $RealHook
    $start = $hookSrc.IndexOf('# --- 6. judge_enabled flag guard')
    $end = $hookSrc.IndexOf('# --- 7. ATTESTATION')
    if ($start -lt 0 -or $end -le $start) {
        Case 'step-6 block extractable from the real hook' $true $false 'marker not found - the hook was renumbered'
    } else {
        Case 'step-6 block extractable from the real hook' $true $true ''
        $block = $hookSrc.Substring($start, $end - $start)
        $hookBody = "#!/bin/sh`n" + $block
        $hookPath = Join-Path $script:Repo '.githooks\pre-commit'
        [System.IO.File]::WriteAllText($hookPath, ($hookBody -replace "`r`n", "`n"))
        Invoke-ScratchGit @('config', 'core.hooksPath', '.githooks') | Out-Null

        # The index still holds judge_enabled: true plus the approving record
        # from case 7; remove the record so the hook must deny.
        Invoke-ScratchGit @('rm', '--cached', '-q', $ratingRel) | Out-Null
        Remove-Item $ratingAbs -Force -ErrorAction SilentlyContinue
        $headBefore = (Invoke-ScratchGit @('rev-parse', 'HEAD') | Select-Object -First 1)
        Invoke-ScratchGit @('commit', '-q', '-m', 'flip the flag') | Out-Null
        $headAfter = (Invoke-ScratchGit @('rev-parse', 'HEAD') | Select-Object -First 1)
        Case 'git commit REFUSED by the hook (HEAD unmoved)' $headBefore $headAfter

        # 10. NEGATIVE CONTROL for case 9: with the guard removed from the
        #     hook, the identical commit LANDS. This is what proves case 9
        #     measured the guard and not some unrelated refusal.
        [System.IO.File]::WriteAllText($hookPath, "#!/bin/sh`nexit 0`n")
        Invoke-ScratchGit @('commit', '-q', '-m', 'flip the flag') | Out-Null
        $headControl = (Invoke-ScratchGit @('rev-parse', 'HEAD') | Select-Object -First 1)
        Case 'control: guard removed -> the same commit LANDS' $true ($headControl -ne $headBefore) "head $headBefore -> $headControl"
    }
} finally {
    if (-not $KeepTemp) { Remove-Item -Recurse -Force $Tmp -ErrorAction SilentlyContinue }
}

Write-Host ""
$failed = @($results | Where-Object { -not $_.pass })
if ($results.Count -ne $EXPECTED_CASES) {
    Write-Host ("CASE COUNT WRONG: ran {0}, expected {1}. A case stopped running." -f $results.Count, $EXPECTED_CASES) -ForegroundColor Red
    exit 1
}
if ($failed.Count -eq 0) {
    Write-Host ("ALL {0} CASES PASS - the flag guard stops the flip, allows a rated one, and records both." -f $results.Count) -ForegroundColor Green
    exit 0
}
Write-Host ("{0} of {1} CASES FAILED" -f $failed.Count, $results.Count) -ForegroundColor Red
$failed | Format-Table case, expected, actual, detail -AutoSize | Out-String | Write-Host
exit 1
