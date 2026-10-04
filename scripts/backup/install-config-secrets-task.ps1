# scripts/backup/install-config-secrets-task.ps1
#
# Registers the Windows Scheduled Task that runs the encrypted config + secrets
# backup (scripts/backup/config_secrets_backup.py run) every night.
#
# Run ONCE, after the one-time key setup in
# documentation/runbooks/config-secrets-backup.md (an age PUBLIC key in
# secrets/config-backup/age-recipients.txt and the pinned age.exe on disk).
# Needs an elevated PowerShell: registering an S4U task is an admin operation.
#
# Schedule: daily at 02:30 LOCAL time - after the nightly sidecars, before the
# 03:15 maintenance/compaction window that may restart Docker, and before the
# Sunday 04:00 NAS sync (install-nas-backup-task.ps1), so the week's newest
# archive is in every NAS slot. The job needs the docker CLI (for
# `docker compose config`), not a running daemon.
#
# Parameters:
#   -AgePath   Required. Absolute path to the pinned age.exe. Passed to the job as
#              --age; the job refuses a binary whose sha256 is not pinned in
#              scripts/backup/config-secrets.toml ([[age_pin]]).
#   -At        Optional. Daily start time (default 02:30).
#   -RunAs     Optional. Account (default: the current user, S4U - no password stored).
#   -RunNow    Optional. Start the task once right after registering it.
#
# Usage (elevated):
#   .\scripts\backup\install-config-secrets-task.ps1 -AgePath "C:\Tools\age\age.exe" -RunNow
#
# Verify:
#   Get-ScheduledTask -TaskPath '\AI-Stack\' -TaskName 'AI-Stack Config Secrets Backup'
#   Get-Content .\logs\config-secrets-backup-<today>.log

[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)]
  [string]$AgePath,
  [string]$At = '02:30',
  [string]$RunAs = (whoami),
  [switch]$RunNow
)

$ErrorActionPreference = 'Stop'

$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
  Write-Host "ERROR: this script must run as administrator (S4U task registration)." -ForegroundColor Red
  exit 1
}

$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$job = Join-Path $PSScriptRoot 'config_secrets_backup.py'
if (-not (Test-Path $job)) {
  Write-Host "ERROR: config_secrets_backup.py not found at $job" -ForegroundColor Red
  exit 1
}
if (-not [System.IO.Path]::IsPathRooted($AgePath) -or -not (Test-Path $AgePath -PathType Leaf)) {
  Write-Host "ERROR: -AgePath must be the absolute path of an existing age.exe ($AgePath)" -ForegroundColor Red
  exit 1
}
$recipients = Join-Path $projectRoot 'secrets\config-backup\age-recipients.txt'
if (-not (Test-Path $recipients -PathType Leaf)) {
  Write-Host "ERROR: no age recipient at $recipients." -ForegroundColor Red
  Write-Host "Do the one-time key setup first: documentation/runbooks/config-secrets-backup.md" -ForegroundColor Yellow
  exit 1
}

# Same interpreter rule as register-sysadmin-tasks.ps1: the repo venv, else PATH.
$venvPy = Join-Path $projectRoot '.venv\Scripts\python.exe'
$py = if (Test-Path $venvPy) { $venvPy } else { (Get-Command python -ErrorAction SilentlyContinue).Source }
if (-not $py) {
  Write-Host "ERROR: no python (expected $venvPy or python on PATH)" -ForegroundColor Red
  exit 1
}

$taskName = 'AI-Stack Config Secrets Backup'
$taskPath = '\AI-Stack\'
$jobArgs = "`"$job`" run --age `"$AgePath`""

Write-Host "==> Registering scheduled task" -ForegroundColor Cyan
Write-Host "    Task name : $taskPath$taskName"
Write-Host "    Schedule  : daily at $At (local time)"
Write-Host "    Run as    : $RunAs (S4U, limited)"
Write-Host "    Executable: $py"
Write-Host "    Arguments : $jobArgs"

if (Get-ScheduledTask -TaskName $taskName -TaskPath $taskPath -ErrorAction SilentlyContinue) {
  Write-Host "    (existing task found -- removing before re-registering)" -ForegroundColor Yellow
  Unregister-ScheduledTask -TaskName $taskName -TaskPath $taskPath -Confirm:$false
}

$action = New-ScheduledTaskAction -Execute $py -Argument $jobArgs -WorkingDirectory $projectRoot
$trigger = New-ScheduledTaskTrigger -Daily -At $At
$settings = New-ScheduledTaskSettingsSet `
  -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries `
  -StartWhenAvailable `
  -RestartCount 2 `
  -RestartInterval (New-TimeSpan -Minutes 15) `
  -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
$principal = New-ScheduledTaskPrincipal -UserId $RunAs -LogonType S4U -RunLevel Limited

Register-ScheduledTask `
  -TaskName $taskName `
  -TaskPath $taskPath `
  -Action $action `
  -Trigger $trigger `
  -Settings $settings `
  -Principal $principal `
  -Description "Nightly age-encrypted archive of the stack's gitignored config + secrets into backups\config-secrets\. See documentation/runbooks/config-secrets-backup.md." | Out-Null

Write-Host ""
Write-Host "==> Registered." -ForegroundColor Green
if ($RunNow) {
  Write-Host "==> -RunNow: starting the task once" -ForegroundColor Cyan
  Start-ScheduledTask -TaskName $taskName -TaskPath $taskPath
  Write-Host "    Then: Get-Content .\logs\config-secrets-backup-$(Get-Date -Format 'yyyy-MM-dd').log -Tail 20"
}
