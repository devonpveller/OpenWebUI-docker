# verify-judge-dryrun.ps1 - executable proof that check-judge-dryrun.ps1 CAN FAIL.
#
#   .\scripts\checks\verify-judge-dryrun.ps1      # ~15s, cleans up after itself
#
# A check nobody has seen fail is not known to check anything. This drill drives
# check-judge-dryrun.ps1 across synthetic fixtures and asserts the exit code of
# every branch - the three CANNOT-TELL paths, the NOT-READY gate, and the one
# GREEN path where a healthy corpus passes. If the dry run ever degrades into
# "always says NOT-READY" or "always exits 0", a case here goes red.
#
# Every fixture is SYNTHETIC (scripts/checks/fixtures/judge-dryrun/ + a temp
# directory). The drill never reads the real personal plane, never touches the
# live journals, and never sets judge_enabled.
#
# NOTHING HERE MAY SKIP. A verifier moved fixtures/judge-dryrun/MANIFEST.sha256
# aside and this drill printed `[SKIP] fixtures-unchanged`, then `ALL 12 CASES
# PASS`, exit 0 - the one case that proves the read-only claim vanished and the
# drill still declared victory, because nothing read the case count. Both halves
# are fixed: a missing manifest is a FAILING case, and $EXPECTED_CASES is
# asserted so a case that stops running is red rather than quieter. (-Regen,
# which that message named and which did not exist, is now a real switch.)
#
# SCOPE. This proves the INSTRUMENT can fail. It does not prove anything is
# mechanically STOPPED - that is verify-judge-flag-guard.ps1, and the hook-bypass
# and personal-plane halves of U5 are separate deliverables entirely.
#
# Exit 0 = all cases as expected. Exit 1 = at least one case wrong, or the
#          number of cases that ran is not the number that should have run.

[CmdletBinding()]
param(
    [switch]$KeepTemp,
    # Rewrite fixtures/judge-dryrun/MANIFEST.sha256 from the current fixture
    # tree. For deliberately changing a fixture - never to make a red go away.
    [switch]$Regen
)

# Asserted at the end. A drill whose case count nothing reads can lose its most
# important case and still print a green summary. This is not hypothetical here.
$EXPECTED_CASES = 34

$ErrorActionPreference = 'Continue'
$RepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$Check = Join-Path $PSScriptRoot 'check-judge-dryrun.ps1'
$Probe = Join-Path $PSScriptRoot 'lib\judge_dryrun.py'
$Fixtures = Join-Path $PSScriptRoot 'fixtures\judge-dryrun'
$Tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("judge-dryrun-drill-" + [guid]::NewGuid().ToString('N').Substring(0, 8))

$results = @()
function Get-NormalizedHash($path) {
    # SHA256 over the file with CR bytes removed, so LF and CRLF checkouts of
    # the same text fixture hash identically.
    # @() around the filter: on an EMPTY file the pipeline yields nothing, which
    # PowerShell hands on as $null, and ComputeHash([byte[]]$null) returns an
    # empty string - so every empty file hashed to "" and matched every other
    # one. Found while regenerating the manifest, not by reading the code.
    $bytes = @([System.IO.File]::ReadAllBytes($path) | Where-Object { $_ -ne 13 })
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        ($sha.ComputeHash([byte[]]$bytes) | ForEach-Object { $_.ToString('X2') }) -join ''
    } finally { $sha.Dispose() }
}
function Case($label, $expected, $actual, $detail = '') {
    $ok = ($expected -eq $actual)
    $script:results += [pscustomobject]@{ case = $label; expected = $expected; actual = $actual; pass = $ok; detail = $detail }
    $color = if ($ok) { 'Green' } else { 'Red' }
    Write-Host ("  [{0}] {1}  expected exit {2}, got {3}  {4}" -f $(if ($ok) { 'PASS' } else { 'FAIL' }), $label, $expected, $actual, $detail) -ForegroundColor $color
}

if ($Regen) {
    # NOT named $regen. PowerShell variable names are CASE-INSENSITIVE, so
    # `$regen` IS the `[switch]$Regen` parameter - assigning the array to it
    # coerced the whole thing to a SwitchParameter and wrote the single word
    # "True" into MANIFEST.sha256. Caught by running -Regen and reading the
    # file, not by reading the code. (check-hook-attestation.ps1 carries the
    # same warning about $base/$Base; this is that trap a second time.)
    $manifestLines = @(Get-ChildItem -Recurse -File $Fixtures |
        Where-Object { $_.Name -ne 'MANIFEST.sha256' } | Sort-Object FullName |
        ForEach-Object { "$($_.FullName.Substring($Fixtures.Length + 1))|$(Get-NormalizedHash $_.FullName)" })
    Set-Content -Path (Join-Path $Fixtures 'MANIFEST.sha256') -Value $manifestLines -Encoding ascii
    Write-Host ("regenerated MANIFEST.sha256 ({0} entries) - review the diff before committing" -f $manifestLines.Count) -ForegroundColor Yellow
    exit 0
}

