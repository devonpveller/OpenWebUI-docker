# scripts/check-agent-org-health.ps1
#
# Health probe for the **agent-org** compose project (project = "agent-org") — the
# governed multi-agent orchestration stack (Mattermost + agent-bridge + worker pool
# + git-egress). A plain `docker compose ...` from the ai-stack project dir CANNOT
# see it (separate project), so this is a by-NAME probe — mirror of
# check-openbrain-health.ps1. Called by both:
#   - the autonomous monitor  (check-tailscale-health.ps1, with -Repair)
#   - the operator, directly   (.\scripts\check-agent-org-health.ps1 [-Repair])
#
# Beyond liveness it guards two "green but functionally dead" classes seen in this
# stack:
#   - agent-bridge STALE DB POOL: if agent-bridge-db restarts AFTER agent-bridge,
#     the bridge's asyncpg pool holds dead connections (governance/effort reads
#     500 until recycled) — same class as openbrain-mcp. Restart re-opens it.
#   - ao-git-egress STALE MOUNT: a container recreated before the /egress shared
#     volume + reload script were wired runs WITHOUT them, so the bridge's
#     operator-managed allowlist writes silently no-op (Permission denied) and the
#     worker egress scope is frozen. Detected via missing /egress mount; repaired
#     by recreating the container from its current compose definition.
#
# Usage:
#   .\scripts\check-agent-org-health.ps1            # detect + report, exit 0/1
#   .\scripts\check-agent-org-health.ps1 -Repair    # also auto-restart/recreate broken pieces
#   .\scripts\check-agent-org-health.ps1 -Quiet     # only WARN/ERROR lines (for the daemon)
#
# Exit code: 0 = all healthy (after any repairs), 1 = at least one unresolved fault.

[CmdletBinding()]
param(
  [switch]$Repair,
  [switch]$Quiet,
  [string]$LogPath
)

$ErrorActionPreference = 'Continue'
$script:Faults = 0

$SCRIPT_DIR  = Split-Path -Parent $PSCommandPath
$PROJECT_DIR = Split-Path -Parent $SCRIPT_DIR
$AO_COMPOSE  = Join-Path $PROJECT_DIR 'agent-org\docker\docker-compose.yml'

function Write-Ao {
  param([string]$Name, [string]$State, [string]$Detail = '')
  $colors  = @{ ok = 'Green'; down = 'Red'; warn = 'Yellow'; fix = 'Cyan' }
  $symbols = @{ ok = '[OK]';  down = '[DOWN]'; warn = '[WARN]'; fix = '[FIX]' }
  if ($Quiet -and $State -eq 'ok') { return }
  $color = $colors[$State]; if (-not $color) { $color = 'Gray' }
  $sym   = $symbols[$State]; if (-not $sym) { $sym = '[..]' }
  Write-Host ("  {0,-7} {1,-26} {2}" -f $sym, $Name, $Detail) -ForegroundColor $color
  if ($LogPath) {
    $level = switch ($State) { 'down' { 'ERROR' } 'warn' { 'WARN' } 'fix' { 'WARN' } default { 'INFO' } }
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    try { "$ts [$level] AgentOrg $($symbols[$State]) $Name $Detail" | Out-File -FilePath $LogPath -Append -Encoding UTF8 } catch { }
  }
}

function Get-CState {
  param([string]$Name)
  $s = docker inspect --format '{{.State.Status}}' $Name 2>$null
  if ($LASTEXITCODE -ne 0) { return 'absent' }
  return $s
}

function Get-CStartedAt {
  param([string]$Name)
  $t = docker inspect --format '{{.State.StartedAt}}' $Name 2>$null
  if ($LASTEXITCODE -ne 0) { return $null }
  return $t
}

# Confirm a container is running; optionally `docker start` it under -Repair.
function Confirm-AoContainer {
  param([string]$Name)
  $state = Get-CState $Name
  if ($state -eq 'running') { Write-Ao $Name ok 'running'; return $true }
  if ($state -eq 'absent')  { Write-Ao $Name down 'not present (container missing)'; $script:Faults++; return $false }
  if ($Repair) {
    Write-Ao $Name fix "state=$state -> docker start"
    docker start $Name 2>&1 | Out-Null
    Start-Sleep 3
    if ((Get-CState $Name) -eq 'running') { Write-Ao $Name ok 'started'; return $true }
    Write-Ao $Name down "could not start (check: docker logs $Name)"; $script:Faults++; return $false
  }
  Write-Ao $Name down "state=$state (run with -Repair to start)"; $script:Faults++; return $false
}

Write-Host "==> agent-org stack health (project: agent-org)" -ForegroundColor Cyan

# ---- 1. Databases (the dependencies) ---------------------------------------
$bridgeDbUp = Confirm-AoContainer 'agent-bridge-db'
Confirm-AoContainer 'mattermost-db' | Out-Null

