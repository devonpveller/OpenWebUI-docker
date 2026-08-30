# verify-judge-flag-guard.ps1 - executable proof that check-judge-flag.ps1
# mechanically STOPS a commit that turns observer.judge_enabled on, leaves an
# audit record when it does, and can no longer be walked past by SPELLING the
# flag differently.
#
#   .\scripts\checks\verify-judge-flag-guard.ps1      # ~60s, cleans up after itself
#
# WHY A SEPARATE DRILL. verify-judge-dryrun.ps1 proves the INSTRUMENT can fail.
# This proves the GUARD can stop something - a different claim, and the one a
# verifier falsified: with only the instrument in place, setting judge_enabled
# true and re-running produced ALL CASES PASS, exit 0.
#
# WHY IT GREW A CORPUS (2026-08-30). The guard's first version decided "is the
# flag being turned on?" with a regex while the daemon decided it with
# yaml.safe_load + pydantic, and every case in this drill used YAML the drill
# itself wrote in the one shape the pattern expected. Two verifiers turned the
# flag ON for the daemon and past the pattern - three ordinary spellings, then
# two quote characters - and this drill stayed green throughout. So the drill
# now replays scripts/checks/fixtures/judge-flag-corpus.json: 43 rows generated
# by MECHANISM (scalar case, scalar quoting, key quoting and hex escaping, flow
# style, anchors, merge keys, document structure, whitespace, comments,
# relocation), each pinned with what the daemon's REAL loader returns. Fourteen
# of them the old pattern could not see.
#
# THE SAME CORPUS FILE is read by little-coder/tests/test_judge_flag_corpus.py,
# which drives the daemon's loader against the gate's decider. One corpus, two
# executors, so a disagreement is a test failure rather than a discovery.
#
# EVERYTHING IS IN A SCRATCH REPOSITORY under the temp directory. This drill
# never stages anything in this repo, never writes this machine's audit log,
# and never sets judge_enabled anywhere outside its own throwaway git repo. The
# hook it drives is not a hand-written stand-in: the real .githooks/pre-commit's
# step-6 block is EXTRACTED BY MARKER, and the real check-judge-flag.ps1 is
# copied in verbatim and pointed at THIS repository's littlecoder package -
# the same module the daemon calls at boot. A drill against a paraphrase of the
# mechanism proves nothing about the mechanism.
#
# Exit 0 = every case as expected. Exit 1 = at least one case wrong.

[CmdletBinding()]
param([switch]$KeepTemp)

$ErrorActionPreference = 'Continue'
$RepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$Guard = Join-Path $PSScriptRoot 'check-judge-flag.ps1'
$Decider = Join-Path $PSScriptRoot 'lib\judge_flag_decide.py'
$Corpus = Join-Path $PSScriptRoot 'fixtures\judge-flag-corpus.json'
$Src = Join-Path $RepoRoot 'little-coder\src'
$RealHook = Join-Path $RepoRoot '.githooks\pre-commit'
$Tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("judge-flag-drill-" + [guid]::NewGuid().ToString('N').Substring(0, 8))

# EXPECTED_CASES exists so a case that silently stops running is a FAILURE and
# not a quieter green. The drill it is modelled on could lose a case and still
# print "ALL 12 CASES PASS" because nothing read the number.
$EXPECTED_CASES = 30

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

