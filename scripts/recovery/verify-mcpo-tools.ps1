<#
.SYNOPSIS
    Executable proof for emergency-recovery.ps1's Confirm-McpoTools (mcpo-resilience,
    2026-10-19). Runs NO recovery mode and touches NO docker daemon.

.DESCRIPTION
    The script under test is never dot-sourced (its main block would run a recovery). Only
    Confirm-McpoTools is lifted out of it with the PowerShell parser and defined in this
    scope; its five docker-facing commands are injected (it takes them as parameters), so no
    `docker` is ever invoked. Write-Log and Start-Sleep are stubs.

      R1 both proxies serve tools                      -> verified, nothing started or restarted
      R2 openbrain-mcp never healthy                   -> NOT verified, no start, no restart (a
                                                          proxy restarted into a dead upstream loads 0 tools)
      R3 proxies absent (up -d stopped at "dependency  -> started ONCE after mcp is healthy, then verified
         failed to start"), mcp healthy later
      R4 one proxy stays at 0, a restart fixes it      -> restarted ONCE, only that one, then verified
      R5 restart does not help                         -> one restart, the name is RETURNED, WARN logged
      R6 count unreadable                              -> never read as "has tools"
      R7 wiring: Start-OB1Stack and Reset-OB1Stack both call Confirm-McpoTools after the loop watch

    Exit code = number of failed checks.   -Script <path> drives another copy (e.g. the base: RED).
#>
[CmdletBinding()]
param([string]$Script = "")

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
if (-not $Script) { $Script = Join-Path $RepoRoot "scripts\recovery\emergency-recovery.ps1" }
$Script:Failures = 0
function Check([bool]$ok, [string]$m) {
    if ($ok) { Write-Host "  PASS  $m" -ForegroundColor Green } else { $Script:Failures++; Write-Host "  FAIL  $m" -ForegroundColor Red }
}

$tok = $null; $err = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($Script, [ref]$tok, [ref]$err)
if ($err -and $err.Count -gt 0) { throw "parse errors in ${Script}: $($err[0].Message)" }
$defs = @($ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $true))
$fn = $defs | Where-Object { $_.Name -eq 'Confirm-McpoTools' } | Select-Object -First 1
if ($fn) { . ([scriptblock]::Create($fn.Extent.Text)) }

$global:Logs = New-Object System.Collections.ArrayList
function Write-Log { param($Level, $Message) [void]$global:Logs.Add("$Level $Message") }
function Start-Sleep { param($Seconds) }

# A scripted host: $global:H holds tool counts per proxy, mcp health, and what was done to whom.
function New-Host([hashtable]$o) {
    $global:H = @{
        health = $o.health; running = $o.running; counts = $o.counts; afterRestart = $o.afterRestart
        started = @(); restarted = @(); healthPolls = 0
    }
}
$cmds = @{
    HealthCommand = { param($Name) $global:H.healthPolls++
        if ($global:H.health -is [int]) { if ($global:H.healthPolls -gt $global:H.health) { 'healthy' } else { 'starting' } }
        else { $global:H.health } }
    StateCommand = { param($Name) if ($global:H.running) { 'running' } else { 'exited' } }
    StartCommand = { param($Names) $global:H.started += ,@($Names); $global:H.running = $true }
    CountCommand = { param($Name, $Server) $global:H.counts[$Name] }
    RestartCommand = { param($Name) $global:H.restarted += $Name
        if ($global:H.afterRestart.ContainsKey($Name)) { $global:H.counts[$Name] = $global:H.afterRestart[$Name] } }
}
function Invoke-Confirm {
    $global:Logs.Clear()
    return , @(Confirm-McpoTools -WaitSeconds 1 -PollSeconds 0 -RestartWaitSeconds 1 @cmds)
}

