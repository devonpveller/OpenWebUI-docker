# disk-guard.ps1 - hourly low-disk sentinel (K.9, 2026-08-22).
#
# ANSWERS: "what happens if the gym is running and C: hits low capacity?"
# Before this: nothing until the WEEKLY rotation or the operator noticed -
# the known failure mode being ao-worker /tmp session logs filling C: (154 GB
# once). Now, every hour:
#
#   WARN  (< $WarnFreeGb, default 30):  safe docker reclaim (dangling images +
#         build cache) + the ao-worker /tmp sweep + a #sysadmin warning.
#   CRIT  (< $CritFreeGb, default 12):  the above, PLUS stop the agent-org
#         gym workers (ao-worker-*) - the largest uncapped writers - and put
#         an URGENT line in #sysadmin. Workers stay down until the operator
#         (or Claude via the sysadmin channel) restarts them; a disk-full
#         crash loses MORE gym work than a paused round does.
#
# Healthy hours exit silently (no MM noise). Register/refresh the hourly
# task with: disk-guard.ps1 -Register (per-user, no elevation).

[CmdletBinding()]
param(
    [switch]$Register,
    [double]$WarnFreeGb = 30,
    [double]$CritFreeGb = 12
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

function CFreeGb { [math]::Round((Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='C:'").FreeSpace / 1GB, 1) }
$free = CFreeGb
if ($free -ge $WarnFreeGb) { exit 0 }   # healthy - stay silent

$logDir = Join-Path $repoRoot 'logs'
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
$log = Join-Path $logDir 'disk-guard.log'
function Log([string]$m) {
    $line = "[{0}] {1}" -f [DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ'), $m
    Write-Host $line; Add-Content -Path $log -Value $line
}
Log "LOW DISK: C: free ${free} GB (warn<${WarnFreeGb}, crit<${CritFreeGb})"

# --- safe reclaim (same guards as the weekly rotation; NEVER volume prune) --
$img = (docker image prune -f 2>&1 | Select-Object -Last 1)
$bld = (docker builder prune -f --keep-storage 5GB 2>&1 | Select-Object -Last 1)
Log "reclaim: images[$img] cache[$bld]"
$py = Join-Path $repoRoot '.venv\Scripts\python.exe'
if (Test-Path $py) {
    & $py (Join-Path $repoRoot 'scripts\sysadmin-mcp\sweep_tmp.py') 2>&1 |
        Select-Object -Last 1 | ForEach-Object { Log "tmp sweep: $_" }
}

$critical = $free -lt $CritFreeGb
$stopped = @()
if ($critical) {
    # Stop the gym workers - the big uncapped /tmp writers. agent-bridge and
    # Mattermost stay up, so nothing is lost except in-flight worker turns.
    $workers = @(docker ps --format '{{.Names}}' | Where-Object { $_ -like 'ao-worker*' -or $_ -like 'ao-ot*' })
    foreach ($w in $workers) {
        docker stop $w 2>&1 | Out-Null
        $stopped += $w
    }
    Log "CRITICAL: stopped gym workers: $($stopped -join ', ')"
}

$after = CFreeGb
$sev = if ($critical) { ':rotating_light: **DISK CRITICAL**' } else { ':warning: **Disk low**' }
$msg = "$sev - C: free ${free} -> ${after} GB after reclaim. images[$img] cache[$bld]." +
       $(if ($stopped.Count) { " Gym workers STOPPED to protect the disk: $($stopped -join ', ') - restart them when space is safe (docker start ...)." } else { "" }) +
       " Weekly compaction: Sundays 03:15; trigger early via the sysadmin channel if needed."
if (Test-Path $py) {
    & $py (Join-Path $repoRoot 'scripts\sysadmin-mcp\mm_post.py') $msg 2>&1 | Out-Null
    Log "posted to #sysadmin"
}
Log "disk-guard done (C: free ${after} GB)"
