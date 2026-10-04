# test-watchdog-portal-alerts.ps1 - the test for stack-watchdog.ps1's PORTAL
# ALERT DELIVERY rule (Test-PortalAlertDelivery). Item pa-channels, 2026-10-04.
#
# portal-alerter writes <repo>\reports\portal-digest\alerter-delivery-state.json;
# the watchdog turns "an alert reached no channel" and "a configured channel
# keeps failing" into ITS OWN page (Send-LoopAlert -> host Telegram + the
# Mattermost mirror), never through portal-alerter. This test loads the
# watchdog's FUNCTIONS only (never its main block) into a sandbox whose
# PROJECT_DIR is a temp directory, exactly as test-watchdog-loops.ps1 does:
#   - Send-TelegramAlert runs <sandbox>\.venv\Scripts\python.exe, a compiled
#     recorder that appends the message to transport-telegram.log; the real
#     telegram_notify.py is never on its path;
#   - the Mattermost mirror runs <sandbox>\scripts\notify-mattermost.sh, a
#     two-line fake that appends to transport-mattermost.log;
#   - every sentinel / state file lands in <sandbox>\logs and <sandbox>\reports.
# DOCKER_HOST is the dead endpoint tcp://127.0.0.1:1; the rule makes no docker
# call, and nothing is posted anywhere.
#
# Usage (repo root):
#   powershell -NoProfile -File scripts\checks\test-watchdog-portal-alerts.ps1
#   ... -Script <path to another stack-watchdog.ps1>   (e.g. the base, for RED)
#   ... -AlerterStateFile <path>   a state file the REAL alerter wrote with every
#       channel failing (scripts/portal/test_alerter_channels.py
#       --export-undelivered-state <path>) - adds case W9.
# Exit code = number of failed cases.

[CmdletBinding()]
param(
    [string]$Script = '',
    [string]$AlerterStateFile = ''
)

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $Script) { $Script = Join-Path $here 'stack-watchdog.ps1' }
$Script = (Resolve-Path $Script).Path
$env:DOCKER_HOST = 'tcp://127.0.0.1:1'
$script:Failures = 0
$script:Results = @()

function Write-Case {
    param([string]$Id, [string]$Title, [bool]$Pass, [string]$Detail)
    $verdict = if ($Pass) { 'PASS' } else { 'FAIL' }
    if (-not $Pass) { $script:Failures++ }
    $script:Results += "$Id $verdict"
    Write-Host ("## {0} - {1}    {2}" -f $Id, $Title, $verdict)
    if ($Detail) { foreach ($l in ($Detail -split "`n")) { Write-Host "    $l" } }
}

function New-Sandbox {
    $root = Join-Path ([IO.Path]::GetTempPath()) ("pawd-sbx-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
    foreach ($d in @('logs', 'scripts\checks', 'scripts\sysadmin-mcp', '.venv\Scripts', 'reports\portal-digest')) {
        New-Item -ItemType Directory -Path (Join-Path $root $d) -Force | Out-Null
    }
    [IO.File]::WriteAllText((Join-Path $root 'scripts\sysadmin-mcp\telegram_notify.py'), "# fake - never run`n")
    $sh = "#!/bin/sh`nprintf '%s\n' `"`$1`" >> `"`$(dirname `"`$0`")/../transport-mattermost.log`"`n"
    [IO.File]::WriteAllText((Join-Path $root 'scripts\notify-mattermost.sh'), $sh)
    $rec = @"
using System; using System.IO;
public static class PawdRecorder__SFX__ { public static int Main(string[] a) {
  string f = Path.GetFullPath(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "..", "..", "transport-telegram.log"));
  File.AppendAllText(f, (a.Length > 1 ? a[1] : "") + "\n"); return 0; } }
"@
    $rec = $rec.Replace('__SFX__', [guid]::NewGuid().ToString('N').Substring(0, 8))
    Add-Type -TypeDefinition $rec -OutputAssembly (Join-Path $root '.venv\Scripts\python.exe') -OutputType ConsoleApplication
    return $root
}

function Get-Transport {
    param([string]$Root)
    $tg = Join-Path $Root 'transport-telegram.log'
    $mm = Join-Path $Root 'transport-mattermost.log'
    return [pscustomobject]@{
        Telegram   = @(if (Test-Path $tg) { Get-Content $tg | Where-Object { $_ } })
        Mattermost = @(if (Test-Path $mm) { Get-Content $mm | Where-Object { $_ } })
    }
}

function Measure-Alerts {
    param($Transport, [string]$Pattern)
    $t = @($Transport.Telegram | Where-Object { $_ -match $Pattern }).Count
    $m = @($Transport.Mattermost | Where-Object { $_ -match $Pattern }).Count
    return "tg=$t mm=$m"
}

