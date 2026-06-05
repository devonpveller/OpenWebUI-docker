# scripts/check-openbrain-health.ps1
#
# Health probe for the **Open Brain** compose project (project = "open-brain"),
# which a plain `docker compose ...` from the ai-stack project dir CANNOT see.
# This is the single, canonical Open Brain probe — called by both:
#   - the autonomous monitor  (check-tailscale-health.ps1, with -Repair)
#   - the user-engaged BAT     (quick-fixes.bat :status_check / :openbrain_check)
#
# It addresses a class of failure that simple liveness checks MISS: a container
# that is "Up" (green) yet functionally dead. The signature case (2026-06-05):
# openbrain-db restarted, openbrain-mcp kept a long-lived DB connection that died,
# and every MCP tool call (Open WebUI tools + Claude connector) returned
# "Broken pipe (os error 32)" -> mcpo surfaced HTTP 500 -- while `docker ps` showed
# openbrain-mcp healthy. See memory: openbrain-mcp-stale-db-connection.
#
# Probes (by container NAME, project-agnostic — never `docker compose`):
#   - openbrain-db          running               (the dependency)
#   - openbrain-mcp         running + STALE-POOL guard (db started after mcp -> restart)
#   - openbrain-mcpo[-ext]  running               (the Open WebUI tool bridge)
#   - openbrain-gateway     http://127.0.0.1:8061/health == "ok"   (functional, no secret)
#   - openbrain-rest        http://127.0.0.1:3001/   (PostgREST proxy reachable)
#   - openbrain-postgrest / -wiki / -wiki-viewer / -entity-worker  running
#
# Usage:
#   .\scripts\check-openbrain-health.ps1            # detect + report, exit 0/1
#   .\scripts\check-openbrain-health.ps1 -Repair    # also auto-restart broken pieces
#   .\scripts\check-openbrain-health.ps1 -Quiet     # only WARN/ERROR lines (for daemon)
#
# Exit code: 0 = all healthy (after any repairs), 1 = at least one unresolved fault.

[CmdletBinding()]
param(
  [switch]$Repair,
  [switch]$Quiet,
  # When set, non-suppressed status lines are ALSO appended (timestamped) to this
  # file. The autonomous monitor passes its own logs\tailscale-health.log so the
  # per-container detail survives — Write-Host output is not capturable via 2>&1.
  [string]$LogPath
)

$ErrorActionPreference = 'Continue'
$script:Faults = 0

function Write-Ob {
  param([string]$Name, [string]$State, [string]$Detail = '')
  # State: ok | down | warn | fix
  $colors  = @{ ok = 'Green'; down = 'Red'; warn = 'Yellow'; fix = 'Cyan' }
  $symbols = @{ ok = '[OK]';  down = '[DOWN]'; warn = '[WARN]'; fix = '[FIX]' }
  if ($Quiet -and $State -eq 'ok') { return }
  $color = $colors[$State]; if (-not $color) { $color = 'Gray' }
  $sym   = $symbols[$State]; if (-not $sym) { $sym = '[..]' }
  Write-Host ("  {0,-7} {1,-26} {2}" -f $sym, $Name, $Detail) -ForegroundColor $color
  if ($LogPath) {
    $level = switch ($State) { 'down' { 'ERROR' } 'warn' { 'WARN' } 'fix' { 'WARN' } default { 'INFO' } }
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    try { "$ts [$level] OpenBrain $($symbols[$State]) $Name $Detail" | Out-File -FilePath $LogPath -Append -Encoding UTF8 } catch { }
  }
}

function Get-CState {
  param([string]$Name)
  $s = docker inspect --format '{{.State.Status}}' $Name 2>$null
  if ($LASTEXITCODE -ne 0) { return 'absent' }
  return $s
}

# Raw ISO-8601 UTC start timestamp. UTC ISO strings are lexicographically
# ordered, so plain string comparison is a safe "started after" test (avoids
# PS 5.1 choking on docker's 9-digit nanosecond fraction).
function Get-CStartedAt {
  param([string]$Name)
  $t = docker inspect --format '{{.State.StartedAt}}' $Name 2>$null
  if ($LASTEXITCODE -ne 0) { return $null }
  return $t
}

function Test-HttpOk {
  param([string]$Url, [int]$TimeoutSec = 5, [string]$MustContain = $null)
  try {
    $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec $TimeoutSec
    if ($r.StatusCode -ne 200) { return $false }
    if ($MustContain -and ($r.Content -notmatch [regex]::Escape($MustContain))) { return $false }
    return $true
  } catch {
    return $false
  }
}

