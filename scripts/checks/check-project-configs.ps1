# check-project-configs.ps1 - pre-commit structural validation (Part K.8, 2026-08-21).
#
# Two cheap gates, each run ONLY when the staged changes make them relevant:
#   1. compose validation - any staged *.yml/*.yaml => render every project's
#      compose file with `docker compose config -q` against .env.example
#      (kept complete on purpose, v3 A.4). Catches exactly the drift class
#      the Part K restructure kept finding by hand: broken includes, dead
#      depends_on, missing env guards, bad network refs.
#   2. PowerShell parse - staged *.ps1 files are tokenized with PSParser so a
#      syntax error can never reach a commit (the ops plane is PS 5.1).
#
# EXIT: 0 = clean/skipped, 1 = blocked. Skips gracefully if docker is absent.

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Push-Location $repoRoot

$staged = @(& git diff --cached --name-only --diff-filter=ACM) | Where-Object { $_ }
if (-not $staged) { Write-Host "  [configs] nothing staged - skip"; Pop-Location; exit 0 }

$failed = 0

# --- 1. compose project validation -----------------------------------------
$ymlStaged = @($staged | Where-Object { $_ -match '\.(yml|yaml)$' })
if ($ymlStaged.Count -gt 0) {
    $dockerOk = $true
    try { docker compose version | Out-Null } catch { $dockerOk = $false }
    if (-not $dockerOk -or $LASTEXITCODE -ne 0) {
        Write-Host "  [configs] docker compose unavailable - compose validation skipped" -ForegroundColor Yellow
    }
    else {
        # (OB1 + agent-org validate against their own gitignored env files and
        # are covered by their own workflows; the portal needs its profile.)
        $projects = @(
            @{ N = 'anchor';    F = 'docker-compose.yml';           A = @('--env-file', '.env.example') }
            @{ N = 'inference'; F = 'inference\docker-compose.yml'; A = @('--env-file', '.env.example') }
            @{ N = 'frontend';  F = 'frontend\docker-compose.yml';  A = @('--env-file', '.env.example') }
            @{ N = 'memory';    F = 'memory\docker-compose.yml';    A = @('--env-file', '.env.example') }
            @{ N = 'search';    F = 'search\docker-compose.yml';    A = @('--env-file', '.env.example') }
            @{ N = 'coder';     F = 'coder\docker-compose.yml';     A = @('--env-file', '.env.example') }
            @{ N = 'portal';    F = 'portal\docker-compose.yml';    A = @('--env-file', '.env.example', '--profile', 'internet') }
        )
        foreach ($p in $projects) {
            & docker compose -f $p.F @($p.A) config -q 2>$null | Out-Null
            if ($LASTEXITCODE -ne 0) {
                # re-run without -q to surface the reason
                $msg = (& docker compose -f $p.F @($p.A) config -q 2>&1 | Select-Object -First 2) -join ' '
                Write-Host "  [configs] COMPOSE INVALID: $($p.N) - $msg" -ForegroundColor Red
                $failed++
            }
        }
        if ($failed -eq 0) { Write-Host "  [configs] all $($projects.Count) compose projects render clean" }
    }
}

# --- 2. staged PowerShell parse ---------------------------------------------
$ps1Staged = @($staged | Where-Object { $_ -match '\.ps1$' -and (Test-Path $_) })
foreach ($f in $ps1Staged) {
    $errs = $null
    [System.Management.Automation.PSParser]::Tokenize((Get-Content $f -Raw), [ref]$errs) | Out-Null
    if ($errs.Count -gt 0) {
        Write-Host "  [configs] PS1 PARSE ERROR: $f - $($errs[0].Message)" -ForegroundColor Red
        $failed++
    }
}
if ($ps1Staged.Count -gt 0 -and $failed -eq 0) {
    Write-Host "  [configs] $($ps1Staged.Count) staged .ps1 file(s) parse clean"
}

Pop-Location
if ($failed -gt 0) { exit 1 }
exit 0