$script:Loader = {
    param([string]$sbx)
    $tokens = $null; $perr = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile($Script, [ref]$tokens, [ref]$perr)
    if ($perr.Count -gt 0) { throw "parse errors in ${Script}: $($perr[0].Message)" }
    $skipVars = @('SCRIPT_DIR', 'PROJECT_DIR', 'LOG_FILE', 'LogDir', 'ErrorActionPreference', 'ProgressPreference')
    foreach ($st in $ast.EndBlock.Statements) {
        if ($st -is [System.Management.Automation.Language.FunctionDefinitionAst]) {
            . ([scriptblock]::Create($st.Extent.Text))
        } elseif ($st -is [System.Management.Automation.Language.AssignmentStatementAst] -and
                  $st.Left -is [System.Management.Automation.Language.VariableExpressionAst] -and
                  $skipVars -notcontains $st.Left.VariablePath.UserPath) {
            . ([scriptblock]::Create($st.Extent.Text))
        }
    }
    $script:SCRIPT_DIR = Join-Path $sbx 'scripts\checks'
    $script:PROJECT_DIR = $sbx
    $script:LOG_FILE = Join-Path $sbx 'logs\tailscale-health.log'
    function Write-LogEntry { param([string]$Message, [string]$Level = 'INFO')
        "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') [$Level] $Message" | Out-File -FilePath $LOG_FILE -Append -Encoding UTF8 }
}

function Set-State {
    param([string]$Root, $Obj)
    $p = Join-Path $Root 'reports\portal-digest\alerter-delivery-state.json'
    [IO.File]::WriteAllText($p, ($Obj | ConvertTo-Json -Depth 6))
}

function New-StateObj {
    param([string]$UndeliveredSince = $null, [int]$Count = 0, [string]$EventName = $null,
          [int]$TgFails = 0, [int]$MmFails = 0, [int]$EmailFails = 0,
          [bool]$EmailConfigured = $false)
    $ch = { param($conf, $n) [ordered]@{ configured = $conf; disabled_reason = $null; consecutive_failures = $n;
            last_ok_at = $null; last_error_at = '2026-10-04T04:00:00.000Z'; last_error = 'x' } }
    return [ordered]@{
        schema = 1; updated_at = '2026-10-04T04:00:00.000Z'
        undelivered_since = $UndeliveredSince; undelivered_count = $Count; last_undelivered_event = $EventName
        last_delivered_at = $null; last_attempt = $null
        channels = [ordered]@{
            telegram   = (& $ch $true $TgFails)
            mattermost = (& $ch $true $MmFails)
            email      = (& $ch $EmailConfigured $EmailFails)
        }
    }
}

function Invoke-Rule {
    try { return [bool](@(Test-PortalAlertDelivery) | Select-Object -Last 1) }
    catch { $script:RuleError = $_.Exception.Message; return $null }
}

