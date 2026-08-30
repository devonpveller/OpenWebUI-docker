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
# Exit 0 = all cases as expected. Exit 1 = at least one case wrong.

[CmdletBinding()]
param([switch]$KeepTemp)

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
    $bytes = [System.IO.File]::ReadAllBytes($path) | Where-Object { $_ -ne 13 }
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
    $expectedManifest = Get-Content (Join-Path $Fixtures 'MANIFEST.sha256') -ErrorAction SilentlyContinue
    if ($expectedManifest) {
        $afterRel = $after | ForEach-Object { $_ -replace [regex]::Escape($Fixtures + '\'), '' }
        $diff = Compare-Object -ReferenceObject ($expectedManifest | Where-Object { $_ }) -DifferenceObject $afterRel
        Case 'fixtures unchanged by the dry run' $null $diff ("differences: " + (($diff | ForEach-Object { $_.InputObject }) -join '; '))
    } else {
        Write-Host "  [SKIP] fixtures-unchanged: no MANIFEST.sha256 (regenerate with -Regen)" -ForegroundColor Yellow
    }
} finally {
    if (-not $KeepTemp) { Remove-Item -Recurse -Force $Tmp -ErrorAction SilentlyContinue }
}

Write-Host ""
$failed = @($results | Where-Object { -not $_.pass })
if ($failed.Count -eq 0) {
    Write-Host ("ALL {0} CASES PASS - the dry run fails when it should and passes when it should." -f $results.Count) -ForegroundColor Green
    exit 0
}
Write-Host ("{0} of {1} CASES FAILED" -f $failed.Count, $results.Count) -ForegroundColor Red
$failed | Format-Table case, expected, actual, detail -AutoSize | Out-String | Write-Host
exit 1
