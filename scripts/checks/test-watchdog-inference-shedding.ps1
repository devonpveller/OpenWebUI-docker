# test-watchdog-inference-shedding.ps1 - the test for stack-watchdog.ps1's
# INFERENCE SHEDDING rule (Test-InferenceShedding). Item ao-queue, 2026-10-07.
#
# llm-queue publishes its shedding EPISODE state on /healthz (`shedding`); the
# watchdog turns it into ONE page per episode plus an all-clear, through its
# own Send-LoopAlert / Resolve-LoopAlert path. This test loads the watchdog's
# FUNCTIONS only (never its main block) into a sandbox whose PROJECT_DIR is a
# temp directory, exactly as test-watchdog-portal-alerts.ps1 does:
#   - Get-LlmQueueShedding (the docker exec read) is REDEFINED to return the
#     case's state object, so no docker call is made;
#   - Send-TelegramAlert runs <sandbox>\.venv\Scripts\python.exe, a compiled
#     recorder that appends the message to transport-telegram.log; the real
#     telegram_notify.py is never on its path;
#   - the Mattermost mirror runs <sandbox>\scripts\notify-mattermost.sh, a
#     two-line fake that appends to transport-mattermost.log;
#   - every sentinel / state file lands in <sandbox>\logs.
# DOCKER_HOST is the dead endpoint tcp://127.0.0.1:1; nothing is posted anywhere.
#
# Usage (repo root):
#   powershell -NoProfile -File scripts\checks\test-watchdog-inference-shedding.ps1
#   ... -Script <path to another stack-watchdog.ps1>   (e.g. the base, for RED)
#   ... -HealthzShedding <file> -HealthzClear <file>   /healthz bodies captured
#       from a REAL llm-queue (the t-ao-queue rig) while shedding and after it
#       cleared - adds case S10.
# Exit code = number of failed cases.