Write-Host "== Confirm-McpoTools"
Check ($null -ne $fn) "Confirm-McpoTools exists in $(Split-Path -Leaf $Script)"
if ($fn) {
    New-Host @{ health = 'healthy'; running = $true; counts = @{ 'openbrain-mcpo' = '48'; 'openbrain-mcpo-ext' = '45' }; afterRestart = @{} }
    $r = Invoke-Confirm
    Check ($r[0].Count -eq 0 -and $global:H.started.Count -eq 0 -and $global:H.restarted.Count -eq 0) "R1 both proxies serve tools: verified, nothing started or restarted"

    New-Host @{ health = 'starting'; running = $false; counts = @{ 'openbrain-mcpo' = '0'; 'openbrain-mcpo-ext' = '0' }; afterRestart = @{} }
    $r = Invoke-Confirm
    Check ($r[0].Count -eq 2 -and $global:H.started.Count -eq 0 -and $global:H.restarted.Count -eq 0 -and ($global:Logs -match '^WARN openbrain-mcp is not healthy').Count -eq 1) "R2 mcp never healthy: not verified, nothing started or restarted, WARN logged"

    New-Host @{ health = 2; running = $false; counts = @{ 'openbrain-mcpo' = '48'; 'openbrain-mcpo-ext' = '45' }; afterRestart = @{} }
    $r = Invoke-Confirm
    Check ($r[0].Count -eq 0 -and $global:H.started.Count -eq 1 -and ($global:H.started[0] -join ',') -eq 'openbrain-mcpo,openbrain-mcpo-ext' -and $global:H.healthPolls -ge 3) "R3 absent proxies are started ONCE, only after mcp turned healthy, then verified"

    New-Host @{ health = 'healthy'; running = $true; counts = @{ 'openbrain-mcpo' = '0'; 'openbrain-mcpo-ext' = '45' }; afterRestart = @{ 'openbrain-mcpo' = '48' } }
    $r = Invoke-Confirm
    Check ($r[0].Count -eq 0 -and ($global:H.restarted -join ',') -eq 'openbrain-mcpo') "R4 one proxy at 0 is restarted ONCE (only it) and ends verified"

    New-Host @{ health = 'healthy'; running = $true; counts = @{ 'openbrain-mcpo' = '45'; 'openbrain-mcpo-ext' = '0' }; afterRestart = @{} }
    $r = Invoke-Confirm
    Check (($r[0] -join ',') -eq 'openbrain-mcpo-ext' -and ($global:H.restarted -join ',') -eq 'openbrain-mcpo-ext' -and ($global:Logs -match 'STILL serves no tools').Count -eq 1) "R5 a restart that does not help: one restart, the name is returned, WARN logged"

    foreach ($bad in @('', 'abc', '-1')) {
        New-Host @{ health = 'healthy'; running = $true; counts = @{ 'openbrain-mcpo' = $bad; 'openbrain-mcpo-ext' = '45' }; afterRestart = @{} }
        $r = Invoke-Confirm
        Check (($r[0] -join ',') -eq 'openbrain-mcpo') "R6 unreadable count '$bad' is never read as having tools"
    }
}

# R7 - wiring, by the parser: both OB1 up paths verify the proxies after the restart-loop watch.
foreach ($name in @('Start-OB1Stack', 'Reset-OB1Stack')) {
    $d = $defs | Where-Object { $_.Name -eq $name } | Select-Object -First 1
    $cmdNames = @()
    if ($d) { $cmdNames = @($d.FindAll({ param($n) $n -is [System.Management.Automation.Language.CommandAst] }, $true) | ForEach-Object { $_.GetCommandName() }) }
    $iLoop = [array]::IndexOf($cmdNames, 'Wait-ForRestartLoops'); $iMcpo = [array]::IndexOf($cmdNames, 'Confirm-McpoTools')
    Check ($iLoop -ge 0 -and $iMcpo -gt $iLoop) "R7 $name calls Confirm-McpoTools after Wait-ForRestartLoops"
}

Write-Host ""
Write-Host "FAILED CHECKS: $Script:Failures"
exit $Script:Failures
