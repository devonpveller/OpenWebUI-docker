# check-watchdog-repair-targets.ps1
#
# WHY THIS EXISTS (2026-08-28). Part K split the stack into per-plane compose
# PROJECTS and left the root `ai-stack` project a pure network anchor with ZERO
# services. stack-watchdog.ps1's DETECTION half was migrated at K.10 to a
# name-based `docker inspect`; its REMEDIATION half was not, so 22 self-heal
# paths spent a week issuing `docker compose up -d <name>` against a project
# that declares nothing. Nobody noticed because the failure is silent three
# times over: wrong project; an un-redirected native stderr does NOT throw under
# $ErrorActionPreference='Stop' in PS 5.1; and the old code never read
# $LASTEXITCODE.
#
# This script is the REPRODUCTION for that fix and the regression guard after
# it: it FAILS against the pre-fix watchdog and PASSES against the fixed one.
# It answers one question - can every container this monitor claims to self-heal
# actually be started by the compose project the inventory says owns it?
#
# It is NOT wired into .githooks/pre-commit; that is the operator's call.
#
# Usage:
#   .\check-watchdog-repair-targets.ps1                       # the sibling watchdog
#   .\check-watchdog-repair-targets.ps1 -WatchdogPath <file>  # e.g. a HEAD checkout
#   .\check-watchdog-repair-targets.ps1 -SkipDocker           # static checks only
#
# Exit 0 = every managed container resolves and is declared. Exit 1 = otherwise.

[CmdletBinding()]
param(
    [string]$WatchdogPath = '',
    [string]$InventoryPath = '',
    [switch]$SkipDocker
)

# DELIBERATELY NOT 'Stop'. This script shells out to docker and reads its exit
# code; under 'Stop' a redirected native stderr write becomes a terminating
# NativeCommandError in PS 5.1 - the very trap that hid the bug being checked.
$ErrorActionPreference = 'Continue'

$ScriptDir = Split-Path -Parent $PSCommandPath
$RepoRoot = Split-Path -Parent (Split-Path -Parent $ScriptDir)
if (-not $WatchdogPath) { $WatchdogPath = Join-Path $ScriptDir 'stack-watchdog.ps1' }
if (-not $InventoryPath) { $InventoryPath = Join-Path $RepoRoot 'scripts\lib\stack-services.json' }

$Failures = New-Object System.Collections.ArrayList
function Add-Failure([string]$Text) { [void]$Failures.Add($Text); Write-Host "  [FAIL] $Text" -ForegroundColor Red }
function Write-Ok([string]$Text) { Write-Host "  [ok]   $Text" -ForegroundColor DarkGray }

Write-Host ''
Write-Host 'watchdog repair-target check' -ForegroundColor Cyan
Write-Host "  watchdog : $WatchdogPath"
Write-Host "  inventory: $InventoryPath"
Write-Host ''

if (-not (Test-Path $WatchdogPath)) { Write-Host "watchdog not found: $WatchdogPath" -ForegroundColor Red; exit 1 }
if (-not (Test-Path $InventoryPath)) { Write-Host "inventory not found: $InventoryPath" -ForegroundColor Red; exit 1 }

# --- CHECK 1: the watchdog parses -------------------------------------------
Write-Host '1. PowerShell parse' -ForegroundColor White
$ParseErrors = $null
$Ast = [System.Management.Automation.Language.Parser]::ParseFile(
    (Resolve-Path $WatchdogPath).Path, [ref]$null, [ref]$ParseErrors)
if ($ParseErrors -and $ParseErrors.Count -gt 0) {
    foreach ($e in $ParseErrors) { Add-Failure "parse error line $($e.Extent.StartLineNumber): $($e.Message)" }
    Write-Host ''
    Write-Host 'PARSE FAILED - later checks would be meaningless.' -ForegroundColor Red
    exit 1
}
Write-Ok 'parses with 0 errors'

