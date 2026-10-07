# disk-guard.ps1 - hourly low-disk sentinel (K.9, 2026-08-22).
#
# ANSWERS: "what happens if the gym is running and C: hits low capacity?"
# Before this: nothing until the WEEKLY rotation or the operator noticed -
# the known failure mode being ao-worker /tmp session logs filling C: (154 GB
# once). Now, every hour:
#
#   PRESSURE (< $WarnFreePct % of the drive, default 10 - Windows' red line;
#         added 2026-09-29, cf-disk-alert): an ALERT ONLY. No reclaim, no stop.
#         Before this, a 930 GB C: sat at 8.7% free (87 GB) and every watcher
#         said healthy, because the only lines were absolute GB.
#   WARN  (< $WarnFreeGb, default 30):  the sysadmin docker reclaim
#         (auto_reclaim.py: unused image tags, old build cache, orphaned
#         anonymous volumes) + the ao-worker /tmp sweep + a #sysadmin warning.
#   CRIT  (< $CritFreeGb, default 12):  the above, PLUS stop the agent-org
#         gym workers (ao-worker-*) - the largest uncapped writers - and put
#         an URGENT line in #sysadmin. Workers stay down until the operator
#         (or Claude via the sysadmin channel) restarts them; a disk-full
#         crash loses MORE gym work than a paused round does.
#
# THE ALERT (cf-disk-alert, 2026-09-29):
#   - names the largest NON-Docker space users on C: (the Docker vhdx is
#     excluded - the reclaim line already covers what is inside it), measured
#     only when an alert is actually about to go out;
#   - is THROTTLED: while the severity stays the same it is re-sent at most
#     every $AlertThrottleHours (default 6). A worse severity, or a run that
#     stopped workers, is always sent. Recovery (a healthy run) clears it. A
#     stamp in the future (clock moved back, hand edit) counts as expired.
#   - CRITICAL is sent BEFORE the space walk; the walk follows as a second
#     message. The percent line compares the UNROUNDED percentage.
#   - goes to #sysadmin (mm_post.py); if that post fails it goes to Telegram
#     (telegram_notify.py, the Docker-independent path); if both fail the log
#     says NOT DELIVERED and the throttle is not armed, so next hour retries.
# The actions (reclaim, sweep, worker stop) run every hour below their lines
# regardless of the throttle - only the message is throttled.
#
# Healthy hours exit silently (no MM noise). Register/refresh the hourly
# task with: disk-guard.ps1 -Register (per-user, no elevation).
# Tested ONLY with faked disk figures, docker and transport:
# scripts/maintenance/test-disk-guard.ps1 (never run this script on a test).

[CmdletBinding()]
param(
    [switch]$Register,
    [double]$WarnFreeGb = 30,
    [double]$CritFreeGb = 12,
    [double]$WarnFreePct = 10,
    [double]$AlertThrottleHours = 6,
    [string[]]$SpaceScanRoots = @(),
    [string[]]$SpaceScanExclude = @(),
    [int]$SpaceScanTop = 5,
    [int]$SpaceScanBudgetSeconds = 180
)

$ErrorActionPreference = 'Continue'
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $repoRoot

