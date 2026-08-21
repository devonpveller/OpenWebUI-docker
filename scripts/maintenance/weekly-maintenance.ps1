# weekly-maintenance.ps1 - the predictable disk rotation (Part K.8, 2026-08-21).
#
# WHY: multi-day agent sessions inflate the Docker WSL vhdx and C: free space
# until the operator intervenes. The pieces all existed (safe-reclaim logic,
# an elevated compact-vhdx task, tmp sweep) but nothing ran them on a
# schedule - the compact task had NO trigger and fired only when a human or
# the sysadmin MCP asked. This wrapper is the missing rotation:
#
#   1. safe docker reclaim  - dangling images + build cache (KEEPS 10GB of
#     cache so plane rebuilds stay fast). NEVER `docker volume prune`
#     (see memory: named volumes hold live state).
#   2. compact trigger      - starts the EXISTING elevated task
#     "AI-Stack Sysadmin Compact VHDX" via schtasks /run (the same no-UAC
#     path the sysadmin MCP uses). That script quiesces Docker, compacts,
#     restarts, and waits for the container count to return; its own
#     MinTrappedGb guard turns no-op weeks into a skip.
#   3. report               - posts the summary to #sysadmin via mm_post.py.
#
# Scheduled: Sundays 03:15 local (before the 04:00 tmp sweep; models are idle).
# Register/refresh the task by running THIS SCRIPT with -Register (no
# elevation needed - it is a per-user, RunLevel=Limited task; the elevated
# half stays inside the pre-registered compact task).

[CmdletBinding()]
param(
    [switch]$Register,
    [switch]$SkipCompact,          # reclaim + report only
    [int]$CompactWaitMinutes = 25
)

$ErrorActionPreference = 'Continue'
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $repoRoot

if ($Register) {
    $action  = New-ScheduledTaskAction -Execute 'powershell.exe' `
        -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At 03:15
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
        -ExecutionTimeLimit (New-TimeSpan -Hours 2)
    Register-ScheduledTask -TaskName 'AI-Stack Weekly Maintenance' `
        -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
    Write-Host "Registered 'AI-Stack Weekly Maintenance' (Sundays 03:15, current user, Limited)."
    exit 0
}

$logDir = Join-Path $repoRoot 'logs'
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
$log = Join-Path $logDir ("maintenance-" + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ') + ".log")
function Log([string]$m) {
    $line = "[{0}] {1}" -f [DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ'), $m
    Write-Host $line; Add-Content -Path $log -Value $line
}

function CFreeGb { [math]::Round((Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='C:'").FreeSpace / 1GB, 1) }
$cBefore = CFreeGb
Log "=== weekly maintenance start (C: free ${cBefore} GB) ==="

# --- 1. safe reclaim ---------------------------------------------------------
Log "docker image prune (dangling only)..."
$img = (docker image prune -f 2>&1 | Select-Object -Last 1)
Log "  $img"
Log "docker builder prune (keep 10GB cache)..."
$bld = (docker builder prune -f --keep-storage 10GB 2>&1 | Select-Object -Last 1)
Log "  $bld"
# NEVER docker volume prune - named volumes hold live state.

# --- 2. compaction via the existing elevated task ----------------------------
$compacted = 'skipped'
if (-not $SkipCompact) {
    $resultFile = Join-Path $repoRoot 'scripts\sysadmin-mcp\state\compact-result.json'
    $beforeStamp = if (Test-Path $resultFile) { (Get-Item $resultFile).LastWriteTimeUtc } else { [DateTime]::MinValue }
    Log "triggering elevated task 'AI-Stack Sysadmin Compact VHDX'..."
    schtasks /run /tn "AI-Stack Sysadmin Compact VHDX" | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Log "WARN: could not start the compact task (schtasks exit $LASTEXITCODE)"
        $compacted = 'trigger-failed'
    }
    else {
        $deadline = (Get-Date).AddMinutes($CompactWaitMinutes)
        $done = $false
        while ((Get-Date) -lt $deadline) {
            Start-Sleep -Seconds 30
            if ((Test-Path $resultFile) -and (Get-Item $resultFile).LastWriteTimeUtc -gt $beforeStamp) {
                try {
                    $r = Get-Content $resultFile -Raw | ConvertFrom-Json
                    if ($r.finished) {
                        $done = $true
                        $compacted = if ($r.ok) { "ok (reclaimed $($r.reclaimed_gb) GB, stack returned: $($r.stack_returned))" }
                                     else { "not-ok: $($r.error)" }
                        break
                    }
                } catch {}
            }
        }
        if (-not $done) { $compacted = "timeout after ${CompactWaitMinutes}m (check compact-result.json + the stack)" }
    }
    Log "compaction: $compacted"
}

# --- 3. verify + report -------------------------------------------------------
$cAfter = CFreeGb
$unhealthy = @(docker ps --filter health=unhealthy --format '{{.Names}}')
$running = @(docker ps -q).Count
Log "post-run: C: free ${cAfter} GB | $running containers running | unhealthy: $($unhealthy.Count)"

$msg = ":broom: **Weekly maintenance** - C: free ${cBefore} -> ${cAfter} GB. " +
       "Reclaim: images[$img] cache[$bld]. Compaction: $compacted. " +
       "$running containers up, $($unhealthy.Count) unhealthy" +
       $(if ($unhealthy.Count) { " (" + ($unhealthy -join ', ') + ")" } else { "" }) + "."
$py = Join-Path $repoRoot '.venv\Scripts\python.exe'
if (Test-Path $py) {
    & $py (Join-Path $repoRoot 'scripts\sysadmin-mcp\mm_post.py') $msg 2>&1 | Out-Null
    Log "posted summary to #sysadmin"
}

Log "=== weekly maintenance done ==="