[CmdletBinding()]
param(
    [string]$Script = '',
    [string]$HealthzShedding = '',
    [string]$HealthzClear = ''
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
    $root = Join-Path ([IO.Path]::GetTempPath()) ("iswd-sbx-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
    foreach ($d in @('logs', 'scripts\checks', 'scripts\sysadmin-mcp', '.venv\Scripts')) {
        New-Item -ItemType Directory -Path (Join-Path $root $d) -Force | Out-Null
    }
    [IO.File]::WriteAllText((Join-Path $root 'scripts\sysadmin-mcp\telegram_notify.py'), "# fake - never run`n")
    $sh = "#!/bin/sh`nprintf '%s\n' `"`$1`" >> `"`$(dirname `"`$0`")/../transport-mattermost.log`"`n"
    [IO.File]::WriteAllText((Join-Path $root 'scripts\notify-mattermost.sh'), $sh)
    $rec = @"
using System; using System.IO;
public static class IswdRecorder__SFX__ { public static int Main(string[] a) {
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
    # The seam: the docker exec read returns the case's state, never a docker call.
    function Get-LlmQueueShedding { return $script:FakeShed }
}

# A /healthz `shedding` object, shaped like llm_queue.shedding.ShedMonitor.snapshot().
function New-Shed {
    param([string]$State = 'ok', [int]$Held = 3, [string]$Proc = '1791400000.0',
          $Current = $null, $Last = $null, [int]$Episodes = 0)
    $o = [ordered]@{ state = $State; held = $Held; cap = 128; alert_at = 96; clear_at = 64; clear_quiet_s = 300.0
                     refusals_total = 0; episodes_total = $Episodes; process_started_at = [double]$Proc
                     current = $Current; last = $Last }
    return ($o | ConvertTo-Json -Depth 5 | ConvertFrom-Json)
}
function New-Ep {
    param([int]$Id, [string]$Trigger = 'refused', [int]$Refusals = 46, [int]$Peak = 128, $Cleared = $null)
    return [ordered]@{ id = $Id; started_at = 1791400200.5; cleared_at = $Cleared; trigger = $Trigger; refusals = $Refusals; peak_held = $Peak }
}

function Invoke-Rule {
    try { return [bool](@(Test-InferenceShedding) | Select-Object -Last 1) }
    catch { $script:RuleError = $_.Exception.Message; return $null }
}

$sbx = New-Sandbox
Write-Host "sandbox $sbx ; DOCKER_HOST=$env:DOCKER_HOST ; script $Script"
try {
    . $script:Loader $sbx
    $haveRule = [bool](Get-Command Test-InferenceShedding -ErrorAction SilentlyContinue)
    Write-Case 'S0' 'the watchdog defines Test-InferenceShedding' $haveRule ''

    # S1 - unreadable (container down / old image): quiet, true
    $script:FakeShed = $null; $script:RuleError = $null
    $r = Invoke-Rule
    $t = Get-Transport $sbx
    Write-Case 'S1' 'no shedding state -> true, nothing sent' (($r -eq $true) -and $t.Telegram.Count -eq 0 -and $t.Mattermost.Count -eq 0) "result=$r err=$script:RuleError"

    # S2 - ok, never shed: quiet, true
    $script:FakeShed = New-Shed
    $r = Invoke-Rule
    $t = Get-Transport $sbx
    Write-Case 'S2' 'state ok, no episode -> true, nothing sent' (($r -eq $true) -and $t.Telegram.Count -eq 0) "result=$r err=$script:RuleError"

    # S3 - shedding: ONE page on Telegram + the MM mirror, ERROR logged, names it the environment
    $script:FakeShed = New-Shed -State 'shedding' -Held 126 -Current (New-Ep 1) -Episodes 1
    $r = Invoke-Rule
    $t = Get-Transport $sbx
    $m = Measure-Alerts $t 'INFERENCE SHEDDING: llm-queue episode 1'
    $log = Get-Content (Join-Path $sbx 'logs\tailscale-health.log') -Raw -ErrorAction SilentlyContinue
    Write-Case 'S3' 'shedding -> false, ONE page (tg+mm), ERROR logged, says environment not code' (($r -eq $false) -and $m -eq 'tg=1 mm=1' -and $log -match '\[ERROR\] INFERENCE SHEDDING' -and (@($t.Telegram) -join ' ') -match '126 of 128 held.*46 capacity refusal.*the environment, not their code') "result=$r $m err=$script:RuleError`n$(@($t.Telegram) -join "`n")"

    # S4 - still shedding next passes: no second page
    $script:FakeShed = New-Shed -State 'shedding' -Held 127 -Current (New-Ep 1 -Refusals 90) -Episodes 1
    $r1 = Invoke-Rule; $r2 = Invoke-Rule
    $t = Get-Transport $sbx
    $m = Measure-Alerts $t 'INFERENCE SHEDDING'
    Write-Case 'S4' 'still shedding on two more passes -> false, still exactly one page' (($r1 -eq $false) -and ($r2 -eq $false) -and $m -eq 'tg=1 mm=1') "r1=$r1 r2=$r2 $m"

    # S5 - cleared: ONE all-clear, then silent
    $script:FakeShed = New-Shed -State 'ok' -Held 12 -Last (New-Ep 1 -Refusals 90 -Cleared 1791401400.0) -Episodes 1
    $r1 = Invoke-Rule; $r2 = Invoke-Rule
    $t = Get-Transport $sbx
    $res = Measure-Alerts $t 'RESOLVED.*llm-queue stopped shedding - episode 1'
    $alerts = Measure-Alerts $t 'ALERT.*INFERENCE SHED'
    Write-Case 'S5' 'cleared -> true, ONE RESOLVED, then silent; no re-page' (($r1 -eq $true) -and ($r2 -eq $true) -and $res -match '^tg=1 ' -and $alerts -eq 'tg=1 mm=1') "r1=$r1 r2=$r2 resolved:$res alerts:$alerts`n$(@($t.Telegram) -join "`n")"

    # S6 - an episode that began and ended BETWEEN two passes: one notice + one all-clear, same pass; then silent
    $before = (Get-Transport $sbx).Telegram.Count
    $script:FakeShed = New-Shed -State 'ok' -Held 5 -Last (New-Ep 2 -Trigger 'held_threshold' -Refusals 0 -Peak 97 -Cleared 1791402000.0) -Episodes 2
    $r1 = Invoke-Rule; $r2 = Invoke-Rule
    $new = @((Get-Transport $sbx).Telegram | Select-Object -Skip $before)
    $a = @($new | Where-Object { $_ -match '^ALERT.*INFERENCE SHED between passes: llm-queue episode 2 \(held_threshold\)' }).Count
    $c = @($new | Where-Object { $_ -match '^RESOLVED.*episode 2' }).Count
    Write-Case 'S6' 'episode between passes -> one ALERT + one RESOLVED in that pass, then silent' (($r1 -eq $true) -and ($r2 -eq $true) -and $a -eq 1 -and $c -eq 1 -and $new.Count -eq 2) "r1=$r1 r2=$r2 new=$($new -join ' | ')"

    # S7 - llm-queue restarted (new process; episodes renumber from 1): its episode 1 pages
    $before = (Get-Transport $sbx).Telegram.Count
    $script:FakeShed = New-Shed -State 'shedding' -Held 128 -Proc '1791409999.0' -Current (New-Ep 1) -Episodes 1
    $r = Invoke-Rule
    $new = @((Get-Transport $sbx).Telegram | Select-Object -Skip $before)
    Write-Case 'S7' 'after an llm-queue restart, a new episode 1 is not mistaken for an old one' (($r -eq $false) -and @($new | Where-Object { $_ -match 'INFERENCE SHEDDING: llm-queue episode 1' }).Count -eq 1) "result=$r new=$($new -join ' | ')"

    # S8 - wired into the health check, after the front-door catastrophe key
    $tokens = $null; $perr = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile($Script, [ref]$tokens, [ref]$perr)
    $hc = $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Invoke-HealthCheck' }, $true) | Select-Object -First 1
    $wired = $hc -and ($hc.Extent.Text -match 'Test-InferenceShedding') -and ($hc.Extent.Text -match "'inference-shedding'")
    Write-Case 'S8' 'Invoke-HealthCheck runs the rule and records inference-shedding' ([bool]$wired) ''

    # S9 - values from the container are reduced to tokens/numbers before they reach a message
    $sbx2 = New-Sandbox
    . $script:Loader $sbx2
    $ep = New-Ep 1 -Trigger "refused`n<b>x</b> token=abc"
    $script:FakeShed = New-Shed -State 'shedding' -Held 100 -Current $ep -Episodes 1
    $null = Invoke-Rule
    $t = Get-Transport $sbx2
    $msg = @($t.Telegram) -join "`n"
    Write-Case 'S9' 'container-written strings are sanitized (no newline/markup/spaces injected)' (($t.Telegram.Count -eq 1) -and $msg -notmatch '<b>' -and $msg -match 'episode 1 \(refused__b_x_/b__token_a\)') $msg
    . $script:Loader $sbx

    # S10 - REAL /healthz bodies from a running llm-queue (the rig)
    if ($HealthzShedding -and $HealthzClear) {
        $sbx3 = New-Sandbox
        . $script:Loader $sbx3
        $script:FakeShed = ([IO.File]::ReadAllText($HealthzShedding) | ConvertFrom-Json).shedding
        $r1 = Invoke-Rule; $r1b = Invoke-Rule
        $script:FakeShed = ([IO.File]::ReadAllText($HealthzClear) | ConvertFrom-Json).shedding
        $r2 = Invoke-Rule; $r2b = Invoke-Rule
        $t = Get-Transport $sbx3
        $a = Measure-Alerts $t '^ALERT.*INFERENCE SHEDDING'
        $c = @($t.Telegram | Where-Object { $_ -match '^RESOLVED.*stopped shedding' }).Count
        Write-Case 'S10' "the real llm-queue's shedding/cleared /healthz -> one page, one all-clear" (($r1 -eq $false) -and ($r1b -eq $false) -and ($r2 -eq $true) -and ($r2b -eq $true) -and $a -eq 'tg=1 mm=1' -and $c -eq 1 -and $t.Telegram.Count -eq 2) "r=$r1/$r1b/$r2/$r2b $a resolved=$c`n$(@($t.Telegram) -join "`n")"
        . $script:Loader $sbx
    }
} finally {
    foreach ($d in @($sbx, $sbx2, $sbx3)) { if ($d -and (Test-Path $d)) { Remove-Item $d -Recurse -Force -ErrorAction SilentlyContinue } }
}

Write-Host ""
Write-Host ("RESULTS: " + ($script:Results -join ' '))
Write-Host "FAILED: $script:Failures"
exit $script:Failures