$sbx = New-Sandbox
Write-Host "sandbox $sbx ; DOCKER_HOST=$env:DOCKER_HOST ; script $Script"
try {
    . $script:Loader $sbx

    # W1 - no state file: quiet, true
    $script:RuleError = $null
    $r = Invoke-Rule
    $t = Get-Transport $sbx
    Write-Case 'W1' 'no state file -> true, nothing sent' (($r -eq $true) -and $t.Telegram.Count -eq 0 -and $t.Mattermost.Count -eq 0) "result=$r err=$script:RuleError"

    # W2 - an alert reached no channel -> the watchdog's own page
    Set-State $sbx (New-StateObj -UndeliveredSince '2026-10-04T04:00:00.000Z' -Count 2 -EventName 'config.drift')
    $r = Invoke-Rule
    $t = Get-Transport $sbx
    $m = Measure-Alerts $t 'reached NO channel'
    $log = if (Test-Path (Join-Path $sbx 'logs\tailscale-health.log')) { Get-Content (Join-Path $sbx 'logs\tailscale-health.log') -Raw } else { '' }
    Write-Case 'W2' 'undelivered marker -> false, ONE page on Telegram + MM mirror, ERROR logged' (($r -eq $false) -and $m -eq 'tg=1 mm=1' -and $log -match '\[ERROR\] PORTAL ALERTS UNDELIVERED') "result=$r $m err=$script:RuleError`n$(@($t.Telegram) -join "`n")"

    # W3 - same state next pass: still false, not re-paged inside the cooldown
    $r = Invoke-Rule
    $t = Get-Transport $sbx
    $m = Measure-Alerts $t 'reached NO channel'
    Write-Case 'W3' 'still undelivered next pass -> false, no second page (cooldown)' (($r -eq $false) -and $m -eq 'tg=1 mm=1') "result=$r $m"

    # W4 - delivered again -> RESOLVED once, then quiet
    Set-State $sbx (New-StateObj)
    $r1 = Invoke-Rule
    $r2 = Invoke-Rule
    $t = Get-Transport $sbx
    $m = Measure-Alerts $t 'RESOLVED.*delivering portal alerts again'
    Write-Case 'W4' 'cleared -> true, one RESOLVED, then silent' (($r1 -eq $true) -and ($r2 -eq $true) -and $m -match '^tg=1 ') "r1=$r1 r2=$r2 $m"

    # W5 - a configured channel keeps failing; an UNconfigured one never pages
    $before = (Get-Transport $sbx).Telegram.Count
    Set-State $sbx (New-StateObj -TgFails 3 -MmFails 2 -EmailFails 9 -EmailConfigured $false)
    $r = Invoke-Rule
    $t = Get-Transport $sbx
    $new = @($t.Telegram | Select-Object -Skip $before)
    $tgPage = @($new | Where-Object { $_ -match 'telegram channel failed 3 sends' }).Count
    $other = @($new | Where-Object { $_ -match 'mattermost channel|email channel' }).Count
    Write-Case 'W5' 'telegram 3 failures (configured) -> page; mattermost 2 and unconfigured email -> none' (($r -eq $false) -and $tgPage -eq 1 -and $other -eq 0) "result=$r new=$($new -join ' | ')"

    # W6 - container-written values are sanitized before they reach a message
    $sbx2 = New-Sandbox
    . $script:Loader $sbx2
    Set-State $sbx2 (New-StateObj -UndeliveredSince "2026-10-04`nINJECT" -Count 1 -EventName "evil`n<b>bold</b> token=abc def")
    $null = Invoke-Rule
    $t = Get-Transport $sbx2
    $msg = @($t.Telegram) -join "`n"
    Write-Case 'W6' 'values from the state file are reduced to identifiers (no newline, markup or spaces injected)' (($t.Telegram.Count -eq 1) -and $msg -notmatch '<b>' -and $msg -match 'since 2026-10-04_INJECT reached' -and $msg -match 'last: evil_') $msg
    . $script:Loader $sbx

    # W7 - wired into the health check
    $tokens = $null; $perr = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile($Script, [ref]$tokens, [ref]$perr)
    $hc = $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Invoke-HealthCheck' }, $true) | Select-Object -First 1
    $wired = $hc -and ($hc.Extent.Text -match 'Test-PortalAlertDelivery') -and ($hc.Extent.Text -match "'portal-alert-delivery'")
    Write-Case 'W7' 'Invoke-HealthCheck runs the rule and records portal-alert-delivery' ([bool]$wired) ''

    # W8 - unreadable state file: skipped with a WARN, never throws
    $p = Join-Path $sbx 'reports\portal-digest\alerter-delivery-state.json'
    [IO.File]::WriteAllText($p, '{ torn')
    $script:RuleError = $null
    $r = Invoke-Rule
    Write-Case 'W8' 'torn state file -> true, no throw' (($r -eq $true) -and -not $script:RuleError) "result=$r err=$script:RuleError"

    # W10 - counts past Int32 still page (they used to fail an [int] cast and read as 0)
    $sbx4 = New-Sandbox
    . $script:Loader $sbx4
    $o = New-StateObj
    $o.channels.telegram.consecutive_failures = 99999999999
    $o.channels.mattermost.consecutive_failures = 1e30
    Set-State $sbx4 $o
    $script:RuleError = $null
    $r = Invoke-Rule
    $t = Get-Transport $sbx4
    $tgPage = @($t.Telegram | Where-Object { $_ -match 'telegram channel failed 99999999999 sends' }).Count
    $mmPage = @($t.Telegram | Where-Object { $_ -match 'mattermost channel failed 9223372036854775807 sends' }).Count
    Write-Case 'W10' 'consecutive_failures beyond Int32 pages (Int64); beyond Int64 is clamped and pages' (($r -eq $false) -and $tgPage -eq 1 -and $mmPage -eq 1) "result=$r err=$script:RuleError`n$(@($t.Telegram) -join "`n")"
    . $script:Loader $sbx

    # W9 - the REAL alerter's all-channels-failed state file
    if ($AlerterStateFile) {
        $sbx3 = New-Sandbox
        . $script:Loader $sbx3
        Copy-Item -LiteralPath $AlerterStateFile -Destination (Join-Path $sbx3 'reports\portal-digest\alerter-delivery-state.json')
        $r = Invoke-Rule
        $t = Get-Transport $sbx3
        $m = Measure-Alerts $t 'reached NO channel \(last: config\.drift\)'
        Write-Case 'W9' "the real alerter's all-failed state -> the watchdog pages" (($r -eq $false) -and $m -eq 'tg=1 mm=1') "result=$r $m err=$script:RuleError"
        . $script:Loader $sbx
    }
} finally {
    foreach ($d in @($sbx, $sbx2, $sbx3, $sbx4)) { if ($d -and (Test-Path $d)) { Remove-Item $d -Recurse -Force -ErrorAction SilentlyContinue } }
}

Write-Host ""
Write-Host ("RESULTS: " + ($script:Results -join ' '))
Write-Host "FAILED: $script:Failures"
exit $script:Failures
