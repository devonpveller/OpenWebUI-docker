# check-judge-dryrun.ps1 - the judge dry run (U5, PLAN.md section 2).
#
# Reports what little-coder's Observer judge WOULD mint from the journals that
# already exist, WITHOUT minting anything and without a single LLM call. It is
# the executable half of
# documentation/implementation-guide/little-coder/JUDGE-CALIBRATION.md, the
# calibration plan for flipping `observer.judge_enabled`.
#
# It never writes to the cohort store or the skill library, and it never sets
# the flag. Read-only, by construction: it runs the real projection
# (littlecoder.cohorts.rebuild) over a read-only view of the journals.
#
# MODES
#   -Container <name>     run inside a live little-coder-shaped container
#                         (default: little-coder). Also: ao-worker-1, ao-worker-2.
#   -JournalsPath <dir>   run on the host against a journals directory
#                         (fixtures, restored backups, a `docker cp` copy).
#
# EXIT CODES (from scripts/checks/lib/judge_dryrun.py -- kept identical so the
# wrapper never launders a failure into a pass):
#   0  a verdict was produced
#   1  verdict NOT-READY and -RequireReady was passed
#   2  usage
#   3  CANNOT TELL - no readable journal evidence
#   4  CANNOT TELL - the cohort store already holds clusters, so the stub
#      similarity projection is no longer what a judge-enabled daemon sees
#   5  CANNOT TELL - littlecoder not importable / config unreadable
#   6  CANNOT TELL - the container or the probe could not be reached
#
# EXAMPLES
#   .\scripts\checks\check-judge-dryrun.ps1
#   .\scripts\checks\check-judge-dryrun.ps1 -Container ao-worker-1
#   .\scripts\checks\check-judge-dryrun.ps1 -RequireReady        # enablement gate
#   .\scripts\checks\check-judge-dryrun.ps1 -OutDir .\dryrun -EmitPrompts

[CmdletBinding()]
param(
    [string]$Container = 'little-coder',
    [string]$JournalsPath,
    [string]$CohortsPath,
    [string]$SkillPath,
    [string]$PolyglotPath,
    [string]$ConfigPath,
    [int]$MinPool = 3,
    [int]$MinDistinct = 3,
    [double]$MaxDegenerate = 0.34,
    [switch]$RequireReady,
    [switch]$EmitPrompts,
    [string]$OutDir,
    [switch]$Json
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$Probe = Join-Path $PSScriptRoot 'lib\judge_dryrun.py'

if (-not (Test-Path $Probe)) {
    Write-Host "CANNOT TELL: probe not found at $Probe" -ForegroundColor Red
    exit 6
}

# --- build the probe argument list -----------------------------------------
$ProbeArgs = @(
    '--min-pool', "$MinPool",
    '--min-distinct', "$MinDistinct",
    '--max-degenerate', "$MaxDegenerate"
)
if ($RequireReady) { $ProbeArgs += '--require-ready' }
if ($EmitPrompts) { $ProbeArgs += '--emit-prompts' }

$Raw = $null
if ($JournalsPath) {
    # ---- host mode ---------------------------------------------------------
    $ProbeArgs += @('--journals', $JournalsPath)
    if ($CohortsPath) { $ProbeArgs += @('--cohorts', $CohortsPath) }
    if ($SkillPath) { $ProbeArgs += @('--skill', $SkillPath) }
    if ($PolyglotPath) { $ProbeArgs += @('--polyglot', $PolyglotPath) }
    if ($ConfigPath) { $ProbeArgs += @('--config', $ConfigPath) }
    $ProbeArgs += @('--src', (Join-Path $RepoRoot 'little-coder\src'))

    Write-Host "judge dry run - host mode, journals: $JournalsPath" -ForegroundColor Cyan
    $Raw = & python $Probe @ProbeArgs 2>&1
    $Code = $LASTEXITCODE
} else {
    # ---- container mode ----------------------------------------------------
    # Container paths are the config's declared mount points (design section 12.8):
    # journals.dir, paths.cohorts_dir, paths.skill_dir, paths.polyglot_dir.
    $ProbeArgs += @(
        '--journals', '/var/lib/little-coder/journals',
        '--cohorts', '/var/lib/little-coder/cohorts',
        '--skill', '/var/lib/little-coder/skill',
        '--polyglot', '/var/lib/little-coder/polyglot',
        '--config', '/app/config/little-coder.config.yaml'
    )

    $Running = & docker ps --filter "name=^/$Container$" --format '{{.Names}}' 2>$null
    if ($Running -ne $Container) {
        Write-Host "CANNOT TELL: container '$Container' is not running" -ForegroundColor Red
        Write-Host "  (start it, or use -JournalsPath against a restored backup)" -ForegroundColor Yellow
        exit 6
    }

    Write-Host "judge dry run - container mode: $Container" -ForegroundColor Cyan
    # Feed the probe on stdin so nothing is copied INTO the container - the
    # container's filesystem is not touched at all.
    $Raw = Get-Content -Raw $Probe | & docker exec -i $Container python - @ProbeArgs 2>&1
    $Code = $LASTEXITCODE
}

$Text = ($Raw | Out-String)
if ([string]::IsNullOrWhiteSpace($Text)) {
    Write-Host "CANNOT TELL: the probe produced no output (exit $Code)" -ForegroundColor Red
    exit 6
}

if ($Json) {
    Write-Output $Text
}

$Report = $null
try { $Report = $Text | ConvertFrom-Json } catch { $Report = $null }
if ($null -eq $Report) {
    Write-Host "CANNOT TELL: the probe did not return JSON (exit $Code)" -ForegroundColor Red
    Write-Host $Text
    exit 6
}

if ($OutDir) {
    if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Path $OutDir -Force | Out-Null }
    $Stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
    $Target = Join-Path $OutDir "judge-dryrun-$Container-$Stamp.json"
    Set-Content -Path $Target -Value $Text -Encoding utf8
    Write-Host "  rating packet: $Target" -ForegroundColor DarkCyan
}

