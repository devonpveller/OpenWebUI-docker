# scripts/portal-on.ps1
#
# Routine on/off lifecycle for the internet-exposed portal (plan sec.12.9).
# Starts only the containers tagged `profiles: [internet]`. The rest of the
# ai-stack is unaffected.
#
# Usage:
#   .\scripts\portal-on.ps1              # bring portal up
#   .\scripts\portal-on.ps1 -WhatIf      # print intended actions, do nothing
#   .\scripts\portal-on.ps1 -SkipTunnel  # bring everything up EXCEPT cloudflared
#                                        # (useful during local dev -- portal exists
#                                        # but is not exposed to the internet)
#
# Distinct from `breach-killswitch.ps1` -- that is for incidents.

[CmdletBinding(SupportsShouldProcess=$true)]
param(
  [switch]$SkipTunnel
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $projectRoot
try {
  Write-Host "==> Bringing portal up (compose profile: internet)" -ForegroundColor Cyan
  Write-Host "    Project root: $projectRoot"
  Write-Host ""

  # Ordered groups -- each group is brought up together, then we wait for
  # health before starting the next group.
  $groups = @(
    @{ Name = 'alerter';   Services = @('portal-alerter') },
    @{ Name = 'auth';      Services = @('authelia') },
    @{ Name = 'caddy';     Services = @('caddy') },
    @{ Name = 'watchers';  Services = @('authelia-watcher','integrity-tripwire','portal-cron') },
    @{ Name = 'backups';   Services = @('caddy-backup','authelia-backup') }
  )

  if (-not $SkipTunnel) {
    $groups += @{ Name = 'tunnel'; Services = @('cloudflared') }
  } else {
    Write-Host "  [SkipTunnel] cloudflared will NOT be started." -ForegroundColor Yellow
    Write-Host "              Portal is reachable only from inside Docker / LAN."
    Write-Host ""
  }

  foreach ($group in $groups) {
    Write-Host "==> Group: $($group.Name) -- $($group.Services -join ', ')" -ForegroundColor Cyan
    $svcArgs = $group.Services
    if ($PSCmdlet.ShouldProcess(($svcArgs -join ', '), 'docker compose --profile internet up -d')) {
      docker compose --profile internet up -d @svcArgs
      if ($LASTEXITCODE -ne 0) { throw "docker compose up failed for group $($group.Name)" }
    }
    Start-Sleep -Seconds 2
  }

  Write-Host ""
  Write-Host "==> Portal is up. Running status check..." -ForegroundColor Green
  & (Join-Path $PSScriptRoot 'portal-status.ps1')
} finally {
  Pop-Location
}