# --- CHECK 2: no bare `docker compose` --------------------------------------
# The AST is used rather than grep on purpose: grep also matches `docker compose`
# inside LOG STRINGS, which are advice to the operator, not invocations. Only
# real CommandAst nodes count here.
Write-Host ''
Write-Host "2. every 'docker compose' invocation names its plane file" -ForegroundColor White
$Commands = $Ast.FindAll({ $args[0] -is [System.Management.Automation.Language.CommandAst] }, $true)
$BareCount = 0
foreach ($c in $Commands) {
    $els = @($c.CommandElements)
    if ($els.Count -lt 2) { continue }
    if ($els[0].Extent.Text -ne 'docker') { continue }
    if ($els[1].Extent.Text -ne 'compose') { continue }
    $hasFile = $false
    foreach ($e in $els) { if ($e.Extent.Text -eq '-f') { $hasFile = $true } }
    if (-not $hasFile) {
        $BareCount++
        Add-Failure ("bare 'docker compose' at line {0}: {1}" -f $c.Extent.StartLineNumber, $c.Extent.Text.Trim())
    }
}
if ($BareCount -eq 0) { Write-Ok 'no bare invocation (all carry -f)' }

# --- CHECK 3: gather the containers this watchdog self-heals ----------------
# Both the current parameter name (-Container) and the pre-2026-08-28 one
# (-ServiceName) are accepted, so this script says something meaningful about an
# OLD copy of the watchdog too - which is what makes it a reproduction rather
# than only a regression guard.
Write-Host ''
Write-Host '3. containers this watchdog self-heals' -ForegroundColor White
$Managed = New-Object System.Collections.ArrayList
$Wanted = @('Confirm-AuxiliaryContainer', 'Invoke-PlaneCompose')
foreach ($c in $Commands) {
    $els = @($c.CommandElements)
    if ($els.Count -lt 2) { continue }
    $name = $els[0].Extent.Text
    if ($Wanted -notcontains $name) { continue }
    for ($i = 1; $i -lt $els.Count; $i++) {
        $el = $els[$i]
        if ($el -isnot [System.Management.Automation.Language.CommandParameterAst]) { continue }
        if (@('Container', 'ServiceName') -notcontains $el.ParameterName) { continue }
        $valAst = $null
        if ($el.Argument) { $valAst = $el.Argument }
        elseif ($i + 1 -lt $els.Count) { $valAst = $els[$i + 1] }
        if ($valAst -is [System.Management.Automation.Language.StringConstantExpressionAst]) {
            [void]$Managed.Add([pscustomobject]@{
                    Container = $valAst.Value
                    Line      = $c.Extent.StartLineNumber
                    Caller    = $name
                })
        }
        else {
            # A variable here is the generic helper's own body, not a call site.
            Write-Ok ('line {0}: {1} takes a variable - the helper body, not a call site' -f $c.Extent.StartLineNumber, $name)
        }
    }
}
# A pre-fix watchdog starts containers with a bare compose call and no helper,
# so also pick up literals from `docker compose <verb> <literal>` - otherwise an
# old copy would look like it manages nothing and would pass vacuously.
foreach ($c in $Commands) {
    $els = @($c.CommandElements)
    if ($els.Count -lt 3) { continue }
    if ($els[0].Extent.Text -ne 'docker' -or $els[1].Extent.Text -ne 'compose') { continue }
    $last = $els[$els.Count - 1]
    if ($last -is [System.Management.Automation.Language.StringConstantExpressionAst] -and
        $last.Value -notmatch '^-' -and $last.Value -match '^[a-z0-9][a-z0-9_.-]*$') {
        [void]$Managed.Add([pscustomobject]@{
                Container = $last.Value
                Line      = $c.Extent.StartLineNumber
                Caller    = 'docker compose (bare)'
            })
    }
}
$Distinct = @($Managed | Sort-Object Container -Unique)
Write-Host ("   {0} call sites, {1} distinct names" -f $Managed.Count, $Distinct.Count)
if ($Distinct.Count -eq 0) { Add-Failure 'no managed containers found - the parser found nothing to check' }

