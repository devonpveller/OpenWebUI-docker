<#
.SYNOPSIS
  ONE-TIME (elevated) registration of the sysadmin Telegram command listener as a logon Scheduled Task.

.DESCRIPTION
  Registers 'sysadmin-telegram-listener' to run telegram_listener.py at logon (restart on failure).
  The listener is the DOCKER-INDEPENDENT break-glass control channel: it long-polls the Telegram Bot
  API and runs a whitelist of recovery commands (status / docker up / recover / compact status /
  gpu-reset / nuclear), only for the operator chat_id. Because it is a HOST process it stays up when
  Docker -- and therefore Mattermost -- is down, which is exactly when the operator needs a way back in.

  PREREQUISITES (or the listener exits immediately and just retries at logon):
    SYSADMIN_TELEGRAM_BOT_TOKEN and SYSADMIN_TELEGRAM_CHAT_ID must be set in the repo-root .env.

  Mirrors the 'sysadmin-bridge' task pattern (logon start, Limited, own single-instance lock port 48293).
  Registering scheduled tasks requires admin -- run this once from an elevated PowerShell. Re-running updates it.
#>
[CmdletBinding()]
param([string]$TaskName = 'sysadmin-telegram-listener')

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { Write-Error 'Must run elevated (Run as administrator).'; exit 1 }

$here = $PSScriptRoot
$repo = (Get-Item $here).Parent.Parent.FullName
$script = Join-Path $here 'telegram_listener.py'
if (-not (Test-Path $script)) { Write-Error "missing $script"; exit 1 }

# prefer venv pythonw (no console window); fall back to system pythonw
$venvPyw = Join-Path $repo '.venv\Scripts\pythonw.exe'
$pyw = if (Test-Path $venvPyw) { $venvPyw } else { 'pythonw' }

# sanity: warn (do not block) if creds are missing
$envFile = Join-Path $repo '.env'
if (Test-Path $envFile) {
  $lines = Get-Content $envFile
  if (-not ($lines | Where-Object { $_ -match '^SYSADMIN_TELEGRAM_BOT_TOKEN=' })) {
    Write-Warning 'SYSADMIN_TELEGRAM_BOT_TOKEN not found in .env; listener will exit until it is set.'
  }
  if (-not ($lines | Where-Object { $_ -match '^SYSADMIN_TELEGRAM_CHAT_ID=' })) {
    Write-Warning 'SYSADMIN_TELEGRAM_CHAT_ID not found in .env; listener will exit until it is set.'
  }
} else {
  Write-Warning "repo .env not found at $envFile"
}

$user = "$env:USERDOMAIN\$env:USERNAME"
$arg = '"' + $script + '"'
$action = New-ScheduledTaskAction -Execute $pyw -Argument $arg -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -AtLogOn
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description 'ai-stack sysadmin: out-of-band Telegram command listener (break-glass control channel; lock port 48293)' -Force | Out-Null

Write-Output "Registered '$TaskName' (logon start, Limited, lock port 48293)."
Write-Output "python (no-console): $pyw"
Write-Output "Start it now with:  schtasks /run /tn $TaskName"
Write-Output "Then text @ai_stack_sysadmin_bot:  help"
