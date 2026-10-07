# test-watchdog-host-memory.ps1 - the test for stack-watchdog.ps1's HOST MEMORY
# check (Test-HostMemory). Item hm-watchdog, 2026-10-07.
#
# Nothing watched host memory: on 2026-10-07 vmmemWSL held 112.3 GiB of the
# 127.7 GiB host with 24.3 GiB available and no alert fired (the 2026-07-05 OOM
# pattern, which wedged WSL until a reboot). This test loads the watchdog's
# FUNCTIONS only (never its main block) into a sandbox whose PROJECT_DIR is a
# temp directory, exactly as test-watchdog-portal-alerts.ps1 does:
#   - the counters are STUBBED through $HostMemReader (a scriptblock returning
#     one sample), and the clock through $WatchdogClock;
#   - Send-TelegramAlert runs <sandbox>\.venv\Scripts\python.exe, a compiled
#     recorder that appends the message to transport-telegram.log; the real
#     telegram_notify.py is never on its path;
#   - the Mattermost mirror runs <sandbox>\scripts\notify-mattermost.sh, a
#     two-line fake that appends to transport-mattermost.log;
#   - every sentinel / state file lands in <sandbox>\logs.
# Nothing is posted anywhere. DOCKER_HOST is the dead endpoint tcp://127.0.0.1:1.
#
# Usage (repo root):
#   powershell -NoProfile -File scripts\checks\test-watchdog-host-memory.ps1
#   ... -Script <path to another stack-watchdog.ps1>   (e.g. the base, for RED)
#   ... -Live   adds case H15: the REAL counters, read-only, senders still stubbed;
#               prints the status line the scheduled pass would write.
# Exit code = number of failed cases.

