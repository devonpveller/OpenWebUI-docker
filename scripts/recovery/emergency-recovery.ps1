[CmdletBinding()]
param(
    [ValidateSet("recover", "nuclear", "gpu-reset")]
    [string]$Action = "recover"
)

# Pin CWD to the repo root: every `docker compose` call below is relative
# (moved to scripts/recovery/ 2026-08-21; previously this script silently
# depended on being launched from the repo root).
Set-Location (Split-Path -Parent (Split-Path -Parent $PSScriptRoot))

$ErrorActionPreference = "Stop"

# ──────────────────────────────────────────────────────────────────────────
# Service inventory — every container the recovery stack maintains.
#
# The MAIN compose project (docker-compose.yml) holds several planes:
#   core    openwebui, llama-cpp-upstream, llama-cpp-embed-upstream, llm-queue,
#           llm-gateway-db, llm-gateway, llm-gateway-ui, tailscale
#           (llm-queue = B2 admission controller between the upstreams and LiteLLM;
#            llm-gateway-ui = master-key'd Admin-UI sidecar / analytics dashboard)
#   memory  mnemory, mnemory-cloud-gateway
#   search  vpn, redis, searxng, gateway (Private Search Gateway)
#   coder   open-terminal, little-coder, lc-egress
#   aux     smolcrawl-pipelines, surrealdb, open_notebook
#   backup  mnemory-backup, openwebui-backup, little-coder-backup,
#           smolcrawl-backup, tailscale-backup, lm-models-backup,
#           open-notebook-backup, openbrain-db-backup, openbrain-wiki-backup
#
# PORTAL plane (caddy, authelia, cloudflared, portal-init, portal-alerter,
# authelia-watcher, authelia-notif-bridge, integrity-tripwire, portal-cron,
# tunnel-watcher, caddy-backup, authelia-backup) is PROFILE-GATED
# (`profiles: [internet]`) — it does NOT start with a plain `docker compose up -d`
# and is deliberately NOT managed here. It is driven by scripts/portal-on.ps1 /
# portal-off.ps1. A nuclear `docker compose down` WILL stop a running portal; it
# is not auto-restored (see Invoke-NuclearRecovery's detect-and-warn).
#
# Open Brain (OB1) is a SEPARATE compose project (OB1\docker\docker-compose.yml,
# project name "open-brain"). Its containers attach to the main stack's
# ai-stack_llm-net as an EXTERNAL network, so OB1 is shut down first and
# brought up last — only after llama-cpp-upstream / llama-cpp-embed-upstream are healthy.
# ──────────────────────────────────────────────────────────────────────────

$Script:OB1Compose = "OB1\docker\docker-compose.yml"

# agent-org (teams-chat agent orchestration) is ALSO a separate compose project
# (project name "agent-org", agent-org\docker\docker-compose.yml). Like OB1 it attaches
# to the main stack's ai-stack_llm-net (external) for local inference, and it optionally
# reaches OB1's openbrain-gateway (audit mirror). So it is shut down FIRST (before OB1)
# and brought up LAST (after OB1). The `workers` + `cloud` profiles are gated
# (profiles: [workers|cloud]) and — like the Portal plane — are NOT managed here; the
# default plane (mattermost + agent-bridge + their DBs) is.
$Script:AgentOrgCompose = "agent-org\docker\docker-compose.yml"

# Main compose services, low-level dependency first.
# (Portal plane omitted on purpose — profile-gated; see the header note.)
$Script:MainStackServices = @(
    "openwebui", "llama-cpp-upstream", "llama-cpp-embed-upstream",
    "llm-queue", "llm-gateway-db", "llm-gateway", "llm-gateway-ui", "tailscale",
    "mnemory", "mnemory-cloud-gateway",
    "smolcrawl-pipelines", "surrealdb", "open_notebook",
    "vpn", "redis", "searxng", "gateway",
    "open-terminal", "little-coder", "lc-egress",
    # Backup cron sidecars (see helper arrays below).
    "mnemory-backup", "openwebui-backup", "little-coder-backup",
    "smolcrawl-backup", "tailscale-backup", "lm-models-backup", "open-notebook-backup",
    "llm-gateway-backup",
    "openbrain-db-backup", "openbrain-wiki-backup"
)

# Backup sidecars touching only main-stack/host resources — safe to nudge anytime.
$Script:MainBackups = @(
    "mnemory-backup", "openwebui-backup", "little-coder-backup",
    "smolcrawl-backup", "tailscale-backup", "lm-models-backup", "open-notebook-backup"
)
# Backup sidecars that attach to OB1-owned external network/volumes — start only
# AFTER the OB1 stack is up (open-brain_obnet + open-brain_* volumes must exist).
$Script:OB1Backups = @("openbrain-db-backup", "openbrain-wiki-backup")

# Open Brain (OB1) services, low-level dependency first.
# The trailing block (cron + 3 HTTP-triggered scheduled services) lives
# in docker-compose.scheduled.yml; compose includes it so a single
# `docker compose up -d` brings everything. They depend on openbrain-rest
# (PostgREST proxy) so they sort after it.
$Script:OB1Services = @(
    "openbrain-db", "openbrain-mcp", "openbrain-ext",
    "openbrain-gateway",
    "openbrain-mcpo", "openbrain-mcpo-ext", "openbrain-postgrest",
    "openbrain-rest", "openbrain-entity-worker",
    "openbrain-suggestion-worker", "openbrain-curator", "openbrain-research", "openbrain-chunk-worker",
    "openbrain-grounding-backfiller",
    "openbrain-wiki", "openbrain-wiki-viewer", "openbrain-workbench", "openbrain-extract",
    "openbrain-cron", "openbrain-gmail-pull", "openbrain-gmail-prune", "openbrain-digest",
    "openbrain-podcast",
    "openbrain-idea-refinery"   # Idea Refinery drain (profile-gated 'idea-refinery'; started via the profile below)
)

