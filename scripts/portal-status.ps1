# scripts/portal-status.ps1
#
# Read-only liveness check for the internet-exposed portal (plan §12.9).
# One line per check, color-coded. Does not modify anything.
#
# Usage:
#   .\scripts\portal-status.ps1

$ErrorActionPreference = 'Continue'

function Write-Check {
  param(
    [string]$Name,
    [string]$State,   # 'ok' | 'down' | 'warn'
    [string]$Detail = ''
  )
  $colors = @{ ok = 'Green'; down = 'Red'; warn = 'Yellow' }
  $symbols = @{ ok = '[OK]'; down = '[DOWN]'; warn = '[WARN]' }
  $color = $colors[$State]
  $sym = $symbols[$State]
  Write-Host ("  {0,-6} {1,-26} {2}" -f $sym, $Name, $Detail) -ForegroundColor $color
}

function Container-State {
  param([string]$Name)
  $state = docker inspect --format '{{.State.Status}}' $Name 2>$null
  if ($LASTEXITCODE -ne 0) { return 'absent' }
  return $state
}

function Container-Health {
  param([string]$Name)
  $h = docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' $Name 2>$null
  if ($LASTEXITCODE -ne 0) { return 'absent' }
  return $h
}

Write-Host "==> Portal status" -ForegroundColor Cyan

$expected = @(
  'portal-alerter',
  'authelia',
  'caddy',
  'authelia-watcher',
  'integrity-tripwire',
  'portal-cron',
  'cloudflared',
  'caddy-backup',
  'authelia-backup'
)

foreach ($name in $expected) {
  $state = Container-State $name
  $health = Container-Health $name
  switch ($state) {
    'absent' { Write-Check $name down 'not present' }
    'running' {
      switch ($health) {
        'healthy' { Write-Check $name ok 'running, healthy' }
        'starting' { Write-Check $name warn 'running, starting' }
        'unhealthy' { Write-Check $name down 'running, unhealthy' }
        'none' { Write-Check $name ok 'running (no healthcheck)' }
        default { Write-Check $name warn "running, health=$health" }
      }
    }
    'exited' { Write-Check $name down 'exited' }
    default { Write-Check $name warn "state=$state" }
  }
}

Write-Host ""
Write-Host "==> Portal-alerter health endpoint" -ForegroundColor Cyan
$alerterRunning = (Container-State 'portal-alerter') -eq 'running'
if ($alerterRunning) {
  $healthJson = docker exec portal-alerter wget -qO- http://127.0.0.1:8080/health 2>$null
  if ($LASTEXITCODE -eq 0 -and $healthJson) {
    try {
      $h = $healthJson | ConvertFrom-Json
      Write-Check '/health' ok "ready=$($h.ready) last_alert=$($h.last_alert_at) last_digest=$($h.last_digest_at)"
      if ($h.last_error) {
        Write-Check 'last_error' warn $h.last_error
      }
      if ($h.coalesce_queue_depth -gt 0) {
        Write-Check 'coalesce_queue' warn "$($h.coalesce_queue_depth) pending"
      }
    } catch {
      Write-Check '/health' warn "unparseable JSON: $healthJson"
    }
  } else {
    Write-Check '/health' down 'not responding'
  }
} else {
  Write-Check '/health' down 'alerter not running'
}

Write-Host ""
Write-Host "==> Tailnet path (canary)" -ForegroundColor Cyan
# This check is intentionally vague — the user knows their tailnet hostname;
# we just confirm the local openwebui container is up so the tailnet path
# would succeed.
$owui = Container-State 'openwebui'
if ($owui -eq 'running') {
  Write-Check 'openwebui' ok 'running — tailnet path still serves'
} else {
  Write-Check 'openwebui' down "state=$owui (tailnet path affected)"
}

Write-Host ""
Write-Host "==> Most recent digest" -ForegroundColor Cyan
# digest-latest.md lives on the alerter's /reports volume.
if ($alerterRunning) {
  $latest = docker exec portal-alerter sh -c "ls -l /reports/digest-latest.md 2>/dev/null || echo missing"
  if ($latest -match 'missing') {
    Write-Check 'digest-latest.md' warn 'no scheduled digest yet'
  } else {
    Write-Check 'digest-latest.md' ok $latest
  }
} else {
  Write-Check 'digest-latest.md' warn 'alerter not running — cannot inspect'
}

Write-Host ""
Write-Host "==> Hint: reach the Cloudflare tunnel dashboard for live edge metrics." -ForegroundColor DarkGray
Write-Host "    https://one.dash.cloudflare.com/  (Zero Trust → Tunnels → ai-stack)" -ForegroundColor DarkGray
