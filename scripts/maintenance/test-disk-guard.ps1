# test-disk-guard.ps1 - tests scripts/maintenance/disk-guard.ps1 with EVERYTHING faked
# (item cf-disk-alert, 2026-09-29).
#
# The guard under test runs from a copy inside a temp sandbox tree, so its repo root, its
# logs\ and its .venv are the sandbox's:
#   - <sandbox>\.venv\Scripts\python.exe is a COMPILED RECORDER: it appends
#     "<script name><TAB><first argument>" to <sandbox>\calls.log, prints a canned line for
#     auto_reclaim.py / sweep_tmp.py, and exits with the number in <sandbox>\rc-<script>.txt
#     (0 when absent). No Python runs, so auto_reclaim, sweep_tmp, mm_post and
#     telegram_notify are never executed: nothing is reclaimed, posted or sent.
#   - Get-CimInstance, docker, Invoke-RestMethod, Start-Sleep, Register-ScheduledTask (and,
#     for one case, Get-Date) are
#     shadowed by functions defined here (a script invoked with & resolves commands through
#     its caller's scope, and functions win over cmdlets and applications). The C: figures
#     are whatever the case sets; DOCKER_HOST is also forced to the dead tcp://127.0.0.1:1.
#   - the space scan is pointed at a fake tree in the sandbox (only when the guard under
#     test has -SpaceScanRoots; an older guard gets no scan parameters at all).
#
# NEVER run disk-guard.ps1 itself on a test: on the host it reclaims Docker space and can
# stop the gym workers.
#
# Usage:  powershell -NoProfile -File scripts\maintenance\test-disk-guard.ps1 [-Script <path>]
#         (-Script another disk-guard.ps1, e.g. a base blob, for RED)
# Exit code = number of failed cases.

[CmdletBinding()]
param([string]$Script = '')

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $Script) { $Script = Join-Path $here 'disk-guard.ps1' }
$Script = (Resolve-Path $Script).Path
$realRepo = (Resolve-Path (Join-Path $here '..\..')).Path
$env:DOCKER_HOST = 'tcp://127.0.0.1:1'
$failures = 0
$results = @()

# Shared state the fakes write to. GLOBAL on purpose: inside a function called from the child
# script, $script: would resolve to the GUARD's script scope, not this one.
$global:DGT = @{ Free = 0.0; Size = 930.5 * 1GB; Workers = @(); Docker = @(); Rest = @(); Auth = @(); Tick = 0; Clock = [datetime]'2026-01-01' }

function Get-CimInstance { param($ClassName, $Filter) [pscustomobject]@{ FreeSpace = $global:DGT.Free; Size = $global:DGT.Size } }
function docker { $global:DGT.Docker += ($args -join ' '); if ($args[0] -eq 'ps') { return $global:DGT.Workers } }
function Invoke-RestMethod { param($Method = 'GET', $Uri, $ContentType, $Body, $TimeoutSec, $Headers) $global:DGT.Rest += "$Method $Uri"; $global:DGT.Auth += [string]$(if ($Headers) { $Headers['Authorization'] } else { '' }); [pscustomobject]@{ instances = @() } }
function Start-Sleep { }
# Get-Date is real unless a case sets DGT.Tick: then every call advances a fake clock by Tick
# seconds (the space walk's budget checks become deterministic). [DateTime]::UtcNow is untouched.
function Get-Date {
    if ($global:DGT -and $global:DGT.Tick) { $global:DGT.Clock = $global:DGT.Clock.AddSeconds($global:DGT.Tick); return $global:DGT.Clock }
    Microsoft.PowerShell.Utility\Get-Date @args
}
function Register-ScheduledTask { throw 'the disk-guard test must never register a task' }

function Write-Case([string]$Id, [string]$Title, [bool]$Pass, [string]$Detail) {
    $v = if ($Pass) { 'PASS' } else { 'FAIL' }
    if (-not $Pass) { $script:failures++ }
    $script:results += "$Id $v"
    Write-Host ("## {0} - {1}    {2}" -f $Id, $Title, $v)
    if ($Detail) { foreach ($l in ($Detail -split "`n")) { Write-Host "    $l" } }
}