# agent-org services, default plane only (workers/cloud profiles are gated + excluded,
# same treatment as the profile-gated Portal). Low-level dependency first; the two
# pg_dump backup sidecars last (they depend_on the healthy DBs).
$Script:AgentOrgServices = @(
    "mattermost-db", "mattermost", "agent-bridge-db", "agent-bridge",
    "agent-bridge-db-backup", "mattermost-db-backup"
)

function Write-Log {
    param([string]$Level, [string]$Message)
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Write-Host "[$timestamp] [$Level] $Message" -ForegroundColor $(
        switch ($Level) {
            "ERROR" { "Red" }
            "WARN" { "Yellow" }
            "SUCCESS" { "Green" }
            "INFO" { "Cyan" }
            default { "White" }
        }
    )
}

function Test-DockerCompose {
    try {
        docker compose version | Out-Null
        return $true
    }
    catch {
        Write-Log "ERROR" "Docker Compose not available: $_"
        return $false
    }
}

function Test-OB1Available {
    # OB1 is an optional, separately-deployed stack. Recovery only drives it
    # when its compose file is present in the workspace.
    return Test-Path $Script:OB1Compose
}

function Test-NetworkConnectivity {
    param([string]$Container)
    try {
        docker compose exec $Container ping -c 1 -W 5 8.8.8.8 2>$null | Out-Null
        return $LASTEXITCODE -eq 0
    }
    catch {
        return $false
    }
}

function Stop-ServiceGroup {
    # Stop a set of related services in one call (best-effort, never throws).
    param([string]$Label, [string[]]$Services)
    Write-Log "INFO" "Stopping $Label ($($Services -join ', '))..."
    try {
        docker compose stop @Services
    }
    catch {
        Write-Log "WARN" "${Label} stop had issues, continuing: $_"
    }
}

function Start-ServiceGroup {
    # Start a set of related services in one call. docker compose resolves
    # each service's depends_on, so listed services self-order.
    param([string]$Label, [string[]]$Services)
    Write-Log "INFO" "Starting $Label ($($Services -join ', '))..."
    try {
        docker compose up -d @Services
        Write-Log "SUCCESS" "$Label started"
    }
    catch {
        Write-Log "WARN" "Failed to start ${Label}: $_"
    }
}

function Stop-OB1Stack {
    # Bring the Open Brain (OB1) compose project to a stop. Done FIRST in a
    # shutdown so the main stack can later drop/recreate ai-stack_llm-net.
    if (-not (Test-OB1Available)) { return }
    Write-Log "INFO" "Stopping Open Brain (OB1) stack..."
    try {
        docker compose -f $Script:OB1Compose stop
    }
    catch {
        Write-Log "WARN" "OB1 stop had issues, continuing: $_"
    }
}

function Start-OB1Stack {
    # Bring the Open Brain (OB1) compose project up. OB1's own depends_on
    # handles its internal ordering; it must run AFTER the main stack so
    # ai-stack_llm-net (external) exists and llama-cpp-upstream is reachable.
    if (-not (Test-OB1Available)) {
        Write-Log "INFO" "Open Brain (OB1) not deployed in this workspace - skipping"
        return
    }
    Write-Log "INFO" "Starting Open Brain (OB1) stack ($($Script:OB1Services.Count) containers)..."
    try {
        # --profile idea-refinery so the (profile-gated) Idea Refinery drain is (re)started too.
        docker compose -f $Script:OB1Compose --profile idea-refinery up -d
        Write-Log "SUCCESS" "Open Brain (OB1) stack started"
    }
    catch {
        Write-Log "WARN" "Failed to start OB1 stack: $_"
    }
}

function Reset-OB1Stack {
    # Recreate the OB1 compose project (nuclear path).
    if (-not (Test-OB1Available)) {
        Write-Log "INFO" "Open Brain (OB1) not deployed - skipping OB1 recreate"
        return
    }
    Write-Log "INFO" "Recreating Open Brain (OB1) stack..."
    try {
        docker compose -f $Script:OB1Compose --profile idea-refinery down
        Start-Sleep -Seconds 5
        docker compose -f $Script:OB1Compose --profile idea-refinery up -d
        Write-Log "SUCCESS" "OB1 stack recreated"
    }
    catch {
        Write-Log "WARN" "OB1 recreate had issues: $_"
    }
}

function Test-AgentOrgAvailable {
    # agent-org is an optional, separately-deployed stack. Recovery only drives it
    # when its compose file is present in the workspace.
    return Test-Path $Script:AgentOrgCompose
}

function Stop-AgentOrgStack {
    # Stop agent-org FIRST (before OB1 and the main stack): it attaches to
    # ai-stack_llm-net (external) and optionally reaches OB1's gateway.
    if (-not (Test-AgentOrgAvailable)) { return }
    Write-Log "INFO" "Stopping agent-org stack..."
    try {
        docker compose -f $Script:AgentOrgCompose stop
    }
    catch {
        Write-Log "WARN" "agent-org stop had issues, continuing: $_"
    }
}

function Start-AgentOrgStack {
    # Bring agent-org up LAST (after the main stack + OB1). Its own depends_on handles
    # internal ordering; the default plane only (workers/cloud profiles are operator-driven).
    if (-not (Test-AgentOrgAvailable)) {
        Write-Log "INFO" "agent-org not deployed in this workspace - skipping"
        return
    }
    Write-Log "INFO" "Starting agent-org stack ($($Script:AgentOrgServices.Count) containers, default plane)..."
    try {
        docker compose -f $Script:AgentOrgCompose up -d
        Write-Log "SUCCESS" "agent-org stack started"
    }
    catch {
        Write-Log "WARN" "Failed to start agent-org stack: $_"
    }
}

