# scripts/portal-off.ps1
#
# Planned-downtime toggle for the portal (plan sec.12.9). Stops every container
# tagged `profiles: [internet]` but preserves their volumes, .env, and
# configuration. Tailnet access remains unaffected.
#
# Usage:
#   .\scripts\portal-off.ps1
#   .\scripts\portal-off.ps1 -WhatIf  # print intended actions, do nothing
#
# DIFFERENT FROM breach-killswitch.ps1:
#   - portal-off:     stop containers, preserve everything.
#   - killswitch:     stop containers, EMAIL FIRST, snapshot logs, rotate
#                     Authelia secrets, print recovery steps.
#
# Use portal-off for "I'm done for the day". Use the killswitch when
# something is actively wrong.

[CmdletBinding(SupportsShouldProcess=$true)]
param()

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $projectRoot
try {
  Write-Host "==> Stopping portal (compose profile: internet)" -ForegroundColor Cyan
  Write-Host "    Project root: $projectRoot"
  Write-Host "    Volumes and configuration are preserved."
  Write-Host ""

  if ($PSCmdlet.ShouldProcess('compose profile: internet', 'docker compose --profile internet stop')) {
    docker compose --profile internet stop
    if ($LASTEXITCODE -ne 0) {
      Write-Warning "docker compose stop returned non-zero exit code $LASTEXITCODE"
    }
  }

  Write-Host ""
  Write-Host "==> Running status check..." -ForegroundColor Green
  & (Join-Path $PSScriptRoot 'portal-status.ps1')
} finally {
  Pop-Location
}
