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

# --- PRE-FLIGHT: portal/.env exists and its required keys are non-blank ------
#
# Added with sl-env-split (2026-09-19), and it is not decoration. Until this
# item, every portal command passed `--env-file <repo root>/.env`, and compose
# hard-refuses a NAMED env file that is absent - so "no env file" could not
# start anything. Compose now loads portal/.env from the project directory,
# where ABSENT is not an error, so that refusal had to be rebuilt. The compose
# file's ${AUTHELIA_JWT_SECRET:?} guard rebuilds half of it (an absent or
# JWT-blank file cannot render); this rebuilds the other half, because a file
# that EXISTS with a blank CLOUDFLARE_TUNNEL_TOKEN or a blank
# AUTHELIA_SESSION_SECRET renders perfectly well and puts an auth gate with no
# session secret on the internet.
#
# The key list is stack.manifest.toml's [planes.portal] `keys`, read from the
# file rather than copied, so adding a key there arms it here. It is NOT read
# via stack.py: the portal is a `manual` plane, deliberately absent from the
# driver's enabled set, so `stack.py doctor` never reaches it - which is the
# whole reason this check lives in the operator's own entrypoint.
#
# WHAT THIS DOES NOT COVER, stated so nobody mistakes it for a plane-wide gate:
# it guards THIS entrypoint only. A portal/.env that exists with, say, a blank
# AUTHELIA_SESSION_SECRET still renders for portal-off.ps1, for
# restore-from-snapshot.ps1's `caddy` and `authelia` entries, and for a
# hand-typed `docker compose -f portal/docker-compose.yml ...`. Only the ABSENT
# file is refused everywhere, by the compose guard. That is narrower than a
# plane-wide blank-key check and wider than what regressed; a real one would
# belong in the driver, behind a `manual` plane it would first have to reach.
$portalEnv = Join-Path $projectRoot 'portal\.env'
if (-not (Test-Path $portalEnv)) {
  Write-Host "REFUSED: portal/.env not found at $portalEnv" -ForegroundColor Red
  Write-Host "  Copy portal/.env.example to portal/.env and fill it in." -ForegroundColor Yellow
  Write-Host "  Migrating an existing host: documentation/runbooks/env-split-migration.md" -ForegroundColor Yellow
  Pop-Location; exit 1
}
$manifestPath = Join-Path $projectRoot 'stack.manifest.toml'
$requiredKeys = @()
if (Test-Path $manifestPath) {
  # Minimal TOML slice: the `keys = [ ... ]` array inside [planes.portal].
  # Deliberately not a TOML parser - PS 5.1 has none, and a missing manifest or
  # an unreadable table must DEGRADE to the built-in list below, never silently
  # check nothing.
  $manifestText = Get-Content $manifestPath -Raw
  $section = [regex]::Match($manifestText, '(?s)\[planes\.portal\](.*?)(?:\r?\n\[)')
  if ($section.Success) {
    $arr = [regex]::Match($section.Groups[1].Value, '(?s)keys\s*=\s*\[(.*?)\]')
    if ($arr.Success) {
      $requiredKeys = @([regex]::Matches($arr.Groups[1].Value, '"([A-Za-z_][A-Za-z0-9_]*)"') |
                        ForEach-Object { $_.Groups[1].Value })
    }
  }
}
if (-not $requiredKeys -or $requiredKeys.Count -eq 0) {
  Write-Host "  [pre-flight] could not read [planes.portal] keys from stack.manifest.toml - using the built-in list" -ForegroundColor Yellow
  $requiredKeys = @('CLOUDFLARE_TUNNEL_TOKEN','AUTHELIA_JWT_SECRET','AUTHELIA_SESSION_SECRET',
                    'AUTHELIA_STORAGE_ENCRYPTION_KEY','PUBLIC_DOMAIN')
}
$portalValues = @{}
foreach ($line in (Get-Content $portalEnv)) {
  if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$') {
    $portalValues[$Matches[1]] = $Matches[2].Trim().Trim('"').Trim("'")
  }
}
$blank = @($requiredKeys | Where-Object { -not $portalValues.ContainsKey($_) -or $portalValues[$_] -eq '' })
if ($blank.Count -gt 0) {
  Write-Host "REFUSED: portal/.env is missing a value for: $($blank -join ', ')" -ForegroundColor Red
  Write-Host "  This plane is INTERNET-EXPOSED. Fill them in before starting it." -ForegroundColor Yellow
  Write-Host "  (the required list is stack.manifest.toml [planes.portal] keys)" -ForegroundColor Yellow
  Pop-Location; exit 1
}
Write-Host "  [pre-flight] portal/.env present; $($requiredKeys.Count) required key(s) non-blank" -ForegroundColor DarkGray

try {
  # Own compose project since 2026-08-21 (CLEANUP-PLAN v3 #5). NO --env-file
  # since sl-env-split (2026-09-19): `-f portal\docker-compose.yml` makes
  # portal/ the project directory, so compose loads portal/.env itself. The
  # `internet` profile stays a COMMAND-LINE flag and is deliberately absent
  # from portal/.env - exposing the stack must never be a standing setting.
  $portalBase = @('-p', 'portal',
                  '-f', (Join-Path $projectRoot 'portal\docker-compose.yml'))
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
