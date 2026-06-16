# scripts/portal-off.ps1
#
# Stop the portal in whatever mode it's currently running (plan sec.12.9).
# IMPORTANT: this script explicitly names every portal service to stop.
# `docker compose stop` without explicit names is project-scoped and would
# stop the entire ai-stack (openwebui, llama-cpp, etc.). The `--profile`
# flag does NOT filter the scope of stop / down / rm operations.
#
# Usage:
#   .\scripts\portal-off.ps1
#   .\scripts\portal-off.ps1 -WhatIf  # dry-run, no changes
#
# DIFFERENT FROM breach-killswitch.ps1:
#   - portal-off:     stop containers, preserve everything.
#   - killswitch:     stop containers, EMAIL FIRST, snapshot logs, rotate
#                     Authelia secrets, print recovery steps.
#
# Use portal-off for "I'm done for the day" / "switching from test to
# production." Use the killswitch when something is actively wrong.

[CmdletBinding(SupportsShouldProcess=$true)]
param()

# docker compose writes status to stderr by design. Don't let it trip
# $ErrorActionPreference = Stop.
$ErrorActionPreference = 'Continue'
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $projectRoot
try {
  Write-Host "==> Stopping portal services explicitly" -ForegroundColor Cyan
  Write-Host "    Project root: $projectRoot"
  Write-Host "    Volumes and configuration are preserved."
  Write-Host ""

  # All portal services. cloudflared only runs in production mode but
  # naming it here is harmless — docker compose silently no-ops on services
  # that aren't running.
  $portalServices = @(
    'cloudflared',
    'caddy-backup', 'authelia-backup',
    'portal-cron', 'integrity-tripwire', 'authelia-watcher',
    'caddy', 'authelia',
    'portal-alerter',
    'portal-init'
  )

  # Both compose files referenced so the local-test override is also
  # known (idempotent — fine to include even in production teardown).
  $stopArgs = @('-f', 'docker-compose.yml', '-f', 'docker-compose.local-test.override.yml', 'stop') + $portalServices
  if ($PSCmdlet.ShouldProcess(($portalServices -join ', '), "docker compose $($stopArgs -join ' ')")) {
    docker compose @stopArgs
    if ($LASTEXITCODE -ne 0) {
      Write-Warning "docker compose stop returned non-zero exit code $LASTEXITCODE"
    }
  }

  Write-Host ""
  Write-Host "==> Sanity check: confirm non-portal stack is untouched" -ForegroundColor Cyan
  $expectedRunning = @('openwebui', 'llama-cpp-upstream', 'mnemory', 'tailscale')
  foreach ($svc in $expectedRunning) {
    $state = docker inspect --format '{{.State.Status}}' $svc 2>$null
    if ($LASTEXITCODE -eq 0 -and $state -eq 'running') {
      Write-Host "  [OK]   $svc is still running" -ForegroundColor Green
    } else {
      Write-Host "  [WARN] $svc state=$state (expected running)" -ForegroundColor Yellow
    }
  }

  Write-Host ""
  Write-Host "==> Running status check..." -ForegroundColor Green
  & (Join-Path $PSScriptRoot 'portal-status.ps1')
} finally {
  Pop-Location
}
