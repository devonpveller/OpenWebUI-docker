# scripts/breach-killswitch.ps1
#
# Emergency stop for the internet-exposed portal (plan sec.8 Step 6, sec.12.6).
# Distinct from portal-off.ps1:
#   1. Sends a "killswitch fired" email FIRST (so the operator inbox has a
#      final record before the alerter goes down).
#   2. Stops the internet-exposed services (preserves tailnet path).
#   3. Snapshots Caddy + Authelia logs to ./incident/<UTC-timestamp>/.
#   4. Rotates AUTHELIA_JWT_SECRET + AUTHELIA_SESSION_SECRET in .env (preserves
#      old values commented out for the IR record).
#   5. Prints recovery steps and EXITS -- does NOT auto-restart anything.
#
# Usage:
#   .\scripts\breach-killswitch.ps1            # do it for real
#   .\scripts\breach-killswitch.ps1 -DryRun    # print intended actions only
#
# Stop the moment a Gmail alert looks credible. False-positive cost: ~5 min.
# Missed-true-positive cost: severe.

[CmdletBinding()]
param(
  [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Push-Location $projectRoot
try {
  $ts = (Get-Date -AsUTC -Format 'yyyyMMddTHHmmssZ')
  $incidentDir = Join-Path $projectRoot "incident/$ts"

  Write-Host "==> BREACH KILLSWITCH" -ForegroundColor Red
  Write-Host "    UTC timestamp: $ts"
  Write-Host "    Incident dir : $incidentDir"
  if ($DryRun) { Write-Host "    [DRY RUN] no changes will be made" -ForegroundColor Yellow }
  Write-Host ""

  # --- Step 1: Email a final notice via the alerter while it's still up. ---
  Write-Host "==> Step 1: emit 'killswitch fired' alert" -ForegroundColor Cyan
  $alertBody = @{
    severity      = 'critical'
    event         = 'killswitch.fired'
    timestamp_utc = (Get-Date -AsUTC -Format 'yyyy-MM-ddTHH:mm:ssZ')
    log_line      = "breach-killswitch.ps1 invoked at $ts on $env:COMPUTERNAME by $env:USERNAME"
  } | ConvertTo-Json -Compress

  if ($DryRun) {
    Write-Host "    [DRY RUN] would POST: $alertBody"
  } else {
    try {
      docker exec portal-alerter wget -q -O- --post-data="$alertBody" `
        --header='Content-Type: application/json' `
        http://127.0.0.1:8080/alert 2>&1 | Out-Null
      Write-Host "    Final alert dispatched." -ForegroundColor Green
    } catch {
      Write-Warning "    Final alert failed: $_ -- continuing with shutdown."
    }
  }

  # --- Step 2: Stop ONLY the internet-exposed services. ---
  Write-Host ""
  Write-Host "==> Step 2: stop internet-exposed services" -ForegroundColor Cyan
  $services = @(
    'cloudflared',
    'caddy',
    'authelia',
    'authelia-watcher',
    'integrity-tripwire',
    'portal-alerter',
    'portal-cron',
    'caddy-backup',
    'authelia-backup'
  )
  if ($DryRun) {
    Write-Host "    [DRY RUN] would: docker compose -p portal --profile internet stop $($services -join ' ')"
  } else {
    docker compose -p portal -f (Join-Path $projectRoot 'portal\docker-compose.yml') --env-file (Join-Path $projectRoot '.env') --profile internet stop @services
    if ($LASTEXITCODE -ne 0) { Write-Warning "compose stop returned $LASTEXITCODE -- review state" }
  }

  # Sanity check: NEVER touch these
  Write-Host "    [guard] NOT touching: tailscale, openwebui, open_notebook, llama-cpp*, mnemory*, surrealdb, search-*, little-coder*, smolcrawl-pipelines, OB1 stack" -ForegroundColor DarkGray

  # --- Step 3: Snapshot logs. ---
  Write-Host ""
  Write-Host "==> Step 3: snapshot logs to $incidentDir" -ForegroundColor Cyan
  if ($DryRun) {
    Write-Host "    [DRY RUN] would create $incidentDir and docker cp caddy:/data and authelia:/data into it"
  } else {
    New-Item -ItemType Directory -Path $incidentDir -Force | Out-Null
    # The containers are stopped but their filesystems are still attached;
    # `docker cp` from a stopped container is valid.
    docker cp caddy:/data/caddy-access.log    "$incidentDir/caddy-access.log"  2>&1 | Out-Null
    docker cp authelia:/data/authelia.log     "$incidentDir/authelia.log"      2>&1 | Out-Null
    docker cp authelia:/data/db.sqlite3       "$incidentDir/authelia-db.sqlite3" 2>&1 | Out-Null
    Write-Host "    Snapshot complete:" -ForegroundColor Green
    Get-ChildItem $incidentDir | Select-Object Name, Length | Format-Table | Out-String | Write-Host
  }

  # --- Step 4: Rotate Authelia secrets in .env. ---
  Write-Host "==> Step 4: rotate Authelia secrets in .env" -ForegroundColor Cyan
  $envPath = Join-Path $projectRoot '.env'
  if (-not (Test-Path $envPath)) {
    Write-Warning "    .env not found at $envPath -- skipping rotation. Rotate manually."
  } else {
    if ($DryRun) {
      Write-Host "    [DRY RUN] would generate new JWT + SESSION secrets via 'docker run --rm authelia/authelia:4.39 authelia crypto rand --length 64' and patch .env (commenting old values for IR record)"
    } else {
      $backupEnv = "$envPath.killswitch-$ts.bak"
      Copy-Item $envPath $backupEnv
      Write-Host "    Backed up .env to $backupEnv" -ForegroundColor DarkGray

      $newJwt     = (docker run --rm authelia/authelia:4.39 authelia crypto rand --length 64 2>$null).Trim()
      $newSession = (docker run --rm authelia/authelia:4.39 authelia crypto rand --length 64 2>$null).Trim()

      $envText = Get-Content $envPath -Raw
      # Comment old values and append new ones; do not delete in case manual
      # review is needed.
      $envText = $envText -replace '(?m)^(AUTHELIA_JWT_SECRET=.*)$', "# rotated-${ts}: `$1"
      $envText = $envText -replace '(?m)^(AUTHELIA_SESSION_SECRET=.*)$', "# rotated-${ts}: `$1"
      $envText += "`n# killswitch rotation $ts`n"
      $envText += "AUTHELIA_JWT_SECRET=$newJwt`n"
      $envText += "AUTHELIA_SESSION_SECRET=$newSession`n"
      Set-Content -Path $envPath -Value $envText -Encoding utf8
      Write-Host "    JWT + SESSION secrets rotated." -ForegroundColor Green
    }
  }

  # --- Step 5: Recovery instructions. ---
  Write-Host ""
  Write-Host "==> NEXT STEPS -- read documentation/runbooks/incident-response.md before restarting" -ForegroundColor Red
  Write-Host "    - Tailnet path is still up. Reach OpenWebUI via https://<tailnet-host>.ts.net/" -ForegroundColor Yellow
  Write-Host "    - Snapshot $incidentDir to an off-host destination NOW (before any restart)." -ForegroundColor Yellow
  Write-Host "    - Restore authelia-data from YESTERDAY'S backup before restarting (today's may be poisoned)." -ForegroundColor Yellow
  Write-Host "    - Re-enroll WebAuthn keys via https://auth.<your-domain>/settings/two-factor after recovery." -ForegroundColor Yellow
  Write-Host "    - If the tunnel container appeared compromised, rotate CLOUDFLARE_TUNNEL_TOKEN." -ForegroundColor Yellow
  Write-Host "    - If the open-brain-email OAuth client is suspected compromised, revoke at" -ForegroundColor Yellow
  Write-Host "      https://myaccount.google.com/permissions -- but doing so ALSO breaks OB1's daily digest" -ForegroundColor Yellow
  Write-Host "      until a new client is provisioned. See plan sec.6.9 coupling caveat." -ForegroundColor Yellow
  Write-Host ""
  Write-Host "==> This script does NOT auto-restart. Bringing the portal back online is a human decision." -ForegroundColor Red
} finally {
  Pop-Location
}