function New-Sandbox {
    $root = Join-Path ([IO.Path]::GetTempPath()) ('cfdg-sbx-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
    foreach ($d in @('logs', 'scripts\maintenance', 'scripts\sysadmin-mcp', '.venv\Scripts', 'scan')) {
        New-Item -ItemType Directory -Path (Join-Path $root $d) -Force | Out-Null
    }
    Copy-Item -LiteralPath $Script -Destination (Join-Path $root 'scripts\maintenance\disk-guard.ps1')
    foreach ($f in @('auto_reclaim.py', 'sweep_tmp.py', 'mm_post.py', 'telegram_notify.py')) {
        [IO.File]::WriteAllText((Join-Path $root "scripts\sysadmin-mcp\$f"), "# fake - never run`n")
    }
    $rec = @"
using System; using System.IO;
public static class CfdgRecorder__SFX__ { public static int Main(string[] a) {
  string root = Path.GetFullPath(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "..", ".."));
  string name = a.Length > 0 ? Path.GetFileName(a[0]) : "";
  File.AppendAllText(Path.Combine(root, "calls.log"), name + "\t" + (a.Length > 1 ? a[1] : "") + "\n");
  if (name == "auto_reclaim.py") Console.WriteLine("RECLAIM fake: images 1.00 GB, build cache 0 B, anon volumes 0 B");
  if (name == "sweep_tmp.py") Console.WriteLine("sweep fake: 0 files");
  string rc = Path.Combine(root, "rc-" + name + ".txt");
  return File.Exists(rc) ? int.Parse(File.ReadAllText(rc).Trim()) : 0; } }
"@
    $rec = $rec.Replace('__SFX__', [guid]::NewGuid().ToString('N').Substring(0, 8))
    Add-Type -TypeDefinition $rec -OutputAssembly (Join-Path $root '.venv\Scripts\python.exe') -OutputType ConsoleApplication
    # fake space users: big 30 MB, mid 12 MB (nested), small file 2 MB, and a 50 MB "Docker" dir
    # the guard is told to exclude
    $scan = Join-Path $root 'scan'
    foreach ($spec in @(@('big\a.bin', 30), @('mid\deep\b.bin', 12), @('small.bin', 2), @('Docker\vhdx.bin', 50))) {
        $p = Join-Path $scan $spec[0]
        New-Item -ItemType Directory -Path (Split-Path -Parent $p) -Force | Out-Null
        $fs = [IO.File]::Create($p); $fs.SetLength([int64]$spec[1] * 1MB); $fs.Close()
    }
    # junctions back to big\ - one at the top, one nested inside mid\ - must not be counted
    cmd /c "mklink /J `"$scan\big-link`" `"$scan\big`"" 2>&1 | Out-Null
    cmd /c "mklink /J `"$scan\mid\deep\nested-link`" `"$scan\big`"" 2>&1 | Out-Null
    # a second scan root for the in-tree budget case: deep\top.bin 5 MB, then 4 levels further down
    foreach ($spec in @(@('scan2\deep\top.bin', 5), @('scan2\deep\a\b\c\d\x.bin', 1))) {
        $p = Join-Path $root $spec[0]
        New-Item -ItemType Directory -Path (Split-Path -Parent $p) -Force | Out-Null
        $fs = [IO.File]::Create($p); $fs.SetLength([int64]$spec[1] * 1MB); $fs.Close()
    }
    return $root
}

function Get-Calls([string]$Root, [string]$Name) {
    $f = Join-Path $Root 'calls.log'
    # the leading comma keeps a one-element result an ARRAY ($x[0] of a bare string is its first char)
    if (-not (Test-Path $f)) { return ,([string[]]@()) }
    return ,([string[]]@(Get-Content -LiteralPath $f | Where-Object { $_ -like "$Name`t*" } | ForEach-Object { $_.Substring($Name.Length + 1) }))
}
# the main alerts only - not the CRITICAL space follow-up
function Get-Alerts([string]$Root) {
    return ,([string[]]@((Get-Calls $Root 'mm_post.py') | Where-Object { $_ -notlike 'DISK CRITICAL follow-up*' }))
}
function Set-AlertState([string]$Root, [string]$Severity, [datetime]$TsUtc) {
    (@{ severity = $Severity; ts = $TsUtc.ToString('yyyy-MM-ddTHH:mm:ssZ'); via = '#sysadmin' } | ConvertTo-Json -Compress) |
        Set-Content -LiteralPath (Join-Path $Root 'logs\disk-guard-alert.json') -Encoding ASCII
}
function Get-GuardLog([string]$Root) {
    $f = Join-Path $Root 'logs\disk-guard.log'
    if (Test-Path $f) { return (Get-Content -LiteralPath $f -Raw) } else { return '' }
}