# --- CHECK 4: resolve each one through the inventory ------------------------
Write-Host ''
Write-Host '4. every managed name resolves to a project that can start it' -ForegroundColor White
$Inv = Get-Content -Path $InventoryPath -Raw | ConvertFrom-Json
$Projects = @{}
foreach ($p in $Inv.projects.PSObject.Properties) { $Projects[$p.Name] = $p.Value }
$Rows = @{}
foreach ($plane in $Inv.planes.PSObject.Properties) {
    foreach ($row in $plane.Value) { if ($row.container) { $Rows[[string]$row.container] = $row } }
}

$ServiceCache = @{}
function Get-DeclaredServices([string]$File, [string]$EnvFile) {
    $key = "$File|$EnvFile"
    if ($ServiceCache.ContainsKey($key)) { return $ServiceCache[$key] }
    $a = @('compose', '-f', (Join-Path $RepoRoot $File.Replace('/', [string][char]92)))
    if ($EnvFile) { $a += @('--env-file', (Join-Path $RepoRoot $EnvFile.Replace('/', [string][char]92))) }
    $a += @('config', '--services')
    $out = & docker @a 2>$null
    if ($LASTEXITCODE -ne 0) { $out = @() }
    $list = @($out | ForEach-Object { "$_".Trim() } | Where-Object { $_ })
    $ServiceCache[$key] = $list
    return $list
}

$Table = New-Object System.Collections.ArrayList
foreach ($m in $Distinct) {
    $name = $m.Container
    $row = $Rows[$name]
    if (-not $row) {
        Add-Failure ("'{0}' (line {1}) is not a CONTAINER in the inventory - 'docker inspect' cannot resolve it, so it can be neither detected nor repaired. If it is a compose SERVICE key, name the container instead." -f $name, $m.Line)
        continue
    }
    $projName = [string]$row.project
    $proj = $Projects[$projName]
    if (-not $proj) {
        Add-Failure ("'{0}' names project '{1}', which is absent from the inventory's projects map" -f $name, $projName)
        continue
    }
    if (-not $proj.file) {
        Add-Failure ("'{0}' resolves to project '{1}', which owns NO services (file=null) - nothing can be started there. This is the Part K anchor defect." -f $name, $projName)
        continue
    }
    $file = [string]$proj.file
    $abs = Join-Path $RepoRoot $file.Replace('/', [string][char]92)
    if (-not (Test-Path $abs)) {
        Add-Failure ("'{0}' resolves to '{1}', which does not exist on disk" -f $name, $file)
        continue
    }
    $svc = if ($row.service) { [string]$row.service } else { $name }

    $declared = '(skipped)'
    if (-not $SkipDocker) {
        $services = Get-DeclaredServices $file ([string]$proj.env_file)
        if (@($services).Count -eq 0) {
            Add-Failure ("could not render '{0}' - cannot confirm '{1}' is declared there" -f $file, $svc)
            $declared = '(render failed)'
        }
        elseif ($services -notcontains $svc) {
            Add-Failure ("'{0}' resolves to service '{1}' in {2}, which does NOT declare it" -f $name, $svc, $file)
            $declared = 'NOT DECLARED'
        }
        else {
            $declared = 'declared'
        }
    }
    [void]$Table.Add([pscustomobject]@{
            Container = $name; Project = $projName; Service = $svc; ComposeFile = $file; Declared = $declared
        })
}

Write-Host ''
Write-Host '5. resolution table' -ForegroundColor White
$Table | Sort-Object Project, Container | Format-Table -AutoSize | Out-String | Write-Host

Write-Host ''
if ($Failures.Count -gt 0) {
    Write-Host ("REPAIR TARGETS BROKEN: {0} problem(s)." -f $Failures.Count) -ForegroundColor Red
    Write-Host 'The watchdog cannot self-heal every container it claims to.' -ForegroundColor Red
    exit 1
}
Write-Host ("REPAIR TARGETS OK: {0} container(s) all resolve to a project that declares them." -f @($Table).Count) -ForegroundColor Green
exit 0
