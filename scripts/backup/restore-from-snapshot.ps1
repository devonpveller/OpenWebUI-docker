# scripts/restore-from-snapshot.ps1
#
# Disaster-recovery driver: restore one or more services from a snapshot
# directory. The snapshot directory layout is the same as `./backups/` —
# either pointed at local backups, or at a NAS slot
# `\\<nas>\<share>\ai-stack\portal\slot-<A|B>\`.
#
# For partial restores or to understand what each step does, see
# documentation/runbooks/restore-from-snapshot.md.
#
# USAGE
#   # Plan only (no changes; default mode):
#   .\scripts\restore-from-snapshot.ps1 -SnapshotRoot .\backups -Date 2026-05-30
#
#   # Restore one service:
#   .\scripts\restore-from-snapshot.ps1 -SnapshotRoot .\backups -Date 2026-05-30 `
#     -Services tailscale -Apply
#
#   # Restore everything (BIG hammer; use after a host wipe):
#   .\scripts\restore-from-snapshot.ps1 -SnapshotRoot \\nas\share\ai-stack\portal\slot-A `
#     -Date 2026-05-30 -Services all -Apply
#
# SAFETY
#   - Without -Apply, the script runs in plan-only mode (no changes).
#   - Verifies every sha256 sentinel BEFORE touching anything.
#   - Stops the consumer containers before swapping data; restarts after.
#   - Logs every action to ./logs/restore-<UTC-ts>.log

[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)]
  [string]$SnapshotRoot,

  [Parameter(Mandatory = $true)]
  [string]$Date,                                # yyyy-MM-dd

  [string[]]$Services = @('all'),

  [switch]$Apply,

  [switch]$SkipSentinelCheck                    # Emergency override only
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Push-Location $projectRoot

# --- Logging setup -----------------------------------------------------
$logDir = Join-Path $projectRoot 'logs'
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
$utcStamp = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ')
$logFile = Join-Path $logDir "restore-$utcStamp.log"