$guardParams = (Get-Command $Script).Parameters
$hasScan = $guardParams.ContainsKey('SpaceScanRoots')

function Invoke-Guard([string]$Root, [double]$FreeGb, [hashtable]$Extra = @{}) {
    $sbx = (Resolve-Path $Root).Path
    if ($sbx.StartsWith($realRepo, [StringComparison]::OrdinalIgnoreCase)) { throw "sandbox $sbx is inside the real repo" }
    $global:DGT.Free = $FreeGb * 1GB
    $p = @{}
    if ($hasScan) {
        $p.SpaceScanRoots = @(Join-Path $sbx 'scan')
        $p.SpaceScanExclude = @(Join-Path $sbx 'scan\Docker')
    }
    foreach ($k in $Extra.Keys) { $p[$k] = $Extra[$k] }
    Push-Location
    try {
        $out = & (Join-Path $sbx 'scripts\maintenance\disk-guard.ps1') @p *>&1 | Out-String
        return $out
    } catch { return "GUARD THREW: $_" }
    finally { Pop-Location }
}

$sandboxes = @()
function Box { $b = New-Sandbox; $script:sandboxes += $b; return $b }
Write-Host "guard under test: $Script ; DOCKER_HOST=$env:DOCKER_HOST ; space-scan parameters: $hasScan"

