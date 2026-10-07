# test-watchdog-host-memory.ps1 - the test for stack-watchdog.ps1's HOST MEMORY
# check (Test-HostMemory). Items hm-watchdog and hm-wset, 2026-10-07.
# hm-wset: the vmmemWSL key pages on WORKING SET against a line derived from the
# .wslconfig cap (cap + 6 GiB, default 70); private bytes are information only.
# -Vmmem in New-Sample is the WORKING SET, -VmmemPriv the private bytes.
#
# Nothing watched host memory: on 2026-10-07 vmmemWSL held 112.3 GiB of the
# 127.7 GiB host with 24.3 GiB available and no alert fired (the 2026-07-05 OOM
# pattern, which wedged WSL until a reboot). This test loads the watchdog's
# FUNCTIONS only (never its main block) into a sandbox whose PROJECT_DIR is a
# temp directory, exactly as test-watchdog-portal-alerts.ps1 does:
#   - the counters are STUBBED through $HostMemReader (a scriptblock whose TEXT
#     runs as the reader in its own runspace; the sample travels in
#     $HostMemReaderInput), and the clock through $WatchdogClock;
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
#   ... -ChildHang  (internal) the H18 child: one pass with a reader stuck 60 s.
# Exit code = number of failed cases. The run ends with [Environment]::Exit, as
# the watchdog's check mode does when a reader is still hung (H16-H18 leave
# readers blocked in an uninterruptible 60 s .NET sleep on purpose).

[CmdletBinding()]
param(
    [string]$Script = '',
    [switch]$Live,
    [switch]$ChildHang
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
          [object]$Vmmem = 30.0, [object]$VmmemPriv = -1, [string]$VmmemState = 'present',
          [object]$LineGiB = 70, [string]$LineNote = 'cap 64 GiB + 6', [bool]$NoLine = $false,
          [object]$Backend = 0.31, [string]$BackendIds = '5424,19648', [string]$BackendState = 'present',
          [hashtable]$Errors = @{})
    $g = [double]1GB
    $b = { param($v) if ($null -eq $v) { $null } else { [double]$v * $g } }
    if ($VmmemPriv -is [int] -and $VmmemPriv -eq -1) { $VmmemPriv = if ($null -eq $Vmmem) { $null } else { [double]$Vmmem + 20.0 } }
    return [pscustomobject]@{
        TotalBytes = (& $b $Total); AvailBytes = (& $b $Avail)
        CommitBytes = (& $b $Commit); CommitLimitBytes = (& $b $Limit)
        Vmmem = [pscustomobject]@{ State = $VmmemState; PrivateBytes = (& $b $VmmemPriv); WorkingSetBytes = (& $b $Vmmem); Id = '30036'; Count = 1; Error = $Errors['vmmemWSL'] }
        VmmemLine = $(if ($NoLine) { $null } else { [pscustomobject]@{ LineGiB = $LineGiB; CapGiB = 64; Note = $LineNote } })
        Backend = [pscustomobject]@{ State = $BackendState; PrivateBytes = (& $b $Backend); Id = $BackendIds; Count = 2; Error = $Errors['com.docker.backend'] }
        Errors = $Errors
    }
}

function Use-Sample {
    param($Sample)
    $script:HostMemReaderInput = $Sample
    $script:HostMemReader = { $HostMemReaderInput }
}

# A reader stuck in an UNINTERRUPTIBLE call (a stop request cannot end it), as a
# hung PDH or process read would be.
$script:HangReader = { [Threading.Thread]::Sleep(60000) }

function Invoke-Rule {
    $script:RuleError = $null
    try { return [bool](@(Test-HostMemory) | Select-Object -Last 1) }
    catch { $script:RuleError = $_.Exception.Message; return $null }
}

$script:FakeNow = [datetime]::new(2026, 10, 7, 12, 0, 0, [DateTimeKind]::Utc)
function Set-Stubs {
    $script:WatchdogClock = { $script:FakeNow }
}