function Write-Log {
  param([string]$Message, [string]$Level = 'INFO')
  $line = "[{0}] [{1}] {2}" -f [DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ'), $Level, $Message
  $color = switch ($Level) {
    'ERROR' { 'Red' }
    'WARN'  { 'Yellow' }
    'OK'    { 'Green' }
    'PLAN'  { 'Cyan' }
    default { 'White' }
  }
  Write-Host $line -ForegroundColor $color
  Add-Content -Path $logFile -Value $line
}

# --- Service catalog ---------------------------------------------------
# Each entry tells the script: what archives to look for, what containers
# to stop/start, what the target volume or bind path is, and what restore
# primitive to use (tar vs pg_restore vs surreal import).
#
# To add a new service to the disaster-recovery flow, add an entry here.

$catalog = [ordered]@{
  'caddy' = @{
    Archives = @(@{ Pattern = "caddy-*.tar.gz";  Target = 'ai-stack_caddy-data';   Type = 'volume-tar' },
                 @{ Pattern = "caddy-*.tar.gz";  Target = 'ai-stack_caddy-config'; Type = 'volume-tar' })
    Stop    = @('caddy')
    Start   = @('caddy')
  }
  'authelia' = @{
    Archives = @(@{ Pattern = "authelia-*.tar.gz"; Target = 'ai-stack_authelia-data'; Type = 'volume-tar' })
    Stop    = @('authelia')
    Start   = @('authelia')
  }
  'openwebui' = @{
    Archives = @(@{ Pattern = "openwebui-*.tar.gz"; Target = 'ai-stack_openwebui-data'; Type = 'volume-tar' })
    Stop    = @('openwebui')
    Start   = @('openwebui')
  }
  'mnemory' = @{
    Archives = @(@{ Pattern = "mnemory-*.tar.gz"; Target = 'ai-stack_mnemory-data'; Type = 'volume-tar' })
    Stop    = @('mnemory','mnemory-cloud-gateway')
    Start   = @('mnemory','mnemory-cloud-gateway')
  }
  'little-coder' = @{
    Archives = @(
      @{ Pattern = "little-coder-journals-*.tar.gz"; Target = 'ai-stack_little-coder-journals'; Type = 'volume-tar' }
      @{ Pattern = "little-coder-skill-*.tar.gz";    Target = 'ai-stack_little-coder-skill';    Type = 'volume-tar' }
      @{ Pattern = "little-coder-cohorts-*.tar.gz";  Target = 'ai-stack_little-coder-cohorts';  Type = 'volume-tar' }
      @{ Pattern = "little-coder-polyglot-*.tar.gz"; Target = 'ai-stack_little-coder-polyglot'; Type = 'volume-tar' }
      @{ Pattern = "little-coder-sessions-*.tar.gz"; Target = 'ai-stack_little-coder-sessions'; Type = 'volume-tar' }
    )
    Stop    = @('little-coder','open-terminal','lc-mcpo','lc-egress')
    Start   = @('lc-egress','open-terminal','little-coder','lc-mcpo')
  }
  'smolcrawl' = @{
    Archives = @(@{ Pattern = "smolcrawl-*.tar.gz"; Target = 'ai-stack_smolcrawl-data'; Type = 'volume-tar' })
    Stop    = @('smolcrawl-pipelines')
    Start   = @('smolcrawl-pipelines')
  }
  'tailscale' = @{
    Archives = @(@{ Pattern = "tailscale-*.tar.gz"; Target = "$projectRoot\data\tailscale"; Type = 'bind-tar' })
    Stop    = @('tailscale')
    Start   = @('tailscale')
  }
  'openbrain-wiki' = @{
    Archives = @(@{ Pattern = "openbrain-wiki-*.tar.gz"; Target = 'open-brain_openbrain-wiki-data'; Type = 'volume-tar' })
    Stop    = @('openbrain-wiki')
    Start   = @('openbrain-wiki')
    Compose = 'OB1\docker\docker-compose.yml'
  }
  'openbrain-db' = @{
    Archives = @(@{ Pattern = "openbrain-*.dump"; Target = 'openbrain'; Type = 'pg-restore' })
    # Stop every writer EXCEPT openbrain-db itself; pg_restore connects in.
    Stop    = @('openbrain-mcp','openbrain-ext','openbrain-mcpo','openbrain-mcpo-ext',
                'openbrain-gateway','openbrain-rest','openbrain-postgrest',
                'openbrain-entity-worker','openbrain-wiki','openbrain-cron',
                'openbrain-gmail-pull','openbrain-gmail-prune','openbrain-digest')
    Start   = @('openbrain-mcp','openbrain-ext','openbrain-mcpo','openbrain-mcpo-ext',
                'openbrain-gateway','openbrain-rest','openbrain-postgrest',
                'openbrain-entity-worker','openbrain-wiki','openbrain-cron',
                'openbrain-gmail-pull','openbrain-gmail-prune','openbrain-digest')
    Compose = 'OB1\docker\docker-compose.yml'
  }
  'open-notebook' = @{
    Archives = @(
      @{ Pattern = "surreal-*.surql.gz";     Target = 'open_notebook';                                  Type = 'surreal-import' }
      @{ Pattern = "notebook-data-*.tar.gz"; Target = 'D:\Open WebUI\open-notebook\notebook_data';      Type = 'bind-tar' }
    )
    Stop    = @('open_notebook')
    Start   = @('open_notebook')
  }
  'lm-models' = @{
    Archives = @(@{ Pattern = "lm-models-*.tar.gz"; Target = 'C:\Users\yamao\.lmstudio\models'; Type = 'bind-tar' })
    Stop    = @('llama-cpp-upstream','llama-cpp-embed-upstream')
    Start   = @('llama-cpp-upstream','llama-cpp-embed-upstream')
  }
}

# Restore order — services that other services depend on first.
$restoreOrder = @(
  'openbrain-db', 'openbrain-wiki', 'open-notebook',
  'mnemory', 'smolcrawl', 'lm-models', 'tailscale',
  'openwebui', 'little-coder',
  'caddy', 'authelia'
)

# --- Resolve which services to restore --------------------------------
$requested = if ($Services -contains 'all') { $restoreOrder } else {
  $bad = $Services | Where-Object { -not $catalog.Contains($_) }
  if ($bad) {
    Write-Log "Unknown service(s): $($bad -join ', '). Valid: $($restoreOrder -join ', ')" 'ERROR'
    exit 1
  }
  $restoreOrder | Where-Object { $Services -contains $_ }
}

Write-Log "=== Disaster-recovery restore ===" 'PLAN'
Write-Log "  Snapshot root : $SnapshotRoot"
Write-Log "  Snapshot date : $Date"
Write-Log "  Services      : $($requested -join ', ')"
Write-Log "  Mode          : $(if ($Apply) { 'APPLY (will mutate)' } else { 'PLAN ONLY (re-run with -Apply to execute)' })"
Write-Log "  Log file      : $logFile"
Write-Log ""

# --- Phase 1: discover archives ---------------------------------------
Write-Log "=== Phase 1: discovering snapshot archives ===" 'PLAN'
$discovered = @{}
foreach ($svc in $requested) {
  $entry = $catalog[$svc]
  $svcDir = Join-Path $SnapshotRoot $svc
  if (-not (Test-Path $svcDir)) {
    Write-Log "  [MISS] $svc : directory not found: $svcDir" 'ERROR'
    continue
  }
  $found = @()
  foreach ($a in $entry.Archives) {
    # Match the date by looking for the YYYYMMDD prefix in the filename.
    $datePrefix = $Date -replace '-', ''
    $matches = Get-ChildItem -Path $svcDir -Filter $a.Pattern -ErrorAction SilentlyContinue |
               Where-Object { $_.Name -match $datePrefix } |
               Sort-Object Name -Descending
    if ($matches.Count -eq 0) {
      Write-Log "  [MISS] $svc : no $($a.Pattern) for date $Date in $svcDir" 'WARN'
      continue
    }
    $found += @{ Pattern = $a.Pattern; Target = $a.Target; Type = $a.Type; File = $matches[0].FullName }
    Write-Log "  [FOUND] $svc : $($matches[0].Name)" 'OK'
  }
  if ($found.Count -gt 0) { $discovered[$svc] = $found }
}
if ($discovered.Count -eq 0) {
  Write-Log "No archives discovered for any requested service. Aborting." 'ERROR'
  exit 1
}
Write-Log ""

# --- Phase 2: verify sha256 sentinels ---------------------------------
Write-Log "=== Phase 2: verifying sha256 sentinels ===" 'PLAN'
if ($SkipSentinelCheck) {
  Write-Log "  -SkipSentinelCheck set; SKIPPING. NOT recommended." 'WARN'
} else {
  $sentinelFail = $false
  foreach ($svc in $discovered.Keys) {
    foreach ($a in $discovered[$svc]) {
      $sentinel = "$($a.File).sha256"
      if (-not (Test-Path $sentinel)) {
        Write-Log "  [WARN] $svc : no .sha256 for $([System.IO.Path]::GetFileName($a.File))" 'WARN'
        continue
      }
      # Run sha256sum via an ephemeral alpine container -- portable, no
      # PS native sha256sum. We mount at /backups because that's the path
      # the backup containers recorded in the sentinel (their bind-mount
      # point inside the container is /backups).
      $svcDir = [System.IO.Path]::GetDirectoryName($a.File)
      $fileName = [System.IO.Path]::GetFileName($sentinel)
      $verify = docker run --rm -v "${svcDir}:/backups:ro" alpine sh -c "cd /backups && sha256sum -c '$fileName' 2>&1" 2>&1
      if ($LASTEXITCODE -eq 0) {
        Write-Log "  [OK] $svc : sentinel verified $([System.IO.Path]::GetFileName($a.File))" 'OK'
      } else {
        Write-Log "  [FAIL] $svc : sentinel mismatch on $([System.IO.Path]::GetFileName($a.File))`n        $verify" 'ERROR'
        $sentinelFail = $true
      }
    }
  }
  if ($sentinelFail) {
    Write-Log "Sentinel verification failed. Aborting before any changes." 'ERROR'
    exit 1
  }
}
Write-Log ""

# --- Phase 3: stop consumer services ----------------------------------
if (-not $Apply) {
  Write-Log "=== Phase 3-5: plan only ===" 'PLAN'
  foreach ($svc in $discovered.Keys) {
    $entry = $catalog[$svc]
    $compose = if ($entry.Compose) { $entry.Compose } else { 'docker-compose.yml' }
    Write-Log "  Would stop: $($entry.Stop -join ', ') (via $compose)" 'PLAN'
    foreach ($a in $discovered[$svc]) {
      Write-Log "    Would restore $($a.Type) -> $($a.Target) from $([System.IO.Path]::GetFileName($a.File))" 'PLAN'
    }
    Write-Log "  Would start: $($entry.Start -join ', ')" 'PLAN'
  }
  Write-Log ""
  Write-Log "Plan complete. Re-run with -Apply to execute." 'PLAN'
  Pop-Location
  exit 0
}

Write-Log "=== Phase 3: stopping consumer services ===" 'PLAN'
foreach ($svc in $discovered.Keys) {
  $entry = $catalog[$svc]
  $compose = if ($entry.Compose) { $entry.Compose } else { 'docker-compose.yml' }
  foreach ($container in $entry.Stop) {
    Write-Log "  Stopping $container ..."
    & docker compose -f $compose stop $container 2>&1 | ForEach-Object { Write-Log "    $_" }
  }
}
Write-Log ""

# --- Phase 4: restore data ---------------------------------------------
Write-Log "=== Phase 4: restoring data ===" 'PLAN'
foreach ($svc in $restoreOrder) {
  if (-not $discovered.ContainsKey($svc)) { continue }
  foreach ($a in $discovered[$svc]) {
    $fileBase = [System.IO.Path]::GetFileName($a.File)
    $fileDir  = [System.IO.Path]::GetDirectoryName($a.File)
    switch ($a.Type) {
      'volume-tar' {
        Write-Log "  $svc : volume-tar $($a.Target) <- $fileBase"
        $cmd = "find /dest -mindepth 1 -delete && cd /dest && tar xzf /in/$fileBase"
        & docker run --rm `
          -v "$($a.Target):/dest" `
          -v "${fileDir}:/in:ro" `
          alpine sh -c $cmd 2>&1 | ForEach-Object { Write-Log "    $_" }
      }
      'bind-tar' {
        Write-Log "  $svc : bind-tar $($a.Target) <- $fileBase"
        if (Test-Path $a.Target) {
          Get-ChildItem $a.Target -Force -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction Continue
        } else {
          New-Item -ItemType Directory -Path $a.Target -Force | Out-Null
        }
        & docker run --rm `
          -v "$($a.Target):/dest" `
          -v "${fileDir}:/in:ro" `
          alpine sh -c "cd /dest && tar xzf /in/$fileBase" 2>&1 | ForEach-Object { Write-Log "    $_" }
      }
      'pg-restore' {
        Write-Log "  $svc : pg_restore -> $($a.Target) from $fileBase"
        if (-not $env:OB1_PG_PASSWORD) {
          Write-Log "    [ERROR] OB1_PG_PASSWORD env var not set. Set it from OB1/docker/.env POSTGRES_PASSWORD" 'ERROR'
          continue
        }
        & docker run --rm `
          --network open-brain_obnet `
          -v "${fileDir}:/in:ro" `
          -e "PGPASSWORD=$env:OB1_PG_PASSWORD" `
          postgres:16-alpine `
          pg_restore --host openbrain-db --port 5432 --username postgres `
            --dbname $($a.Target) --clean --if-exists --no-owner --no-acl `
            "/in/$fileBase" 2>&1 | ForEach-Object { Write-Log "    $_" }
      }
      'surreal-import' {
        Write-Log "  $svc : surreal import -> $($a.Target) from $fileBase"
        # The backup-dir is bind-mounted into open-notebook-backup at /backups.
        # Decompress on the host (file is also visible inside the container);
        # then exec surreal import pointing at the file path.
        $decompressed = $a.File -replace '\.gz$', ''
        & docker run --rm -v "${fileDir}:/work" alpine sh -c "cd /work && gzip -dkf $fileBase" 2>&1 |
          ForEach-Object { Write-Log "    $_" }
        $decompressedName = [System.IO.Path]::GetFileName($decompressed)
        & docker exec open-notebook-backup surreal import `
          --endpoint http://surrealdb:8000 --username root --password root `
          --auth-level root --namespace open_notebook --database open_notebook `
          "/backups/$decompressedName" 2>&1 | ForEach-Object { Write-Log "    $_" }
        Remove-Item $decompressed -Force -ErrorAction SilentlyContinue
      }
      default {
        Write-Log "  [ERROR] $svc : unknown restore type $($a.Type)" 'ERROR'
      }
    }
  }
}
Write-Log ""

# --- Phase 5: restart services ----------------------------------------
Write-Log "=== Phase 5: starting consumer services ===" 'PLAN'
foreach ($svc in $restoreOrder) {
  if (-not $discovered.ContainsKey($svc)) { continue }
  $entry = $catalog[$svc]
  $compose = if ($entry.Compose) { $entry.Compose } else { 'docker-compose.yml' }
  foreach ($container in $entry.Start) {
    Write-Log "  Starting $container ..."
    & docker compose -f $compose start $container 2>&1 | ForEach-Object { Write-Log "    $_" }
  }
}
Write-Log ""

Write-Log "=== Restore complete ===" 'OK'
Write-Log "  Verify by:"
Write-Log "    - opening Open WebUI and confirming chats are present"
Write-Log "    - 'docker exec openbrain-db psql -U postgres -d openbrain -c \"select count(*) from thoughts;\"'"
Write-Log "    - opening open_notebook and confirming notebooks render"
Write-Log "    - .\scripts\portal-status.ps1 (if portal services were restored)"
Write-Log "  Log saved to: $logFile"

Pop-Location
exit 0