function Reset-AgentOrgStack {
    # Recreate the agent-org compose project (nuclear path).
    if (-not (Test-AgentOrgAvailable)) {
        Write-Log "INFO" "agent-org not deployed - skipping agent-org recreate"
        return
    }
    Write-Log "INFO" "Recreating agent-org stack..."
    try {
        docker compose -f $Script:AgentOrgCompose down
        Start-Sleep -Seconds 5
        docker compose -f $Script:AgentOrgCompose up -d
        Write-Log "SUCCESS" "agent-org stack recreated"
    }
    catch {
        Write-Log "WARN" "agent-org recreate had issues: $_"
    }
}

function Test-BasicConnectivity {
    Write-Log "INFO" "Performing basic connectivity checks..."

    try {
        $containers = docker compose ps --format json | ConvertFrom-Json

        # Snapshot every maintained service's state into a lookup table.
        $states = @{}
        foreach ($svc in $Script:MainStackServices) {
            $s = ($containers | Where-Object { $_.Service -eq $svc }).State
            $states[$svc] = if ($s) { $s } else { "absent" }
        }

        # Report container states grouped by plane so 20+ services stay readable.
        Write-Log "INFO" ("Core   - openwebui: {0}, llama-cpp-upstream: {1}, llama-cpp-embed-upstream: {2}, tailscale: {3}" -f `
            $states["openwebui"], $states["llama-cpp-upstream"], $states["llama-cpp-embed-upstream"], $states["tailscale"])
        Write-Log "INFO" ("Memory - mnemory: {0}, mnemory-cloud-gateway: {1}" -f `
            $states["mnemory"], $states["mnemory-cloud-gateway"])
        Write-Log "INFO" ("Search - vpn: {0}, redis: {1}, searxng: {2}, gateway: {3}" -f `
            $states["vpn"], $states["redis"], $states["searxng"], $states["gateway"])
        Write-Log "INFO" ("Coder  - open-terminal: {0}, little-coder: {1}, lc-egress: {2}" -f `
            $states["open-terminal"], $states["little-coder"], $states["lc-egress"])
        Write-Log "INFO" ("Aux    - smolcrawl-pipelines: {0}, surrealdb: {1}, open_notebook: {2}" -f `
            $states["smolcrawl-pipelines"], $states["surrealdb"], $states["open_notebook"])
        Write-Log "INFO" ("Backup - mnemory: {0}, owui: {1}, lc: {2}, smolcrawl: {3}, tailscale: {4}, lm-models: {5}, on: {6}, ob1-db: {7}, ob1-wiki: {8}" -f `
            $states["mnemory-backup"], $states["openwebui-backup"], $states["little-coder-backup"], `
            $states["smolcrawl-backup"], $states["tailscale-backup"], $states["lm-models-backup"], `
            $states["open-notebook-backup"], $states["openbrain-db-backup"], $states["openbrain-wiki-backup"])

        # Open Brain (OB1) — separate compose project, reported as a count.
        if (Test-OB1Available) {
            try {
                $ob1 = docker compose -f $Script:OB1Compose ps --format json | ConvertFrom-Json
                $ob1Running = @($ob1 | Where-Object { $_.State -eq "running" }).Count
                Write-Log "INFO" "OB1    - $ob1Running/$($Script:OB1Services.Count) openbrain containers running"
            }
            catch {
                Write-Log "WARN" "OB1 status unavailable: $_"
            }
        }

        # agent-org — separate compose project, reported as a count (default plane).
        if (Test-AgentOrgAvailable) {
            try {
                $ao = docker compose -f $Script:AgentOrgCompose ps --format json | ConvertFrom-Json
                $aoRunning = @($ao | Where-Object { $_.State -eq "running" }).Count
                Write-Log "INFO" "AgOrg  - $aoRunning/$($Script:AgentOrgServices.Count) agent-org containers running"
            }
            catch {
                Write-Log "WARN" "agent-org status unavailable: $_"
            }
        }

        # The recovery decision rests on the core plane: if it is healthy the
        # rest is a cheap up -d nudge; if not, a real recovery is needed.
        if ($states["openwebui"] -eq "running" -and $states["llama-cpp-upstream"] -eq "running" -and `
            $states["llama-cpp-embed-upstream"] -eq "running" -and $states["tailscale"] -eq "running") {
            # Test OpenWebUI health
            try {
                docker compose exec openwebui curl -f -s http://localhost:8080/health 2>$null | Out-Null
                if ($LASTEXITCODE -eq 0) {
                    Write-Log "INFO" "OpenWebUI health check: PASSED"

                    # Test llama-cpp-upstream connectivity
                    try {
                        docker compose exec llama-cpp-upstream curl -f -s http://localhost:8080/health 2>$null | Out-Null
                        if ($LASTEXITCODE -eq 0) {
                            Write-Log "INFO" "llama-cpp-upstream connectivity: PASSED"

                            # Test external connectivity
                            if (Test-NetworkConnectivity "tailscale") {
                                Write-Log "SUCCESS" "All basic checks PASSED - issue may be timing/performance related"
                                return $true
                            }
                            else {
                                Write-Log "WARN" "External connectivity failed"
                            }
                        }
                        else {
                            Write-Log "WARN" "llama-cpp-upstream connectivity failed"
                        }
                    }
                    catch {
                        Write-Log "WARN" "llama-cpp-upstream connectivity test failed: $_"
                    }
                }
                else {
                    Write-Log "WARN" "OpenWebUI health check failed"
                }
            }
            catch {
                Write-Log "WARN" "OpenWebUI health test failed: $_"
            }
        }
        else {
            Write-Log "WARN" "Not all core containers are running - recovery needed"
        }
    }
    catch {
        Write-Log "ERROR" "Failed to check container status: $_"
    }

    return $false
}

function Invoke-MinimalRecovery {
    Write-Log "INFO" "========================================="
    Write-Log "INFO" "MINIMAL RECOVERY - GENTLE RESTART"
    Write-Log "INFO" "========================================="

    Write-Log "INFO" "Attempting gentle service restart..."

    # Just restart services without destroying containers.
    try {
        # NETNS ORDERING (critical): `tailscale` runs `network_mode:
        # service:openwebui`, so it lives INSIDE openwebui's network namespace.
        # Restarting openwebui recreates that namespace and orphans tailscale
        # (it stays "Up" but loses all connectivity / serve config). Therefore
        # NEVER restart them in one `docker compose restart` call — that restarts
        # tailscale first (or without waiting), then openwebui pulls the netns out
        # from under it. The order MUST be: inference (netns-independent) → restart
        # openwebui → WAIT until it is healthy → only THEN restart tailscale so it
        # re-attaches to the new, stable namespace.
        docker compose restart llama-cpp-upstream llama-cpp-embed-upstream

        docker compose restart openwebui
        if (-not (Wait-ForHealthy "openwebui" 240)) {
            Write-Log "WARN" "OpenWebUI not healthy after restart; restarting tailscale anyway so it is not left orphaned..."
        }

        # Tailscale LAST — re-attaches to openwebui's (now stable) netns and
        # re-applies its serve config via entrypoint.sh.
        docker compose restart tailscale
        Wait-ForHealthy "tailscale" 90 | Out-Null

        # Ensure every auxiliary container is running (cheap no-op if already
        # up). Their depends_on only fires at the initial compose-up, so a
        # llama-cpp-upstream restart can leave dependents (mnemory, the search gateway,
        # the little-coder plane) degraded without this nudge. surrealdb must
        # precede open_notebook; the search/coder planes self-order via their
        # own depends_on.
        # Inference admission plane first (llm-queue sits between the upstreams
        # and LiteLLM; both must be up before callers — design B2). A
        # llama-cpp-upstream restart drops nothing here (httpx reconnects), but
        # nudge them so a cold dependent comes back.
        docker compose up -d llm-queue llm-gateway

        docker compose up -d mnemory mnemory-cloud-gateway `
            smolcrawl-pipelines surrealdb open_notebook `
            vpn redis searxng gateway `
            open-terminal little-coder lc-egress

        # Backup cron sidecars touching only main/host resources (safe anytime).
        Start-ServiceGroup "main backups" $Script:MainBackups

        # Open Brain (OB1) — separate compose project.
        Start-OB1Stack

        # OB1-attached backups — only after OB1 (obnet + open-brain volumes exist).
        Start-ServiceGroup "OB1 backups" $Script:OB1Backups

        # agent-org — separate compose project, brought up last (downstream of OB1).
        Start-AgentOrgStack

        Write-Log "INFO" "Waiting for services to stabilize..."
        Start-Sleep -Seconds 60

        # Test if this fixed the issue
        if (Test-BasicConnectivity) {
            Write-Log "SUCCESS" "Minimal recovery successful!"
            return $true
        }
        else {
            Write-Log "WARN" "Minimal recovery insufficient, proceeding to standard recovery"
            return $false
        }
    }
    catch {
        Write-Log "ERROR" "Minimal recovery failed: $_"
        return $false
    }
}

function Test-GPUAvailability {
    try {
        $result = docker compose exec openwebui python -c "import torch; print('CUDA:', torch.cuda.is_available())" 2>$null
        return $result -like "*True*"
    }
    catch {
        return $false
    }
}

function Stop-ServiceGracefully {
    param([string]$ServiceName, [int]$TimeoutSeconds = 30)

    Write-Log "INFO" "Stopping $ServiceName service..."
    try {
        docker compose stop $ServiceName

        # Wait for graceful shutdown
        $elapsed = 0
        while ($elapsed -lt $TimeoutSeconds) {
            $status = docker compose ps $ServiceName --format json 2>$null | ConvertFrom-Json
            if (-not $status -or $status.State -eq "exited") {
                Write-Log "SUCCESS" "$ServiceName stopped gracefully"
                return $true
            }
            Start-Sleep -Seconds 2
            $elapsed += 2
        }

        Write-Log "WARN" "$ServiceName did not stop gracefully, forcing stop..."
        docker compose kill $ServiceName
        return $true
    }
    catch {
        Write-Log "ERROR" "Failed to stop $ServiceName`: $($_.Exception.Message)"
        return $false
    }
}

function Wait-ForHealthy {
    param([string]$ServiceName, [int]$TimeoutSeconds = 120)

    Write-Log "INFO" "Waiting for $ServiceName to become healthy..."
    $elapsed = 0

    while ($elapsed -lt $TimeoutSeconds) {
        try {
            $status = docker compose ps $ServiceName --format json 2>$null | ConvertFrom-Json
            if ($status.Health -eq "healthy") {
                Write-Log "SUCCESS" "$ServiceName is healthy"
                return $true
            }
            elseif ($status.State -eq "running" -and -not $status.Health) {
                # Some services don't have health checks
                Write-Log "SUCCESS" "$ServiceName is running (no health check)"
                return $true
            }

            $healthStatus = if ($status.Health) { $status.Health } else { $status.State }
            Write-Log "INFO" "$ServiceName status: $healthStatus (${elapsed}s elapsed)"

            Start-Sleep -Seconds 5
            $elapsed += 5
        }
        catch {
            Write-Log "WARN" "Error checking $ServiceName status: $_"
            Start-Sleep -Seconds 5
            $elapsed += 5
        }
    }

    Write-Log "ERROR" "$ServiceName failed to become healthy within ${TimeoutSeconds}s"
    return $false
}

function Invoke-EmergencyRecovery {
    Write-Log "INFO" "========================================="
    Write-Log "INFO" "EMERGENCY TAILSCALE NETWORK RECOVERY"
    Write-Log "INFO" "========================================="

    if (-not (Test-DockerCompose)) {
        throw "Docker Compose is not available"
    }

    # Check current status
    Write-Log "INFO" "Current container status:"
    docker compose ps

    # CRITICAL: Perform diagnostics before destructive actions
    Write-Log "INFO" "Running pre-recovery diagnostics..."
    if (Test-BasicConnectivity) {
        Write-Log "SUCCESS" "Basic connectivity working - trying minimal recovery first"
        if (Invoke-MinimalRecovery) {
            return  # Success, no need for destructive recovery
        }
    }

    Write-Log "INFO" "Minimal recovery failed or basic checks failed - proceeding with full recovery"

    # ── Phase 1: Graceful shutdown in reverse dependency order ─────────────
    Write-Log "INFO" "Phase 1: Graceful shutdown"
    Write-Log "WARN" "This restarts the full workspace: core, memory, search, coder planes + OB1"

    # agent-org first (downstream of OB1 + the main stack's llm-net).
    Stop-AgentOrgStack

    # Open Brain (OB1) next — it attaches to the main stack's llm-net.
    Stop-OB1Stack

    # Watchtower (independent monitor).

    # little-coder control plane (reverse dependency order).
    Stop-ServiceGroup "little-coder control plane" `
        @("little-coder-backup", "lc-egress", "little-coder", "open-terminal")

    # Private Search Gateway (reverse dependency order).
    Stop-ServiceGroup "Private Search Gateway" `
        @("gateway", "searxng", "redis", "vpn")

    # Tailscale (shares the OpenWebUI network namespace).
    if (-not (Stop-ServiceGracefully "tailscale" 30)) {
        Write-Log "WARN" "Tailscale stop had issues, continuing..."
    }

    # OpenWebUI-dependent auxiliary services (open_notebook before surrealdb).
    Stop-ServiceGroup "auxiliary services" @("smolcrawl-pipelines", "open_notebook", "surrealdb")

    # Mnemory memory layer (gateway before mnemory).
    Stop-ServiceGroup "Mnemory layer" @("mnemory-cloud-gateway", "mnemory", "mnemory-backup")

    # OpenWebUI backup scheduler.
    Stop-ServiceGroup "OpenWebUI backup" @("openwebui-backup")

    # LiteLLM gateway — stops after callers, before the upstream inference servers
    # (callers reach inference through it; the upstreams must outlive it).
    Stop-ServiceGroup "LiteLLM gateway" @("llm-gateway-backup", "llm-gateway-ui", "llm-gateway", "llm-gateway-db")

    # llm-queue (B2 admission controller) — stops after the gateway (which
    # forwards through it), before the upstreams it forwards to.
    if (-not (Stop-ServiceGracefully "llm-queue" 30)) {
        Write-Log "WARN" "llm-queue stop had issues, continuing..."
    }

    # llama-cpp-upstream inference services.
    if (-not (Stop-ServiceGracefully "llama-cpp-upstream" 30)) {
        Write-Log "WARN" "llama-cpp-upstream stop had issues, continuing..."
    }
    if (-not (Stop-ServiceGracefully "llama-cpp-embed-upstream" 30)) {
        Write-Log "WARN" "llama-cpp-embed-upstream stop had issues, continuing..."
    }

    # OpenWebUI last — it provides the shared network namespace.
    if (-not (Stop-ServiceGracefully "openwebui" 45)) {
        Write-Log "WARN" "OpenWebUI stop had issues, continuing..."
    }

    # ── Phase 2: Clean up any orphaned network namespaces ──────────────────
    Write-Log "INFO" "Phase 2: Network namespace cleanup"
    Start-Sleep -Seconds 15

    # ── Phase 3: Restart in correct dependency order ───────────────────────
    Write-Log "INFO" "Phase 3: Service restart"

    # Start OpenWebUI first (with GPU passthrough)
    Write-Log "INFO" "Starting OpenWebUI with GPU support..."
    try {
        docker compose up -d openwebui
        if (-not (Wait-ForHealthy "openwebui" 240)) {
            throw "OpenWebUI failed to become healthy"
        }
    }
    catch {
        Write-Log "ERROR" "Failed to start OpenWebUI: $_"
        throw
    }

    # Verify GPU is working
    if (Test-GPUAvailability) {
        Write-Log "SUCCESS" "GPU acceleration is available"
    }
    else {
        Write-Log "WARN" "GPU acceleration may not be working"
    }

    # Brief pause to ensure network namespace is stable
    Write-Log "INFO" "Allowing network namespace to stabilize..."
    Start-Sleep -Seconds 20

    # Start llama-cpp-upstream services (GPU inference) — many planes depend on these
    Write-Log "INFO" "Starting llama-cpp-upstream with GPU support..."
    try {
        docker compose up -d llama-cpp-upstream
        if (-not (Wait-ForHealthy "llama-cpp-upstream" 120)) {
            Write-Log "WARN" "llama-cpp-upstream health check failed, but continuing..."
        }
    }
    catch {
        Write-Log "ERROR" "Failed to start llama-cpp-upstream: $_"
        throw
    }

    Write-Log "INFO" "Starting llama-cpp-embed-upstream..."
    try {
        docker compose up -d llama-cpp-embed-upstream
        if (-not (Wait-ForHealthy "llama-cpp-embed-upstream" 60)) {
            Write-Log "WARN" "llama-cpp-embed-upstream health check failed, but continuing..."
        }
    }
    catch {
        Write-Log "ERROR" "Failed to start llama-cpp-embed-upstream: $_"
        throw
    }

    # llm-queue (B2 admission controller) — between the upstreams and LiteLLM.
    # Starts AFTER the upstreams are healthy, BEFORE the gateway (which forwards
    # chat through it). Restart-fast (stateless proxy, no model load).
    Write-Log "INFO" "Starting llm-queue (inference admission controller)..."
    try {
        docker compose up -d llm-queue
        if (-not (Wait-ForHealthy "llm-queue" 60)) {
            Write-Log "WARN" "llm-queue health check failed, but continuing..."
        }
    }
    catch {
        Write-Log "ERROR" "Failed to start llm-queue: $_"
    }

    # LiteLLM gateway — the front door every caller reaches inference through.
    # Starts after BOTH upstream inference servers + llm-queue are healthy; before any caller.
    Write-Log "INFO" "Starting LiteLLM gateway (db, then gateway)..."
    try {
        docker compose up -d llm-gateway-db
        Wait-ForHealthy "llm-gateway-db" 60 | Out-Null
        docker compose up -d llm-gateway
        if (-not (Wait-ForHealthy "llm-gateway" 150)) {
            Write-Log "WARN" "llm-gateway health check failed, but continuing..."
        }
        docker compose up -d llm-gateway-backup
        # llm-gateway-ui: master-key'd Admin-UI sidecar (analytics dashboard).
        # Depends only on llm-gateway-db (already healthy) — serves no inference,
        # so it is non-critical to the recovery path; start it best-effort.
        docker compose up -d llm-gateway-ui
    }
    catch {
        Write-Log "ERROR" "Failed to start LiteLLM gateway: $_"
    }

    # Start Tailscale (depends on OpenWebUI network)
    Write-Log "INFO" "Starting Tailscale with shared network namespace..."
    try {
        docker compose up -d tailscale
        if (-not (Wait-ForHealthy "tailscale" 90)) {
            Write-Log "WARN" "Tailscale health check failed, testing connectivity..."
        }
    }
    catch {
        Write-Log "ERROR" "Failed to start Tailscale: $_"
        throw
    }

    # Start Mnemory memory layer (depends on llama-cpp-upstream services)
    Write-Log "INFO" "Starting Mnemory memory service..."
    try {
        docker compose up -d mnemory
        if (-not (Wait-ForHealthy "mnemory" 90)) {
            Write-Log "WARN" "Mnemory health check failed, but continuing..."
        }
    }
    catch {
        Write-Log "WARN" "Failed to start Mnemory: $_"
        # Don't throw - Mnemory is not critical for basic functionality
    }

    # mnemory-cloud-gateway (cloud MCP proxy — depends on mnemory)
    Start-ServiceGroup "mnemory-cloud-gateway" @("mnemory-cloud-gateway")

    # Backup schedulers (independent cron sidecars, main/host resources).
    # OB1-attached backups (openbrain-db/wiki) start later, after Start-OB1Stack.
    Start-ServiceGroup "backup schedulers" $Script:MainBackups

    # Start SmolCrawl Pipelines (depends on OpenWebUI)
    Write-Log "INFO" "Starting SmolCrawl Pipelines..."
    try {
        docker compose up -d smolcrawl-pipelines
        if (-not (Wait-ForHealthy "smolcrawl-pipelines" 90)) {
            Write-Log "WARN" "SmolCrawl Pipelines health check failed, but continuing..."
        }
    }
    catch {
        Write-Log "WARN" "Failed to start SmolCrawl Pipelines: $_"
    }

    # Start surrealdb first, then open-notebook (which depends on it).
    Write-Log "INFO" "Starting surrealdb (open-notebook database)..."
    try {
        docker compose up -d surrealdb
        Start-Sleep -Seconds 10
        Write-Log "SUCCESS" "surrealdb started"
    }
    catch {
        Write-Log "WARN" "Failed to start surrealdb: $_"
    }

    Write-Log "INFO" "Starting open-notebook..."
    try {
        docker compose up -d open_notebook
        if (-not (Wait-ForHealthy "open_notebook" 90)) {
            Write-Log "WARN" "open-notebook health check failed, but continuing..."
        }
    }
    catch {
        Write-Log "WARN" "Failed to start open-notebook: $_"
    }

    # Private Search Gateway — vpn -> redis -> searxng -> gateway.
    # depends_on chains the internal order; vpn (Mullvad) is searxng's egress and
    # the WireGuard tunnel is slow to build. (tor retired 2026-08-21; the vpn
    # proxy carries page fetches too.)
    Start-ServiceGroup "Private Search Gateway" @("vpn", "redis", "searxng", "gateway")
    if (-not (Wait-ForHealthy "gateway" 150)) {
        Write-Log "WARN" "Search gateway slow to come up, but continuing..."
    }

    # little-coder control plane — open-terminal (workspace) before the
    # little-coder daemon (control), then the MCP edge and egress sidecars.
    Write-Log "INFO" "Starting open-terminal (little-coder workspace plane)..."
    try {
        docker compose up -d open-terminal
        if (-not (Wait-ForHealthy "open-terminal" 90)) {
            Write-Log "WARN" "open-terminal health check failed, but continuing..."
        }
    }
    catch {
        Write-Log "WARN" "Failed to start open-terminal: $_"
    }

    Write-Log "INFO" "Starting little-coder (control daemon)..."
    try {
        docker compose up -d little-coder
        if (-not (Wait-ForHealthy "little-coder" 120)) {
            Write-Log "WARN" "little-coder health check failed, but continuing..."
        }
    }
    catch {
        Write-Log "WARN" "Failed to start little-coder: $_"
    }

    Start-ServiceGroup "little-coder edges" @("lc-egress", "little-coder-backup")

    # Start Watchtower (independent service)
    Write-Log "INFO" "Starting Watchtower monitoring service..."
    try {
        Write-Log "SUCCESS" "Watchtower started"
    }
    catch {
        Write-Log "WARN" "Failed to start Watchtower: $_"
    }

    # Open Brain (OB1) last — needs ai-stack_llm-net + llama-cpp-upstream healthy.
    Start-OB1Stack

    # OB1-attached backups — only now that obnet + open-brain volumes exist.
    Start-ServiceGroup "OB1 backups" $Script:OB1Backups

    # agent-org — separate compose project, brought up last (downstream of OB1).
    Start-AgentOrgStack

    # ── Phase 4: Connectivity verification ─────────────────────────────────
    Write-Log "INFO" "Phase 4: Connectivity verification"
    Start-Sleep -Seconds 25

    # Test external connectivity
    if (Test-NetworkConnectivity "tailscale") {
        Write-Log "SUCCESS" "External network connectivity restored"
    }
    else {
        Write-Log "ERROR" "External network connectivity failed"
        throw "Network connectivity test failed"
    }

    # ── Phase 5: Service verification ──────────────────────────────────────
    Write-Log "INFO" "Phase 5: Service verification"

    try {
        Write-Log "INFO" "llama-cpp-upstream status:"
        docker compose exec llama-cpp-upstream curl -s http://localhost:8080/health

        Write-Log "INFO" "Tailscale status:"
        docker compose exec tailscale tailscale --socket=/tmp/tailscaled.sock status

        Write-Log "INFO" "Tailscale serve configuration:"
        docker compose exec tailscale tailscale --socket=/tmp/tailscaled.sock serve status

        Write-Log "INFO" "Mnemory status:"
        docker compose exec mnemory python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8051/health').read().decode())" 2>$null

        Write-Log "INFO" "SmolCrawl Pipelines status:"
        docker compose exec smolcrawl-pipelines curl -s http://localhost:9099/ 2>$null

        Write-Log "INFO" "open-notebook API status:"
        docker compose exec open_notebook python3 -c "import urllib.request; print(urllib.request.urlopen('http://localhost:5055/api/config').read().decode())" 2>$null

        Write-Log "INFO" "Private Search Gateway status:"
        docker compose exec gateway curl -s http://localhost:8080/healthz 2>$null

        Write-Log "INFO" "open-terminal status:"
        docker compose exec open-terminal curl -s http://localhost:8000/health 2>$null

        Write-Log "INFO" "little-coder daemon status:"
        docker compose exec little-coder curl -s http://localhost:8090/health 2>$null

        Write-Log "INFO" "surrealdb running state:"
        docker compose ps surrealdb --format "table {{.Service}}\t{{.Status}}" 2>$null

        Write-Log "INFO" "Memory + coder plane status:"
        docker compose ps mnemory-cloud-gateway lc-egress --format "table {{.Service}}\t{{.Status}}" 2>$null

        Write-Log "INFO" "Backup schedulers + Watchtower status:"
        docker compose ps mnemory-backup openwebui-backup little-coder-backup `
            smolcrawl-backup tailscale-backup lm-models-backup open-notebook-backup `
            openbrain-db-backup openbrain-wiki-backup --format "table {{.Service}}\t{{.Status}}" 2>$null

        if (Test-OB1Available) {
            Write-Log "INFO" "Open Brain (OB1) status:"
            docker compose -f $Script:OB1Compose ps --format "table {{.Service}}\t{{.Status}}" 2>$null
        }
    }
    catch {
        Write-Log "WARN" "Unable to verify service configurations: $_"
    }

    Write-Log "SUCCESS" "========================================="
    Write-Log "SUCCESS" "EMERGENCY RECOVERY COMPLETED"
    Write-Log "SUCCESS" "========================================="
}

function Invoke-NuclearRecovery {
    Write-Log "WARN" "========================================="
    Write-Log "WARN" "NUCLEAR RECOVERY - FULL STACK RESTART"
    Write-Log "WARN" "========================================="

    # CRITICAL: Last-chance diagnostic check
    Write-Log "INFO" "Performing final diagnostic before nuclear option..."
    if (Test-BasicConnectivity) {
        Write-Log "SUCCESS" "Basic connectivity working - trying minimal recovery instead of nuclear"
        if (Invoke-MinimalRecovery) {
            Write-Log "SUCCESS" "Minimal recovery successful - nuclear option avoided!"
            return
        }
    }

    Write-Log "WARN" "All diagnostics failed - proceeding with nuclear recovery..."
    Write-Log "WARN" "This will destroy and rebuild containers..."

    # Detect whether the (profile-gated) internet portal is running. `docker
    # compose down` will stop it, and recovery does NOT auto-restore it (bringing
    # the internet front-end back up must stay a deliberate operator action).
    $portalWasUp = $false
    try {
        $pc = docker compose ps caddy --format json 2>$null | ConvertFrom-Json
        if ($pc -and $pc.State -eq "running") { $portalWasUp = $true }
    }
    catch {}
    if ($portalWasUp) {
        Write-Log "WARN" "Internet portal (caddy/authelia/cloudflared) is running; 'compose down' will stop it. It will NOT be auto-restored."
    }

    # Bring OB1 down FIRST so the main `docker compose down` can drop the
    # ai-stack_llm-net network OB1 attaches to as an external network.
    if (Test-OB1Available) {
        Write-Log "INFO" "Tearing down Open Brain (OB1) stack..."
        try { docker compose -f $Script:OB1Compose down }
        catch { Write-Log "WARN" "OB1 teardown had issues: $_" }
    }

    Write-Log "INFO" "Performing complete main-stack shutdown..."
    docker compose down

    Write-Log "INFO" "Cleaning up network namespaces..."
    Start-Sleep -Seconds 20

    Write-Log "INFO" "Starting full main stack with proper dependency order..."
    docker compose up -d

    Write-Log "INFO" "Waiting for complete stack initialization..."
    Start-Sleep -Seconds 90

    # Open Brain (OB1) last — main stack (and ai-stack_llm-net) is up now.
    Start-OB1Stack

    # agent-org — separate compose project, downstream of OB1 (started after the
    # OB1 backups nudge below).
    Start-AgentOrgStack

    # OB1-attached backups — the main `up -d` above could not start
    # openbrain-db-backup while obnet was down; nudge them now that OB1 is up.
    Start-ServiceGroup "OB1 backups" $Script:OB1Backups

    if ($portalWasUp) {
        Write-Log "WARN" "Portal was running before recovery. Re-run scripts/portal-on.ps1 to restore the internet front-end (recovery does not auto-start it)."
    }

    # Test connectivity
    if (Test-NetworkConnectivity "tailscale") {
        Write-Log "SUCCESS" "Nuclear recovery successful"

        # Test GPU
        if (Test-GPUAvailability) {
            Write-Log "SUCCESS" "GPU acceleration restored"
        }
        else {
            Write-Log "WARN" "GPU may need additional recovery"
        }

        if (Test-OB1Available) {
            Write-Log "INFO" "Open Brain (OB1) status:"
            docker compose -f $Script:OB1Compose ps --format "table {{.Service}}\t{{.Status}}" 2>$null
        }
    }
    else {
        throw "Nuclear recovery failed - manual intervention required"
    }
}

function Invoke-GPUReset {
    Write-Log "INFO" "========================================="
    Write-Log "INFO" "GPU RECOVERY - REBUILDING GPU SERVICES"
    Write-Log "INFO" "========================================="

    Write-Log "INFO" "Stopping GPU-dependent services for reset..."
    docker compose down llama-cpp-upstream llama-cpp-embed-upstream openwebui mnemory

    Write-Log "INFO" "Rebuilding OpenWebUI with fresh GPU configuration..."
    docker compose build --no-cache openwebui

    Write-Log "INFO" "Starting OpenWebUI with GPU support..."
    docker compose up -d openwebui

    if (Wait-ForHealthy "openwebui" 240) {
        Write-Log "INFO" "Starting llama-cpp-upstream with GPU support..."
        docker compose up -d llama-cpp-upstream

        if (Wait-ForHealthy "llama-cpp-upstream" 120) {
            if (Test-GPUAvailability) {
                Write-Log "SUCCESS" "GPU reset successful - CUDA is available"

                # Test llama-cpp-upstream GPU access
                try {
                    docker compose exec llama-cpp-upstream curl -s http://localhost:8080/health
                    Write-Log "SUCCESS" "llama-cpp-upstream GPU integration verified"

                    # Also start embedding service
                    docker compose up -d llama-cpp-embed-upstream
                    Write-Log "INFO" "llama-cpp-embed-upstream started"

                    # Inference admission plane (B2): llm-queue then LiteLLM, so
                    # callers reach inference through the gateway → queue → upstream.
                    docker compose up -d llm-queue llm-gateway
                    Write-Log "INFO" "llm-queue + LiteLLM gateway started"

                    # Restart the planes that consume llama-cpp-upstream inference:
                    # the memory layer, the little-coder plane, and OB1.
                    docker compose up -d mnemory mnemory-cloud-gateway mnemory-backup
                    Write-Log "INFO" "Mnemory layer started"

                    docker compose up -d open-terminal little-coder lc-egress little-coder-backup
                    Write-Log "INFO" "little-coder control plane started"

                    Start-OB1Stack
                    Start-AgentOrgStack
                }
                catch {
                    Write-Log "WARN" "llama-cpp-upstream may need additional time to initialize"
                }
            }
            else {
                Write-Log "ERROR" "GPU reset failed - CUDA not available"
                throw "GPU reset failed"
            }
        }
        else {
            Write-Log "WARN" "llama-cpp-upstream startup slow but continuing..."
            # Start embedding service anyway
            docker compose up -d llama-cpp-embed-upstream
        }
    }
    else {
        throw "OpenWebUI failed to start after GPU reset"
    }
}

# Main execution
try {
    switch ($Action.ToLower()) {
        "recover" {
            try {
                Invoke-EmergencyRecovery
            }
            catch {
                Write-Log "WARN" "Standard recovery failed, attempting nuclear option..."
                Invoke-NuclearRecovery
            }
        }
        "nuclear" { Invoke-NuclearRecovery }
        "gpu-reset" { Invoke-GPUReset }
        default {
            Write-Log "ERROR" "Unknown action: $Action"
            Write-Log "INFO" "Usage: .\emergency-recovery.ps1 -Action [recover|nuclear|gpu-reset]"
            exit 1
        }
    }
}
catch {
    Write-Log "ERROR" "Recovery operation failed: $_"
    Write-Log "INFO" "Manual intervention may be required"
    Write-Log "INFO" "Consider checking:"
    Write-Log "INFO" "  - Docker Desktop is running"
    Write-Log "INFO" "  - NVIDIA drivers are installed"
    Write-Log "INFO" "  - Docker GPU runtime is configured"
    Write-Log "INFO" "  - Disk space is available"
    exit 1
}
