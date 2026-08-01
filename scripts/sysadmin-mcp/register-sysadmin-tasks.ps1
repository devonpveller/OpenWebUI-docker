<#
.SYNOPSIS
  ONE-TIME (elevated) registration of the systems-administrator Windows Scheduled Tasks.

.DESCRIPTION
  Registers two tasks (idempotent, -Force):
    1. "AI-Stack Sysadmin Compact VHDX"  — RunLevel=Highest, ON-DEMAND (no trigger). Runs
       compact-vhdx.ps1 elevated with no UAC when the sysadmin-mcp triggers it via `schtasks /run`,
       always behind the approval gate.
    2. "AI-Stack Sysadmin Disk Check"    — Limited, WEEKLY Sunday 09:00. Runs check_disk.py, which
       posts a #sysadmin alert only when disk pressure trips a threshold (capability #1 detector).

  Registering scheduled tasks requires admin — run this once from an elevated PowerShell (or let the
  sysadmin-mcp offer to launch it via a single UAC). Re-running updates the tasks.
#>
[CmdletBinding()]
param(
  [string]$CompactTaskName = 'AI-Stack Sysadmin Compact VHDX',
  [string]$CheckTaskName   = 'AI-Stack Sysadmin Disk Check',
  [string]$SweepTaskName   = 'AI-Stack Sysadmin Tmp Sweep',
  [string]$BackupCheckTaskName = 'AI-Stack Sysadmin Backup Check'
)

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { Write-Error "Must run elevated (Run as administrator), then re-run."; exit 1 }

$here    = $PSScriptRoot
$repo    = (Get-Item $here).Parent.Parent.FullName
$compact = Join-Path $here 'compact-vhdx.ps1'
$check   = Join-Path $here 'check_disk.py'
if (-not (Test-Path $compact)) { Write-Error "missing $compact"; exit 1 }
if (-not (Test-Path $check))   { Write-Error "missing $check"; exit 1 }

# resolve a stable python path (prefer the repo venv, else PATH)
$venvPy = Join-Path $repo '.venv\Scripts\python.exe'
$py = if (Test-Path $venvPy) { $venvPy } else { 'python' }
Write-Output "python for weekly check: $py"

$user = "$env:USERDOMAIN\$env:USERNAME"

# 1) elevated, on-demand compaction task
$aCompact = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$compact`""
$pHighest = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Highest
$sCompact = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 40) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $CompactTaskName -Action $aCompact -Principal $pHighest -Settings $sCompact `
  -Description 'ai-stack systems-administrator: compact docker_data.vhdx (gated, on-demand)' -Force | Out-Null
Write-Output "Registered '$CompactTaskName' (RunLevel=Highest, on-demand)."

# 2) weekly Sunday disk-pressure detector (Limited)
$aCheck  = New-ScheduledTaskAction -Execute $py -Argument "`"$check`"" -WorkingDirectory $repo
$pLimit  = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$tWeekly = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At 9am
$sCheck  = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 15) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $CheckTaskName -Action $aCheck -Trigger $tWeekly -Principal $pLimit -Settings $sCheck `
  -Description 'ai-stack systems-administrator: weekly disk-pressure check -> #sysadmin alert' -Force | Out-Null
Write-Output "Registered '$CheckTaskName' (weekly Sunday 09:00)."

# 3) daily /tmp prevention sweep (Limited) — deletes only OLD lc-*.jsonl on running workers
$sweep = Join-Path $here 'sweep_tmp.py'
if (Test-Path $sweep) {
  $aSweep = New-ScheduledTaskAction -Execute $py -Argument "`"$sweep`"" -WorkingDirectory $repo
  $tDaily = New-ScheduledTaskTrigger -Daily -At 4am
  $sSweep = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 15) -MultipleInstances IgnoreNew
  Register-ScheduledTask -TaskName $SweepTaskName -Action $aSweep -Trigger $tDaily -Principal $pLimit -Settings $sSweep `
    -Description 'ai-stack systems-administrator: daily /tmp lc-*.jsonl sweep (old files only, running workers)' -Force | Out-Null
  Write-Output "Registered '$SweepTaskName' (daily 04:00)."
} else { Write-Warning "sweep_tmp.py not found; skipped daily sweep task." }

# 4) daily backup-freshness monitor (Limited) — posts a #sysadmin alert when any
#    RUNNING backup's newest artifact is older than its cadence threshold. Closes
#    the gap where coverage was checked but recency never was (a *-backup can be
#    "up" yet silently producing nothing). 09:30 = after the nightly backups and
#    the disk check, so a missed nightly run is caught the same morning.
$bchk = Join-Path $here 'check_backups.py'
if (Test-Path $bchk) {
  $aBchk = New-ScheduledTaskAction -Execute $py -Argument "`"$bchk`"" -WorkingDirectory $repo
  $tBchk = New-ScheduledTaskTrigger -Daily -At 9:30am
  $sBchk = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 15) -MultipleInstances IgnoreNew
  Register-ScheduledTask -TaskName $BackupCheckTaskName -Action $aBchk -Trigger $tBchk -Principal $pLimit -Settings $sBchk `
    -Description 'ai-stack systems-administrator: daily backup-freshness check -> #sysadmin alert' -Force | Out-Null
  Write-Output "Registered '$BackupCheckTaskName' (daily 09:30)."
} else { Write-Warning "check_backups.py not found; skipped backup-freshness task." }

Write-Output "DONE. compact_execute armed; disk check + /tmp sweep + backup-freshness scheduled."