if ($Register) {
    $action  = New-ScheduledTaskAction -Execute 'powershell.exe' `
        -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).Date `
        -RepetitionInterval (New-TimeSpan -Hours 1) -RepetitionDuration (New-TimeSpan -Days 3650)
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
    Register-ScheduledTask -TaskName 'AI-Stack Disk Guard' `
        -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
    Write-Host "Registered 'AI-Stack Disk Guard' (hourly, current user, Limited)."
    exit 0
}

# Default scan, in this order: the user profile (minus AppData), the top of C: (pagefile/hiberfil
# and friends; minus Users - the profile is listed on its own - and Windows, which is the OS and
# slow to walk) and LocalAppData (minus Docker, whose vhdx is the Docker share). Measured on this
# host 2026-09-29, warm: ~23 s, ~84 s and 160 s+ (LocalAppData\Temp alone ran past 90 s), so the
# walk is bounded by $SpaceScanBudgetSeconds and an item cut short is marked partial.
if (-not $SpaceScanRoots -or $SpaceScanRoots.Count -eq 0) {
    $SpaceScanRoots = @($env:USERPROFILE, 'C:\', $env:LOCALAPPDATA) | Where-Object { $_ }
}
if (-not $SpaceScanExclude -or $SpaceScanExclude.Count -eq 0) {
    $SpaceScanExclude = @(
        (Join-Path $env:USERPROFILE 'AppData'), (Join-Path $env:LOCALAPPDATA 'Docker'),
        'C:\Users', 'C:\Windows'
    )
}

function CDisk {
    $d = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='C:'"
    $freeB = [double]$d.FreeSpace; $sizeB = [double]$d.Size
    # FreePctRaw is what the percent line compares (unrounded, so the line is exactly $WarnFreePct);
    # FreePct is what messages show, TRUNCATED to 0.1 so a value under the line never reads as it.
    $raw = if ($sizeB -gt 0) { 100 * $freeB / $sizeB } else { 100 }
    [pscustomobject]@{ FreeGb = [math]::Round($freeB / 1GB, 1); TotalGb = [math]::Round($sizeB / 1GB, 1)
        FreePct = [math]::Floor($raw * 10) / 10; FreePctRaw = $raw }
}
function CFreeGb { (CDisk).FreeGb }

$sevRank = @{ healthy = 0; pressure = 1; warn = 2; critical = 3 }
$disk = CDisk
$free = $disk.FreeGb
$severity = 'healthy'
if ($free -lt $CritFreeGb) { $severity = 'critical' }
elseif ($free -lt $WarnFreeGb) { $severity = 'warn' }
elseif ($disk.FreePctRaw -lt $WarnFreePct) { $severity = 'pressure' }

$logDir = Join-Path $repoRoot 'logs'
$alertState = Join-Path $logDir 'disk-guard-alert.json'
if ($severity -eq 'healthy') {
    # healthy - stay silent; a recovered disk re-arms the next alert immediately
    if (Test-Path $alertState) { Remove-Item -LiteralPath $alertState -Force -ErrorAction SilentlyContinue }
    exit 0
}

if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
$log = Join-Path $logDir 'disk-guard.log'
function Log([string]$m) {
    $line = "[{0}] {1}" -f [DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ'), $m
    Write-Host $line; Add-Content -Path $log -Value $line
}
Log "LOW DISK ($severity): C: free ${free} GB = $($disk.FreePct)% of $($disk.TotalGb) GB (pressure<${WarnFreePct}%, warn<${WarnFreeGb} GB, crit<${CritFreeGb} GB)"

$py = Join-Path $repoRoot '.venv\Scripts\python.exe'
$reclaimLine = ''
$stopped = @()
$orgAction = ""
$critical = $severity -eq 'critical'
if ($severity -eq 'warn' -or $critical) {
    # --- docker reclaim: the sysadmin's own plan/execute (scripts/sysadmin-mcp/auto_reclaim.py) --
    # Until 2026-09-27 this ran `image prune -f` + `builder prune -f`, which can only take UNTAGGED
    # leftovers: it reported "images 0B, cache 0B" while ~34 GB of unused tagged images and ~750
    # orphaned anonymous volumes sat in the vhdx. auto_reclaim.py applies the operator's rules
    # (docker_reclaim.py): unused image tags 14+ days old that no compose render names and the
    # keep-list does not protect; build cache older than 168h; anonymous volumes no container
    # references, 7+ days old. NAMED volumes are never removed. Its last stdout line is the
    # per-category summary that goes into the alert below.
    $reclaimLine = 'RECLAIM not run (no .venv python)'
    if (Test-Path $py) {
        $reclaimOut = @(& $py (Join-Path $repoRoot 'scripts\sysadmin-mcp\auto_reclaim.py') 2>&1 | ForEach-Object { "$_" })
        if ($reclaimOut.Count) { $reclaimLine = $reclaimOut[-1] } else { $reclaimLine = 'RECLAIM produced no output' }
    }
    Log "reclaim: $reclaimLine"
    if (Test-Path $py) {
        & $py (Join-Path $repoRoot 'scripts\sysadmin-mcp\sweep_tmp.py') 2>&1 |
            Select-Object -Last 1 | ForEach-Object { Log "tmp sweep: $_" }
    }
}

if ($critical) {
    # GRACEFUL FIRST (operator direction 2026-08-22): delegate the stop to the
    # org's own governance - engage the agent-bridge kill-switch so the
    # orchestrator stops dispatching and workers wind down their in-flight
    # turns, then wait a bounded grace window watching the scheduler drain.
    $graceMinutes = 5
    # ao-auth: the bridge's control routes need the operator bearer (AO_OPERATOR_TOKEN in
    # agent-org\docker\.env). Read here, never logged. Absent -> the bridge refuses (401), the catch
    # below logs it and the hard stop still runs.
    $bridgeHeaders = @{}
    $aoEnv = Join-Path $repoRoot 'agent-org\docker\.env'
    if (Test-Path -LiteralPath $aoEnv) {
        foreach ($l in (Get-Content -LiteralPath $aoEnv)) {
            if ($l -match '^\s*AO_OPERATOR_TOKEN\s*=\s*(.+?)\s*$') {
                $bridgeHeaders['Authorization'] = 'Bearer ' + $Matches[1].Trim('"', "'")
            }
        }
    }
    if (-not $bridgeHeaders.Count) { Log "WARN: AO_OPERATOR_TOKEN not found in $aoEnv - the bridge will refuse the kill-switch" }
    try {
        Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8830/kill-switch' -Headers $bridgeHeaders `
            -ContentType 'application/json' -Body '{"on": true}' -TimeoutSec 10 | Out-Null
        $orgAction = "kill-switch engaged"
        Log "org kill-switch ENGAGED via agent-bridge; waiting up to ${graceMinutes}m for the scheduler to drain..."
        $deadline = (Get-Date).AddMinutes($graceMinutes)
        while ((Get-Date) -lt $deadline) {
            Start-Sleep -Seconds 20
            try {
                $sched = Invoke-RestMethod -Uri 'http://127.0.0.1:8830/scheduler' -Headers $bridgeHeaders -TimeoutSec 10
                if (-not $sched.instances -or @($sched.instances).Count -eq 0) {
                    Log "scheduler drained cleanly"
                    $orgAction = "kill-switch engaged, drained cleanly"
                    break
                }
            } catch { break }  # bridge went away - fall through to the hard stop
        }
    }
    catch {
        $orgAction = "bridge unresponsive - hard stop only"
        Log "WARN: agent-bridge kill-switch unreachable ($_) - falling back to hard stop"
    }

    # HARD FALLBACK: whatever is still running gets stopped - the disk wins.
    $workers = @(docker ps --format '{{.Names}}' | Where-Object { $_ -like 'ao-worker*' -or $_ -like 'ao-ot*' })
    foreach ($w in $workers) {
        docker stop $w 2>&1 | Out-Null
        $stopped += $w
    }
    if ($stopped.Count) { Log "CRITICAL: hard-stopped gym workers: $($stopped -join ', ')" }
    else { Log "CRITICAL: no worker containers left running after the graceful drain" }
}

