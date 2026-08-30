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
            # cmd /c so compose's stderr WARNINGS (e.g. an unset optional var)
            # can't become PS 5.1 NativeCommandErrors under EAP=Stop.
            $argStr = ($p.A -join ' ')
            cmd /c "docker compose -f $($p.F) $argStr config -q 2>nul" | Out-Null
            if ($LASTEXITCODE -ne 0) {
                $msg = (cmd /c "docker compose -f $($p.F) $argStr config -q 2>&1" | Select-Object -First 2) -join ' '
                Write-Host "  [configs] COMPOSE INVALID: $($p.N) - $msg" -ForegroundColor Red
                $failed++
            }
        }
        if ($failed -eq 0) { Write-Host "  [configs] all $($projects.Count) compose projects render clean" }

        # --- stack-services.json drift verifier (D-12, 2026-08-22) -----------
        # The inventory's curated fields (critical/host_health/notes) stay
        # hand-owned, but the MACHINE guarantees the (container -> project)
        # rows are complete and correct against the rendered compose configs.
        $invPath = 'scripts\lib\stack-services.json'
        if (Test-Path $invPath) {
            $inv = Get-Content $invPath -Raw | ConvertFrom-Json
            $known = @{}
            foreach ($plane in $inv.planes.PSObject.Properties) {
                foreach ($row in $plane.Value) { $known[$row.container] = $row.project }
            }
            $renderTargets = @(
                @{ P = 'inference'; F = 'inference\docker-compose.yml'; A = @('--env-file', '.env.example') }
                @{ P = 'frontend';  F = 'frontend\docker-compose.yml';  A = @('--env-file', '.env.example') }
                @{ P = 'memory';    F = 'memory\docker-compose.yml';    A = @('--env-file', '.env.example') }
                @{ P = 'search';    F = 'search\docker-compose.yml';    A = @('--env-file', '.env.example') }
                @{ P = 'coder';     F = 'coder\docker-compose.yml';     A = @('--env-file', '.env.example') }
            )
            # OB1 renders only where its gitignored env exists (not in CI).
            if (Test-Path 'OB1\docker\.env') {
                $renderTargets += @{ P = 'open-brain'; F = 'OB1\docker\docker-compose.yml'; A = @() }
            }
            $drift = @()
            foreach ($rt in $renderTargets) {
                # Regex extraction, NOT ConvertFrom-Json: PS 5.1's parser rejects
                # the rendered config's case-duplicate env keys (HTTP_PROXY vs
                # http_proxy on open-terminal). container_name lines are enough.
                $argStr = ($rt.A -join ' ')
                $raw = (cmd /c "docker compose -f $($rt.F) $argStr config --format json 2>nul") -join "`n"
                if (-not $raw) { continue }
                $names = [regex]::Matches($raw, '"container_name":\s*"([^"]+)"') |
                    ForEach-Object { $_.Groups[1].Value } | Sort-Object -Unique
                foreach ($cname in $names) {
                    if (-not $known.ContainsKey($cname)) {
                        $drift += "MISSING from stack-services.json: $cname (project $($rt.P))"
                    }
                    elseif ($known[$cname] -ne $rt.P) {
                        $drift += "WRONG project for ${cname}: json says '$($known[$cname])', compose says '$($rt.P)'"
                    }
                }
            }
            if ($drift.Count) {
                foreach ($d in $drift) { Write-Host "  [configs] INVENTORY DRIFT: $d" -ForegroundColor Red }
                Write-Host "  [configs] fix scripts\lib\stack-services.json (curated fields are yours; container/project rows must match compose)" -ForegroundColor Yellow
                $failed += $drift.Count
            }
            else { Write-Host "  [configs] stack-services.json inventory matches the compose configs" }
        }
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

# --- 3. staged JSON, under the STRICTER of the two parsers -------------------
#
# WHY PYTHON AND NOT ConvertFrom-Json. PowerShell's JSON parser is lenient: it accepts a
# raw newline inside a string literal, which is invalid JSON. Python's does not. That is
# not academic here - scripts/agent-harness/harness.config.json is read by BOTH
# config.ps1 and config.py (it says so in its own header), and a multi-line comment string
# written into it parsed fine in every PowerShell path while json.loads rejected it. The
# result would have been a harness that worked from the scripts and broke in the Mattermost
# bridge, with nothing at commit time to say so.
#
# So the gate uses the stricter parser deliberately. If python is unavailable it says so
# and skips - a check that cannot run must never masquerade as one that passed.
$jsonStaged = @($staged | Where-Object { $_ -match '\.json$' -and (Test-Path $_) })
if ($jsonStaged.Count -gt 0) {
    $py = (Get-Command python -ErrorAction SilentlyContinue)
    if (-not $py) {
        Write-Host "  [configs] python not found - staged JSON NOT validated (this is a gap, not a pass)" -ForegroundColor Yellow
    } else {
        $jsonBad = 0
        foreach ($f in $jsonStaged) {
            $prev = $ErrorActionPreference
            $ErrorActionPreference = 'Continue'
            $out = & python -c "import json,sys; json.load(open(sys.argv[1], encoding='utf-8'))" $f 2>&1
            $code = $LASTEXITCODE
            $ErrorActionPreference = $prev
            if ($code -ne 0) {
                Write-Host "  [configs] INVALID JSON: $f" -ForegroundColor Red
                Write-Host ("             " + (($out | Out-String).Trim() -split "`n" | Select-Object -Last 1)) -ForegroundColor Red
                $failed++; $jsonBad++
            }
        }
        if ($jsonBad -eq 0) { Write-Host "  [configs] $($jsonStaged.Count) staged .json file(s) are strict-valid" }
    }
}

Pop-Location
if ($failed -gt 0) { exit 1 }
exit 0
