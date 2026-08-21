<#
.SYNOPSIS
  ONE-TIME (elevated) registration of the @sysadmin persona bridge as a logon Scheduled Task.

.DESCRIPTION
  Registers 'sysadmin-bridge' to run sysadmin-bridge-launch.ps1 at logon (restarts on failure),
  starting a 2nd claude-sessions-bridge instance watching #sysadmin as bot-sysadmin.

  PREREQUISITES (do these first, or the bridge refuses to start / auth-fails):
    1. Create a Mattermost bot 'bot-sysadmin'; put its token in agent-org/docker/.env (or repo .env)
       as SYSADMIN_MM_BOT_TOKEN=...
    2. Create the #sysadmin channel; add bot-sysadmin + the operator; set sysadmin_channel_id
       (name or 26-char id) in scripts/sysadmin-mcp/config.json.
    3. Run this script elevated.

  Mirrors the existing 'claude-sessions-bridge' task pattern (logon start, own lock port 48292).
#>
[CmdletBinding()]
param([string]$TaskName = 'sysadmin-bridge')

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { Write-Error 'Must run elevated (Run as administrator).'; exit 1 }

$here = $PSScriptRoot
$launch = Join-Path $here 'sysadmin-bridge-launch.ps1'
if (-not (Test-Path $launch)) { Write-Error "missing $launch"; exit 1 }

# sanity: warn (do not block) if the channel is unset
$cfg = Get-Content (Join-Path $here 'config.json') -Raw | ConvertFrom-Json
if ([string]::IsNullOrWhiteSpace([string]$cfg.sysadmin_channel_id)) {
  Write-Warning 'config.json sysadmin_channel_id is not set; the bridge will refuse to start until you set it.'
}

$user = "$env:USERDOMAIN\$env:USERNAME"
# build the -Argument with a literal double-quoted path via concatenation (no backtick escaping)
$arg = '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + $launch + '"'
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arg
$trigger = New-ScheduledTaskTrigger -AtLogOn
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description 'ai-stack systems-administrator persona (2nd claude-sessions-bridge on #sysadmin)' -Force | Out-Null

Write-Output "Registered '$TaskName' (logon start, lock port 48292)."
Write-Output "Start it now with:  schtasks /run /tn $TaskName"
Write-Output 'Ensure SYSADMIN_MM_BOT_TOKEN is in .env and sysadmin_channel_id is set in config.json.'
