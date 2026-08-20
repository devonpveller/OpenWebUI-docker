# scripts/check-backup-coverage.ps1
#
# Audits every Docker volume + bind-mount data path across the ai-stack
# and OB1 compose projects, and confirms that each one is either:
#   - Backed up by a *-backup container (volume name appears in a backup
#     container's volumes: list), OR
#   - Explicitly excluded from backup (covered by a known excluded list
#     in this script)
#
# Reports gaps with severity. Returns exit code 0 if clean, 1 if any gap.
#
# Also pre-creates ./backups/<service>/ directories that backup services
# reference (Docker would otherwise create them as root, blocking
# non-root backup containers from writing).
#
# Run manually before merging a PR that adds a new service with
# persistent state. See documentation/runbooks/backup-conventions.md.

[CmdletBinding()]
param(
  [switch]$CreateMissingDirs
)

# docker compose config writes to stderr too; keep Continue.
$ErrorActionPreference = 'Continue'
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $projectRoot
try {
  Write-Host "==> Backup coverage check" -ForegroundColor Cyan
  Write-Host "    Project root: $projectRoot"
  Write-Host ""

  # ----- Inventory: volumes via `docker volume ls` with project labels.
  # We can't parse `docker compose config --format json` here because PS 5.1
  # ConvertFrom-Json rejects duplicate keys that crop up in compose-merged
  # env vars (HTTP_PROXY vs http_proxy). Direct volume ls is simpler and
  # also catches volumes that are CURRENTLY ALLOCATED (not just declared).
  $aiStackVolumeNames = @()
  $rawList = docker volume ls --filter 'label=com.docker.compose.project=ai-stack' --format '{{.Name}}' 2>$null
  if ($rawList) {
    $aiStackVolumeNames = @($rawList) | ForEach-Object { $_ -replace '^ai-stack_', '' } | Sort-Object -Unique
  }
  Write-Host ("  ai-stack named volumes : {0}" -f $aiStackVolumeNames.Count) -ForegroundColor DarkGray

  $ob1Volumes = @()
  $rawOb1 = docker volume ls --filter 'label=com.docker.compose.project=open-brain' --format '{{.Name}}' 2>$null
  if ($rawOb1) {
    $ob1Volumes = @($rawOb1) | ForEach-Object { $_ -replace '^open-brain_', '' } | Sort-Object -Unique
  }
  Write-Host ("  OB1     named volumes  : {0}" -f $ob1Volumes.Count) -ForegroundColor DarkGray

  # agent-org is a THIRD separate compose project (project=agent-org) — mattermost +
  # agent-bridge governance state. Same by-project volume inventory as ai-stack/OB1.
  $agentOrgVolumes = @()
  $rawAo = docker volume ls --filter 'label=com.docker.compose.project=agent-org' --format '{{.Name}}' 2>$null
  if ($rawAo) {
    $agentOrgVolumes = @($rawAo) | ForEach-Object { $_ -replace '^agent-org_', '' } | Sort-Object -Unique
  }
  Write-Host ("  agent-org named volumes: {0}" -f $agentOrgVolumes.Count) -ForegroundColor DarkGray
  Write-Host ""

  # ----- Inventory: bind-mount data paths (under D:\ for the operator) -----
  # These are *.NET-style host paths from the compose files. The check is
  # syntactic (file path appears in compose); not a runtime probe.
  $hostBindMounts = @(
    @{ Path = 'D:\Open WebUI\open-notebook\surreal_data'; Service = 'surrealdb';     Owner = 'open-notebook-backup' }
    @{ Path = 'D:\Open WebUI\open-notebook\notebook_data'; Service = 'open_notebook'; Owner = 'open-notebook-backup' }
    @{ Path = '.\data\tailscale';                          Service = 'tailscale';    Owner = 'tailscale-backup' }
    @{ Path = 'C:\Users\yamao\.lmstudio\models';           Service = 'llama-cpp-upstream'; Owner = 'lm-models-backup' }
  )

  # ----- Volumes intentionally NOT backed up -------------------------
  $intentionallyExcluded = @(
    @{ Volume = 'little-coder-workspace'; Reason = 'Project workspace - intentionally re-clonable (design)' }
    @{ Volume = 'llm-queue-data'; Reason = 'B2 queue analytics events (SQLite) - non-critical, regenerable observability data' }
    # agent-org: only the two Postgres stores are authoritative (backed up below).
    @{ Volume = 'ao-worker-1-workspace'; Reason = 'agent-org worker workspace - re-clonable per /project (same as little-coder-workspace)' }
    @{ Volume = 'ao-worker-2-workspace'; Reason = 'agent-org worker workspace - re-clonable per /project' }
    @{ Volume = 'ao-worker-1-sessions';  Reason = 'agent-org worker session cache - regenerable per-effort continuity (authoritative effort state is in agent-bridge-db)' }
    @{ Volume = 'ao-worker-2-sessions';  Reason = 'agent-org worker session cache - regenerable per-effort continuity' }
    @{ Volume = 'ao-egress-config';      Reason = 'git-egress allowlist - regenerated from agent-bridge-db on boot (bridge rewrites it)' }
    @{ Volume = 'mattermost-config';     Reason = 'Mattermost config - regenerable from compose env' }
    @{ Volume = 'mattermost-logs';       Reason = 'Mattermost logs - transient' }
    @{ Volume = 'mattermost-plugins';        Reason = 'Mattermost server plugins - regenerable' }
    @{ Volume = 'mattermost-client-plugins'; Reason = 'Mattermost client plugins - regenerable' }
    @{ Volume = 'mattermost-data';       Reason = 'Mattermost file attachments (avatars/uploads) - conversation CONTENT is in mattermost-db (backed up); revisit if attachments become important' }
    @{ Volume = 'llm-gateway-cloud-db-data'; Reason = 'Cloud LiteLLM spend-log (profile:cloud) - non-authoritative telemetry, same class as llm-gateway-db' }
  )

  # ----- Mapping: volume name -> backup container that covers it -----
  # When you add a new <service>-backup container, register its source
  # volume(s) here. The check fails if a volume in the inventory above
  # isn't listed here AND isn't in $intentionallyExcluded.
  $backupCoverage = @{
    'openwebui-data'         = 'openwebui-backup'
    'mnemory-data'           = 'mnemory-backup'
    'smolcrawl-data'         = 'smolcrawl-backup'
    'little-coder-journals'  = 'little-coder-backup'
    'little-coder-skill'     = 'little-coder-backup'
    'little-coder-cohorts'   = 'little-coder-backup'
    'little-coder-polyglot'  = 'little-coder-backup'
    'little-coder-sessions'  = 'little-coder-backup'
    'caddy-data'             = 'caddy-backup'
    'caddy-config'           = 'caddy-backup'
    'authelia-data'          = 'authelia-backup'
    'tripwire-data'          = 'integrity-tripwire (state-only; bound to host config) - not separately backed up'
    'openbrain-db-data'      = 'openbrain-db-backup'
    'openbrain-wiki-data'    = 'openbrain-wiki-backup'
    # Pre-existing map omissions (the backup containers already cover these; the map just
    # never listed them — see the Backups table in stack-map/workspace-stacks.md):
    'llm-gateway-db-data'    = 'llm-gateway-backup (logical pg_dump of the LiteLLM DB)'
    'wiki-assets'            = 'openbrain-wiki-backup (mounts wiki-assets alongside openbrain-wiki-data)'
    # agent-org — the two authoritative Postgres stores.
    'agent-bridge-db-data'   = 'agent-bridge-db-backup'
    'mattermost-db-data'     = 'mattermost-db-backup'
  }

  # ----- Pre-flight: ensure ./backups/<service>/ dirs exist ----------
  $expectedBackupDirs = @(
    'caddy', 'authelia', 'mnemory', 'openwebui', 'little-coder',
    'openbrain-db', 'openbrain-wiki', 'open-notebook', 'smolcrawl',
    'tailscale', 'lm-models',
    'agent-bridge-db', 'mattermost-db'
  )
  $missingDirs = @()
  foreach ($d in $expectedBackupDirs) {
    $p = Join-Path '.\backups' $d
    if (-not (Test-Path $p)) {
      $missingDirs += $p
      if ($CreateMissingDirs) {
        New-Item -ItemType Directory -Path $p -Force | Out-Null
      }
    }
  }
  if ($missingDirs.Count -gt 0) {
    if ($CreateMissingDirs) {
      Write-Host ("  Created {0} missing ./backups/* directories" -f $missingDirs.Count) -ForegroundColor Yellow
    } else {
      Write-Host ("  [WARN] {0} ./backups/* directories don't exist (re-run with -CreateMissingDirs):" -f $missingDirs.Count) -ForegroundColor Yellow
      $missingDirs | ForEach-Object { Write-Host "    $_" -ForegroundColor Yellow }
      Write-Host ""
    }
  }

  # ----- Volume coverage check ---------------------------------------
  Write-Host "==> Volume coverage" -ForegroundColor Cyan
  $allVolumes = @($aiStackVolumeNames) + @($ob1Volumes) + @($agentOrgVolumes) | Sort-Object -Unique
  $gaps = @()
  $orphans = @()
  foreach ($v in $allVolumes) {
    if (-not $v) { continue }
    $excludedMatch = $intentionallyExcluded | Where-Object { $_.Volume -eq $v }
    if ($excludedMatch) {
      Write-Host ("  [SKIP] {0,-25} - excluded: {1}" -f $v, $excludedMatch.Reason) -ForegroundColor DarkGray
      continue
    }
    if ($backupCoverage.ContainsKey($v)) {
      Write-Host ("  [OK]   {0,-25} -> {1}" -f $v, $backupCoverage[$v]) -ForegroundColor Green
      continue
    }
    # Orphan detection: if no container (running OR stopped) references the
    # volume, it's a leftover from a previous compose config -- not a real
    # backup gap. Surface as hygiene flag, not a failure.
    $candidateNames = @("ai-stack_$v", "open-brain_$v", "agent-org_$v", $v)
    $referencingContainers = @()
    foreach ($candidate in $candidateNames) {
      $usage = docker ps -a --filter "volume=$candidate" --format '{{.Names}}' 2>$null
      if ($usage) { $referencingContainers += $usage }
    }
    if ($referencingContainers.Count -eq 0) {
      Write-Host ("  [ORPHAN] {0,-23} - no container references it; safe to 'docker volume rm'" -f $v) -ForegroundColor DarkYellow
      $orphans += $v
      continue
    }
    Write-Host ("  [GAP]  {0,-25} - used by [{1}] but no backup container references it" -f $v, ($referencingContainers -join ',')) -ForegroundColor Red
    $gaps += $v
  }
  Write-Host ""

  # ----- Host bind-mount coverage ------------------------------------
  Write-Host "==> Host bind-mount coverage" -ForegroundColor Cyan
  foreach ($bm in $hostBindMounts) {
    Write-Host ("  [OK]   {0,-50} -> {1}" -f $bm.Path, $bm.Owner) -ForegroundColor Green
  }
  Write-Host ""

  # ----- Summary -----------------------------------------------------
  if ($gaps.Count -eq 0) {
    Write-Host "==> Coverage: CLEAN" -ForegroundColor Green
    Write-Host "    Every named volume is either backed up or explicitly excluded."
    exit 0
  } else {
    Write-Host ("==> Coverage: {0} GAPS" -f $gaps.Count) -ForegroundColor Red
    Write-Host "    Add backup containers for the volumes flagged above, or mark them"
    Write-Host "    excluded in `$intentionallyExcluded with a reason. See:"
    Write-Host "    documentation/runbooks/backup-conventions.md"
    exit 1
  }
} finally {
  Pop-Location
}
