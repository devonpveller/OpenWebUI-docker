<#
.SYNOPSIS
  Launch a SECOND claude-sessions-bridge instance as the @sysadmin persona (watches #sysadmin).

.DESCRIPTION
  Reuses the existing bridge.py codebase with a distinct identity/channel/charter via env only:
    BRIDGE_CHANNEL_ID   = #sysadmin channel id (from config.json sysadmin_channel_id)
    BRIDGE_TOKEN_KEY    = SYSADMIN_MM_BOT_TOKEN  (bot-sysadmin token in agent-org/docker/.env)
    BRIDGE_LOCK_PORT    = 48292  (the #claude-sessions bridge owns 48291)
    BRIDGE_CHARTER_FILE = this dir's charter.md (appended to the bridge's REMOTE_NOTE)
    BRIDGE_OPERATORS    = config.json sysadmin_operators (else bridge default: profnovice)

  The sysadmin MCP tools ride in automatically via repo .mcp.json (bridge uses --mcp-config without
  --strict-mcp-config). Prereqs: create the bot-sysadmin account + token, create #sysadmin, and set
  sysadmin_channel_id in config.json. Registered to run at logon by register-sysadmin-bridge.ps1.
#>
[CmdletBinding()]
param()

$here = $PSScriptRoot
$repo = (Get-Item $here).Parent.Parent.FullName
$bridgeDir = Join-Path $repo 'scripts\claude-sessions-bridge'
$cfgPath = Join-Path $here 'config.json'

$cfg = Get-Content $cfgPath -Raw | ConvertFrom-Json
if ([string]::IsNullOrWhiteSpace([string]$cfg.sysadmin_channel_id)) {
  Write-Error "Set 'sysadmin_channel_id' in scripts/sysadmin-mcp/config.json (the #sysadmin channel name or 26-char id)."
  exit 1
}

$venvPy = Join-Path $repo '.venv\Scripts\python.exe'
$pyExe = if (Test-Path $venvPy) { $venvPy } else { 'python' }
# resolve a #name OR a 26-char id -> channel id via the bot-sysadmin identity
$channel = (& $pyExe (Join-Path $here 'resolve_channel.py'))
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($channel)) {
  Write-Error "Could not resolve the sysadmin channel '$($cfg.sysadmin_channel_id)'. Ensure bot-sysadmin is a member of #sysadmin and SYSADMIN_MM_BOT_TOKEN is set in .env."
  exit 1
}

$env:BRIDGE_CHANNEL_ID   = $channel.Trim()
$env:BRIDGE_TOKEN_KEY    = 'SYSADMIN_MM_BOT_TOKEN'
$env:BRIDGE_LOCK_PORT    = '48292'
$env:BRIDGE_CHARTER_FILE = (Join-Path $here 'charter.md')
$env:BRIDGE_STATE_DIR    = (Join-Path $here 'bridge-state')  # isolated from the #claude-sessions bridge's state.json
if ($cfg.sysadmin_operators) { $env:BRIDGE_OPERATORS = [string]$cfg.sysadmin_operators }

$venvPyw = Join-Path $repo '.venv\Scripts\pythonw.exe'
$pyw = if (Test-Path $venvPyw) { $venvPyw } else { 'pythonw' }

Set-Location $bridgeDir
& $pyw (Join-Path $bridgeDir 'bridge.py')