New-Item -ItemType Directory -Path $Tmp -Force | Out-Null
Write-Host "verify-judge-dryrun - temp: $Tmp" -ForegroundColor Cyan
Write-Host ""

try {
    # ---------------------------------------------------------------------
    # 1. RED: no journals directory at all -> CANNOT TELL (3)
    # ---------------------------------------------------------------------
    $missing = Join-Path $Tmp 'no-such-journals'
    & $Check -JournalsPath $missing -PolyglotPath $Fixtures | Out-Null
    Case 'missing journals dir -> CANNOT TELL' 3 $LASTEXITCODE

    # ---------------------------------------------------------------------
    # 2. RED: the directory exists but holds no records -> CANNOT TELL (3)
    # ---------------------------------------------------------------------
    $empty = Join-Path $Tmp 'empty-journals'
    New-Item -ItemType Directory -Path $empty -Force | Out-Null
    & $Check -JournalsPath $empty | Out-Null
    Case 'empty journals dir -> CANNOT TELL' 3 $LASTEXITCODE

    # ---------------------------------------------------------------------
    # 3. RED: a prior cohort store holding a cluster -> CANNOT TELL (4)
    #    (stub-similarity projection stops being faithful; clusters.py:144-150)
    # ---------------------------------------------------------------------
    $withStore = Join-Path $Tmp 'cohorts-with-cluster'
    New-Item -ItemType Directory -Path $withStore -Force | Out-Null
    Copy-Item (Join-Path $Fixtures 'cohort-store-with-cluster.json') (Join-Path $withStore 'cohort-store.json')
    & $Check -JournalsPath (Join-Path $Fixtures 'noisy') -CohortsPath $withStore | Out-Null
    Case 'prior store has clusters -> CANNOT TELL' 4 $LASTEXITCODE

    # ---------------------------------------------------------------------
    # 4. RED: littlecoder not importable -> CANNOT TELL (5)
    #    Driven at the probe directly: the wrapper always points --src at the
    #    repo, so this branch is only reachable on a machine where the source
    #    tree is absent - which is exactly when a silent 0 would be worst.
    # ---------------------------------------------------------------------
    & python $Probe --journals (Join-Path $Fixtures 'noisy') --src (Join-Path $Tmp 'no-such-src') | Out-Null
    Case 'littlecoder not importable -> CANNOT TELL' 5 $LASTEXITCODE

    # ---------------------------------------------------------------------
    # 5. RED: an unreadable config -> CANNOT TELL (5)
    # ---------------------------------------------------------------------
    $badCfg = Join-Path $Tmp 'broken.config.yaml'
    Set-Content -Path $badCfg -Value "schema_version: 1`nnot_a_real_key: {" -Encoding ascii
    & $Check -JournalsPath (Join-Path $Fixtures 'noisy') -ConfigPath $badCfg | Out-Null
    Case 'unreadable config -> CANNOT TELL' 5 $LASTEXITCODE

    # ---------------------------------------------------------------------
    # 6. RED: a container that is not running -> CANNOT TELL (6)
    # ---------------------------------------------------------------------
    & $Check -Container 'no-such-container-u5judge' | Out-Null
    Case 'container not running -> CANNOT TELL' 6 $LASTEXITCODE

    # ---------------------------------------------------------------------
    # 7. The noisy corpus: a verdict IS produced (0) ...
    # ---------------------------------------------------------------------
    $noisyOut = & $Check -JournalsPath (Join-Path $Fixtures 'noisy') -PolyglotPath (Join-Path $Fixtures 'polyglot') -Json 2>&1 | Out-String
    Case 'noisy corpus -> a verdict is produced' 0 $LASTEXITCODE
    $noisyVerdict = if ($noisyOut -match '"verdict":\s*"([A-Z-]+)"') { $Matches[1] } else { '<none>' }
    Case 'noisy corpus verdict is NOT-READY' 'NOT-READY' $noisyVerdict

    # ---------------------------------------------------------------------
    # 8. ... and -RequireReady turns that verdict into a failing gate (1)
    # ---------------------------------------------------------------------
    & $Check -JournalsPath (Join-Path $Fixtures 'noisy') -PolyglotPath (Join-Path $Fixtures 'polyglot') -RequireReady | Out-Null
    Case 'noisy corpus + -RequireReady -> gate fails' 1 $LASTEXITCODE

    # ---------------------------------------------------------------------
    # 9. GREEN: a healthy corpus clears every mechanical precondition.
    #    Without this case the drill would prove only that the check says no.
    # ---------------------------------------------------------------------
    $cfg = Join-Path $Tmp 'ready.config.yaml'
    $gen = Join-Path $Tmp 'gen_cfg.py'
    $fkDir = (Join-Path $Fixtures 'founding-knowledge') -replace '\\', '/'
    @"
import sys, yaml
src, dst, fk = sys.argv[1], sys.argv[2], sys.argv[3]
cfg = yaml.safe_load(open(src, encoding='utf-8'))
paths = [fk + '/environment.md', fk + '/engineering-principles.md', fk + '/project-context.md']
args = []
for tok in cfg['agent']['extra_args']:
    args.append(tok)
cleaned, i = [], 0
while i < len(args):
    if args[i] == '--append-system-prompt':
        i += 2
        continue
    cleaned.append(args[i]); i += 1
for p in paths:
    cleaned += ['--append-system-prompt', p]
cfg['agent']['extra_args'] = cleaned
cfg['observer']['founding_knowledge_paths'] = paths
yaml.safe_dump(cfg, open(dst, 'w', encoding='utf-8'))
"@ | Set-Content -Path $gen -Encoding ascii
    & python $gen (Join-Path $RepoRoot 'little-coder\config\little-coder.config.yaml') $cfg $fkDir
    if ($LASTEXITCODE -ne 0) { throw 'could not generate the ready-fixture config' }

    $readyOut = & $Check -JournalsPath (Join-Path $Fixtures 'ready') -PolyglotPath (Join-Path $Fixtures 'polyglot') -ConfigPath $cfg -RequireReady -Json 2>&1 | Out-String
    Case 'healthy corpus + -RequireReady -> gate passes' 0 $LASTEXITCODE
    $readyVerdict = if ($readyOut -match '"verdict":\s*"([A-Z-]+)"') { $Matches[1] } else { '<none>' }
    Case 'healthy corpus verdict is READY-FOR-RATING' 'READY-FOR-RATING' $readyVerdict
    $mintable = if ($readyOut -match '"pools_mintable":\s*(\d+)') { [int]$Matches[1] } else { -1 }
    Case 'healthy corpus has >=1 mintable pool' $true ($mintable -ge 1) "pools_mintable=$mintable"

    # ---------------------------------------------------------------------
    # 10. THE WRITE DETECTOR ITSELF. `wrote_nothing` used to be a hardcoded
    #     literal: a copy of the probe with a write injected still reported
    #     true. It is measured now, and these two cases are what say so - a
    #     detector nobody has seen fire is not known to detect anything.
    # ---------------------------------------------------------------------
    $wd = Join-Path $Tmp 'write-detector-skill'
    New-Item -ItemType Directory -Path (Join-Path $wd 'knowledge') -Force | Out-Null
    $wdOff = & python $Probe --journals (Join-Path $Fixtures 'ready') --skill $wd --src (Join-Path $RepoRoot 'little-coder\src') 2>&1 | Out-String
    Case 'read-only run -> exit 0' 0 $LASTEXITCODE
    Case 'read-only run -> wrote_nothing true' $true ($wdOff -match '"wrote_nothing": true')
    $wdOn = & python $Probe --journals (Join-Path $Fixtures 'ready') --skill $wd --src (Join-Path $RepoRoot 'little-coder\src') --prove-write-detector 2>&1 | Out-String
    Case 'a probe that WRITES -> INTEGRITY exit 8' 8 $LASTEXITCODE
    Case 'a probe that WRITES -> wrote_nothing false' $true ($wdOn -match '"wrote_nothing": false')
    Case 'the written file is NAMED in the report' $true ($wdOn -match 'CREATED PROVE-WRITE-DETECTOR')

    # ---------------------------------------------------------------------
    # 11. EMPTY IS NOT POISONED. Same file count, different state, different
    #     remedy. A raw *.md count cannot tell these apart, which is why it
    #     was replaced.
    # ---------------------------------------------------------------------
    $emptyLib = Join-Path $Tmp 'skill-empty'
    New-Item -ItemType Directory -Path (Join-Path $emptyLib 'knowledge') -Force | Out-Null
    $emptyOut = & $Check -JournalsPath (Join-Path $Fixtures 'ready') -SkillPath $emptyLib -PolyglotPath (Join-Path $Fixtures 'polyglot') -Json 2>&1 | Out-String
    Case 'empty skill library -> state empty' $true ($emptyOut -match '"state": "empty"')

    $poisonLib = Join-Path $Tmp 'skill-poisoned'
    New-Item -ItemType Directory -Path (Join-Path $poisonLib 'knowledge') -Force | Out-Null
    Set-Content -Path (Join-Path $poisonLib 'knowledge\corrupt.md') -Value 'this is not a skill file' -Encoding ascii
    Set-Content -Path (Join-Path $poisonLib 'knowledge\leftover.md.tmp') -Value 'junk' -Encoding ascii
    $poisonOut = & $Check -JournalsPath (Join-Path $Fixtures 'ready') -SkillPath $poisonLib -PolyglotPath (Join-Path $Fixtures 'polyglot') -Json 2>&1 | Out-String
    Case 'poisoned skill library -> state poisoned' $true ($poisonOut -match '"state": "poisoned"')
    Case 'poisoned library names the unparseable file' $true ($poisonOut -match 'UNPARSEABLE: knowledge/corrupt.md')
    Case 'poisoned library names the stray tmp' $true ($poisonOut -match 'STRAY TMP: knowledge/leftover.md.tmp')
    Case 'poisoned library blocks the verdict' 'NOT-READY' $(if ($poisonOut -match '"verdict": "([A-Z-]+)"') { $Matches[1] } else { 'none' })

    # ---------------------------------------------------------------------
    # 12. THE PLAIN ANSWER. "nothing, because there are no journals to read"
    #     must be SAID, not left for the caller to infer from an exit code.
    # ---------------------------------------------------------------------
    $noneOut = & $Check -JournalsPath (Join-Path $Tmp 'empty-journals') -Json 2>&1 | Out-String
    Case 'no journals -> would_have_minted says NOTHING KNOWABLE' $true ($noneOut -match 'NOTHING KNOWABLE.*no journal records')
    Case 'noisy corpus -> would_have_minted says NOTHING (below min_pool)' $true ($noisyOut -match 'NOTHING\. 5 occurrence')
    # The MIDDLE answer, and the one the live plane gives: the judge IS invoked,
    # and every invocation would be handed noise. Neither existing fixture
    # reaches it - noisy/ stops below min_pool, ready/ clears the bar.
    $invOut = & $Check -JournalsPath (Join-Path $Fixtures 'invoked-but-noise') -PolyglotPath (Join-Path $Fixtures 'polyglot') -Json 2>&1 | Out-String
    Case 'invoked-but-noise -> a verdict is produced' 0 $LASTEXITCODE
    Case 'invoked-but-noise -> would_have_minted says NOTHING WORTH MINTING' $true ($invOut -match 'NOTHING WORTH MINTING')
    Case 'invoked-but-noise -> judge invoked on 1 pool, 0 mintable' $true (($invOut -match '"pools_judge_would_be_invoked_on": 1') -and ($invOut -match '"pools_mintable": 0'))
    Case 'healthy corpus -> would_have_minted counts the pools' $true ($readyOut -match 'would be handed to the judge')

    # ---------------------------------------------------------------------
    # 13. THE FLAG THIS TOOL EXISTS TO GATE. judge_enabled: true used to
    #     change no verdict and no exit code at all: a verifier set it and
    #     still got ALL CASES PASS, exit 0. The config is generated INTO THE
    #     TEMP DIRECTORY at drill time; judge_enabled is never set to true in
    #     any file this repository tracks.
    # ---------------------------------------------------------------------
    $onCfg = Join-Path $Tmp 'judge-on.config.yaml'
    $flip = Join-Path $Tmp 'flip_cfg.py'
    @"
import sys, yaml
src, dst = sys.argv[1], sys.argv[2]
cfg = yaml.safe_load(open(src, encoding='utf-8'))
cfg['observer']['judge_enabled'] = True
yaml.safe_dump(cfg, open(dst, 'w', encoding='utf-8'))
"@ | Set-Content -Path $flip -Encoding ascii
    & python $flip $cfg $onCfg
    if ($LASTEXITCODE -ne 0) { throw 'could not generate the judge-on fixture config' }

    & $Check -JournalsPath (Join-Path $Fixtures 'ready') -PolyglotPath (Join-Path $Fixtures 'polyglot') -ConfigPath $onCfg | Out-Null
    Case 'judge_enabled already true -> MISCONFIGURED exit 7' 7 $LASTEXITCODE
    & $Check -JournalsPath (Join-Path $Fixtures 'ready') -PolyglotPath (Join-Path $Fixtures 'polyglot') -ConfigPath $onCfg -RequireReady | Out-Null
    Case 'exit 7 does not depend on -RequireReady' 7 $LASTEXITCODE

    # A HUMAN RATING makes the flip legitimate - without this case the check
    # would be a permanent tripwire after an honest enablement, which is the
    # kind of guard people switch off.
    $rating = Join-Path $Tmp 'rating.yaml'
    Set-Content -Path $rating -Encoding ascii -Value @(
        'rated_by: drill-operator',
        'rated_at: 2026-08-30T00:00:00Z',
        'rated_report: fixtures/judge-dryrun/ready',
        'verdict: approve')
    & $Check -JournalsPath (Join-Path $Fixtures 'ready') -PolyglotPath (Join-Path $Fixtures 'polyglot') -ConfigPath $onCfg -RatingRecord $rating | Out-Null
    Case 'judge_enabled true + valid rating -> allowed' 0 $LASTEXITCODE

    $badRating = Join-Path $Tmp 'rating-bad.yaml'
    Set-Content -Path $badRating -Encoding ascii -Value @('rated_by: someone', 'verdict: approve')
    & $Check -JournalsPath (Join-Path $Fixtures 'ready') -PolyglotPath (Join-Path $Fixtures 'polyglot') -ConfigPath $onCfg -RatingRecord $badRating | Out-Null
    Case 'judge_enabled true + INCOMPLETE rating -> still 7' 7 $LASTEXITCODE

    # ---------------------------------------------------------------------
    # 10. The dry run WROTE NOTHING. Proven, not asserted in prose: the
    #     fixture tree's file list and hashes are identical afterwards.
    # ---------------------------------------------------------------------
    # Hash LINE-ENDING-NORMALIZED content, not raw bytes: git checkout settings
    # decide whether these text fixtures land LF or CRLF, and a manifest that
    # depended on that would go red in a fresh clone for a reason that has
    # nothing to do with the dry run.
    $after = Get-ChildItem -Recurse -File $Fixtures |
        Where-Object { $_.Name -ne 'MANIFEST.sha256' } | Sort-Object FullName |
        ForEach-Object { "$($_.FullName)|$(Get-NormalizedHash $_.FullName)" }
    $manifestPath = Join-Path $Fixtures 'MANIFEST.sha256'
    $expectedManifest = @(Get-Content $manifestPath -ErrorAction SilentlyContinue | Where-Object { $_ })
    # A MISSING MANIFEST IS A FAILURE, NOT A SKIP. This case is the one that
    # proves the read-only claim; if it cannot run, the drill does not know, and
    # a drill that does not know must not exit 0. It used to print [SKIP] and
    # still exit 0 with the case count silently down from 13 to 12.
    Case 'MANIFEST.sha256 is present' $true (Test-Path $manifestPath) "regenerate deliberately with -Regen"
    $afterRel = $after | ForEach-Object { $_ -replace [regex]::Escape($Fixtures + '\'), '' }
    $diff = if ($expectedManifest.Count -gt 0) {
        Compare-Object -ReferenceObject $expectedManifest -DifferenceObject $afterRel
    } else {
        # No baseline -> a sentinel difference, so this case is RED rather than
        # vacuously green against an empty reference set.
        'no manifest to compare against'
    }
    Case 'fixtures unchanged by the dry run' $null $diff ("differences: " + (($diff | ForEach-Object { if ($_.InputObject) { $_.InputObject } else { $_ } }) -join '; '))
} finally {
    if (-not $KeepTemp) { Remove-Item -Recurse -Force $Tmp -ErrorAction SilentlyContinue }
}

Write-Host ""
$failed = @($results | Where-Object { -not $_.pass })
if ($results.Count -ne $EXPECTED_CASES) {
    Write-Host ("CASE COUNT WRONG: ran {0}, expected {1}. A case stopped running - which is" -f $results.Count, $EXPECTED_CASES) -ForegroundColor Red
    Write-Host "  exactly how the read-only case once vanished while the drill still passed." -ForegroundColor Red
    $failed | Format-Table case, expected, actual, detail -AutoSize | Out-String | Write-Host
    exit 1
}
if ($failed.Count -eq 0) {
    Write-Host ("ALL {0} CASES PASS - the dry run fails when it should and passes when it should." -f $results.Count) -ForegroundColor Green
    exit 0
}
Write-Host ("{0} of {1} CASES FAILED" -f $failed.Count, $results.Count) -ForegroundColor Red
$failed | Format-Table case, expected, actual, detail -AutoSize | Out-String | Write-Host
exit 1