# ---- 2. agent-bridge + STALE-DB-POOL guard (green-but-dead class) -----------
$bridgeUp = Confirm-AoContainer 'agent-bridge'
if ($bridgeUp -and $bridgeDbUp) {
  $dbStarted     = Get-CStartedAt 'agent-bridge-db'
  $bridgeStarted = Get-CStartedAt 'agent-bridge'
  if ($dbStarted -and $bridgeStarted -and ($dbStarted -gt $bridgeStarted)) {
    Write-Ao 'agent-bridge' warn "STALE DB POOL: db started $dbStarted > bridge $bridgeStarted"
    if ($Repair) {
      Write-Ao 'agent-bridge' fix 'docker restart agent-bridge (re-open DB pool)'
      docker restart agent-bridge 2>&1 | Out-Null
      Start-Sleep 5
      $bridgeStarted2 = Get-CStartedAt 'agent-bridge'
      if ((Get-CState 'agent-bridge') -eq 'running' -and $bridgeStarted2 -gt $dbStarted) {
        Write-Ao 'agent-bridge' ok 'restarted; pool now fresher than db'
      } else {
        Write-Ao 'agent-bridge' down 'restart did not resolve stale pool'; $script:Faults++
      }
    } else {
      Write-Ao 'agent-bridge' warn 'run with -Repair to restart (fixes governance/effort read 500s)'
      $script:Faults++
    }
  }
  # Functional liveness: /health is a static ok, so this catches a dead process
  # (not a stale pool — the StartedAt guard above covers that). python is always
  # present in the bridge image; no curl dependency.
  docker exec agent-bridge python -c "import urllib.request,sys; urllib.request.urlopen('http://localhost:8000/health',timeout=5); sys.exit(0)" 2>$null | Out-Null
  if ($LASTEXITCODE -eq 0) { Write-Ao 'agent-bridge' ok '/health responding (:8000)' }
  else { Write-Ao 'agent-bridge' warn '/health not responding on :8000 (process wedged?)'; $script:Faults++ }
}

# ---- 3. Mattermost (the chat surface) --------------------------------------
Confirm-AoContainer 'mattermost' | Out-Null

# ---- 4. ao-git-egress + STALE-MOUNT guard (operator-managed allowlist) ------
# A stale ao-git-egress (recreated before the /egress volume + reload script were
# wired) runs with NO mounts, so the bridge's /project + /egress allowlist writes
# hit Permission denied and never take effect. Detect via the missing /egress
# destination; repair by recreating from the current compose definition.
if (Confirm-AoContainer 'ao-git-egress') {
  $mounts = docker inspect ao-git-egress --format '{{range .Mounts}}{{.Destination}};{{end}}' 2>$null
  if ($mounts -notmatch '/egress') {
    Write-Ao 'ao-git-egress' warn 'STALE MOUNT: /egress volume not mounted — bridge allowlist writes are inert'
    if ($Repair -and (Test-Path $AO_COMPOSE)) {
      Write-Ao 'ao-git-egress' fix 'docker compose up -d --force-recreate ao-git-egress (re-attach /egress)'
      docker compose -f $AO_COMPOSE up -d --force-recreate ao-git-egress 2>&1 | Out-Null
      Start-Sleep 4
      $mounts2 = docker inspect ao-git-egress --format '{{range .Mounts}}{{.Destination}};{{end}}' 2>$null
      if ($mounts2 -match '/egress') { Write-Ao 'ao-git-egress' ok 'recreated; /egress mounted' }
      else { Write-Ao 'ao-git-egress' down 'recreate did not attach /egress'; $script:Faults++ }
    } else {
      Write-Ao 'ao-git-egress' warn 'run with -Repair to recreate (re-attaches the shared allowlist)'
      $script:Faults++
    }
  } else {
    # Mounted correctly — confirm the allowlist file exists (the reload script seeds it).
    docker exec ao-git-egress sh -c 'test -s /egress/egress-allowlist.txt' 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { Write-Ao 'ao-git-egress' ok '/egress allowlist present' }
    else { Write-Ao 'ao-git-egress' warn '/egress mounted but allowlist file missing (bridge will rewrite on next /egress change)' }
  }
}

# ---- 5. Worker pool (control + execution planes) ---------------------------
Confirm-AoContainer 'ao-worker-1' | Out-Null
Confirm-AoContainer 'ao-worker-2' | Out-Null
Confirm-AoContainer 'ao-ot-1'     | Out-Null
Confirm-AoContainer 'ao-ot-2'     | Out-Null

# ---- 6. Backup sidecars (nightly pg_dump) ----------------------------------
Confirm-AoContainer 'agent-bridge-db-backup' | Out-Null
Confirm-AoContainer 'mattermost-db-backup'   | Out-Null

# ---- Summary ---------------------------------------------------------------
if ($script:Faults -eq 0) {
  Write-Ao 'agent-org' ok 'all checks passed'
  exit 0
} else {
  $hint = if ($Repair) { 'some faults unresolved' } else { 're-run with -Repair to auto-fix' }
  Write-Host ("==> agent-org: {0} fault(s) - {1}" -f $script:Faults, $hint) -ForegroundColor Red
  exit 1
}