[CmdletBinding()]
param(
    [string]$Script = '',
    [switch]$Live
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
    $root = Join-Path ([IO.Path]::GetTempPath()) ("hmwd-sbx-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
    foreach ($d in @('logs', 'scripts\checks', 'scripts\sysadmin-mcp', '.venv\Scripts')) {
        New-Item -ItemType Directory -Path (Join-Path $root $d) -Force | Out-Null
    }
    [IO.File]::WriteAllText((Join-Path $root 'scripts\sysadmin-mcp\telegram_notify.py'), "# fake - never run`n")
    $sh = "#!/bin/sh`nprintf '%s\n' `"`$1`" >> `"`$(dirname `"`$0`")/../transport-mattermost.log`"`n"
    [IO.File]::WriteAllText((Join-Path $root 'scripts\notify-mattermost.sh'), $sh)
    $rec = @"
using System; using System.IO;
public static class HmwdRecorder__SFX__ { public static int Main(string[] a) {
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

function Get-Log {
    param([string]$Root)
    $p = Join-Path $Root 'logs\tailscale-health.log'
    if (Test-Path $p) { return @(Get-Content $p) }
    return @()
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

# One stubbed sample, in GiB. A $null value plus an Errors entry is an
# unreadable counter; State 'absent' is a process that is not running.
function New-Sample {
    param([object]$Total = 127.7, [object]$Avail = 90.0, [object]$Commit = 40.0, [object]$Limit = 199.7,
          [object]$Vmmem = 30.0, [string]$VmmemState = 'present',
          [object]$Backend = 0.31, [string]$BackendIds = '5424,19648', [string]$BackendState = 'present',
          [hashtable]$Errors = @{})
    $g = [double]1GB
    $b = { param($v) if ($null -eq $v) { $null } else { [double]$v * $g } }
    return [pscustomobject]@{
        TotalBytes = (& $b $Total); AvailBytes = (& $b $Avail)
        CommitBytes = (& $b $Commit); CommitLimitBytes = (& $b $Limit)
        Vmmem = [pscustomobject]@{ State = $VmmemState; PrivateBytes = (& $b $Vmmem); Id = '30036'; Count = 1; Error = $Errors['vmmemWSL'] }
        Backend = [pscustomobject]@{ State = $BackendState; PrivateBytes = (& $b $Backend); Id = $BackendIds; Count = 2; Error = $Errors['com.docker.backend'] }
        Errors = $Errors
    }
}

function Use-Sample {
    param($Sample)
    $script:NextSample = $Sample
    $script:HostMemReader = { $script:NextSample }
}

function Invoke-Rule {
    $script:RuleError = $null
    try { return [bool](@(Test-HostMemory) | Select-Object -Last 1) }
    catch { $script:RuleError = $_.Exception.Message; return $null }
}

$script:FakeNow = [datetime]::new(2026, 10, 7, 12, 0, 0, [DateTimeKind]::Utc)
function Set-Stubs {
    $script:WatchdogClock = { $script:FakeNow }
}

$sbx = $null; $sbx2 = $null; $sbx3 = $null; $sbx4 = $null; $sbx5 = $null; $sbx6 = $null
$sbx = New-Sandbox
Write-Host "sandbox $sbx ; DOCKER_HOST=$env:DOCKER_HOST ; script $Script"
try {
    . $script:Loader $sbx
    Set-Stubs

    # H0 - the check exists (RED at base: it does not)
    $exists = [bool](Get-Command Test-HostMemory -ErrorAction SilentlyContinue)
    Write-Case 'H0' 'stack-watchdog.ps1 defines Test-HostMemory' $exists ''

    # H1 - below every threshold: true, nothing sent, one OK status line
    Use-Sample (New-Sample)
    $r = Invoke-Rule
    $t = Get-Transport $sbx
    $log = Get-Log $sbx
    $okLine = @($log | Where-Object { $_ -match '\[INFO\] hostmem: avail=90\.0GiB of 127\.7GiB.*commit=40\.0/199\.7GiB.*vmmemWSL=30\.0GiB.*com\.docker\.backend=0\.31GiB.*-> OK$' })
    Write-Case 'H1' 'healthy sample -> true, nothing sent, one OK status line' (($r -eq $true) -and $t.Telegram.Count -eq 0 -and $t.Mattermost.Count -eq 0 -and $okLine.Count -eq 1) "result=$r err=$script:RuleError`n$($log -join "`n")"

    # H2 - the measured 2026-10-07 state: vmmemWSL 112.3 of 127.7, 24.3 available
    Use-Sample (New-Sample -Avail 24.3 -Commit 150.0 -Vmmem 112.3)
    $r = Invoke-Rule
    $t = Get-Transport $sbx
    $m = Measure-Alerts $t 'vmmemWSL'
    $other = @($t.Telegram | Where-Object { $_ -notmatch 'vmmemWSL holds' }).Count
    $warnLine = @(Get-Log $sbx | Where-Object { $_ -match '\[WARN\] hostmem: .*-> ALERT hostmem-vmmem' }).Count
    Write-Case 'H2' 'crossing (the 10-07 replay: vmmemWSL 112.3 GiB) -> false, ONE page on Telegram + MM mirror, WARN status line' (($r -eq $false) -and $m -eq 'tg=1 mm=1' -and $other -eq 0 -and $warnLine -eq 1) "result=$r $m other=$other err=$script:RuleError`n$(@($t.Telegram) -join "`n")"

    # H3 - staying high next pass: false, no second page (cooldown), logged
    $r = Invoke-Rule
    $t = Get-Transport $sbx
    $m = Measure-Alerts $t 'vmmemWSL holds'
    $still = @(Get-Log $sbx | Where-Object { $_ -match 'HOSTMEM \[hostmem-vmmem\] still firing' }).Count
    Write-Case 'H3' 'staying high -> false, throttled (no second page), still-firing line' (($r -eq $false) -and $m -eq 'tg=1 mm=1' -and $still -ge 1) "result=$r $m still=$still"

    # H3b - the cooldown is a re-page interval, not a mute
    $sent = Join-Path $sbx 'logs\.hostmem-alert-hostmem-vmmem'
    if (Test-Path $sent) { (Get-Item $sent).LastWriteTime = (Get-Date).AddHours(-3) }
    $tga = Join-Path $sbx 'logs\.tg-alert-hostmem-vmmem'
    if (Test-Path $tga) { (Get-Item $tga).LastWriteTime = (Get-Date).AddHours(-3) }
    $r = Invoke-Rule
    $t = Get-Transport $sbx
    $m = Measure-Alerts $t 'vmmemWSL holds'
    Write-Case 'H3b' 'still high after the cooldown -> paged again (once)' (($r -eq $false) -and $m -eq 'tg=2 mm=2') "result=$r $m"

    # H4 - inside the hysteresis band (below 80, above the 72 all-clear line): no RESOLVED
    Use-Sample (New-Sample -Vmmem 75.0)
    $r = Invoke-Rule
    $t = Get-Transport $sbx
    $res = Measure-Alerts $t 'RESOLVED'
    $hold = @(Get-Log $sbx | Where-Object { $_ -match 'hostmem: .*-> HOLDING hostmem-vmmem' }).Count
    Write-Case 'H4' 'between the threshold and the all-clear line -> no RESOLVED, HOLDING line' (($r -eq $false) -and $res -eq 'tg=0 mm=0' -and $hold -eq 1) "result=$r $res hold=$hold"

    # H5 - recovering: one all-clear, then quiet
    Use-Sample (New-Sample -Vmmem 30.0)
    $r1 = Invoke-Rule
    $r2 = Invoke-Rule
    $t = Get-Transport $sbx
    $res = Measure-Alerts $t 'RESOLVED ai-stack: .*vmmemWSL'
    $gone = -not (Test-Path $sent)
    Write-Case 'H5' 'recovering -> true, ONE RESOLVED (all-clear), then silent; alert re-armed' (($r1 -eq $true) -and ($r2 -eq $true) -and $res -match '^tg=1 ' -and $gone) "r1=$r1 r2=$r2 $res rearmed=$gone`n$(@($t.Telegram) -join "`n")"

    # H6 - an unreadable counter: a visible UNKNOWN line and a page, never silence
    $sbx2 = New-Sandbox
    . $script:Loader $sbx2
    Set-Stubs
    Use-Sample (New-Sample -Avail $null -Errors @{ available = 'Access is denied' })
    $r = Invoke-Rule
    $t = Get-Transport $sbx2
    $unk = @(Get-Log $sbx2 | Where-Object { $_ -match '\[WARN\] hostmem: avail=UNKNOWN\(Access is denied\).*-> UNKNOWN available' }).Count
    $m = Measure-Alerts $t 'cannot read'
    Write-Case 'H6' 'unreadable counter -> false, WARN line with avail=UNKNOWN, one cannot-read page' (($r -eq $false) -and $unk -eq 1 -and $m -eq 'tg=1 mm=1') "result=$r unk=$unk $m err=$script:RuleError`n$((Get-Log $sbx2) -join "`n")"
    Use-Sample (New-Sample)
    $r = Invoke-Rule
    $t = Get-Transport $sbx2
    $res = Measure-Alerts $t 'RESOLVED ai-stack: .*readable'
    Write-Case 'H6b' 'counter readable again -> true, one RESOLVED' (($r -eq $true) -and $res -match '^tg=1 ') "result=$r $res"

    # H7 - the reader itself throws: an UNKNOWN line, false, the cycle is not aborted
    $script:HostMemReader = { throw 'pdh exploded' }
    $r = Invoke-Rule
    $line = @(Get-Log $sbx2 | Where-Object { $_ -match '\[WARN\] hostmem: UNKNOWN - .*pdh exploded' }).Count
    Write-Case 'H7' 'reader throws -> false, no throw out of the check, UNKNOWN line' (($r -eq $false) -and -not $script:RuleError -and $line -eq 1) "result=$r err=$script:RuleError line=$line"

    # H8 / H9 / H10 - each remaining metric pages under its own key
    $sbx3 = New-Sandbox
    . $script:Loader $sbx3
    Set-Stubs
    Use-Sample (New-Sample -Avail 10.0)
    $r = Invoke-Rule
    $t = Get-Transport $sbx3
    $m = Measure-Alerts $t 'HOST MEMORY LOW: 10\.0 GiB available'
    Write-Case 'H8' 'available 10 GiB (< 16 GiB floor) -> page hostmem-available' (($r -eq $false) -and $m -eq 'tg=1 mm=1') "result=$r $m`n$(@($t.Telegram) -join "`n")"

    Use-Sample (New-Sample -Commit 176.0)
    $r = Invoke-Rule
    $t = Get-Transport $sbx3
    $m = Measure-Alerts $t 'COMMIT CHARGE HIGH: 176\.0 of 199\.7 GiB \(88\.1%;'
    Write-Case 'H9' 'commit 176/199.7 (88% >= 85%) -> page hostmem-commit' (($r -eq $false) -and $m -eq 'tg=1 mm=1') "result=$r $m`n$(@($t.Telegram) -join "`n")"

    Use-Sample (New-Sample -Backend 14.0)
    $r = Invoke-Rule
    $t = Get-Transport $sbx3
    $m = Measure-Alerts $t 'com\.docker\.backend holds 14\.0 GiB'
    Write-Case 'H10' 'com.docker.backend 14 GiB (>= 12) -> page hostmem-backend' (($r -eq $false) -and $m -eq 'tg=1 mm=1') "result=$r $m`n$(@($t.Telegram) -join "`n")"

    # H11 - com.docker.backend GROWTH over time (the 07-05 leak signal)
    $sbx4 = New-Sandbox
    . $script:Loader $sbx4
    Set-Stubs
    $script:FakeNow = [datetime]::new(2026, 10, 7, 12, 0, 0, [DateTimeKind]::Utc)
    Use-Sample (New-Sample -Backend 1.0); $null = Invoke-Rule
    $script:FakeNow = $script:FakeNow.AddMinutes(30)
    Use-Sample (New-Sample -Backend 3.0); $r1 = Invoke-Rule
    $script:FakeNow = $script:FakeNow.AddMinutes(30)
    Use-Sample (New-Sample -Backend 5.2); $r2 = Invoke-Rule
    $t = Get-Transport $sbx4
    $m = Measure-Alerts $t 'com\.docker\.backend GREW 4\.2 GiB'
    $abs = Measure-Alerts $t 'com\.docker\.backend holds'
    Write-Case 'H11' '1.0 -> 3.0 -> 5.2 GiB in 60 min (same processes) -> one growth page, below the absolute ceiling' (($r1 -eq $true) -and ($r2 -eq $false) -and $m -eq 'tg=1 mm=1' -and $abs -eq 'tg=0 mm=0') "r1=$r1 r2=$r2 $m abs=$abs`n$(@($t.Telegram) -join "`n")"

    # H11b - a restarted backend (new PIDs) is a new baseline, not growth
    $sbx5 = New-Sandbox
    . $script:Loader $sbx5
    Set-Stubs
    Use-Sample (New-Sample -Backend 1.0 -BackendIds '100,200'); $null = Invoke-Rule
    $script:FakeNow = $script:FakeNow.AddMinutes(60)
    Use-Sample (New-Sample -Backend 5.2 -BackendIds '300,400'); $r = Invoke-Rule
    $t = Get-Transport $sbx5
    Write-Case 'H11b' 'same growth across a backend restart (new PIDs) -> no page' (($r -eq $true) -and $t.Telegram.Count -eq 0) "result=$r tg=$($t.Telegram.Count)"

    # H11c - growth spread over longer than the window is not growth
    $sbx6 = New-Sandbox
    . $script:Loader $sbx6
    Set-Stubs
    Use-Sample (New-Sample -Backend 1.0); $null = Invoke-Rule
    $script:FakeNow = $script:FakeNow.AddHours(7)
    Use-Sample (New-Sample -Backend 5.2); $r = Invoke-Rule
    $t = Get-Transport $sbx6
    Write-Case 'H11c' '+4.2 GiB over 7 h (window 6 h) -> no page' (($r -eq $true) -and $t.Telegram.Count -eq 0) "result=$r tg=$($t.Telegram.Count)"

    # H12 - alert only: the check's functions take no action on the host
    $tokens = $null; $perr = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile($Script, [ref]$tokens, [ref]$perr)
    $hmFns = @($ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -match 'HostMem' }, $true))
    # Every command and method the functions INVOKE (from the AST, so message
    # text that merely names docker or WSL does not count) must be on this list.
    $allowedCmds = @('Get-Process', 'Add-Type', 'New-Object', 'Join-Path', 'Test-Path', 'Get-Item', 'Get-Date', 'Out-File',
                     'Remove-Item', 'ConvertFrom-Json', 'ConvertTo-Json', 'Measure-Object', 'Sort-Object', 'Where-Object',
                     'ForEach-Object', 'Select-Object', 'Write-LogEntry', 'Send-CatastropheAlert', 'Resolve-Catastrophe',
                     'Get-LoopNowUtc', 'ConvertTo-UtcInstant', 'Format-HostMemGiB', 'ConvertTo-HostMemReason',
                     'Get-HostMemProcess', 'Get-HostMemorySample', 'Update-HostMemAlert', 'Get-HostMemBackendGrowth')
    $allowedMembers = @('Dispose', 'ToString', 'Max', 'Substring', 'ReadAllText', 'WriteAllText', 'AddHours', 'TryParse', 'ContainsKey', 'Keys')
    $hits = @()
    $removeTargets = @()
    foreach ($f in $hmFns) {
        foreach ($c in $f.Body.FindAll({ param($n) $n -is [System.Management.Automation.Language.CommandAst] }, $true)) {
            $name = $c.GetCommandName()
            if (-not $name) { if ($c.InvocationOperator -eq 'Ampersand' -and $c.Extent.Text -match '^\&\s*\$HostMemReader') { continue }; $hits += "$($f.Name): dynamic '$($c.Extent.Text)'"; continue }
            if ($allowedCmds -notcontains $name) { $hits += "$($f.Name): $name" }
            if ($name -eq 'Remove-Item') { $removeTargets += $c.Extent.Text }
        }
        foreach ($c in $f.Body.FindAll({ param($n) $n -is [System.Management.Automation.Language.InvokeMemberExpressionAst] }, $true)) {
            $mn = $c.Member.Extent.Text
            if ($allowedMembers -notcontains $mn) { $hits += "$($f.Name): .$mn()" }
        }
    }
    # Remove-Item may only touch the check's own sentinel / state files.
    $badRemove = @($removeTargets | Where-Object { $_ -notmatch '-LiteralPath \$(p|path) ' })
    Write-Case 'H12' 'the host-memory functions invoke only read / log / alert commands (no kill, restart, docker, wsl or task change)' (($hmFns.Count -ge 3) -and $hits.Count -eq 0 -and $badRemove.Count -eq 0) "functions=$(@($hmFns | ForEach-Object Name) -join ',') hits=$($hits -join '; ') badRemove=$($badRemove -join '; ')"

    # H13 - wired into the health check, BEFORE the Docker-engine step
    $hc = $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Invoke-HealthCheck' }, $true) | Select-Object -First 1
    $txt = if ($hc) { $hc.Body.Extent.Text } else { '' }
    $iMem = $txt.IndexOf('Test-HostMemory'); $iEng = $txt.IndexOf('Confirm-DockerEngine')
    $wired = ($iMem -ge 0) -and ($iEng -ge 0) -and ($iMem -lt $iEng) -and ($txt -match "'host-memory'")
    Write-Case 'H13' 'Invoke-HealthCheck runs Test-HostMemory before Confirm-DockerEngine and records host-memory' ([bool]$wired) "memAt=$iMem engineAt=$iEng"

    # H14 - thresholds are named config values
    $names = @('HostMemAvailableFloorGiB', 'HostMemAvailableFloorPercent', 'HostMemCommitMaxPercent', 'HostMemCommitHeadroomFloorGiB',
               'HostMemVmmemMaxGiB', 'HostMemBackendMaxGiB', 'HostMemBackendGrowthGiB', 'HostMemBackendGrowthWindowHours',
               'HostMemClearMarginPercent', 'HostMemAlertCooldownHours')
    $missing = @($names | Where-Object { $null -eq (Get-Variable -Name $_ -ValueOnly -ErrorAction SilentlyContinue) })
    Write-Case 'H14' 'every threshold is a named config value' ($missing.Count -eq 0) "missing=$($missing -join ',')"

    # H15 - live: the REAL counters, read-only, senders stubbed by the sandbox
    if ($Live) {
        . $script:Loader $sbx
        $script:WatchdogClock = $null
        $me = [Diagnostics.Process]::GetCurrentProcess()
        $cpu0 = $me.TotalProcessorTime; $ws0 = $me.WorkingSet64
        $sw = [Diagnostics.Stopwatch]::StartNew()
        $r = Invoke-Rule
        $ms = "$($sw.ElapsedMilliseconds) cpu_ms=$([int](($me.Refresh(), $me.TotalProcessorTime)[1] - $cpu0).TotalMilliseconds) ws_delta_mb=$([int](($me.WorkingSet64 - $ws0) / 1MB))"
        $line = @(Get-Log $sbx | Where-Object { $_ -match 'hostmem: avail=' } | Select-Object -Last 1)
        $real = ($line.Count -eq 1) -and ($line[0] -match 'avail=\d+\.\dGiB of \d+\.\dGiB') -and ($line[0] -match 'commit=\d+\.\d/\d+\.\dGiB')
        Write-Case 'H15' 'live read-only pass prints real current values (no alert leaves the sandbox)' ($real -and -not $script:RuleError) "result=$r ms=$ms err=$script:RuleError`n$($line -join '')"
    }
} finally {
    foreach ($d in @($sbx, $sbx2, $sbx3, $sbx4, $sbx5, $sbx6)) { if ($d -and (Test-Path $d)) { Remove-Item $d -Recurse -Force -ErrorAction SilentlyContinue } }
}

Write-Host ""
Write-Host ("RESULTS: " + ($script:Results -join ' '))
Write-Host "FAILED: $script:Failures"
exit $script:Failures