# --- H18's child: one pass with a hung reader, then the check-mode exit path.
if ($ChildHang) {
    $c = New-Sandbox
    . $script:Loader $c
    $script:HostMemReadTimeoutSeconds = 3
    $script:HostMemReader = $script:HangReader
    $r = Invoke-Rule
    Write-Host "CHILD result=$r err=$script:RuleError"
    Remove-Item $c -Recurse -Force -ErrorAction SilentlyContinue
    if (Get-Command Exit-WatchdogProcess -ErrorAction SilentlyContinue) { Exit-WatchdogProcess -Code 7 }
    exit 7
}

$sbx = $null; $sbx2 = $null; $sbx3 = $null; $sbx4 = $null; $sbx5 = $null; $sbx6 = $null
$sbx7 = $null; $sbx8 = $null; $sbx9 = $null; $sbx10 = $null; $sbx11 = $null; $sbx12 = $null; $sbx13 = $null; $sbx14 = $null
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
    $okLine = @($log | Where-Object { $_ -match '\[INFO\] hostmem: avail=90\.0GiB of 127\.7GiB.*commit=40\.0/199\.7GiB.*vmmemWSL=ws=30\.0GiB priv=50\.0GiB line=70GiB \(cap 64 GiB \+ 6\).*com\.docker\.backend=0\.31GiB.*-> OK$' })
    Write-Case 'H1' 'healthy sample -> true, nothing sent, one OK status line' (($r -eq $true) -and $t.Telegram.Count -eq 0 -and $t.Mattermost.Count -eq 0 -and $okLine.Count -eq 1) "result=$r err=$script:RuleError`n$($log -join "`n")"

    # H2 - the measured 2026-10-07 state: vmmemWSL 112.3 of 127.7, 24.3 available
    Use-Sample (New-Sample -Avail 24.3 -Commit 150.0 -Vmmem 112.3)
    $r = Invoke-Rule
    $t = Get-Transport $sbx
    $m = Measure-Alerts $t 'vmmemWSL'
    $other = @($t.Telegram | Where-Object { $_ -notmatch 'vmmemWSL holds' }).Count
    $warnLine = @(Get-Log $sbx | Where-Object { $_ -match '\[WARN\] hostmem: .*-> ALERT hostmem-vmmem' }).Count
    Write-Case 'H2' 'crossing (working set 112.3 GiB vs the 70 GiB line) -> false, ONE page on Telegram + MM mirror, WARN status line' (($r -eq $false) -and $m -eq 'tg=1 mm=1' -and $other -eq 0 -and $warnLine -eq 1) "result=$r $m other=$other err=$script:RuleError`n$(@($t.Telegram) -join "`n")"

    # H3 - staying high next pass: false, no second page (cooldown), logged
    $r = Invoke-Rule
    $t = Get-Transport $sbx
    $m = Measure-Alerts $t 'vmmemWSL holds'
    $still = @(Get-Log $sbx | Where-Object { $_ -match 'HOSTMEM \[hostmem-vmmem\] (still )?firing; not re-paged' }).Count
    Write-Case 'H3' 'staying high -> false, throttled (no second page), still-firing line' (($r -eq $false) -and $m -eq 'tg=1 mm=1' -and $still -ge 1) "result=$r $m still=$still"

    # H3b - the cooldown is a re-page interval, not a mute
    $sent = Join-Path $sbx 'logs\.hostmem-firing-hostmem-vmmem'
    $script:FakeNow = $script:FakeNow.AddHours(3)
    $tga = Join-Path $sbx 'logs\.tg-alert-hostmem-vmmem'
    if (Test-Path $tga) { (Get-Item $tga).LastWriteTime = (Get-Date).AddHours(-3) }
    $r = Invoke-Rule
    $t = Get-Transport $sbx
    $m = Measure-Alerts $t 'vmmemWSL holds'
    Write-Case 'H3b' 'still high after the cooldown -> paged again (once)' (($r -eq $false) -and $m -eq 'tg=2 mm=2') "result=$r $m"

    # H4 - inside the hysteresis band (below 70, above the 63 all-clear line): no RESOLVED
    Use-Sample (New-Sample -Vmmem 66.0)
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
    $gone = (-not (Test-Path $sent)) -and (Test-Path $tga)
    Write-Case 'H5' 'recovering -> true, ONE RESOLVED (all-clear), then silent; firing marker gone, 1 h Telegram floor kept' (($r1 -eq $true) -and ($r2 -eq $true) -and $res -match '^tg=1 ' -and $gone) "r1=$r1 r2=$r2 $res rearmed=$gone`n$(@($t.Telegram) -join "`n")"

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
                     'Get-HostMemProcess', 'Get-HostMemVmmemLine', 'Get-HostMemorySample', 'Update-HostMemAlert', 'Get-HostMemBackendGrowth',
                     'Get-HostMemorySampleBounded', 'New-HostMemUnknownSample')
    $allowedMembers = @('Dispose', 'ToString', 'Max', 'Substring', 'ReadAllText', 'WriteAllText', 'AddHours', 'TryParse', 'ContainsKey', 'Keys',
                        'CreateRunspace', 'CreateDefault2', 'Open', 'SetVariable', 'Create', 'AddScript', 'BeginInvoke', 'WaitOne',
                        'BeginStop', 'EndInvoke', 'Trim', 'ReadAllLines', 'Parse', 'ToLowerInvariant', 'ToUpperInvariant')
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
    $badRemove = @($removeTargets | Where-Object { $_ -notmatch '-LiteralPath \$(p|path|firingPath) ' })
    Write-Case 'H12' 'the host-memory functions invoke only read / log / alert commands (no kill, restart, docker, wsl or task change)' (($hmFns.Count -ge 3) -and $hits.Count -eq 0 -and $badRemove.Count -eq 0) "functions=$(@($hmFns | ForEach-Object Name) -join ',') hits=$($hits -join '; ') badRemove=$($badRemove -join '; ')"

    # H13 - wired into the health check, BEFORE the Docker-engine step
    $hc = $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Invoke-HealthCheck' }, $true) | Select-Object -First 1
    $txt = if ($hc) { $hc.Body.Extent.Text } else { '' }
    # Positions of the CALLS (command ASTs), so a comment naming either one does not count.
    $iMem = -1; $iEng = -1
    if ($hc) {
        foreach ($c in $hc.Body.FindAll({ param($n) $n -is [System.Management.Automation.Language.CommandAst] }, $true)) {
            $cn = $c.GetCommandName()
            if ($cn -eq 'Test-HostMemory' -and $iMem -lt 0) { $iMem = $c.Extent.StartOffset }
            if ($cn -eq 'Confirm-DockerEngine' -and $iEng -lt 0) { $iEng = $c.Extent.StartOffset }
        }
    }
    $wired = ($iMem -ge 0) -and ($iEng -ge 0) -and ($iMem -lt $iEng) -and ($txt -match "'host-memory'")
    Write-Case 'H13' 'Invoke-HealthCheck runs Test-HostMemory before Confirm-DockerEngine and records host-memory' ([bool]$wired) "memAt=$iMem engineAt=$iEng"

    # H14 - thresholds are named config values
    $names = @('HostMemAvailableFloorGiB', 'HostMemAvailableFloorPercent', 'HostMemCommitMaxPercent', 'HostMemCommitHeadroomFloorGiB',
               'HostMemVmmemMaxGiB', 'HostMemBackendMaxGiB', 'HostMemBackendGrowthGiB', 'HostMemBackendGrowthWindowHours',
               'HostMemClearMarginPercent', 'HostMemAlertCooldownHours', 'HostMemAvailableClearMinGiB', 'HostMemReadTimeoutSeconds')
    $missing = @($names | Where-Object { $null -eq (Get-Variable -Name $_ -ValueOnly -ErrorAction SilentlyContinue) })
    Write-Case 'H14' 'every threshold is a named config value' ($missing.Count -eq 0) "missing=$($missing -join ',')"

    # H16 - F1(a): a reader stuck 60 s is abandoned at the deadline
    $sbx7 = New-Sandbox
    . $script:Loader $sbx7
    Set-Stubs
    $script:HostMemReadTimeoutSeconds = 3
    $script:HostMemReader = $script:HangReader
    $rsBefore = @(Get-Runspace).Count
    $sw = [Diagnostics.Stopwatch]::StartNew()
    $r = Invoke-Rule
    $ms = $sw.ElapsedMilliseconds
    $t = Get-Transport $sbx7
    $unk = @(Get-Log $sbx7 | Where-Object { $_ -match '\[WARN\] hostmem: avail=UNKNOWN\(timeout after 3s\).*-> UNKNOWN ' }).Count
    $m = Measure-Alerts $t 'cannot read'
    Write-Case 'H16' 'reader stuck 60 s, deadline 3 s -> back within 5 s, false, UNKNOWN(timeout) line, ONE page' (($r -eq $false) -and $ms -lt 5000 -and $unk -eq 1 -and $m -eq 'tg=1 mm=1') "result=$r ms=$ms unk=$unk $m err=$script:RuleError"

    # H16b - while it stays hung: later passes report it, start no new reader, page no more
    $times = @()
    for ($i = 0; $i -lt 5; $i++) {
        $script:FakeNow = $script:FakeNow.AddMinutes(10)
        $sw = [Diagnostics.Stopwatch]::StartNew(); $null = Invoke-Rule; $times += $sw.ElapsedMilliseconds
    }
    $rsAfter = @(Get-Runspace).Count
    $t = Get-Transport $sbx7
    $still = @(Get-Log $sbx7 | Where-Object { $_ -match 'avail=UNKNOWN\(timeout: an earlier read is still hung' }).Count
    $m = Measure-Alerts $t 'cannot read'
    Write-Case 'H16b' '5 more passes while it is hung -> each fast, UNKNOWN(still hung), no new runspace, no second page' (($still -eq 5) -and ($rsAfter - $rsBefore) -le 1 -and (($times | Measure-Object -Maximum).Maximum -lt 1000) -and $m -eq 'tg=1 mm=1') "runspaces $rsBefore -> $rsAfter; pass ms $($times -join ',') ; still=$still $m"

    # H16c - a timed-out reader that DOES finish is disposed and reading resumes
    $sbx8 = New-Sandbox
    . $script:Loader $sbx8
    Set-Stubs
    $script:HostMemReadTimeoutSeconds = 1
    $script:HostMemReader = { Start-Sleep -Seconds 3; $HostMemReaderInput }
    $script:HostMemReaderInput = New-Sample
    $r1 = Invoke-Rule
    Start-Sleep -Seconds 1
    $script:HostMemReadTimeoutSeconds = 15
    Use-Sample (New-Sample)
    $r2 = Invoke-Rule
    $t = Get-Transport $sbx8
    $pend = $script:HostMemPendingReader
    Write-Case 'H16c' 'timed-out reader that ends -> disposed next pass; reading resumes; one RESOLVED' (($r1 -eq $false) -and ($r2 -eq $true) -and ($null -eq $pend) -and ((Measure-Alerts $t 'RESOLVED') -match '^tg=1 ')) "r1=$r1 r2=$r2 pendingCleared=$($null -eq $pend) $(Measure-Alerts $t 'RESOLVED') err=$script:RuleError"

    # H17 - F1(b): with a hung reader the health pass still reaches Confirm-DockerEngine in time
    $sbx9 = New-Sandbox
    . $script:Loader $sbx9
    Set-Stubs
    $script:HostMemReadTimeoutSeconds = 3
    $script:HostMemReader = $script:HangReader
    $script:EngineCalled = $false
    function Confirm-DockerEngine { $script:EngineCalled = $true; return $false }
    function Confirm-HostTaskByPort { param($TaskName, $Port, $Label) }
    $cwd = Get-Location
    $sw = [Diagnostics.Stopwatch]::StartNew()
    $hcErr = $null
    try { $null = Invoke-HealthCheck } catch { $hcErr = $_.Exception.Message }
    $ms = $sw.ElapsedMilliseconds
    Set-Location $cwd
    $t = Get-Transport $sbx9
    $unk = @(Get-Log $sbx9 | Where-Object { $_ -match 'hostmem: avail=UNKNOWN\(timeout after 3s\)' }).Count
    Write-Case 'H17' 'Invoke-HealthCheck with a reader stuck 60 s -> Confirm-DockerEngine runs, pass ends within 5 s, UNKNOWN(timeout), one page' ($script:EngineCalled -and $ms -lt 5000 -and -not $hcErr -and $unk -eq 1 -and (Measure-Alerts $t 'cannot read') -eq 'tg=1 mm=1') "engine=$script:EngineCalled ms=$ms err=$hcErr unk=$unk $(Measure-Alerts $t 'cannot read')"

    # H17b - F1(b): a memory check that THROWS cannot stop the pass either
    . $script:Loader $sbx9
    Set-Stubs
    $script:EngineCalled = $false
    function Confirm-DockerEngine { $script:EngineCalled = $true; return $false }
    function Confirm-HostTaskByPort { param($TaskName, $Port, $Label) }
    function Test-HostMemory { throw 'memory check blew up' }
    $hcErr = $null
    try { $null = Invoke-HealthCheck } catch { $hcErr = $_.Exception.Message }
    Set-Location $cwd
    $esc = @(Get-Log $sbx9 | Where-Object { $_ -match 'escaped its own guard: memory check blew up' }).Count
    Write-Case 'H17b' 'Test-HostMemory throws -> Invoke-HealthCheck still reaches Confirm-DockerEngine, logs it' ($script:EngineCalled -and -not $hcErr -and $esc -eq 1) "engine=$script:EngineCalled err=$hcErr logged=$esc"
    . $script:Loader $sbx

    # H18 - F1: a hung reader cannot keep the watchdog PROCESS alive (IgnoreNew would drop later passes)
    $tok = $null; $pe = $null
    $mainAst = [System.Management.Automation.Language.Parser]::ParseFile($Script, [ref]$tok, [ref]$pe)
    $sw0 = $mainAst.FindAll({ param($n) $n -is [System.Management.Automation.Language.SwitchStatementAst] }, $false) | Select-Object -Last 1
    $checkClause = ''
    if ($sw0) { foreach ($cl in $sw0.Clauses) { if ($cl.Item1.Extent.Text -match '"check"') { $checkClause = $cl.Item2.Extent.Text } } }
    $childArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $MyInvocation.MyCommand.Path, '-Script', $Script, '-ChildHang')
    $sw = [Diagnostics.Stopwatch]::StartNew()
    $childOut = & powershell.exe @childArgs 2>&1
    $childCode = $LASTEXITCODE
    $ms = $sw.ElapsedMilliseconds
    $wired = $checkClause -match 'Exit-WatchdogProcess'
    Write-Case 'H18' 'a pass whose reader is stuck 60 s: the process exits soon after the 3 s deadline (exit code kept), and -Mode check uses that exit' (($ms -lt 20000) -and $childCode -eq 7 -and $wired) "child wall ms=$ms exit=$childCode checkModeWired=$wired`n$(@($childOut | Where-Object { $_ -match 'CHILD' }) -join '')"

    # H19 - F2: values swinging across the band for 2 h page once, no ALERT/RESOLVED ping-pong
    $sbx10 = New-Sandbox
    . $script:Loader $sbx10
    Set-Stubs
    $script:FakeNow = [datetime]::new(2026, 10, 7, 12, 0, 0, [DateTimeKind]::Utc)
    for ($i = 0; $i -lt 12; $i++) {
        $av = if ($i % 2 -eq 0) { 15.5 } else { 17.8 }
        $vm = if ($i % 2 -eq 0) { 70.5 } else { 62.0 }
        Use-Sample (New-Sample -Avail $av -Vmmem $vm)
        $null = Invoke-Rule
        $script:FakeNow = $script:FakeNow.AddMinutes(10)
    }
    $t = Get-Transport $sbx10
    $avA = Measure-Alerts $t 'HOST MEMORY LOW'
    $avR = Measure-Alerts $t 'RESOLVED.*available'
    $vmA = Measure-Alerts $t 'vmmemWSL holds'
    $vmR = Measure-Alerts $t 'RESOLVED.*vmmemWSL'
    $ok19 = ($avA -eq 'tg=1 mm=1') -and ($avR -eq 'tg=0 mm=0') -and ($vmA -eq 'tg=1 mm=1') -and ($vmR -match '^tg=[01] mm=0$')
    Write-Case 'H19' '12 passes (2 h) of 15.5<->17.8 GiB available and 70.5<->62.0 GiB vmmemWSL -> one page each, at most one RESOLVED' $ok19 "available: alert $avA resolved $avR ; vmmemWSL: alert $vmA resolved $vmR"

    # H19b - the cooldown is per page, not a mute: the next swing after 2 h pages again
    Use-Sample (New-Sample -Avail 15.5 -Vmmem 70.5)
    foreach ($k in @('hostmem-available', 'hostmem-vmmem')) {
        $f = Join-Path $sbx10 "logs\.tg-alert-$k"
        if (Test-Path $f) { (Get-Item $f).LastWriteTime = (Get-Date).AddHours(-2) }
    }
    $null = Invoke-Rule
    $t = Get-Transport $sbx10
    Write-Case 'H19b' 'at 2 h after the first page, still swinging -> paged once more' (((Measure-Alerts $t 'HOST MEMORY LOW') -eq 'tg=2 mm=2') -and ((Measure-Alerts $t 'vmmemWSL holds') -eq 'tg=2 mm=2')) "available $(Measure-Alerts $t 'HOST MEMORY LOW') vmmemWSL $(Measure-Alerts $t 'vmmemWSL holds')"
    . $script:Loader $sbx

    # --- hm-wset: working set vs the cap-derived line; private bytes informational
    $sbx11 = New-Sandbox
    . $script:Loader $sbx11
    Set-Stubs
    $script:FakeNow = [datetime]::new(2026, 10, 7, 13, 0, 0, [DateTimeKind]::Utc)

    # H20 - the first live alert: private 84 GiB, working set 55 GiB -> no page
    Use-Sample (New-Sample -Vmmem 55.0 -VmmemPriv 84.0)
    $r = Invoke-Rule
    $t = Get-Transport $sbx11
    $ln = @(Get-Log $sbx11 | Where-Object { $_ -match '\[INFO\] hostmem: .*vmmemWSL=ws=55\.0GiB priv=84\.0GiB line=70GiB.*-> OK$' }).Count
    Write-Case 'H20' 'private 84 GiB + working set 55 GiB -> true, no page, status line shows ws and priv' (($r -eq $true) -and $t.Telegram.Count -eq 0 -and $t.Mattermost.Count -eq 0 -and $ln -eq 1) "result=$r tg=$($t.Telegram.Count) line=$ln err=$script:RuleError`n$((Get-Log $sbx11) -join "`n")"

    # H21 - working set 71 GiB (over the 70 line) pages, whatever private is
    Use-Sample (New-Sample -Vmmem 71.0 -VmmemPriv 72.0)
    $r = Invoke-Rule
    $t = Get-Transport $sbx11
    $m = Measure-Alerts $t 'vmmemWSL holds 71\.0 GiB of physical RAM'
    $other = @($t.Telegram | Where-Object { $_ -notmatch 'vmmemWSL holds' }).Count
    Write-Case 'H21' 'working set 71 GiB -> false, ONE vmmem page (message names working set), nothing else' (($r -eq $false) -and $m -eq 'tg=1 mm=1' -and $other -eq 0) "result=$r $m other=$other`n$(@($t.Telegram) -join "`n")"

    # H21b - the edge: just under the line does not page (fresh sandbox)
    $sbx12 = New-Sandbox
    . $script:Loader $sbx12
    Set-Stubs
    Use-Sample (New-Sample -Vmmem 69.9 -VmmemPriv 110.0)
    $r = Invoke-Rule
    $t = Get-Transport $sbx12
    Write-Case 'H21b' 'working set 69.9 GiB with private 110 GiB -> true, no page' (($r -eq $true) -and $t.Telegram.Count -eq 0) "result=$r tg=$($t.Telegram.Count)"

    # H22 - recovery: one all-clear once the working set is under 63 GiB, private still high
    . $script:Loader $sbx11   # the loader points PROJECT_DIR at one sandbox at a time
    Set-Stubs
    # (the catastrophe path keeps a 1 h Telegram floor per key; age it as H3b does)
    $tgb = Join-Path $sbx11 'logs\.tg-alert-hostmem-vmmem'
    if (Test-Path $tgb) { (Get-Item $tgb).LastWriteTime = (Get-Date).AddHours(-3) }
    Use-Sample (New-Sample -Vmmem 55.0 -VmmemPriv 84.0)
    $r1 = Invoke-Rule
    $r2 = Invoke-Rule
    $t = Get-Transport $sbx11
    $res = Measure-Alerts $t 'RESOLVED ai-stack: .*vmmemWSL'
    Write-Case 'H22' 'working set back to 55 GiB (private still 84) -> true, ONE RESOLVED, then silent' (($r1 -eq $true) -and ($r2 -eq $true) -and $res -match '^tg=1 ') "r1=$r1 r2=$r2 $res`n$((Get-Log $sbx11) -join "`n")"

    # H23 - unreadable working set -> UNKNOWN page (hostmem-unreadable), no vmmem breach
    $sbx13 = New-Sandbox
    . $script:Loader $sbx13
    Set-Stubs
    $u = New-Sample -Vmmem $null -VmmemPriv 84.0 -VmmemState 'unknown' -Errors @{ vmmemWSL = 'working set read as 0 for PID 1 (access denied?)' }
    Use-Sample $u
    $r = Invoke-Rule
    $t = Get-Transport $sbx13
    $unk = Measure-Alerts $t 'cannot read: vmmemWSL'
    $vmPage = Measure-Alerts $t 'vmmemWSL holds'
    $ul = @(Get-Log $sbx13 | Where-Object { $_ -match '\[WARN\] hostmem: .*vmmemWSL=UNKNOWN\(working set read as 0.*-> UNKNOWN vmmemWSL' }).Count
    Write-Case 'H23' 'unreadable working set -> false, UNKNOWN vmmemWSL line and one cannot-read page, no vmmem breach page' (($r -eq $false) -and $unk -eq 'tg=1 mm=1' -and $vmPage -eq 'tg=0 mm=0' -and $ul -eq 1) "result=$r $unk $vmPage line=$ul`n$((Get-Log $sbx13) -join "`n")"

    # H24 - cap parsing (Get-HostMemVmmemLine on temp .wslconfig files)
    $cfgDir = Join-Path ([IO.Path]::GetTempPath()) ("hmwd-cfg-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
    New-Item -ItemType Directory -Path $cfgDir -Force | Out-Null
    $hasCapFn = [bool](Get-Command Get-HostMemVmmemLine -ErrorAction SilentlyContinue)
    function Test-Cap { param([string]$Name, [string]$Body)
        $f = Join-Path $cfgDir $Name
        if ($null -ne $Body) { [IO.File]::WriteAllText($f, $Body) }
        if (-not $hasCapFn) { return [pscustomobject]@{ LineGiB = $null; Note = 'Get-HostMemVmmemLine missing' } }
        return Get-HostMemVmmemLine -Path $f -DefaultGiB 70 -MarginGiB 6
    }
    $c1 = Test-Cap 'a.wslconfig' "# comment`n[wsl2]`nmemory=64GB`n`nswap=16GB`n"
    $c2 = Test-Cap 'b.wslconfig' "[wsl2]`nmemory = 32GB  # trimmed`n"
    $c3 = Test-Cap 'c.wslconfig' "[wsl2]`nmemory=65536MB`n"
    $c4 = Test-Cap 'd.wslconfig' "[experimental]`nmemory=999GB`n[wsl2]`nswap=1GB`n"
    $c5 = Test-Cap 'e.wslconfig' "[wsl2]`nmemory=lots`n"
    $c6 = Test-Cap 'missing.wslconfig' $null
    $c7 = Test-Cap 'g.wslconfig' "[wsl2]`nmemory=0GB`n"
    $c8 = Test-Cap 'h.wslconfig' "A[[[=`n=memory=`n"
    Write-Case 'H24' 'memory=64GB -> 70 GiB line; 32GB -> 38; 65536MB -> 70; other sections ignored' (($c1.LineGiB -eq 70) -and ($c2.LineGiB -eq 38) -and ($c3.LineGiB -eq 70) -and ($c4.LineGiB -eq 70 -and $c4.Note -match '^default - no memory=')) "c1=$($c1.LineGiB) c2=$($c2.LineGiB) c3=$($c3.LineGiB) c4=$($c4.LineGiB)/$($c4.Note)"
    $okDef = @(@($c5, $c6, $c7, $c8) | Where-Object { $_.LineGiB -eq 70 -and $_.Note -match '^default - \S' }).Count -eq 4
    Write-Case 'H24b' 'garbled / missing / zero / no-key .wslconfig -> 70 GiB default with a visible note, never a throw' $okDef "notes: $($c5.Note) | $($c6.Note) | $($c7.Note) | $($c8.Note)"
    Remove-Item $cfgDir -Recurse -Force -ErrorAction SilentlyContinue

    # H25 - the line is part of the status line and the page: a 32GB cap pages at 38 GiB; no cap info -> default with a note
    $sbx14 = New-Sandbox
    . $script:Loader $sbx14
    Set-Stubs
    Use-Sample (New-Sample -Vmmem 40.0 -LineGiB 38 -LineNote 'cap 32 GiB + 6')
    $r = Invoke-Rule
    $t = Get-Transport $sbx14
    $m = Measure-Alerts $t 'vmmemWSL holds 40\.0 GiB .*alert at 38 GiB'
    Use-Sample (New-Sample -Vmmem 30.0 -NoLine $true)
    $null = Invoke-Rule
    $nl = @(Get-Log $sbx14 | Where-Object { $_ -match '\[INFO\] hostmem: .*ws=30\.0GiB priv=50\.0GiB line=70GiB \(default - no cap info' }).Count
    Write-Case 'H25' 'cap 32GB -> pages at 40 GiB working set (line 38); a sample without cap info -> 70 GiB line with a visible note' (($r -eq $false) -and $m -eq 'tg=1 mm=1' -and $nl -eq 1) "result=$r $m note=$nl`n$((Get-Log $sbx14) -join "`n")"

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
        $real = ($line.Count -eq 1) -and ($line[0] -match 'avail=\d+\.\dGiB of \d+\.\dGiB') -and ($line[0] -match 'commit=\d+\.\d/\d+\.\dGiB') -and ($line[0] -match 'vmmemWSL=(ws=\d+\.\dGiB priv=(\d+\.\dGiB|UNKNOWN) line=\d+GiB|absent)')
        Write-Case 'H15' 'live read-only pass prints real current values (no alert leaves the sandbox)' ($real -and -not $script:RuleError) "result=$r ms=$ms err=$script:RuleError`n$($line -join '')"
    }
} finally {
    foreach ($d in @($sbx, $sbx2, $sbx3, $sbx4, $sbx5, $sbx6, $sbx7, $sbx8, $sbx9, $sbx10, $sbx11, $sbx12, $sbx13, $sbx14)) { if ($d -and (Test-Path $d)) { Remove-Item $d -Recurse -Force -ErrorAction SilentlyContinue } }
}

Write-Host ""
Write-Host ("RESULTS: " + ($script:Results -join ' '))
Write-Host "FAILED: $script:Failures"
[Environment]::Exit($script:Failures)