if ($Report.status -eq 'cannot_tell') {
    Write-Host ""
    Write-Host "CANNOT TELL (exit $Code): $($Report.reason)" -ForegroundColor Red
    Write-Host "A dry run that does not know is not a pass." -ForegroundColor Yellow
    exit $Code
}

# --- human-readable rendering ----------------------------------------------
Write-Host ""
Write-Host "  config              : $($Report.config)"
Write-Host "  judge_enabled       : $($Report.judge_enabled_in_config)"
Write-Host "  journal records     : $($Report.records.total) (errors $($Report.records.errors), outcomes $($Report.records.outcomes), tool_calls $($Report.records.tool_calls))"
Write-Host "  occurrences         : $($Report.totals.occurrences) across $($Report.totals.pools) pool(s)"
Write-Host "  judge invoked on    : $($Report.totals.pools_judge_would_be_invoked_on) pool(s)  (min_pool=$($Report.thresholds.min_pool))"
Write-Host "  mintable pools      : $($Report.totals.pools_mintable)"
Write-Host "  skill library files : $($Report.skill_library_files)"
Write-Host "  polyglot corpus     : $($Report.polyglot_corpus_files) file(s)"
Write-Host ""
foreach ($p in $Report.pools) {
    $mark = if ($p.mintable) { 'MINTABLE' } elseif ($p.judge_would_be_invoked) { 'invoked, NOT mintable' } else { 'below min_pool' }
    Write-Host ("  [{0}|{1}] n={2} distinct={3} degenerate={4} -> {5}" -f `
        $p.lang, $p.task_shape, $p.pool_size, $p.distinct_signals, $p.degenerate_ratio, $mark)
    foreach ($s in $p.sample) { Write-Host "        signal: '$s'" -ForegroundColor DarkGray }
}
Write-Host ""
if ($Report.blockers.Count -gt 0) {
    Write-Host "BLOCKERS:" -ForegroundColor Yellow
    foreach ($b in $Report.blockers) { Write-Host "  - $b" -ForegroundColor Yellow }
    Write-Host ""
}
Write-Host "  would_mint: $($Report.would_mint_note)" -ForegroundColor DarkGray
Write-Host ""

if ($Report.verdict -eq 'READY-FOR-RATING') {
    Write-Host "VERDICT: READY-FOR-RATING - the mechanical preconditions hold." -ForegroundColor Green
    Write-Host "         Design section 13 still requires a HUMAN rating of the emitted" -ForegroundColor Green
    Write-Host "         prompts before judge_enabled is flipped. This script never flips it." -ForegroundColor Green
} else {
    Write-Host "VERDICT: NOT-READY - enabling the judge now would mint from noise." -ForegroundColor Red
}
exit $Code
