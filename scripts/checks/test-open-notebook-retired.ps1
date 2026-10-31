# test-open-notebook-retired.ps1 - the open_notebook app stays stopped when it is retired
# (intake-gate-relay round 2, D-1; operator decision 2026-10-31: the app is stopped before intake
# writes bronze, DESIGN-intake section 12 item 1).
#
# THE SWITCH is the ob1 compose profile `notebook-app` (open_notebook alone; surrealdb and
# open-notebook-backup stay in `notebook`). It is ON when it is in .stack\state.json
# (planes.ob1.profiles) or in COMPOSE_PROFILES of OB1\docker\.env - the two sources the driver
# unions. This test covers the two PowerShell starters:
#   W  stack-watchdog.ps1: its open_notebook step must not `compose up` / `restart` a retired app,
#      and must still repair it when the switch is ON (the control);
#   R  emergency-recovery.ps1: the --profile flags its OB1 `up` passes must not render open_notebook
#      when retired, and must when ON; its OB1 container count must not count a retired app.
#   S  restore-from-snapshot.ps1 (round 3, TG2-1): Phase 5 must not `compose start` a retired app.
#   C  the CALL SITES (round 3, TG2-3): Invoke-HealthCheck reaches open_notebook only through
#      Invoke-OpenNotebookCheck (D4); both OB1 `up` steps of emergency recovery take their flags from
#      Get-OB1StartProfiles (D6/D7); Phase 5 of the restore takes its list from Get-RestoreStartList;
#      the switch-ON repair keeps `--profile notebook` (D12).
# The functions are loaded from the scripts by AST (never their main blocks); docker is a stub that
# records calls, except one real `docker compose config --services` render (no daemon needed).
#
#   powershell -NoProfile -File scripts\checks\test-open-notebook-retired.ps1 [-Repo <tree under test>]
# Exit code = number of failed cases. ASCII only.
param([string]$Repo = (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)))

$ErrorActionPreference = 'Stop'
$Repo = (Resolve-Path $Repo).Path
$failed = 0
function Say([string]$Name, [bool]$Ok, [string]$Detail = '') {
    if ($Ok) { Write-Host "  [OK]   $Name" } else { Write-Host "  [FAIL] $Name $Detail"; $script:failed++ }
}

function Get-Defs([string]$Path, [string[]]$Functions, [string[]]$Vars) {
    $tokens = $null; $errs = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile($Path, [ref]$tokens, [ref]$errs)
    $out = @()
    foreach ($f in $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $true)) {
        if ($Functions -contains $f.Name) { $out += $f.Extent.Text }
    }
    foreach ($a in $ast.EndBlock.Statements) {
        if ($a -is [System.Management.Automation.Language.AssignmentStatementAst]) {
            $lhs = $a.Left.Extent.Text
            foreach ($v in $Vars) { if ($lhs -eq $v) { $out += $a.Extent.Text } }
        }
    }
    return ($out -join "`n")
}