# --- throttle: one message per severity per $AlertThrottleHours ---------------------------------
$last = $null
if (Test-Path $alertState) {
    try { $last = Get-Content -LiteralPath $alertState -Raw | ConvertFrom-Json } catch { $last = $null }
}
$due = $true
$why = 'first alert of this episode'
if ($last -and $last.severity -and $last.ts) {
    $lastTs = [DateTime]::MinValue
    $parsed = [DateTime]::TryParse([string]$last.ts, [Globalization.CultureInfo]::InvariantCulture,
        [Globalization.DateTimeStyles]::AdjustToUniversal -bor [Globalization.DateTimeStyles]::AssumeUniversal, [ref]$lastTs)
    $lastRank = if ($sevRank.ContainsKey([string]$last.severity)) { $sevRank[[string]$last.severity] } else { 0 }
    $ageH = if ($parsed) { ([DateTime]::UtcNow - $lastTs).TotalHours } else { [double]::MaxValue }
    if ($stopped.Count) { $why = 'workers were stopped this run' }
    elseif ($sevRank[$severity] -gt $lastRank) { $why = "severity rose from $($last.severity)" }
    elseif ($ageH -lt 0) { $why = "the last-alert stamp is in the future ($($last.ts)) - treated as expired" }
    elseif ($ageH -ge $AlertThrottleHours) { $why = "last alert $([math]::Round($ageH, 1))h ago" }
    else {
        $due = $false
        Log ("alert throttled: '{0}' already sent {1}h ago (re-sent after {2}h unless it gets worse)" -f $last.severity, [math]::Round($ageH, 1), $AlertThrottleHours)
    }
}
if (-not $due) { Log "disk-guard done (C: free $(CFreeGb) GB)"; exit 0 }

