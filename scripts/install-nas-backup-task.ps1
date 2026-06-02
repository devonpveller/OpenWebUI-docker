# scripts/install-nas-backup-task.ps1
#
# Registers a Windows Scheduled Task that runs backup-to-nas.ps1 weekly
# on Sundays at 04:00 (local time, intentionally after the nightly compose
# backups at 03:00 UTC have landed).
#
# Run this script ONCE during setup. It requires administrator privileges
# to register a system-level scheduled task.
#
# Parameters:
#   -NasUncRoot       Required. e.g. \\192.168.1.50\backups\portal
#   -RunNow           Switch: run the task immediately after registering
#                     (useful for the first install -- verifies the SMB
#                     credential works, the slot folder gets created,
#                     and the sentinel integrity check passes).
#   -RunAs            Optional. Username under which the task runs. Default
#                     is the current user (S4U logon -- no Windows password
#                     needed). The task reads its NAS credentials from
#                     secrets/nas-backup-cred.dat (DPAPI-encrypted, machine
#                     scope) which set-nas-credential.ps1 populates.
#
# Usage:
#   # Step 1 - save the NAS credential (as the user who will run the task):
#   cmdkey /add:192.168.1.50 /user:nasuser /pass:nas-password
#
#   # Step 2 - register the task (admin PowerShell):
#   .\scripts\install-nas-backup-task.ps1 -NasUncRoot "\\192.168.1.50\backups\portal" -RunNow

[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)]
  [string]$NasUncRoot,

  [Parameter(Mandatory = $false)]
  [string]$RunAs = (whoami),

  [switch]$RunNow
)

$ErrorActionPreference = 'Stop'

# Pre-flight: must be admin to register a system-level scheduled task
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
  Write-Host "ERROR: this script must run as administrator." -ForegroundColor Red
  Write-Host "Right-click PowerShell -> Run as administrator, then re-run." -ForegroundColor Yellow
  exit 1
}

$projectRoot = Split-Path -Parent $PSScriptRoot
$syncScript = Join-Path $PSScriptRoot 'backup-to-nas.ps1'

if (-not (Test-Path $syncScript)) {
  Write-Host "ERROR: backup-to-nas.ps1 not found at $syncScript" -ForegroundColor Red
  exit 1
}

# Resolve PowerShell executable. Prefer pwsh (PS 7+) if installed, fall back
# to Windows PowerShell.
$pwshExe = (Get-Command pwsh -ErrorAction SilentlyContinue).Source
if (-not $pwshExe) {
  $pwshExe = (Get-Command powershell -ErrorAction SilentlyContinue).Source
}
if (-not $pwshExe) {
  Write-Host "ERROR: neither pwsh nor powershell found in PATH" -ForegroundColor Red
  exit 1
}

# Build the argument string. Quote NasUncRoot for any embedded spaces.
$scriptArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$syncScript`" -NasUncRoot `"$NasUncRoot`""

$taskName = 'AI-Stack Portal NAS Backup'
$taskPath = '\AI-Stack\'

Write-Host "==> Registering scheduled task" -ForegroundColor Cyan
Write-Host "    Task name : $taskPath$taskName"
Write-Host "    Schedule  : Sundays at 04:00 (local time)"
Write-Host "    Run as    : $RunAs"
Write-Host "    Executable: $pwshExe"
Write-Host "    Arguments : $scriptArgs"

# Remove any pre-existing task with the same name to keep registrations clean
if (Get-ScheduledTask -TaskName $taskName -TaskPath $taskPath -ErrorAction SilentlyContinue) {
  Write-Host "    (existing task found -- removing before re-registering)" -ForegroundColor Yellow
  Unregister-ScheduledTask -TaskName $taskName -TaskPath $taskPath -Confirm:$false
}

$action = New-ScheduledTaskAction -Execute $pwshExe -Argument $scriptArgs -WorkingDirectory $projectRoot
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At 4am
# Wake the machine if sleeping, allow on battery, retry if missed by a power-state
$settings = New-ScheduledTaskSettingsSet `
  -WakeToRun `
  -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries `
  -StartWhenAvailable `
  -RestartCount 3 `
  -RestartInterval (New-TimeSpan -Minutes 15) `
  -ExecutionTimeLimit (New-TimeSpan -Hours 4)

# Run as the named user via S4U (Service for User) -- the task runs with the
# user's primary token but WITHOUT needing the Windows account password.
# The backup-to-nas.ps1 script does NOT depend on the Credential Manager
# vault: it reads its NAS credentials from a DPAPI-LocalMachine-encrypted
# file at secrets/nas-backup-cred.dat (created via set-nas-credential.ps1),
# so this task can authenticate to the NAS as the dedicated backup user
# without ever knowing the operator's Windows password.
$principal = New-ScheduledTaskPrincipal -UserId $RunAs -LogonType S4U -RunLevel Highest

Register-ScheduledTask `
  -TaskName $taskName `
  -TaskPath $taskPath `
  -Action $action `
  -Trigger $trigger `
  -Settings $settings `
  -Principal $principal `
  -Description "Weekly two-slot mirror of D:\Open WebUI\ai-stack\backups\ to the NAS. See scripts/backup-to-nas.ps1." | Out-Null

Write-Host ""
Write-Host "==> Registered. Verify with:" -ForegroundColor Green
Write-Host "    Get-ScheduledTask -TaskPath '$taskPath' | Format-Table TaskName, State, Triggers"

if ($RunNow) {
  Write-Host ""
  Write-Host "==> -RunNow given; firing the task immediately" -ForegroundColor Cyan
  Start-ScheduledTask -TaskName $taskName -TaskPath $taskPath
  Start-Sleep -Seconds 3
  Write-Host "    Status:"
  Get-ScheduledTaskInfo -TaskName $taskName -TaskPath $taskPath | Select-Object LastRunTime, LastTaskResult, NextRunTime | Format-List
  Write-Host "    Live log tail (Ctrl+C to stop):"
  $logFile = Join-Path $projectRoot ('logs\nas-sync-' + (Get-Date -Format 'yyyy-MM-dd') + '.log')
  Get-Content -Path $logFile -Wait -Tail 50 -ErrorAction SilentlyContinue
}