function New-Root([string[]]$StateProfiles, [string]$EnvProfiles) {
    $r = Join-Path ([System.IO.Path]::GetTempPath()) ("onr-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
    New-Item -ItemType Directory -Force (Join-Path $r '.stack'), (Join-Path $r 'OB1\docker') | Out-Null
    $state = @{ planes = @{ ob1 = @{ profiles = $StateProfiles } } } | ConvertTo-Json -Depth 5
    Set-Content -Path (Join-Path $r '.stack\state.json') -Value $state -Encoding ASCII
    if ($EnvProfiles) { Set-Content -Path (Join-Path $r 'OB1\docker\.env') -Value "COMPOSE_PROFILES=$EnvProfiles" -Encoding ASCII }
    return $r
}

$retired = @('idea-refinery', 'research', 'wiki', 'notebook')   # this host's set, without the app
$on      = $retired + @('notebook-app')

# ---------------------------------------------------------------- W: the watchdog
Write-Host "W stack-watchdog.ps1"
$wd = Join-Path $Repo 'scripts\checks\stack-watchdog.ps1'
$wdDefs = Get-Defs $wd @('Test-OpenNotebookHealth', 'Repair-OpenNotebook', 'Test-OpenNotebookDeployed', 'Invoke-OpenNotebookCheck') @()
foreach ($case in @(@{ Name = 'retired (notebook-app off)'; P = $retired; Env = ''; WantStart = $false },
                    @{ Name = 'retired, env file without notebook-app'; P = @(); Env = 'research,wiki,notebook'; WantStart = $false },
                    @{ Name = 'control: notebook-app ON in the state file'; P = $on; Env = ''; WantStart = $true },
                    @{ Name = 'control: notebook-app ON in OB1\docker\.env'; P = @(); Env = 'notebook,notebook-app'; WantStart = $true })) {
    $root = New-Root $case.P $case.Env
    $calls = [System.Collections.ArrayList]::new()
    $sb = [scriptblock]::Create(@"
param(`$PROJECT_DIR, `$calls)
function Write-LogEntry { param(`$m, `$l) }
function Start-Sleep { param(`$s) }
function Test-ServiceHealth { param(`$n) return (`$n -ne 'open_notebook') }
function docker { [void]`$calls.Add((`$args -join ' ')); `$global:LASTEXITCODE = 1 }
$wdDefs
if (Get-Command Invoke-OpenNotebookCheck -ErrorAction SilentlyContinue) { Invoke-OpenNotebookCheck | Out-Null }
else { if (-not (Test-OpenNotebookHealth)) { Repair-OpenNotebook | Out-Null } }   # the base's inline step
"@)
    Push-Location $root
    try { & $sb $root $calls } finally { Pop-Location }
    $started = @($calls | Where-Object { $_ -match 'up -d open_notebook' -or $_ -match '^restart open_notebook' -or $_ -match '^start open_notebook' })
    if ($case.WantStart) {
        Say "W $($case.Name): the watchdog starts it" ($started.Count -gt 0) ("calls: " + ($calls -join ' | '))
        $up = @($calls | Where-Object { $_ -match 'up -d open_notebook' })
        Say "W $($case.Name): ...with BOTH profiles (D12: notebook carries its depends_on)" (
            $up.Count -gt 0 -and ($up[0] -match '--profile notebook( |$)') -and ($up[0] -match '--profile notebook-app')) ($up -join ' | ')
    }
    else { Say "W $($case.Name): the watchdog does NOT start it" ($started.Count -eq 0) ("calls: " + ($started -join ' | ')) }
    Remove-Item -Recurse -Force $root
}

# ---------------------------------------------------------------- R: emergency recovery
Write-Host "R emergency-recovery.ps1"
$er = Join-Path $Repo 'scripts\recovery\emergency-recovery.ps1'
$erDefs = Get-Defs $er @('Test-OB1ProfileEnabled', 'Get-OB1StartProfiles', 'Get-OB1ExpectedCount') @(
    '$Script:OB1Compose', '$Script:OB1Profiles', '$Script:OB1PantryProfile', '$Script:OB1IntakeProfile',
    '$Script:OB1NotebookAppProfile', '$Script:OB1OptInProfiles', '$Script:OB1Services', '$Script:OB1OptionalServices',
    '$Script:OB1OptionalServiceProfile')
$compose = Join-Path $Repo 'OB1\docker\docker-compose.yml'
$example = Join-Path $Repo 'OB1\docker\.env.example'
$reset = Join-Path ([System.IO.Path]::GetTempPath()) ("onr-reset-" + [guid]::NewGuid().ToString('N').Substring(0, 8) + '.yml')
# open_notebook's `env_file: ./.env` is required; a tree without OB1\docker\.env renders through this
Set-Content -Path $reset -Encoding ASCII -Value "services:`n  open_notebook:`n    env_file: !reset []`n"
foreach ($case in @(@{ Name = 'retired'; P = $retired; Want = $false }, @{ Name = 'control: notebook-app ON'; P = $on; Want = $true })) {
    $root = New-Root $case.P ''
    $sb = [scriptblock]::Create(@"
$erDefs
`$flags = Get-OB1StartProfiles
`$count = if (Get-Command Get-OB1ExpectedCount -ErrorAction SilentlyContinue) { Get-OB1ExpectedCount } else {
    `$c = `$Script:OB1Services.Count; foreach (`$s in `$Script:OB1OptionalServices) { if (Test-OB1ProfileEnabled `$Script:OB1OptionalServiceProfile[`$s]) { `$c++ } }; `$c }
`$counted = (@(`$Script:OB1Services) -contains 'open_notebook') -or ((@(`$Script:OB1OptionalServices) -contains 'open_notebook') -and (Test-OB1ProfileEnabled `$Script:OB1OptionalServiceProfile['open_notebook']))
,@(`$flags, `$counted)
"@)
    Push-Location $root
    try { $res = & $sb } finally { Pop-Location }
    $flags = @($res[0]); $counted = [bool]$res[1]
    $old = $env:COMPOSE_PROFILES; Remove-Item Env:COMPOSE_PROFILES -ErrorAction SilentlyContinue
    $files = @('-f', $compose); if (-not (Test-Path (Join-Path $Repo 'OB1\docker\.env'))) { $files += @('-f', $reset) }
    $svcs = & docker compose @files --env-file $example @flags config --services 2>$null
    if ($old) { $env:COMPOSE_PROFILES = $old }
    $has = @($svcs) -contains 'open_notebook'
    $keeps = (@($svcs) -contains 'surrealdb') -and (@($svcs) -contains 'open-notebook-backup')
    if ($case.Want) {
        Say "R $($case.Name): its OB1 up renders open_notebook and counts it" ($has -and $counted) "flags: $($flags -join ' '); rendered: $has; counted: $counted"
    } else {
        Say "R $($case.Name): its OB1 up does NOT render open_notebook" (-not $has) "flags: $($flags -join ' ')"
        Say "R $($case.Name): ...and does not count it as an expected container" (-not $counted)
        Say "R $($case.Name): surrealdb and open-notebook-backup still render" $keeps
    }
    Remove-Item -Recurse -Force $root
}
Remove-Item -Force $reset

# ---------------------------------------------------------------- S: restore-from-snapshot Phase 5
Write-Host "S restore-from-snapshot.ps1"
$rs = Join-Path $Repo 'scripts\backup\restore-from-snapshot.ps1'
$rsDefs = Get-Defs $rs @('Test-NotebookAppEnabled', 'Get-RestoreStartList') @()
$listed = @('openbrain-mcp', 'openbrain-wiki', 'open_notebook')
foreach ($case in @(@{ Name = 'retired'; P = $retired; Env = 'research,wiki,notebook'; Want = $false },
                    @{ Name = 'control: notebook-app ON'; P = $on; Env = ''; Want = $true })) {
    $root = New-Root $case.P $case.Env
    $sb = [scriptblock]::Create(@"
function Write-Log { param(`$m, `$l) }
$rsDefs
if (Get-Command Get-RestoreStartList -ErrorAction SilentlyContinue) { @(Get-RestoreStartList @('$($listed -join "','")')) }
else { @('$($listed -join "','")') }   # the base: Phase 5 starts every listed container
"@)
    Push-Location $root
    try { $got = @(& $sb) } finally { Pop-Location }
    $has = $got -contains 'open_notebook'
    if ($case.Want) { Say "S $($case.Name): Phase 5 starts open_notebook" $has ($got -join ',') }
    else {
        Say "S $($case.Name): Phase 5 does NOT start open_notebook" (-not $has) ($got -join ',')
        Say "S $($case.Name): ...and still starts the other listed containers" (($got -contains 'openbrain-mcp') -and ($got -contains 'openbrain-wiki'))
    }
    Remove-Item -Recurse -Force $root
}

# ---------------------------------------------------------------- C: the call sites
Write-Host "C call sites"
function Get-FnText([string]$Path, [string]$Name) {
    $tokens = $null; $errs = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile($Path, [ref]$tokens, [ref]$errs)
    $f = $ast.Find({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $Name }, $true)
    if ($f) { return $f.Body.Extent.Text } else { return '' }
}
function Strip-Comments([string]$Text) { return (($Text -split "`n") | ForEach-Object { ($_ -replace '#.*$', '') }) -join "`n" }
$hc = Strip-Comments (Get-FnText $wd 'Invoke-HealthCheck')
Say "C D4: Invoke-HealthCheck calls Invoke-OpenNotebookCheck" ($hc -match '\bInvoke-OpenNotebookCheck\b')
Say "C D4: ...and names no other open_notebook probe or repair" (-not ($hc -match 'Test-OpenNotebookHealth|Repair-OpenNotebook|open_notebook'))
foreach ($fn in @('Start-OB1Stack', 'Reset-OB1Stack')) {
    $lines = (Strip-Comments (Get-FnText $er $fn)) -split "`n"
    $ups = @(); $last = ''
    foreach ($ln in $lines) {
        if ($ln -match '\$prof\s*=\s*(.+)$') { $last = $Matches[1].Trim() }
        if ($ln -match 'compose' -and $ln -match '\bup\b') { $ups += ,@($ln.Trim(), $last) }
    }
    $ok = ($ups.Count -gt 0)
    foreach ($u in $ups) { if (-not ($u[0] -match '@prof\b' -and $u[1] -eq 'Get-OB1StartProfiles')) { $ok = $false } }
    Say "C D6/D7: every OB1 ``up`` in $fn takes its flags from Get-OB1StartProfiles" $ok (($ups | ForEach-Object { "$($_[0]) <- $($_[1])" }) -join ' | ')
}
$p5 = (Get-Content -Raw $rs) -replace "`r", ''
$i = $p5.IndexOf('Phase 5'); $seg = if ($i -ge 0) { $p5.Substring($i) } else { '' }
$seg = Strip-Comments $seg
Say "C TG2-1: restore Phase 5 takes its start list from Get-RestoreStartList" ($seg -match 'foreach \(\$container in \(Get-RestoreStartList ') ($seg.Substring(0, [Math]::Min(300, $seg.Length)))

Write-Host "$failed failed"
exit $failed