# Confirm a container is running; optionally start/restart it under -Repair.
# Returns $true if running at the end.
function Confirm-ObContainer {
  param(
    [string]$Name,
    [switch]$Critical
  )
  $state = Get-CState $Name
  if ($state -eq 'running') { Write-Ob $Name ok 'running'; return $true }
  if ($state -eq 'absent')  { Write-Ob $Name down 'not present (container missing)'; $script:Faults++; return $false }

  # exited / created / restarting / paused
  if ($Repair) {
    Write-Ob $Name fix "state=$state -> docker start"
    docker start $Name 2>&1 | Out-Null
    Start-Sleep 3
    if ((Get-CState $Name) -eq 'running') { Write-Ob $Name ok 'started'; return $true }
    Write-Ob $Name down "could not start (check: docker logs $Name)"; $script:Faults++; return $false
  }

  Write-Ob $Name down "state=$state (run with -Repair to start)"; $script:Faults++; return $false
}

Write-Host "==> Open Brain stack health (project: open-brain)" -ForegroundColor Cyan

# ---- 1. Database (the dependency the stale-pool bug hinges on) --------------
$dbUp = Confirm-ObContainer 'openbrain-db' -Critical

# ---- 2. MCP server + the stale-pool guard (today's failure mode) -----------
$mcpUp = Confirm-ObContainer 'openbrain-mcp' -Critical
if ($dbUp -and $mcpUp) {
  $dbStarted  = Get-CStartedAt 'openbrain-db'
  $mcpStarted = Get-CStartedAt 'openbrain-mcp'
  if ($dbStarted -and $mcpStarted -and ($dbStarted -gt $mcpStarted)) {
    # DB came up AFTER mcp -> mcp's pooled connection is stale -> broken-pipe 500s.
    Write-Ob 'openbrain-mcp' warn "STALE DB POOL: db started $dbStarted > mcp $mcpStarted"
    if ($Repair) {
      Write-Ob 'openbrain-mcp' fix 'docker restart openbrain-mcp (re-open DB connection)'
      docker restart openbrain-mcp 2>&1 | Out-Null
      Start-Sleep 4
      $mcpStarted2 = Get-CStartedAt 'openbrain-mcp'
      if ((Get-CState 'openbrain-mcp') -eq 'running' -and $mcpStarted2 -gt $dbStarted) {
        Write-Ob 'openbrain-mcp' ok 'restarted; connection now fresher than db'
      } else {
        Write-Ob 'openbrain-mcp' down 'restart did not resolve stale pool'; $script:Faults++
      }
    } else {
      Write-Ob 'openbrain-mcp' warn 'run with -Repair to restart (fixes OWUI 500 / broken-pipe)'
      $script:Faults++
    }
  } else {
    Write-Ob 'openbrain-mcp' ok 'DB connection fresher than db restart (no stale pool)'
  }
}

# ---- 3. The Open WebUI tool bridge -----------------------------------------
Confirm-ObContainer 'openbrain-mcpo'     | Out-Null
Confirm-ObContainer 'openbrain-mcpo-ext' | Out-Null

# ---- 4. Functional probes on host-published endpoints (no secret needed) ----
# Gateway /health is the privacy proxy Claude/cloud clients reach at :8061.
if ((Get-CState 'openbrain-gateway') -eq 'running') {
  if (Test-HttpOk 'http://127.0.0.1:8061/health' 5 'ok') {
    Write-Ob 'openbrain-gateway' ok '/health == ok (:8061)'
  } else {
    Write-Ob 'openbrain-gateway' warn '/health not OK on :8061'
    if ($Repair) {
      Write-Ob 'openbrain-gateway' fix 'docker restart openbrain-gateway'
      docker restart openbrain-gateway 2>&1 | Out-Null
      Start-Sleep 3
      if (Test-HttpOk 'http://127.0.0.1:8061/health' 5 'ok') { Write-Ob 'openbrain-gateway' ok '/health recovered' }
      else { Write-Ob 'openbrain-gateway' down '/health still failing'; $script:Faults++ }
    } else { $script:Faults++ }
  }
} else {
  Confirm-ObContainer 'openbrain-gateway' | Out-Null
}

# PostgREST proxy reachable (exercises the DB read path independently of mcp).
if ((Get-CState 'openbrain-rest') -eq 'running') {
  if (Test-HttpOk 'http://127.0.0.1:3001/' 5) { Write-Ob 'openbrain-rest' ok 'PostgREST proxy reachable (:3001)' }
  else { Write-Ob 'openbrain-rest' warn 'PostgREST proxy not answering on :3001'; $script:Faults++ }
} else {
  Confirm-ObContainer 'openbrain-rest' | Out-Null
}

# ---- 5. Remaining OB containers — liveness only ----------------------------
foreach ($svc in @('openbrain-postgrest','openbrain-wiki','openbrain-wiki-viewer','openbrain-entity-worker')) {
  Confirm-ObContainer $svc | Out-Null
}

Write-Host ""
if ($script:Faults -eq 0) {
  Write-Host "==> Open Brain: all checks passed" -ForegroundColor Green
  exit 0
} else {
  $hint = if ($Repair) { 'some faults unresolved' } else { 're-run with -Repair to auto-fix' }
  Write-Host ("==> Open Brain: {0} fault(s) -- {1}" -f $script:Faults, $hint) -ForegroundColor Yellow
  exit 1
}