# --- delivery: #sysadmin, else Telegram; never claim a send that did not happen -----------------
function Send-Alert([string]$Text) {
    # Returns the channel that took it ('#sysadmin' / 'Telegram') or '' when none did.
    if (-not (Test-Path $py)) { Log "WARN: no .venv python at $py - cannot post"; return '' }
    & $py (Join-Path $repoRoot 'scripts\sysadmin-mcp\mm_post.py') $Text 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) { Log "posted to #sysadmin"; return '#sysadmin' }
    Log "WARN: #sysadmin post failed (mm_post exit $LASTEXITCODE) - trying Telegram"
    $tg = "ai-stack disk-guard (Mattermost post failed): " + (($Text -replace '\*\*', '') -replace ':rotating_light: |:warning: ', '')
    & $py (Join-Path $repoRoot 'scripts\sysadmin-mcp\telegram_notify.py') $tg 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) { Log "sent to Telegram"; return 'Telegram' }
    Log "WARN: Telegram send failed (telegram_notify exit $LASTEXITCODE)"
    return ''
}

# --- the largest NON-Docker space users on C: (read-only walk, bounded) --------------------------
function Measure-TreeBytes([string]$Path, [datetime]$Deadline) {
    # Sums file lengths under $Path without following junctions/symlinks. When the budget runs out
    # part-way it returns (-1 - bytes counted so far); the caller reports that item as partial.
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
    if (-not $item) { return [int64]0 }
    if (-not $item.PSIsContainer) { return [int64]$item.Length }
    $total = [int64]0
    $stack = New-Object System.Collections.Stack
    $stack.Push($item.FullName)
    while ($stack.Count -gt 0) {
        if ((Get-Date) -gt $Deadline) { return [int64](-1 - $total) }
        $dir = $stack.Pop()
        try {
            foreach ($e in (New-Object IO.DirectoryInfo $dir).EnumerateFileSystemInfos()) {
                if ($e.Attributes -band [IO.FileAttributes]::ReparsePoint) { continue }
                if ($e -is [IO.DirectoryInfo]) { $stack.Push($e.FullName) } else { $total += $e.Length }
            }
        } catch { }   # access denied / vanished mid-walk: count what was readable
    }
    return $total
}
function Format-Bytes([double]$b) {
    if ($b -ge 1GB) { return ('{0:N1} GB' -f ($b / 1GB)) }
    return ('{0:N0} MB' -f ($b / 1MB))
}
function Get-SpaceLine {
    try {
        $deadline = (Get-Date).AddSeconds($SpaceScanBudgetSeconds)
        $ex = @($SpaceScanExclude | Where-Object { $_ } | ForEach-Object { $_.TrimEnd('\').ToLowerInvariant() })
        $rows = @()
        $partial = $false
        foreach ($root in $SpaceScanRoots) {
            foreach ($c in @(Get-ChildItem -LiteralPath $root -Force -ErrorAction SilentlyContinue)) {
                if ($ex -contains $c.FullName.TrimEnd('\').ToLowerInvariant()) { continue }
                if ($c.Attributes -band [IO.FileAttributes]::ReparsePoint) { continue }
                if ((Get-Date) -gt $deadline) { $partial = $true; break }
                $n = Measure-TreeBytes $c.FullName $deadline
                $isPartial = $n -lt 0
                if ($isPartial) { $n = -1 - $n; $partial = $true }
                $rows += [pscustomobject]@{ Path = $c.FullName; Bytes = [double]$n; Partial = $isPartial }
            }
        }
        $top = @($rows | Where-Object { $_.Bytes -gt 0 } | Sort-Object Bytes -Descending | Select-Object -First $SpaceScanTop)
        if ($top.Count) {
            return 'Largest non-Docker space users on C: ' +
                (($top | ForEach-Object { "$($_.Path) $(Format-Bytes $_.Bytes)$(if ($_.Partial) { ' (partial)' })" }) -join '; ') +
                $(if ($partial) { " (scan stopped at its ${SpaceScanBudgetSeconds}s budget; sizes may be low)" } else { '' }) + '.'
        }
        return 'Largest non-Docker space users on C: none measured' +
            $(if ($partial) { " (scan stopped at its ${SpaceScanBudgetSeconds}s budget)" } else { '' }) + '.'
    } catch { return "Largest non-Docker space users on C: scan failed ($_)." }
}

$after = CFreeGb
$sev = switch ($severity) {
    'critical' { ':rotating_light: **DISK CRITICAL**' }
    'warn'     { ':warning: **Disk low**' }
    default    { ":warning: **Disk under ${WarnFreePct}%**" }
}
$head = "$sev - C: free ${free} GB = $($disk.FreePct.ToString('0.0', [Globalization.CultureInfo]::InvariantCulture))% of $($disk.TotalGb) GB"
function Get-AlertText([string]$SpaceLine) {
    if ($severity -eq 'pressure') {
        return "$head, below the ${WarnFreePct}% line. Alert only: the automatic docker reclaim starts under ${WarnFreeGb} GB. " +
               "$SpaceLine Ask @sysadmin for a reclaim/compaction plan if the Docker share is large."
    }
    return "$head -> ${after} GB after reclaim. $reclaimLine (freed inside the Docker vhdx; C: gets it back at the next compaction)." +
           $(if ($orgAction) { " Org: $orgAction." } else { "" }) +
           $(if ($stopped.Count) { " Gym workers stopped: $($stopped -join ', ')." } else { "" }) +
           $(if ($critical) { " To RESUME after space is safe: release the kill-switch (POST http://127.0.0.1:8830/kill-switch {\""on\"":false} with the operator bearer AO_OPERATOR_TOKEN, or ask the org via Mattermost) and docker start the workers." } else { "" }) +
           " $SpaceLine" +
           " Weekly compaction: Sundays 03:15; trigger early via the sysadmin channel if needed."
}

# CRITICAL goes out BEFORE the space walk (up to $SpaceScanBudgetSeconds) and the walk follows as a
# second message, so the urgent line is not held back by it. The others carry the walk inline.
if ($critical) {
    $msg = Get-AlertText 'Largest non-Docker space users on C: in a follow-up message.'
} else {
    $spaceLine = Get-SpaceLine
    Log "space: $spaceLine"
    $msg = Get-AlertText $spaceLine
}
Log "alert ($why): $msg"
$delivered = Send-Alert $msg

if ($delivered) {
    try {
        (@{ severity = $severity; ts = [DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ'); via = $delivered } |
            ConvertTo-Json -Compress) | Set-Content -LiteralPath $alertState -Encoding ASCII
    } catch { Log "WARN: could not write $alertState ($_)" }
    if ($critical) {
        $spaceLine = Get-SpaceLine
        Log "space: $spaceLine"
        $null = Send-Alert ("DISK CRITICAL follow-up - C: free ${after} GB. $spaceLine")
    }
} else {
    Log "ALERT NOT DELIVERED on any channel - the throttle stays open, next hour retries"
}
Log "disk-guard done (C: free ${after} GB)"