try {
    # D1 - the percent line: 8.7% free of 930.5 GB (81 GB, above every GB line) alerts, and
    # reclaims nothing
    $r = Box; $global:DGT.Workers = @(); $global:DGT.Docker = @(); $global:DGT.Rest = @()
    $o = Invoke-Guard $r 81.0
    $mm = Get-Calls $r 'mm_post.py'; $rc = Get-Calls $r 'auto_reclaim.py'
    Write-Case 'D1' 'C: at 8.7% free (81 GB of 930.5) alerts once, alert only' `
        ($mm.Count -eq 1 -and $mm[0] -match '8\.7%' -and $rc.Count -eq 0 -and $global:DGT.Docker.Count -eq 0 -and $global:DGT.Rest.Count -eq 0) `
        ("posts=$($mm.Count) reclaim-runs=$($rc.Count) docker=$($global:DGT.Docker.Count) rest=$($global:DGT.Rest.Count)`nmessage: $($mm -join ' | ')")

    # D2 - healthy stays silent
    $r = Box
    $o = Invoke-Guard $r 200.0
    $all = @(Get-Content (Join-Path $r 'calls.log') -ErrorAction SilentlyContinue)
    Write-Case 'D2' 'healthy (200 GB = 21.5%) is silent: no call, no log' `
        ($all.Count -eq 0 -and -not (Get-GuardLog $r)) "calls=$($all.Count) log-bytes=$((Get-GuardLog $r).Length)"

    # D3 - WARN keeps its actions: reclaim + sweep + one post carrying the reclaim line
    $r = Box; $global:DGT.Docker = @(); $global:DGT.Rest = @()
    $o = Invoke-Guard $r 20.0
    $mm = Get-Calls $r 'mm_post.py'
    Write-Case 'D3' 'WARN (20 GB): reclaim, sweep, one post with the reclaim line; no stop' `
        ((Get-Calls $r 'auto_reclaim.py').Count -eq 1 -and (Get-Calls $r 'sweep_tmp.py').Count -eq 1 -and $mm.Count -eq 1 -and
         $mm[0] -match 'RECLAIM fake' -and $mm[0] -match 'Disk low' -and $global:DGT.Rest.Count -eq 0 -and
         (Get-Calls $r 'telegram_notify.py').Count -eq 0 -and
         @($global:DGT.Docker | Where-Object { $_ -like 'stop*' }).Count -eq 0) `
        "reclaim=$((Get-Calls $r 'auto_reclaim.py').Count) sweep=$((Get-Calls $r 'sweep_tmp.py').Count) posts=$($mm.Count) telegram=$((Get-Calls $r 'telegram_notify.py').Count) rest=$($global:DGT.Rest.Count)`nmessage: $($mm -join ' | ')"

    # D4 - CRITICAL keeps its actions: kill-switch, hard stop of the workers, URGENT post
    $r = Box; $global:DGT.Workers = @('ao-worker-1', 'ao-worker-2', 'openwebui'); $global:DGT.Docker = @(); $global:DGT.Rest = @()
    $o = Invoke-Guard $r 10.0
    $mm = Get-Calls $r 'mm_post.py'
    $stops = @($global:DGT.Docker | Where-Object { $_ -like 'stop *' })
    Write-Case 'D4' 'CRITICAL (10 GB): kill-switch, stop ao-worker-* only; alert first, space walk as a follow-up' `
        (($global:DGT.Rest -join ',') -match 'POST http://127.0.0.1:8830/kill-switch' -and ($stops -join ',') -eq 'stop ao-worker-1,stop ao-worker-2' -and
         $mm.Count -eq 2 -and $mm[0] -match 'DISK CRITICAL' -and $mm[0] -match 'ao-worker-1' -and $mm[0] -notmatch 'big 30 MB' -and
         $mm[1] -like 'DISK CRITICAL follow-up*' -and $mm[1] -match 'big 30 MB') `
        "rest=$($global:DGT.Rest -join ', ')`nstops=$($stops -join ', ') posts=$($mm.Count)`nmessage: $($mm -join ' | ')"
    $global:DGT.Workers = @()

    # D4a - ao-auth: the kill-switch and the drain poll carry the operator bearer read from the
    # plane .env (a fake token built from parts); with no token the guard warns, still tries, and
    # the hard stop runs regardless
    $fakeTok = (@('fake', 'dg', 'operator', '7c1e') -join '-')
    $r = Box; $global:DGT.Workers = @('ao-worker-1'); $global:DGT.Docker = @(); $global:DGT.Rest = @(); $global:DGT.Auth = @()
    New-Item -ItemType Directory -Path (Join-Path $r 'agent-org\docker') -Force | Out-Null
    [IO.File]::WriteAllText((Join-Path $r 'agent-org\docker\.env'), "# plane env`nAO_OPERATOR_TOKEN=$fakeTok`n")
    $o = Invoke-Guard $r 10.0
    $authOk = ($global:DGT.Auth.Count -ge 2 -and @($global:DGT.Auth | Where-Object { $_ -ne "Bearer $fakeTok" }).Count -eq 0)
    $logOk = -not ((Get-GuardLog $r) -match [regex]::Escape($fakeTok)) -and -not ((@(Get-Calls $r 'mm_post.py') -join ' ') -match [regex]::Escape($fakeTok))
    $r2 = Box; $global:DGT.Rest = @(); $global:DGT.Auth = @(); $global:DGT.Docker = @()
    $o2 = Invoke-Guard $r2 10.0
    $noTokOk = (($global:DGT.Rest -join ',') -match 'POST http://127.0.0.1:8830/kill-switch' -and
                @($global:DGT.Auth | Where-Object { $_ }).Count -eq 0 -and (Get-GuardLog $r2) -match 'AO_OPERATOR_TOKEN not found' -and
                @($global:DGT.Docker | Where-Object { $_ -like 'stop *' }).Count -eq 1)
    Write-Case 'D4a' 'CRITICAL: bridge calls carry the operator bearer from agent-org\docker\.env; never logged; no token -> warn + hard stop' `
        ($authOk -and $logOk -and $noTokOk) "auth-calls=$($global:DGT.Auth.Count) authOk=$authOk logOk=$logOk noTokOk=$noTokOk"
    $global:DGT.Workers = @()

    # D5 - throttle: same severity inside the window is not re-sent; worse is; an aged alert is;
    # healthy clears it
    $r = Box; $steps = @()
    $null = Invoke-Guard $r 20.0; $steps += "warn:$((Get-Alerts $r).Count)"
    $null = Invoke-Guard $r 20.0; $steps += "warn-again:$((Get-Alerts $r).Count)"
    $reclaim2 = (Get-Calls $r 'auto_reclaim.py').Count
    $null = Invoke-Guard $r 10.0; $steps += "critical:$((Get-Alerts $r).Count)"
    $null = Invoke-Guard $r 10.0; $steps += "critical-again:$((Get-Alerts $r).Count)"
    $st = Join-Path $r 'logs\disk-guard-alert.json'
    $aged = $false
    if (Test-Path $st) {
        $j = Get-Content $st -Raw | ConvertFrom-Json
        $j.ts = [DateTime]::UtcNow.AddHours(-7).ToString('yyyy-MM-ddTHH:mm:ssZ')
        ($j | ConvertTo-Json -Compress) | Set-Content -LiteralPath $st -Encoding ASCII; $aged = $true
    }
    $null = Invoke-Guard $r 10.0; $steps += "critical-7h-later:$((Get-Alerts $r).Count)"
    $null = Invoke-Guard $r 200.0; $cleared = -not (Test-Path $st); $steps += "healthy:cleared=$cleared"
    $null = Invoke-Guard $r 81.0; $steps += "pressure-new-episode:$((Get-Alerts $r).Count)"
    $want = 'warn:1,warn-again:1,critical:2,critical-again:2,critical-7h-later:3,healthy:cleared=True,pressure-new-episode:4'
    Write-Case 'D5' 'hourly runs: one message per severity per 6 h, worse always, recovery re-arms' `
        (($steps -join ',') -eq $want -and $reclaim2 -eq 2 -and (Get-GuardLog $r) -match 'alert throttled') `
        "got : $($steps -join ',')`nwant: $want`nreclaim ran on both warn runs: $reclaim2 ; state aged: $aged ; log says throttled: $((Get-GuardLog $r) -match 'alert throttled')"

    # D6 - Mattermost down: the post failure is logged as a failure and Telegram carries the alert
    $r = Box
    Set-Content -LiteralPath (Join-Path $r 'rc-mm_post.py.txt') -Value '1' -Encoding ASCII
    $null = Invoke-Guard $r 20.0
    $tg = Get-Calls $r 'telegram_notify.py'; $lg1 = Get-GuardLog $r
    $st = Join-Path $r 'logs\disk-guard-alert.json'
    $via = if (Test-Path $st) { (Get-Content $st -Raw | ConvertFrom-Json).via } else { '(no state)' }
    $null = Invoke-Guard $r 20.0   # the next hour, Mattermost still down: throttled, nothing re-sent
    $lg = Get-GuardLog $r
    Write-Case 'D6' 'Mattermost post fails -> logged as failed, sent via Telegram, and that delivery arms the throttle' `
        ((Get-Calls $r 'mm_post.py').Count -eq 1 -and (Get-Calls $r 'telegram_notify.py').Count -eq 1 -and $tg[0] -match 'Mattermost post failed' -and $tg[0] -match '20 GB' -and
         $lg1 -match '#sysadmin post failed' -and $lg1 -match 'sent to Telegram' -and $lg1 -notmatch 'posted to #sysadmin' -and
         $lg1 -notmatch 'NOT DELIVERED' -and $via -eq 'Telegram' -and $lg -match 'alert throttled') `
        "run 1: mm attempts=1? telegram=$($tg.Count) state via=$via NOT-DELIVERED line: $($lg1 -match 'NOT DELIVERED')`nrun 2: mm attempts total=$((Get-Calls $r 'mm_post.py').Count) telegram total=$((Get-Calls $r 'telegram_notify.py').Count) throttled: $($lg -match 'alert throttled')`ntelegram text: $($tg -join ' | ')"

    # D7 - both down: NOT DELIVERED in the log, the throttle is not armed, next run tries again
    $r = Box
    Set-Content -LiteralPath (Join-Path $r 'rc-mm_post.py.txt') -Value '1' -Encoding ASCII
    Set-Content -LiteralPath (Join-Path $r 'rc-telegram_notify.py.txt') -Value '1' -Encoding ASCII
    $null = Invoke-Guard $r 20.0
    $armed = Test-Path (Join-Path $r 'logs\disk-guard-alert.json')
    $null = Invoke-Guard $r 20.0
    $lg = Get-GuardLog $r
    Write-Case 'D7' 'both channels fail -> NOT DELIVERED logged, retried next run' `
        ($lg -match 'NOT DELIVERED' -and -not $armed -and (Get-Calls $r 'mm_post.py').Count -eq 2 -and (Get-Calls $r 'telegram_notify.py').Count -eq 2 -and
         $lg -notmatch 'posted to #sysadmin|sent to Telegram') `
        "state armed after a failed delivery: $armed ; mm attempts=$((Get-Calls $r 'mm_post.py').Count) telegram attempts=$((Get-Calls $r 'telegram_notify.py').Count)"

    # D8 - the alert names the largest NON-Docker space users, largest first, junction not doubled
    $r = Box
    $null = Invoke-Guard $r 81.0
    $mm = Get-Calls $r 'mm_post.py'; $m = if ($mm.Count) { $mm[0] } else { '' }
    $ib = $m.IndexOf('big 30 MB'); $im = $m.IndexOf('mid 12 MB'); $is = $m.IndexOf('small.bin 2 MB')
    Write-Case 'D8' 'alert lists big 30 MB > mid 12 MB > small.bin 2 MB, no Docker, no junction' `
        ($hasScan -and $ib -ge 0 -and $im -gt $ib -and $is -gt $im -and $m -notmatch 'Docker\\vhdx|scan\\Docker |big-link') `
        "space-scan parameters present: $hasScan`nmessage: $m"

    # D9 - a scan that runs out of budget still alerts and says so
    $r = Box
    $x = @{}; if ($guardParams.ContainsKey('SpaceScanBudgetSeconds')) { $x.SpaceScanBudgetSeconds = 0 }
    $null = Invoke-Guard $r 81.0 $x
    $mm = Get-Calls $r 'mm_post.py'; $m = if ($mm.Count) { $mm[0] } else { '' }
    Write-Case 'D9' 'space scan out of budget: alert still sent, marked as cut short' `
        ($mm.Count -eq 1 -and $m -match 'budget') "message: $m"

    # D10 - a throttle stamp in the FUTURE counts as expired (clock moved back / hand edit)
    $r = Box
    Set-AlertState $r 'warn' ([DateTime]::UtcNow.AddYears(1))
    $null = Invoke-Guard $r 20.0
    Write-Case 'D10' 'warn with a same-severity stamp 1 year in the future is sent, not muted' `
        ((Get-Alerts $r).Count -eq 1 -and (Get-GuardLog $r) -match 'in the future') "alerts=$((Get-Alerts $r).Count)"

    # D11 - a run that stops workers is always sent, even inside a fresh critical throttle
    $r = Box; $global:DGT.Workers = @(); $steps = @()
    $null = Invoke-Guard $r 10.0; $steps += "critical-no-workers:$((Get-Alerts $r).Count)"
    $global:DGT.Workers = @('ao-worker-1')
    $null = Invoke-Guard $r 10.0; $steps += "critical-stops-a-worker:$((Get-Alerts $r).Count)"
    $global:DGT.Workers = @()
    $null = Invoke-Guard $r 10.0; $steps += "critical-no-workers-again:$((Get-Alerts $r).Count)"
    $want = 'critical-no-workers:1,critical-stops-a-worker:2,critical-no-workers-again:2'
    Write-Case 'D11' 'stopping workers bypasses the throttle; a critical run that stops nothing does not' `
        (($steps -join ',') -eq $want) "got : $($steps -join ',')`nwant: $want"

    # D12 - the 10% line is exact and unrounded: 100.0 of 1000 GB (10.00%) is silent, 99.6 (9.96%) alerts
    $global:DGT.Size = 1000 * 1GB
    $r = Box; $null = Invoke-Guard $r 100.0; $at = (Get-Alerts $r).Count
    $r = Box; $null = Invoke-Guard $r 99.6; $under = Get-Alerts $r
    $global:DGT.Size = 930.5 * 1GB
    Write-Case 'D12' 'percent line: exactly 10.00% silent, 9.96% alerts (shown truncated as 9.9%)' `
        ($at -eq 0 -and $under.Count -eq 1 -and $under[0] -match '= 9\.9% of 1000 GB') "at 10.00%: alerts=$at ; at 9.96%: $($under -join ' | ')"

    # D13 - a budget that runs out INSIDE a tree marks that item partial (fake clock, 1 s per read)
    $r = Box
    $x = @{ SpaceScanRoots = @(Join-Path $r 'scan2'); SpaceScanExclude = @('Z:\none'); SpaceScanBudgetSeconds = 2 }
    $global:DGT.Tick = 1
    try { $null = Invoke-Guard $r 81.0 $x } finally { $global:DGT.Tick = 0 }
    $mm = Get-Alerts $r; $m = if ($mm.Count) { $mm[0] } else { '' }
    Write-Case 'D13' 'walk cut short inside a tree: item listed as partial, message says the budget ran out' `
        ($hasScan -and $m -match 'deep 5 MB \(partial\)' -and $m -match 'budget') "message: $m"
}
catch {
    Write-Case 'ERR' 'the harness stopped part-way (a later case did not run)' $false "$_"
}
finally {
    foreach ($b in $sandboxes) {
        cmd /c "rmdir `"$b\scan\big-link`"" 2>&1 | Out-Null
        cmd /c "rmdir `"$b\scan\mid\deep\nested-link`"" 2>&1 | Out-Null
        Remove-Item -LiteralPath $b -Recurse -Force -ErrorAction SilentlyContinue
    }
    Remove-Variable -Name DGT -Scope Global -ErrorAction SilentlyContinue
}
Write-Host ""
Write-Host ("RESULT: {0} case(s), {1} failed - {2}" -f $results.Count, $failures, ($results -join ', '))
exit $failures