function Invoke-Guard {
    param([string]$SrcOverride = '')
    $src = if ($SrcOverride) { $SrcOverride } else { $Src }
    & powershell -NoProfile -ExecutionPolicy Bypass -File $script:ScratchGuard -RepoRoot $script:Repo -SrcPath $src | Out-Null
    return $LASTEXITCODE
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
    # The scratch config is a throwaway written at drill time. Nothing with
    # judge_enabled: true is committed to this repository by this drill or
    # shipped in its fixtures -- the corpus rows below live in a JSON fixture,
    # not in a YAML file anything loads.
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

    # The REAL guard and the REAL decider, verbatim. The decider imports
    # littlecoder from THIS repository (-SrcPath), so what the drill exercises
    # is the same judge_gate the daemon calls - not a copy of it.
    Copy-Item $Guard (Join-Path $script:Repo 'scripts\checks\check-judge-flag.ps1')
    Copy-Item $Decider (Join-Path $script:Repo 'scripts\checks\lib\judge_flag_decide.py')
    $script:ScratchGuard = Join-Path $script:Repo 'scripts\checks\check-judge-flag.ps1'

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
    Case 'no yaml staged -> allowed' 0 (Invoke-Guard)
    Case 'no yaml staged -> NO audit line' 0 @(Get-AuditLines).Count

    # -----------------------------------------------------------------
    # 2. NEGATIVE CONTROL: the flag staged OFF. Must not be denied - a
    #    guard that fires on the correct value gets switched off.
    # -----------------------------------------------------------------
    Set-Flag 'false'
    Invoke-ScratchGit @('add', 'lc.config.yaml') | Out-Null
    Case 'judge_enabled false staged -> allowed' 0 (Invoke-Guard)
    Case 'judge_enabled false -> still NO audit line' 0 @(Get-AuditLines).Count

    # -----------------------------------------------------------------
    # 3. RED: the flag staged ON with no rating record.
    # -----------------------------------------------------------------
    Set-Flag 'true'
    Invoke-ScratchGit @('add', 'lc.config.yaml') | Out-Null
    Case 'judge_enabled true, no rating -> DENIED' 1 (Invoke-Guard)
    $audit = @(Get-AuditLines)
    Case 'the denial is in the audit record' $true (($audit.Count -eq 1) -and ($audit[-1] -match 'DENY.*no-rating-record-staged')) ($audit -join ' | ')

    # -----------------------------------------------------------------
    # 4. THE LAUNDERING ATTEMPT: stage ON, then put the file back to OFF
    #    in the working tree. git commits the INDEX, so the guard must
    #    read the index too, or `git add` + edit-back defeats it.
    # -----------------------------------------------------------------
    Set-Flag 'false'   # working tree now says false; index still says true
    Case 'staged-on + working-tree-off -> still DENIED' 1 (Invoke-Guard)
    Set-Flag 'true'
    Invoke-ScratchGit @('add', 'lc.config.yaml') | Out-Null

    # -----------------------------------------------------------------
    # 5. RED: an INVALID rating record does not buy the flip.
    # -----------------------------------------------------------------
    $ratingRel = 'little-coder/config/judge-enablement-rating.yaml'
    $ratingAbs = Join-Path $script:Repo 'little-coder\config\judge-enablement-rating.yaml'
    Set-Content -Path $ratingAbs -Value "rated_by: someone`nverdict: approve`n" -Encoding ascii
    Invoke-ScratchGit @('add', $ratingRel) | Out-Null
    Case 'incomplete rating record -> DENIED' 1 (Invoke-Guard)
    $audit = @(Get-AuditLines)
    Case 'the invalid record is named in the audit record' $true ($audit[-1] -match 'rating-record-invalid.*missing required key') ($audit[-1])

    # -----------------------------------------------------------------
    # 6. RED: a record that is complete but does not APPROVE.
    # -----------------------------------------------------------------
    Set-Content -Path $ratingAbs -Value "rated_by: someone`nrated_at: 2026-08-30`nrated_report: dryrun.json`nverdict: reject`n" -Encoding ascii
    Invoke-ScratchGit @('add', $ratingRel) | Out-Null
    Case 'rating record verdict=reject -> DENIED' 1 (Invoke-Guard)

    # -----------------------------------------------------------------
    # 7. GREEN: a complete, approving record allows the flip and says so
    #    in the audit record. Without this case the drill would prove
    #    only that the guard says no.
    # -----------------------------------------------------------------
    Set-Content -Path $ratingAbs -Value "rated_by: operator`nrated_at: 2026-08-30T12:00:00Z`nrated_report: dryrun/judge-dryrun-little-coder.json`nverdict: approve`n" -Encoding ascii
    Invoke-ScratchGit @('add', $ratingRel) | Out-Null
    Case 'valid approving rating record -> ALLOWED' 0 (Invoke-Guard)
    $audit = @(Get-AuditLines)
    Case 'the allow is in the audit record too' $true ($audit[-1] -match 'ALLOW') ($audit[-1])

    # -----------------------------------------------------------------
    # 8. FAIL CLOSED, twice. The guard has no rule of its own any more:
    #    it asks littlecoder.judge_gate. Both ways of losing that answer
    #    must DENY, not wave the flip through.
    # -----------------------------------------------------------------
    $deciderInRepo = Join-Path $script:Repo 'scripts\checks\lib\judge_flag_decide.py'
    Move-Item $deciderInRepo "$deciderInRepo.hidden"
    Case 'decider missing -> DENIED (fail closed)' 1 (Invoke-Guard)
    $audit = @(Get-AuditLines)
    Case 'the cannot-tell is in the audit record' $true ($audit[-1] -match 'CANNOT-TELL') ($audit[-1])
    Move-Item "$deciderInRepo.hidden" $deciderInRepo

    # littlecoder unreachable: the decider runs but cannot import the module
    # that holds the decision. It must NOT fall back to a pattern.
    Case 'littlecoder unimportable -> DENIED (fail closed)' 1 (Invoke-Guard (Join-Path $Tmp 'no-such-src'))

    # -----------------------------------------------------------------
    # 9. THE GUARD AGAINST THE REAL FILE'S SHAPE. Every case above uses a
    #    YAML this drill wrote, so all of them would still pass if the guard
    #    did not see the shape the SHIPPED config actually uses. This takes
    #    the real little-coder.config.yaml, flips the flag in a copy INSIDE
    #    THE TEMP DIRECTORY, and checks the guard sees it. The repository's
    #    own config is never modified.
    # -----------------------------------------------------------------
    $realCfg = Join-Path $RepoRoot 'little-coder\config\little-coder.config.yaml'
    if (-not (Test-Path $realCfg)) {
        Case 'real config present to test the guard against' $true $false $realCfg
        Case 'the flip actually changed the real config text' $true $false 'skipped - no real config'
        Case 'guard sees the REAL config shape when flipped' $true $false 'skipped - no real config'
    } else {
        Case 'real config present to test the guard against' $true $true ''
        # Flip only the boolean, byte for byte otherwise - a rewritten YAML
        # would test the rewriter's formatting, not the shipped file's.
        # A literal replace, no regex metacharacters - and then ASSERT the text
        # actually changed. Without that assertion a spacing change in the
        # shipped config would make this replace a no-op, the guard would
        # correctly allow an unflipped file, and the case would go GREEN for
        # the wrong reason.
        # Case 7 left a VALID rating record in the index, and with that staged
        # the guard is right to allow anything. Clear it first, or this case
        # measures the rating record rather than the guard - which is what it
        # did on its first run: FAIL, expected 1 got 0, for the wrong reason.
        Invoke-ScratchGit @('rm', '--cached', '-q', $ratingRel) | Out-Null
        Remove-Item $ratingAbs -Force -ErrorAction SilentlyContinue
        $realText = Get-Content -Raw $realCfg
        $flipped = $realText.Replace('judge_enabled: false', 'judge_enabled: true')
        Case 'the flip actually changed the real config text' $true ($flipped -ne $realText) 'shipped spacing may have changed'
        $realCopy = Join-Path $script:Repo 'real.config.yaml'
        Set-Content -Path $realCopy -Value $flipped -Encoding ascii
        Invoke-ScratchGit @('add', 'real.config.yaml') | Out-Null
        Case 'guard sees the REAL config shape when flipped' 1 (Invoke-Guard)
        Invoke-ScratchGit @('rm', '--cached', '-q', 'real.config.yaml') | Out-Null
        Remove-Item $realCopy -Force -ErrorAction SilentlyContinue
    }

    # -----------------------------------------------------------------
    # 10. THE CORPUS. The neighbouring-case defence: every spelling that
    #     turns the flag ON for the daemon's real loader must be DENIED by
    #     the SHIPPED guard, and every OFF control must still be allowed.
    #     Rows and their pinned daemon answers come from the fixture that
    #     little-coder/tests/test_judge_flag_corpus.py also reads.
    # -----------------------------------------------------------------
    Invoke-ScratchGit @('rm', '--cached', '-q', 'lc.config.yaml') | Out-Null
    Remove-Item (Join-Path $script:Repo 'lc.config.yaml') -Force -ErrorAction SilentlyContinue

    if (-not (Test-Path $Corpus)) {
        Case 'corpus fixture present' $true $false $Corpus
        Case 'corpus is large enough to mean something' $true $false 'skipped'
        Case 'every daemon-ON row is a guard-ON row (pins)' $true $false 'skipped'
        Case 'every spelling the daemon reads ON is DENIED' 0 -1 'skipped'
        Case 'those denials are DENY, not CANNOT-TELL' 0 -1 'skipped'
        Case 'every OFF control is still ALLOWED' 0 -1 'skipped'
        Case 'the replaced regex would have missed >= 10 of them' $true $false 'skipped'
    } else {
        Case 'corpus fixture present' $true $true ''
        $corpusDoc = Get-Content -Raw $Corpus | ConvertFrom-Json
        $rows = @($corpusDoc.cases)
        Case 'corpus is large enough to mean something' $true ($rows.Count -ge 40) "rows=$($rows.Count)"

        $pinBreaks = @($rows | Where-Object { ($_.daemon_expected -is [bool]) -and $_.daemon_expected -and (-not $_.guard_expected) })
        Case 'every daemon-ON row is a guard-ON row (pins)' $true ($pinBreaks.Count -eq 0) (($pinBreaks | ForEach-Object { $_.id }) -join ',')

        $denyRows = @($rows | Where-Object { $_.guard_expected })
        $allowRows = @($rows | Where-Object { -not $_.guard_expected })
        $corpusFile = Join-Path $script:Repo 'corpus.config.yaml'
        $corpusRel = 'corpus.config.yaml'

        # CANNOT-TELL also exits 1. Without reading the audit lines back, a
        # decider that had stopped working would make this loop pass for
        # exactly the wrong reason - which is what a BOM in the request file
        # did on this drill's first run.
        $auditBefore = @(Get-AuditLines).Count
        $wrong = @()
        foreach ($row in $denyRows) {
            [System.IO.File]::WriteAllText($corpusFile, $row.yaml)
            Invoke-ScratchGit @('add', $corpusRel) | Out-Null
            if ((Invoke-Guard) -ne 1) { $wrong += $row.id }
        }
        Case 'every spelling the daemon reads ON is DENIED' 0 $wrong.Count (($wrong) -join ',')
        $newLines = @(Get-AuditLines)[$auditBefore..(@(Get-AuditLines).Count - 1)]
        $notReal = @($newLines | Where-Object { $_ -notmatch 'DENY .*no-rating-record-staged' })
        Case 'those denials are DENY, not CANNOT-TELL' 0 $notReal.Count (($notReal | Select-Object -First 1) -join '')

        # All OFF controls staged AT ONCE: one allowed commit, not sixteen.
        # A guard that denied any of them would deny this.
        $offDir = Join-Path $script:Repo 'off'
        New-Item -ItemType Directory -Path $offDir -Force | Out-Null
        foreach ($row in $allowRows) {
            [System.IO.File]::WriteAllText((Join-Path $offDir ($row.id + '.yaml')), $row.yaml)
        }
        Invoke-ScratchGit @('rm', '--cached', '-q', $corpusRel) | Out-Null
        Remove-Item $corpusFile -Force -ErrorAction SilentlyContinue
        Invoke-ScratchGit @('add', 'off') | Out-Null
        Case 'every OFF control is still ALLOWED' 0 (Invoke-Guard) "n=$($allowRows.Count)"
        Invoke-ScratchGit @('rm', '--cached', '-q', '-r', 'off') | Out-Null
        Remove-Item -Recurse -Force $offDir -ErrorAction SilentlyContinue

        # Kept executable so the reason the pattern was removed does not decay
        # into a claim in a document.
        $legacy = [regex]::new($corpusDoc.legacy_regex)
        $missed = @($rows | Where-Object { ($_.daemon_expected -is [bool]) -and $_.daemon_expected -and (-not $legacy.IsMatch($_.yaml)) })
        Case 'the replaced regex would have missed >= 10 of them' $true ($missed.Count -ge 10) "missed=$($missed.Count)"
    }

    # -----------------------------------------------------------------
    # 11. ONE RECORD, TWO ENFORCEMENT POINTS. The commit-time guard and the
    #     daemon's boot-time gate must be talking about the SAME FILE, or
    #     staging a record satisfies a rule the daemon never applies.
    #     Three readers of one fact: judge_gate's constant, this guard's
    #     default parameter, and the coder compose mount.
    # -----------------------------------------------------------------
    $gateRepoPath = (& python -c "import sys; sys.path.insert(0, sys.argv[1]); from littlecoder import judge_gate; print(judge_gate.RATING_RECORD_REPO_PATH)" $Src 2>&1 | Out-String).Trim()
    $guardDefault = ''
    $m = [regex]::Match((Get-Content -Raw $Guard), '\$RatingRecordPath\s*=\s*"([^"]+)"')
    if ($m.Success) { $guardDefault = $m.Groups[1].Value }
    Case 'judge_gate and the guard name the same record' $gateRepoPath $guardDefault

    $gateContainerPath = (& python -c "import sys; sys.path.insert(0, sys.argv[1]); from littlecoder import judge_gate; print(judge_gate.DEFAULT_RATING_RECORD_PATH)" $Src 2>&1 | Out-String).Trim()
    $composeText = Get-Content -Raw (Join-Path $RepoRoot 'coder\docker-compose.yml')
    # The mount that makes the staged record readable by the daemon.
    $mounted = $composeText -match '\.\./little-coder/config:/app/config'
    $derived = if ($mounted) { '/app/config/' + (Split-Path -Leaf $gateRepoPath) } else { '<no mount>' }
    Case 'the compose mount produces the container path the gate reads' $gateContainerPath $derived

    # -----------------------------------------------------------------
    # 12. END TO END: does the exit code actually reach git?
    #    A guard that returns 1 into a hook that ignores it stops nothing.
    #    The hook body is the REAL step-6 block, extracted by marker.
    # -----------------------------------------------------------------
    Set-Flag 'true'
    Invoke-ScratchGit @('add', 'lc.config.yaml') | Out-Null
    $hookSrc = Get-Content -Raw $RealHook
    $start = $hookSrc.IndexOf('# --- 6. judge_enabled flag guard')
    $end = $hookSrc.IndexOf('# --- 7. ATTESTATION')
    if ($start -lt 0 -or $end -le $start) {
        Case 'step-6 block extractable from the real hook' $true $false 'marker not found - the hook was renumbered'
        Case 'git commit REFUSED by the hook (HEAD unmoved)' 'x' 'y' 'skipped'
        Case 'control: guard removed -> the same commit LANDS' $true $false 'skipped'
    } else {
        Case 'step-6 block extractable from the real hook' $true $true ''
        # The hook calls the guard by relative path with no -SrcPath, so the
        # scratch repo needs the package where the guard's own default looks
        # for it. Copying it is what makes this an END-TO-END case: the hook
        # body is unmodified.
        Copy-Item -Recurse -Force (Join-Path $Src 'littlecoder') (Join-Path $script:Repo 'little-coder\src\littlecoder')
        $block = $hookSrc.Substring($start, $end - $start)
        $hookBody = "#!/bin/sh`n" + $block
        $hookPath = Join-Path $script:Repo '.githooks\pre-commit'
        [System.IO.File]::WriteAllText($hookPath, ($hookBody -replace "`r`n", "`n"))
        Invoke-ScratchGit @('config', 'core.hooksPath', '.githooks') | Out-Null

        # The index holds judge_enabled: true and NO rating record, so the hook
        # must deny.
        $headBefore = (Invoke-ScratchGit @('rev-parse', 'HEAD') | Select-Object -First 1)
        Invoke-ScratchGit @('commit', '-q', '-m', 'flip the flag') | Out-Null
        $headAfter = (Invoke-ScratchGit @('rev-parse', 'HEAD') | Select-Object -First 1)
        Case 'git commit REFUSED by the hook (HEAD unmoved)' $headBefore $headAfter

        # 13. NEGATIVE CONTROL for case 12: with the guard removed from the
        #     hook, the identical commit LANDS. This is what proves case 12
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
    Write-Host ("ALL {0} CASES PASS - the flag guard stops the flip in every spelling the corpus carries, allows a rated one, and records both." -f $results.Count) -ForegroundColor Green
    exit 0
}
Write-Host ("{0} of {1} CASES FAILED" -f $failed.Count, $results.Count) -ForegroundColor Red
$failed | Format-Table case, expected, actual, detail -AutoSize | Out-String | Write-Host
exit 1
