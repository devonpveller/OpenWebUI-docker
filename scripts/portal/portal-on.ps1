# scripts/portal-on.ps1
#
# Portal lifecycle entrypoint (plan sec.12.9).
#
# Two mutually-exclusive modes:
#
#   -Test  (LOCAL DEVELOPMENT / SAFE TESTING)
#     * Uses compose profile `local-test`
#     * Applies docker-compose.local-test.override.yml
#     * Binds caddy to 127.0.0.1:8443:80 so a local browser can reach it
#     * Does NOT start cloudflared (no internet exposure)
#     * Use this for working on the portal, verifying routing, validating
#       changes BEFORE going live.
#
#   (default, no flag) = PRODUCTION
#     * Uses compose profile `internet`
#     * No host ports bound (Cloudflare Tunnel is the only ingress)
#     * Starts cloudflared (portal becomes reachable from the internet
#       via https://${PUBLIC_DOMAIN}/)
#
# Either mode brings up the same set of portal services in the same
# dependency order; only the tunnel + port binding differ. This means a
# clean dev->prod handoff: validate in -Test, then `portal-off`, then
# `portal-on` (no flag) to go live.
#
# Usage:
#   .\scripts\portal-on.ps1              # production mode
#   .\scripts\portal-on.ps1 -Test        # local test mode
#   .\scripts\portal-on.ps1 -WhatIf      # dry-run, no changes
#
# Distinct from `breach-killswitch.ps1` -- that is for incidents.

[CmdletBinding(SupportsShouldProcess=$true)]
param(
  [switch]$Test
)

# docker compose writes status to stderr by design (e.g., "Container starting").
# In Windows PowerShell, $ErrorActionPreference = 'Stop' treats those lines as
# fatal NativeCommandError records. Keep Continue and check $LASTEXITCODE.
$ErrorActionPreference = 'Continue'
$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Push-Location $projectRoot
try {
  # Own compose project since 2026-08-21 (CLEANUP-PLAN v3 #5). The root .env
  # is passed explicitly - interpolation no longer finds it from portal/.
  $portalBase = @('-p', 'portal',
                  '-f', (Join-Path $projectRoot 'portal\docker-compose.yml'),
                  '--env-file', (Join-Path $projectRoot '.env'))
  if ($Test) {
    $composeArgs = $portalBase + @('-f', (Join-Path $projectRoot 'portal\local-test.override.yml'))
    Write-Host "==> Bringing portal up in TEST mode (no tunnel)" -ForegroundColor Cyan
    Write-Host "    No internet exposure. Caddy bound to 127.0.0.1:8443."
  } else {
    $composeArgs = $portalBase + @('--profile', 'internet')
    Write-Host "==> Bringing portal up in PRODUCTION mode" -ForegroundColor Cyan
    Write-Host "    cloudflared will connect; portal becomes internet-reachable."
  }
  Write-Host "    Project root: $projectRoot"
  Write-Host ""

  # Ordered groups -- each group is brought up together, then we briefly
  # pause before starting the next group so dependent healthchecks have
  # time to flip green.
  $groups = @(
    @{ Name = 'alerter';   Services = @('portal-alerter') },
    @{ Name = 'auth';      Services = @('authelia') },
    @{ Name = 'caddy';     Services = @('caddy') },
    @{ Name = 'watchers';  Services = @('authelia-watcher','authelia-notif-bridge','integrity-tripwire','portal-cron') },
    @{ Name = 'backups';   Services = @('caddy-backup','authelia-backup') }
  )

  # cloudflared only in production. In test mode the tunnel never runs;
  # the local-test override file binds caddy to 127.0.0.1 for browser access.
  if (-not $Test) {
    $groups += @{ Name = 'tunnel'; Services = @('cloudflared','tunnel-watcher') }
  }

  foreach ($group in $groups) {
    Write-Host "==> Group: $($group.Name) -- $($group.Services -join ', ')" -ForegroundColor Cyan
    $svcArgs = $group.Services
    $allArgs = $composeArgs + @('up', '-d') + $svcArgs
    if ($PSCmdlet.ShouldProcess(($svcArgs -join ', '), "docker compose $($allArgs -join ' ')")) {
      docker compose @allArgs
      if ($LASTEXITCODE -ne 0) { throw "docker compose up failed for group $($group.Name)" }
    }
    Start-Sleep -Seconds 2
  }

  Write-Host ""
  if ($Test) {
    Write-Host "==> Portal is up in TEST mode." -ForegroundColor Green
    Write-Host "    Browser: http://127.0.0.1:8443/  (will hit catch-all 421 unless you" -ForegroundColor Yellow
    Write-Host "             pass a matching Host header, or add to your Windows hosts:" -ForegroundColor Yellow
    Write-Host "                 127.0.0.1 devinveller.ai auth.devinveller.ai" -ForegroundColor Yellow
    Write-Host "             then visit http://devinveller.ai:8443/." -ForegroundColor Yellow
    Write-Host "    Cookies will NOT persist over HTTP -- this validates routing, not the" -ForegroundColor Yellow
    Write-Host "    full login UX. For UX testing, switch to production mode." -ForegroundColor Yellow
  } else {
    Write-Host "==> Portal is up in PRODUCTION mode." -ForegroundColor Green
    Write-Host "    Public URL: https://${env:PUBLIC_DOMAIN}/"
  }
  Write-Host ""
  Write-Host "==> Running status check..." -ForegroundColor Green
  & (Join-Path $PSScriptRoot 'portal-status.ps1')
} finally {
  Pop-Location
}
