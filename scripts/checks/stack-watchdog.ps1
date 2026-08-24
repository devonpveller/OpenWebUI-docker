# Enhanced Tailscale Health Check and Recovery Service for Windows
# This script provides autonomous management of Tailscale connectivity

[CmdletBinding()]
param(
    [Parameter(Mandatory=$false)]
    [ValidateSet("check", "daemon", "install-task", "install-service")]
    [string]$Mode = "check",
    
    [Parameter(Mandatory=$false)]
    [ValidateRange(10, 3600)]
    [int]$IntervalSeconds = 60,
    
    [Parameter(Mandatory=$false)]
    [string]$InstallTaskName = "StackWatchdog"
)

# Set strict error handling
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

# Constants
$SCRIPT_DIR = Split-Path -Parent $PSCommandPath
$PROJECT_DIR = Split-Path -Parent (Split-Path -Parent $PSCommandPath) | Split-Path -Parent
$LOG_FILE = Join-Path $PROJECT_DIR "logs\tailscale-health.log"

# --- docker compose stderr guard (added 2026-06-05) ---------------------------
# The caddy service references ${WORKBENCH_KEY} (docker-compose.yml). When that
# variable is absent from THIS process's environment, every `docker compose ...`
# call prints "The \"WORKBENCH_KEY\" variable is not set..." to stderr. Combined
# with $ErrorActionPreference='Stop' above, the first docker call that redirects
# stderr (e.g. `docker compose logs ... 2>$null` in Test-EntrypointHealth) turns
# that benign warning into a TERMINATING error (PS 5.1 native-stderr gotcha) and
# the whole health check aborts at step 1 -- the window just flashes and exits 1,
# checking/repairing nothing. Defining the var here silences the warning at the
# source for every docker invocation this script makes. This only
# affects this script's own process env; it does NOT modify .env or any container.
#
# The value is a non-empty PLACEHOLDER, not the real key: Windows cannot store an
# empty env var (PowerShell deletes it on `=''`), and docker only suppresses the
# "is not set" warning for a DEFINED, non-empty value. This monitor never creates
# or recreates the caddy service (the sole consumer of WORKBENCH_KEY -- it is not
# in the monitor's managed-service list), so this placeholder never reaches caddy;
# and even if it somehow did, a wrong key makes caddy reject /workbench (fail
# closed). A real value present in the environment (e.g. a manual run from a
# configured shell) is preserved and takes precedence.
if (-not (Test-Path Env:\WORKBENCH_KEY) -or [string]::IsNullOrEmpty($env:WORKBENCH_KEY)) {
    $env:WORKBENCH_KEY = 'healthcheck-noop-placeholder'
}

# Create logs directory if it doesn't exist
$LogDir = Split-Path -Parent $LOG_FILE
if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}

# Function to write structured log entries
function Write-LogEntry {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$Message,
        
        [Parameter()]
        [ValidateSet("INFO", "WARN", "ERROR", "SUCCESS", "DEBUG")]
        [string]$Level = "INFO"
    )
    
    $Timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $LogEntry = "$Timestamp [$Level] $Message"
    
    try {
        $LogEntry | Out-File -FilePath $LOG_FILE -Append -Encoding UTF8
        Write-Information $LogEntry -InformationAction Continue
    }
    catch {
        Write-Warning "Failed to write to log file: $_"
        Write-Host $LogEntry
    }
}

# Function to check Docker Compose service health
function Test-ServiceHealth {
    param($ServiceName)
    
    try {
        # Name-based lookup ONLY (K.10, 2026-08-22): since Part K the root
        # project owns no services, so `docker compose ps <svc>` here always
        # wrote "no such service" noise into the health log. Container names
        # are stable across every project - inspect by name.
        $InspectJson = docker inspect $ServiceName --format '{{json .State}}' 2>$null
        if (-not $InspectJson) { return $false }
        $State = $InspectJson | ConvertFrom-Json
        $Status = [pscustomobject]@{
            State  = $State.Status
            Health = if ($State.Health) { $State.Health.Status } else { $null }
        }
        if (-not $Status) {
            return $false
        }
        
        # For OpenWebUI with GPU, allow extra time for CUDA initialization
        if ($ServiceName -eq "openwebui" -and $Status.State -eq "running") {
            # Additional check for GPU-enabled OpenWebUI readiness
            $HealthStatus = $Status.Health
            if ($HealthStatus -eq "healthy") {
                return $true
            } elseif ($HealthStatus -eq "starting") {
                # GPU initialization may take longer, give it more time
                Write-LogEntry "OpenWebUI with GPU is starting, allowing extra time for CUDA initialization..." "INFO"
                return $false
            }
        }
        
        return $Status.State -eq "running" -and $Status.Health -ne "unhealthy"
    } catch {
        return $false
    }
}

# Function to test network connectivity
function Test-NetworkConnectivity {
    [CmdletBinding()]
    param()
    
    try {
        $null = docker exec tailscale ping -c 1 8.8.8.8 2>$null
        return $LASTEXITCODE -eq 0
    }
    catch {
        return $false
    }
}

# Function to test Tailscale connection
function Test-TailscaleConnection {
    [CmdletBinding()]
    param()
    
    try {
        $null = docker exec tailscale tailscale --socket=/tmp/tailscaled.sock status 2>$null
        return $LASTEXITCODE -eq 0
    }
    catch {
        return $false
    }
}

# Inventory of expected `tailscale serve` mappings inside the tailscale
# container. Each entry is what entrypoint.sh's setup_*_serve functions
# put in place at container startup. The health check verifies all of
# these are present; the repair re-adds only the missing ones (never
# resets the full config, which would clobber working mappings).
#
# Fields:
#   Name           Human-readable identifier
#   TailscalePort  The :PORT exposed on the tailnet
#   TailscalePath  Path prefix (use "/" for root)
#   LocalPort      The socat-listening port inside tailscale container
#                  that the mapping forwards to
$ExpectedTailscaleServes = @(
    @{ Name = 'openwebui';            TailscalePort = 443;  TailscalePath = '/';                LocalPort = 8080 }
    @{ Name = 'llama-cpp-upstream';       TailscalePort = 443;  TailscalePath = '/llama-cpp';       LocalPort = 8235 }
    @{ Name = 'llama-cpp-embed-upstream'; TailscalePort = 443;  TailscalePath = '/llama-cpp-embed'; LocalPort = 8236 }
    @{ Name = 'open-notebook-ui';     TailscalePort = 8443; TailscalePath = '/';                LocalPort = 8237 }
    @{ Name = 'open-notebook-api';    TailscalePort = 5055; TailscalePath = '/';                LocalPort = 8238 }
    @{ Name = 'quartz-wiki-viewer';   TailscalePort = 8444; TailscalePath = '/';                LocalPort = 8239 }
    @{ Name = 'mattermost';           TailscalePort = 8446; TailscalePath = '/';                LocalPort = 8241 }
    @{ Name = 'llm-gateway-ui';       TailscalePort = 8445; TailscalePath = '/';                LocalPort = 8240 }
)

# Function to test serve configuration.
# Returns an array of @{Name; TailscalePort; TailscalePath; LocalPort}
# for any expected mapping that is NOT present in the live config.
# Returns empty array when fully configured.
#
# Note the `,` unary prefix on returns -- without it, PowerShell unwraps
# single-element arrays to a scalar (hashtable), making `.Count` return
# the hashtable's key count instead of 1. Verified bite 2026-05-31.
function Get-MissingTailscaleServes {
    [CmdletBinding()]
    param()

    try {
        $RawResult = docker exec tailscale sh -c 'tailscale --socket=/tmp/tailscaled.sock serve status' 2>$null
        $Result = ($RawResult -join "`n")
        if (-not $Result) {
            return ,@($ExpectedTailscaleServes)
        }
        $missing = @()
        foreach ($exp in $ExpectedTailscaleServes) {
            # Two-axis check:
            #   - the per-port header exists ("xxx.ts.net (tailnet only)" for 443
            #     or "xxx.ts.net:N (tailnet only)" for other ports)
            #   - the target local port string "127.0.0.1:NNNN" appears
            # That avoids spurious matches across unrelated port blocks.
            $hasLocalPort = $Result -like "*127.0.0.1:$($exp.LocalPort)*"
            if (-not $hasLocalPort) {
                $missing += $exp
            }
        }
        return ,$missing
    }
    catch {
        return ,@($ExpectedTailscaleServes)
    }
}

# Back-compat: old call sites that just want a bool. Returns $true when
# nothing is missing.
function Test-ServeConfiguration {
    [CmdletBinding()]
    param()
    $m = Get-MissingTailscaleServes
    return (@($m).Count -eq 0)
}

# Probe a local socat listener inside tailscale container. Returns $true
# if it accepts a TCP connection (any response, including HTTP errors).
# Used so we don't try to add a tailscale-serve mapping pointing at a
# dead socat -- that would just shadow the real (broken) state.
function Test-TailscaleLocalPort {
    [CmdletBinding()]
    param([Parameter(Mandatory)][int]$Port)
    try {
        # Single sh -c with the port baked in via PowerShell interpolation.
        # Connection-refused / no-route returns non-zero; any HTTP response
        # (200, 307, 404, 502) returns zero. We treat any zero as "alive".
        $cmd = "wget -q --spider -T 3 http://127.0.0.1:$Port/ 2>/dev/null"
        docker exec tailscale sh -c $cmd 2>$null | Out-Null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

# Repair: add any missing `tailscale serve` mappings. ADDITIVE only --
# never calls `serve reset`, never removes working mappings. Skips any
# mapping whose local socat listener is dead (logged as WARN so the
# operator/loop can address the deeper root cause).
function Repair-TailscaleServes {
    [CmdletBinding()]
    param()
    $missing = Get-MissingTailscaleServes
    if ($missing.Count -eq 0) {
        Write-LogEntry "All expected tailscale serve mappings present ($($ExpectedTailscaleServes.Count) checked)" "DEBUG"
        return $true
    }
    Write-LogEntry "tailscale serve drift: $($missing.Count)/$($ExpectedTailscaleServes.Count) mappings missing" "WARN"
    $allOk = $true
    foreach ($m in $missing) {
        Write-LogEntry "  missing: $($m.Name) :$($m.TailscalePort)$($m.TailscalePath) -> http://127.0.0.1:$($m.LocalPort)" "WARN"
        if (-not (Test-TailscaleLocalPort -Port $m.LocalPort)) {
            Write-LogEntry "  skipping repair: 127.0.0.1:$($m.LocalPort) is not accepting connections (socat dead or upstream gone) -- entrypoint.sh handles socat restart" "WARN"
            $allOk = $false
            continue
        }
        try {
            # Tailscale CLI flags:
            #   --https=PORT   the tailnet-exposed port
            #   --set-path     for non-root path prefixes (llama-cpp, llama-cpp-embed)
            #   --bg           leave the proxy running in the background
            if ($m.TailscalePath -eq '/') {
                docker exec tailscale tailscale --socket=/tmp/tailscaled.sock serve --https=$($m.TailscalePort) --bg "http://127.0.0.1:$($m.LocalPort)" | Out-Null
            } else {
                docker exec tailscale tailscale --socket=/tmp/tailscaled.sock serve --https=$($m.TailscalePort) --set-path=$($m.TailscalePath) --bg "http://127.0.0.1:$($m.LocalPort)" | Out-Null
            }
            if ($LASTEXITCODE -eq 0) {
                Write-LogEntry "  added: $($m.Name) :$($m.TailscalePort)$($m.TailscalePath)" "SUCCESS"
            } else {
                Write-LogEntry "  add FAILED (exit $LASTEXITCODE): $($m.Name)" "ERROR"
                $allOk = $false
            }
        } catch {
            Write-LogEntry "  add FAILED ($($m.Name)): $($_.Exception.Message)" "ERROR"
            $allOk = $false
        }
    }
    return $allOk
}

# Function to validate entrypoint and detect common issues
function Test-EntrypointHealth {
    [CmdletBinding()]
    param()
    
    try {
        # Check if entrypoint.sh has Windows line endings
        $EntrypointPath = Join-Path $PROJECT_DIR "entrypoint.sh"
        if (Test-Path $EntrypointPath) {
            $Content = Get-Content $EntrypointPath -Raw
            if ($Content -match "`r`n") {
                Write-LogEntry "WARNING: entrypoint.sh has Windows line endings (CRLF). This can cause container startup failures." "WARN"
                Write-LogEntry "Run: (Get-Content .\entrypoint.sh -Raw) -replace '`r`n', '`n' | Set-Content .\entrypoint.sh -NoNewline" "INFO"
                return $false
            }
        }
        
        # Check for common Docker build issues in logs. Isolated in its own
        # try/catch: a failure to READ the logs (docker stderr, daemon hiccup)
        # must NOT be misread as "entrypoint invalid" and abort the whole health
        # check -- that exact misclassification (a docker stderr warning bubbling
        # up under -Stop) is what crashed every run before 2026-06-05.
        try {
            # cmd /c merges the streams BEFORE PowerShell sees them - the tailscale
            # container logs to stderr, which PS 5.1 would otherwise wrap into a
            # NativeCommandError (the WARN the operator saw 2026-08-22).
            $Logs = cmd /c "docker logs tailscale --tail 5 2>&1" | Out-String
            if ($Logs -match "no such file or directory" -and $Logs -match "entrypoint") {
                Write-LogEntry "CRITICAL: Entrypoint script not found in container. Rebuild required." "ERROR"
                Write-LogEntry "Run: docker compose build --no-cache tailscale" "INFO"
                return $false
            }
        } catch {
            Write-LogEntry "Could not read tailscale logs for entrypoint check (non-fatal): $($_.Exception.Message)" "WARN"
        }

        return $true
    }
    catch {
        Write-LogEntry "Failed to validate entrypoint: $($_.Exception.Message)" "ERROR"
        return $false
    }
}

# Function to recover Tailscale service
# Function to repair OpenWebUI-llama-cpp connectivity
function Repair-LlamaCppConnectivity {
    Write-LogEntry "Starting OpenWebUI-llama-cpp connectivity recovery..." "WARN"
    
    try {
        # Check if llama-cpp-upstream container is running
        if (-not (Test-ServiceHealth "llama-cpp-upstream")) {
            Write-LogEntry "llama-cpp-upstream container not running, starting..." "WARN"
            docker compose -f inference\docker-compose.yml --env-file .env up -d llama-cpp-upstream | Out-Null
            Start-Sleep 30

            if (-not (Test-ServiceHealth "llama-cpp-upstream")) {
                Write-LogEntry "Failed to start llama-cpp-upstream container" "ERROR"
                return $false
            }
        }

        # Also check llama-cpp-embed-upstream
        if (-not (Test-ServiceHealth "llama-cpp-embed-upstream")) {
            Write-LogEntry "llama-cpp-embed-upstream container not running, starting..." "WARN"
            docker compose -f inference\docker-compose.yml --env-file .env up -d llama-cpp-embed-upstream | Out-Null
            Start-Sleep 15
        }
        
        # Wait for llama-cpp API to become available
        Write-LogEntry "Waiting for llama-cpp API to become ready..."
        $MaxWaitTime = 120
        $WaitTime = 0
        
        while ($WaitTime -lt $MaxWaitTime) {
            try {
                docker exec llama-cpp-upstream curl -s -f --max-time 5 http://localhost:8080/health | Out-Null
                if ($LASTEXITCODE -eq 0) {
                    Write-LogEntry "llama-cpp API is now responding" "SUCCESS"
                    break
                }
            } catch {}
            
            Start-Sleep 10
            $WaitTime += 10
            
            if ($WaitTime % 30 -eq 0) {
                Write-LogEntry "Still waiting for llama-cpp API... (${WaitTime}s/${MaxWaitTime}s)" "INFO"
            }
        }
        
        # Test final connectivity
        if (Test-LlamaCppConnectivity) {
            Write-LogEntry "llama-cpp connectivity restored" "SUCCESS"
            return $true
        } else {
            # SAFETY GUARD (2026-06-12): never restart openwebui from here. openwebui
            # owns the network namespace that `tailscale` shares (network_mode:
            # service:openwebui), so restarting openwebui orphans tailscale and takes
            # the tailnet down -- the exact cascade this block used to cause. The rest
            # of this script is deliberately netns-safe (see Repair-TailscaleService,
            # which refuses to auto-restart openwebui for the same reason). An inference
            # problem is repaired by restarting the inference upstream/gateway, never the
            # netns anchor -- leave it for operator review rather than break the tailnet.
            Write-LogEntry "llama-cpp upstream API responded but connectivity check still failing -- NOT restarting openwebui (would orphan tailscale netns); leaving for operator review" "ERROR"
            return $false
        }
    } catch {
        Write-LogEntry "llama-cpp connectivity recovery failed: $($_.Exception.Message)" "ERROR"
        return $false
    }
}

function Repair-TailscaleService {
    Write-LogEntry "Starting Tailscale service recovery..." "WARN"
    
    try {
        # First try gentle restart (preserves network namespace)
        Write-LogEntry "Attempting gentle restart (preserving GPU container)..."
        docker compose -f frontend\docker-compose.yml --env-file .env stop tailscale | Out-Null
        Start-Sleep 5
        
        # Ensure OpenWebUI is still healthy before restarting Tailscale
        if (-not (Test-ServiceHealth "openwebui")) {
            Write-LogEntry "OpenWebUI became unhealthy during restart, aborting gentle restart" "ERROR"
            return $false
        }
        
        docker compose -f frontend\docker-compose.yml --env-file .env start tailscale | Out-Null
        Start-Sleep 45  # Increased wait time for GPU container dependencies
        
        # Verify gentle restart worked
        if (Test-NetworkConnectivity -and Test-TailscaleConnection) {
            Write-LogEntry "Gentle restart successful" "SUCCESS"
            return $true
        }
        
        # If gentle restart failed, try network namespace recovery
        Write-LogEntry "Gentle restart failed, attempting network namespace recovery..." "WARN"
        
        # Ensure OpenWebUI is healthy before namespace recovery
        if (-not (Test-ServiceHealth "openwebui")) {
            Write-LogEntry "OpenWebUI is not healthy, cannot perform safe namespace recovery" "ERROR"
            return $false
        }
        
        # Use the proper network namespace recovery method
        docker compose -f frontend\docker-compose.yml --env-file .env stop tailscale | Out-Null
        docker compose -f frontend\docker-compose.yml --env-file .env rm -f tailscale | Out-Null
        Start-Sleep 5  # Give OpenWebUI time to stabilize
        docker compose -f frontend\docker-compose.yml --env-file .env up -d tailscale | Out-Null
        Start-Sleep 60  # Increased wait for GPU container + network namespace reattachment
        
        # Final verification
        if (Test-NetworkConnectivity -and Test-TailscaleConnection) {
            Write-LogEntry "Network namespace recovery successful" "SUCCESS"
            return $true
        }
        else {
            Write-LogEntry "Network namespace recovery failed, may need OpenWebUI restart" "ERROR"
            return $false
        }
    } 
    catch {
        Write-LogEntry "Recovery failed with error: $($_.Exception.Message)" "ERROR"
        return $false
    }
}

# Function to test Open Terminal health
function Test-OpenTerminalHealth {
    [CmdletBinding()]
    param()

    try {
        # open-terminal is the little-coder workspace plane -- it left openwebui's
        # network namespace (it is on lc-net / llm-net now), so probe it INSIDE
        # its own container, not via openwebui's localhost:8000.
        $Response = docker exec open-terminal curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/health 2>$null
        if ($LASTEXITCODE -eq 0 -and $Response -eq "200") {
            Write-LogEntry "Open Terminal health check passed" "DEBUG"
            return $true
        } else {
            Write-LogEntry "Open Terminal is not responding on open-terminal:8000 (HTTP $Response)" "WARN"
            return $false
        }
    }
    catch {
        Write-LogEntry "Open Terminal health check failed: $($_.Exception.Message)" "ERROR"
        return $false
    }
}

# Function to recover Open Terminal service
function Repair-OpenTerminal {
    Write-LogEntry "Attempting to restart open-terminal container..." "WARN"
    try {
        docker compose up -d open-terminal | Out-Null
        Start-Sleep 10
        if (Test-OpenTerminalHealth) {
            Write-LogEntry "Open Terminal recovered successfully" "SUCCESS"
            return $true
        } else {
            Write-LogEntry "Open Terminal recovery failed" "ERROR"
            return $false
        }
    }
    catch {
        Write-LogEntry "Open Terminal recovery error: $($_.Exception.Message)" "ERROR"
        return $false
    }
}

# Generic helper: ensure a non-critical compose container is running.
# Uses Test-ServiceHealth (which reads docker's compose-defined healthcheck
# status, or just the running state for containers without a healthcheck).
# Used for mnemory and the backup sidecars --
# none are required for the core OpenWebUI/Tailscale/LLM path, so failures
# are logged but do not fail the overall health check.
function Confirm-AuxiliaryContainer {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$ServiceName,
        [int]$RestartWaitSeconds = 15
    )
    if (Test-ServiceHealth $ServiceName) {
        Write-LogEntry "$ServiceName container healthy" "DEBUG"
        return $true
    }
    Write-LogEntry "$ServiceName is unhealthy or stopped, attempting recovery..." "WARN"
    try {
        docker compose up -d $ServiceName | Out-Null
        Start-Sleep $RestartWaitSeconds
        if (Test-ServiceHealth $ServiceName) {
            Write-LogEntry "$ServiceName recovered successfully" "SUCCESS"
            return $true
        }
        Write-LogEntry "$ServiceName recovery did not converge - feature may be degraded" "WARN"
        return $false
    } catch {
        Write-LogEntry "$ServiceName recovery error: $($_.Exception.Message)" "WARN"
        return $false
    }
}

# Function to test llama-cpp connectivity
function Test-LlamaCppConnectivity {
    # Omit the exec probe if the container isn't running -- `docker compose exec`
    # against a stopped service writes to stderr, which (with ErrorActionPreference
    # = "Stop" at the top of this script) bubbles up as a thrown exception and
    # lands in the catch block as a misleading [ERROR]. A stopped container is
    # a normal transient state during recovery, not a script-level failure.
    if (-not (Test-ServiceHealth "llama-cpp-upstream")) {
        Write-LogEntry "llama-cpp-upstream container is not running" "DEBUG"
        return $false
    }
    try {
        Write-LogEntry "Testing llama-cpp-upstream connectivity..." "DEBUG"
        $LlamaCppResponse = docker exec llama-cpp-upstream curl -s -f --max-time 10 http://localhost:8080/health 2>$null
        if ($LASTEXITCODE -eq 0 -and $LlamaCppResponse) {
            Write-LogEntry "llama-cpp connectivity verified" "DEBUG"
            return $true
        } else {
            Write-LogEntry "llama-cpp not responding on localhost:8080" "WARN"
            return $false
        }
    } catch {
        Write-LogEntry "llama-cpp connectivity test failed: $($_.Exception.Message)" "WARN"
        return $false
    }
}

# Function to test llama-cpp-embed connectivity (independent of main llama-cpp).
# Embed has its own model/process and can fail while main llama-cpp is healthy.
#
# IMPORTANT: llama.cpp server's HTTP handler stalls /health and /v1/models while
# embedding requests are in flight (verified: /health times out at 30s, but
# /v1/embeddings keeps returning 200 in the logs). So a /health timeout does
# NOT mean the container is dead -- it just means it's busy. We use a two-stage
# probe: try /health quickly; if it stalls, fall back to scanning recent logs
# for active embedding traffic. Docker's healthcheck has the same blind spot
# and frequently marks this container "unhealthy" while it is in fact serving.
function Test-LlamaCppEmbedConnectivity {
    # Stage 0: container must be running. Note we deliberately DO NOT require
    # Health -ne "unhealthy" here (Test-ServiceHealth does), because the docker
    # healthcheck false-positives under load -- see comment above.
    $Status = $null
    try {
        $InspectJson = docker inspect llama-cpp-embed-upstream --format '{{json .State}}' 2>$null
        $StateObj = if ($InspectJson) { $InspectJson | ConvertFrom-Json } else { $null }
        $Status = if ($StateObj) { [pscustomobject]@{ State = $StateObj.Status; Health = if ($StateObj.Health) { $StateObj.Health.Status } else { $null } } } else { $null }
    } catch { }
    if (-not $Status -or $Status.State -ne "running") {
        Write-LogEntry "llama-cpp-embed container is not running" "DEBUG"
        return $false
    }

    # Stage 1: quick /health probe. Short timeout -- we don't want to block the
    # monitor for 30 s on every cycle when the server is busy.
    try {
        Write-LogEntry "Testing llama-cpp-embed connectivity..." "DEBUG"
        $EmbedResponse = docker exec llama-cpp-embed-upstream curl -s -f --max-time 5 http://localhost:8080/health 2>$null
        if ($LASTEXITCODE -eq 0 -and $EmbedResponse) {
            Write-LogEntry "llama-cpp-embed /health OK" "DEBUG"
            return $true
        }
    } catch { }

    # Stage 2: /health didn't answer. Scan recent logs for active embedding
    # traffic -- if the server has served an embedding request in the last 2 min
    # it is alive, just blocked on inference. Patterns match llama.cpp server's
    # request-completion lines ("done request: POST /v1/embeddings ... 200")
    # and slot lifecycle markers.
    try {
        $RecentLog = cmd /c "docker logs --tail 40 --since 2m llama-cpp-embed-upstream 2>&1" | Out-String
        if ($RecentLog -match 'POST /v1/embeddings.*\s200\b' -or
            $RecentLog -match 'launch_slot_|done request:|slot\s+release:') {
            Write-LogEntry "llama-cpp-embed /health unresponsive but actively serving embedding requests (busy, not dead)" "INFO"
            return $true
        }
    } catch { }

    Write-LogEntry "llama-cpp-embed /health unreachable AND no recent embedding activity in logs" "WARN"
    return $false
}

# Function to repair llama-cpp-embed (start if missing, restart otherwise).
function Repair-LlamaCppEmbed {
    Write-LogEntry "Starting llama-cpp-embed recovery..." "WARN"
    try {
        if (-not (Test-ServiceHealth "llama-cpp-embed-upstream")) {
            Write-LogEntry "llama-cpp-embed-upstream container not running, starting..." "WARN"
            docker compose up -d llama-cpp-embed-upstream | Out-Null
        } else {
            Write-LogEntry "llama-cpp-embed-upstream running but unresponsive, restarting..." "WARN"
            docker compose restart llama-cpp-embed-upstream | Out-Null
        }

        # Wait for the API to come back. bge-m3 model load is fast, but allow
        # up to 120 s to be safe.
        $MaxWaitTime = 120
        $WaitTime = 0
        while ($WaitTime -lt $MaxWaitTime) {
            Start-Sleep 10
            $WaitTime += 10
            if (Test-LlamaCppEmbedConnectivity) {
                Write-LogEntry "llama-cpp-embed connectivity restored after ${WaitTime}s" "SUCCESS"
                return $true
            }
            if ($WaitTime % 30 -eq 0) {
                Write-LogEntry "Still waiting for llama-cpp-embed... (${WaitTime}s/${MaxWaitTime}s)" "INFO"
            }
        }
        Write-LogEntry "llama-cpp-embed recovery did not converge - embedding/RAG features may be degraded" "ERROR"
        return $false
    } catch {
        Write-LogEntry "llama-cpp-embed recovery failed: $($_.Exception.Message)" "ERROR"
        return $false
    }
}

# Function to test open-notebook health (FastAPI on port 5055).
# open_notebook depends on surrealdb; if surrealdb is down, the API will report
# dbStatus != "online" but still return 200, so we only require a 200 response
# here and treat surrealdb as a separate auxiliary check.
# The image ships only Python (no wget/curl), so the probe uses urllib like
# the mnemory healthcheck pattern.
function Test-OpenNotebookHealth {
    [CmdletBinding()]
    param()
    if (-not (Test-ServiceHealth "open_notebook")) {
        Write-LogEntry "open_notebook container is not running" "DEBUG"
        return $false
    }
    try {
        Write-LogEntry "Testing open-notebook API..." "DEBUG"
        docker exec open_notebook python3 -c "import urllib.request,sys; urllib.request.urlopen('http://localhost:5055/api/config', timeout=5); sys.exit(0)" 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Write-LogEntry "open-notebook API responding on port 5055" "DEBUG"
            return $true
        }
        Write-LogEntry "open-notebook API not responding on localhost:5055" "WARN"
        return $false
    } catch {
        Write-LogEntry "open-notebook health check failed: $($_.Exception.Message)" "WARN"
        return $false
    }
}

# Function to repair open-notebook. Ensures surrealdb (its database dependency)
# is up first, then restarts open_notebook and waits for the API.
# `docker compose restart` writes container-state messages ("Restarting", "Started")
# to stderr; with $ErrorActionPreference = "Stop" at the top of this script those
# would bubble up as thrown exceptions. Redirect stderr so docker's normal
# progress output doesn't trip the catch block.
function Repair-OpenNotebook {
    Write-LogEntry "Starting open-notebook recovery..." "WARN"
    try {
        if (-not (Test-ServiceHealth "surrealdb")) {
            Write-LogEntry "surrealdb (open-notebook DB) not running, starting..." "WARN"
            docker compose -f OB1\docker\docker-compose.yml up -d surrealdb 2>&1 | Out-Null
            Start-Sleep 10
        }

        if (-not (Test-ServiceHealth "open_notebook")) {
            Write-LogEntry "open_notebook container not running, starting..." "WARN"
            docker compose -f OB1\docker\docker-compose.yml up -d open_notebook 2>&1 | Out-Null
        } else {
            Write-LogEntry "open_notebook running but API unresponsive, restarting..." "WARN"
            docker restart open_notebook 2>&1 | Out-Null
        }

        # Frontend (Next.js) waits for FastAPI via wait-for-api.sh, so first start
        # is slower than a plain restart. Allow up to 90 s.
        $MaxWaitTime = 90
        $WaitTime = 0
        while ($WaitTime -lt $MaxWaitTime) {
            Start-Sleep 10
            $WaitTime += 10
            if (Test-OpenNotebookHealth) {
                Write-LogEntry "open-notebook recovered after ${WaitTime}s" "SUCCESS"
                return $true
            }
            if ($WaitTime % 30 -eq 0) {
                Write-LogEntry "Still waiting for open-notebook... (${WaitTime}s/${MaxWaitTime}s)" "INFO"
            }
        }
        Write-LogEntry "open-notebook recovery did not converge - notebook UI may be unavailable" "WARN"
        return $false
    } catch {
        Write-LogEntry "open-notebook recovery failed: $($_.Exception.Message)" "ERROR"
        return $false
    }
}

# ---------------------------------------------------------------------------
# Extended-plane checks (added 2026-06-05): the private web-search gateway,
# little-coder, mnemory-cloud-gateway, and the SEPARATE "open-brain" compose project
# (including the openbrain-mcp stale-DB-pool guard that caused Open WebUI tool
# 500s / "Broken pipe" on 2026-06-05).
# ---------------------------------------------------------------------------

# search-gateway /healthz is fast process liveness (always 200 if the event loop
# is serving). We gate restarts on THIS, not /readyz: /readyz does a deep check
# (SearXNG through the VPN chain) that can be slow, so it would
# false-trigger plane restarts on a 60s loop. /readyz is probed informationally
# (longer timeout, logged only) below.
function Test-SearchGatewayHealth {
    try {
        $r = Invoke-WebRequest -Uri 'http://127.0.0.1:8085/healthz' -UseBasicParsing -TimeoutSec 5
        return ($r.StatusCode -eq 200)
    } catch {
        return $false
    }
}

# Informational only: is the full web-search path actually ready (redis +
# searxng + vpn reachable)? Can be slow, so logged but never used to restart.
function Get-SearchGatewayReady {
    try {
        $r = Invoke-WebRequest -Uri 'http://127.0.0.1:8085/readyz' -UseBasicParsing -TimeoutSec 15
        return ($r.StatusCode -eq 200)
    } catch {
        return $false
    }
}

# Open Brain is a SEPARATE compose project (project=open-brain); this monitor's
# `docker compose` commands (ai-stack project) cannot see it. Delegate to the
# canonical by-name probe scripts\check-openbrain-health.ps1, which includes the
# stale-DB-pool guard: after openbrain-db restarts, openbrain-mcp keeps a dead
# connection and every MCP tool call (OWUI tools + Claude connector) returns
# "Broken pipe (os error 32)" -> mcpo HTTP 500, while the container still shows
# "Up". -Repair makes the fix `docker restart openbrain-mcp`.
function Invoke-OpenBrainHealth {
    $obScript = Join-Path $SCRIPT_DIR 'check-openbrain-health.ps1'
    if (-not (Test-Path $obScript)) {
        Write-LogEntry "Open Brain probe not found: $obScript" "WARN"
        return
    }
    try {
        # Run via the call operator (not dot-source) so the child's `exit` does
        # not terminate this daemon. -Repair auto-restarts broken pieces; -Quiet
        # keeps per-OK lines out of the loop; -LogPath routes the child's
        # WARN/FIX/DOWN detail straight into this monitor's log (its Write-Host
        # output is otherwise not capturable via 2>&1).
        & $obScript -Repair -Quiet -LogPath $LOG_FILE | Out-Null
        $code = $LASTEXITCODE
        if ($code -eq 0) {
            Write-LogEntry "Open Brain stack healthy" "DEBUG"
        } else {
            Write-LogEntry "Open Brain stack reported unresolved fault(s) (exit $code) - see OpenBrain WARN/ERROR lines above" "WARN"
        }
    } catch {
        Write-LogEntry "Open Brain probe error: $($_.Exception.Message)" "WARN"
    }
}

# agent-org is a SEPARATE compose project (project=agent-org); this monitor's
# `docker compose` (ai-stack) can't see it. Delegate to the canonical by-name
# probe scripts\check-agent-org-health.ps1 -- same pattern as Open Brain. It
# guards the agent-bridge stale-DB-pool + the ao-git-egress stale-mount classes.
# -Repair auto-restarts/recreates broken pieces; -Quiet keeps per-OK lines out of
# the loop; -LogPath routes its detail into this monitor's log.
function Invoke-AgentOrgHealth {
    $aoScript = Join-Path $SCRIPT_DIR 'check-agent-org-health.ps1'
    if (-not (Test-Path $aoScript)) {
        Write-LogEntry "agent-org probe not found: $aoScript" "WARN"
        return
    }
    try {
        & $aoScript -Repair -Quiet -LogPath $LOG_FILE | Out-Null
        $code = $LASTEXITCODE
        if ($code -eq 0) {
            Write-LogEntry "agent-org stack healthy" "DEBUG"
        } else {
            Write-LogEntry "agent-org stack reported unresolved fault(s) (exit $code) - see AgentOrg WARN/ERROR lines above" "WARN"
        }
    } catch {
        Write-LogEntry "agent-org probe error: $($_.Exception.Message)" "WARN"
    }
}

# Function to perform comprehensive health check
# --- HOST Tailscale daemon (a separate tailnet node from the container!) ---
# 2026-07-05: after an OOM-crash reboot the host daemon sat in 'NoState' (the
# tray app was not running and unattended mode was not yet enabled) -- the
# operator's remote access was dead while every container-side check passed.
# Detect and best-effort repair by (re)starting the tray app; the daemon-level
# fix (unattended mode) is set, this is the belt-and-braces layer.
# Non-fatal: the container tailnet node is independent of the host node.
function Test-HostTailscaleBackend {
    [CmdletBinding()]
    param()
    try {
        $exe = Join-Path $env:ProgramFiles 'Tailscale\tailscale.exe'
        if (-not (Test-Path $exe)) { return $true }  # host tailscale not installed
        $raw = (& $exe status --json 2>$null) -join "`n"
        if (-not $raw) { throw "empty status output" }
        $state = ($raw | ConvertFrom-Json).BackendState
        if ($state -eq 'Running') {
            Write-LogEntry "host Tailscale backend Running" "DEBUG"
            return $true
        }
        Write-LogEntry "host Tailscale backend state '$state' (not Running) - starting tray app to reattach" "WARN"
        if (-not (Get-Process -Name 'tailscale-ipn' -ErrorAction SilentlyContinue)) {
            Start-Process (Join-Path $env:ProgramFiles 'Tailscale\tailscale-ipn.exe') | Out-Null
        }
        Start-Sleep 20
        $raw2 = (& $exe status --json 2>$null) -join "`n"
        $state2 = ($raw2 | ConvertFrom-Json).BackendState
        if ($state2 -eq 'Running') {
            Write-LogEntry "host Tailscale recovered (Running)" "SUCCESS"
            return $true
        }
        Write-LogEntry "host Tailscale still '$state2' - needs operator (service restart may require elevation)" "ERROR"
        return $false
    } catch {
        Write-LogEntry "host Tailscale check inconclusive: $($_.Exception.Message)" "WARN"
        return $true
    }
}

# --- claude-sessions bridge (Mattermost <-> Claude Code, HOST process) -----
# The bridge that connects #claude-sessions in Mattermost to headless claude -p
# runs as a HOST Scheduled Task ('claude-sessions-bridge', venv pythonw shim +
# interpreter pair), not a container -- no container check can see it. Liveness
# proxy: its single-instance lock, a LISTEN socket on 127.0.0.1:48291 held by
# the interpreter (bridge.py binds it at process start, BEFORE the wait for
# Mattermost, so it listens within seconds of launch). 2026-07-23: the pair
# dies together if either member is killed; a reboot restarts the task but
# nothing else watched it -- this check closes that gap. Repair = restart the
# Scheduled Task (the canonical launcher; NEVER spawn pythonw directly -- the
# task owns the process tree). Unrecovered failure alerts to Mattermost
# (notify-mattermost.sh posts via the MM API directly, independent of the
# bridge), throttled to one ping per 12h while the outage persists.
$CLAUDE_BRIDGE_TASK = 'claude-sessions-bridge'
$CLAUDE_BRIDGE_LOCK_PORT = 48291   # bridge.py BRIDGE_LOCK_PORT default
# The bridge talks to Mattermost over the HOST port-forward (bridge.py BRIDGE_MM_URL
# default http://localhost:8065). This is a DIFFERENT path from the tailnet serve
# route (which is container->container via socat) and can fail independently.
$CLAUDE_BRIDGE_MM_URL = if ($env:BRIDGE_MM_URL) { $env:BRIDGE_MM_URL } elseif ($env:MM_URL) { $env:MM_URL } else { 'http://localhost:8065' }

function Test-ClaudeSessionsBridge {
    [CmdletBinding()]
    param()
    try {
        $conn = Get-NetTCPConnection -LocalPort $CLAUDE_BRIDGE_LOCK_PORT -State Listen -ErrorAction SilentlyContinue
        if (-not $conn) { return $false }
        # Confirm the listener really is the bridge's python -- a foreign
        # squatter on this port would also block the bridge from ever starting,
        # and a task restart cannot fix that (worth an explicit ERROR).
        $owner = Get-Process -Id (@($conn)[0].OwningProcess) -ErrorAction SilentlyContinue
        if ($owner -and $owner.ProcessName -notmatch '^python') {
            Write-LogEntry "claude-sessions bridge lock port $CLAUDE_BRIDGE_LOCK_PORT held by '$($owner.ProcessName)' (PID $($owner.Id)) -- NOT the bridge; it cannot start until the port is freed" "ERROR"
            return $false
        }
        return $true
    } catch {
        Write-LogEntry "claude-sessions bridge probe error: $($_.Exception.Message)" "WARN"
        return $false
    }
}

# Probe the bridge's ACTUAL Mattermost dependency from the host. 'Process alive'
# (lock port held) is NOT the same as 'bridge can poll': 2026-07-24 the bridge
# held the lock port for ~4h while Mattermost's host port-forward went stale
# after an MM container restart, so it logged 'poll error' every 36s and picked
# up ZERO chats while every container-side check said healthy. This closes that
# blind spot by hitting the same endpoint the bridge polls.
function Test-ClaudeBridgeMattermostReachable {
    [CmdletBinding()]
    param()
    try {
        $r = Invoke-WebRequest -Uri "$CLAUDE_BRIDGE_MM_URL/api/v4/system/ping" -UseBasicParsing -TimeoutSec 6
        return ($r.StatusCode -eq 200)
    } catch {
        return $false
    }
}

# Repair path for 'bridge alive but its MM endpoint is dead'. Restarting the
# BRIDGE would not help here (proven 2026-07-24 -- the fault is the dependency,
# not the process). Distinguish a wedged host port-forward (MM healthy inside
# its container, but com.docker.backend drops host connections) from MM being
# genuinely down (that is agent-org's domain -- Invoke-AgentOrgHealth ran just
# before this). Only the wedged-forward case is repaired here, by restarting the
# mattermost container so Docker rebuilds the port mapping. That briefly blips
# the tailnet serve route too, but socat re-resolves per connection and recovers.
function Repair-ClaudeBridgeMattermostForward {
    [CmdletBinding()]
    param()
    $health = $null
    try { $health = (docker inspect -f '{{.State.Health.Status}}' mattermost 2>$null) } catch { }
    if ($health -ne 'healthy') {
        Write-LogEntry "bridge MM endpoint $CLAUDE_BRIDGE_MM_URL unreachable AND mattermost container health='$health' -- MM itself is degraded; agent-org health check owns that, NOT restarting MM from here" "ERROR"
        return $false
    }
    Write-LogEntry "bridge MM endpoint $CLAUDE_BRIDGE_MM_URL unreachable but mattermost container is healthy -- wedged Docker host port-forward; restarting mattermost to rebuild the port mapping" "WARN"
    try {
        docker restart mattermost 2>&1 | Out-Null
    } catch {
        Write-LogEntry "docker restart mattermost failed: $($_.Exception.Message)" "ERROR"
        return $false
    }
    $waited = 0
    while ($waited -lt 90) {
        Start-Sleep 6
        $waited += 6
        if (Test-ClaudeBridgeMattermostReachable) {
            Write-LogEntry "bridge MM endpoint reachable again after ${waited}s (port-forward rebuilt); the bridge opens a fresh connection each poll and self-recovers within one cycle" "SUCCESS"
            return $true
        }
    }
    Write-LogEntry "bridge MM endpoint still unreachable 90s after mattermost restart -- needs operator" "ERROR"
    return $false
}

function Confirm-ClaudeSessionsBridge {
    [CmdletBinding()]
    param()
    $sentinel = Join-Path $PROJECT_DIR 'logs\.claude-bridge-alert'
    $mmSentinel = Join-Path $PROJECT_DIR 'logs\.claude-bridge-mm-alert'
    if (Test-ClaudeSessionsBridge) {
        # Process is alive. Now confirm it can actually REACH Mattermost -- an
        # alive-but-deaf bridge (wedged host port-forward) looks identical to a
        # healthy one at the process level. See Test-ClaudeBridgeMattermostReachable.
        if (Test-ClaudeBridgeMattermostReachable) {
            Write-LogEntry "claude-sessions bridge healthy (lock port $CLAUDE_BRIDGE_LOCK_PORT listening + MM endpoint reachable)" "DEBUG"
            Remove-Item $sentinel -Force -ErrorAction SilentlyContinue
            Remove-Item $mmSentinel -Force -ErrorAction SilentlyContinue
            return $true
        }
        Write-LogEntry "claude-sessions bridge process is alive but its Mattermost endpoint $CLAUDE_BRIDGE_MM_URL is unreachable -- bridge is deaf (not registering chats)" "WARN"
        if (Repair-ClaudeBridgeMattermostForward) {
            Remove-Item $mmSentinel -Force -ErrorAction SilentlyContinue
            return $true
        }
        # Dependency repair failed. Best-effort MM alert (may not land if the MM
        # host path is still down -- notify-mattermost.sh posts via localhost:8065
        # too), throttled 12h via its own sentinel so it retries once MM is back.
        try {
            $shouldPing = $true
            if (Test-Path $mmSentinel) {
                if (((Get-Date) - (Get-Item $mmSentinel).LastWriteTime).TotalHours -lt 12) { $shouldPing = $false }
            }
            if ($shouldPing) {
                $bash = 'C:\Program Files\Git\bin\bash.exe'
                if (Test-Path $bash) {
                    $scriptPath = ($PROJECT_DIR -replace '\\', '/') + '/scripts/notify-mattermost.sh'
                    $null | & $bash $scriptPath "WARNING claude-sessions bridge is alive but cannot reach Mattermost at $CLAUDE_BRIDGE_MM_URL and auto-repair FAILED -- @bot-claude is not registering chats. Check the mattermost container + Docker host port-forward." 2>$null | Out-Null
                }
                (Get-Date -Format o) | Out-File $mmSentinel -Encoding utf8 -Force
            }
        } catch { Write-LogEntry "claude-sessions bridge MM-endpoint alert failed: $($_.Exception.Message)" "WARN" }
        return $false
    }
    Write-LogEntry "claude-sessions bridge is DOWN (no listener on 127.0.0.1:$CLAUDE_BRIDGE_LOCK_PORT), restarting its Scheduled Task..." "WARN"
    try {
        $task = Get-ScheduledTask -TaskName $CLAUDE_BRIDGE_TASK -ErrorAction SilentlyContinue
        if (-not $task) {
            Write-LogEntry "Scheduled Task '$CLAUDE_BRIDGE_TASK' not found -- cannot repair (renamed/removed?)" "ERROR"
        } else {
            # Stop first: a wedged still-'Running' task instance makes
            # Start-ScheduledTask a no-op.
            Stop-ScheduledTask -TaskName $CLAUDE_BRIDGE_TASK -ErrorAction SilentlyContinue
            Start-Sleep 2
            Start-ScheduledTask -TaskName $CLAUDE_BRIDGE_TASK
            $waited = 0
            while ($waited -lt 30) {
                Start-Sleep 5
                $waited += 5
                if (Test-ClaudeSessionsBridge) {
                    Write-LogEntry "claude-sessions bridge recovered after ${waited}s" "SUCCESS"
                    Remove-Item $sentinel -Force -ErrorAction SilentlyContinue
                    return $true
                }
            }
            Write-LogEntry "claude-sessions bridge did not come back within 30s of task restart" "ERROR"
        }
    } catch {
        Write-LogEntry "claude-sessions bridge recovery error: $($_.Exception.Message)" "ERROR"
    }
    try {
        $shouldPing = $true
        if (Test-Path $sentinel) {
            if (((Get-Date) - (Get-Item $sentinel).LastWriteTime).TotalHours -lt 12) { $shouldPing = $false }
        }
        if ($shouldPing) {
            # Git bash EXPLICITLY (same reasoning as Test-BackupRecency below).
            $bash = 'C:\Program Files\Git\bin\bash.exe'
            if (Test-Path $bash) {
                $scriptPath = ($PROJECT_DIR -replace '\\', '/') + '/scripts/notify-mattermost.sh'
                $null | & $bash $scriptPath "WARNING claude-sessions bridge (Mattermost <-> Claude) is DOWN and auto-restart FAILED -- @bot-claude will not respond. Check Scheduled Task '$CLAUDE_BRIDGE_TASK' and scripts/claude-sessions-bridge/state/bridge.log" 2>$null | Out-Null
            }
            (Get-Date -Format o) | Out-File $sentinel -Encoding utf8 -Force
        }
    } catch { Write-LogEntry "claude-sessions bridge MM alert failed: $($_.Exception.Message)" "WARN" }
    return $false
}

# --- Backup recency: an "Up" sidecar can still produce nothing ------------
# The backup scripts precheck-skip with exit 0 (deliberately: never tar broken
# state), so a wrong probe target means NO artifacts and NO error. That let
# five sidecars go silent for ~5 weeks (2026-05-29 -> 07-05) unnoticed. This
# watches the OUTPUT instead: newest artifact per backups/<dir> must be
# younger than its cadence allows. Alerts to the log + Mattermost (throttled).
$ExpectedBackupRecency = @(
    @{ Dir = 'agent-bridge-db'; MaxAgeHours = 52 }
    @{ Dir = 'authelia';        MaxAgeHours = 52 }
    @{ Dir = 'caddy';           MaxAgeHours = 52 }
    @{ Dir = 'little-coder';    MaxAgeHours = 52 }
    @{ Dir = 'llm-gateway';     MaxAgeHours = 52 }
    @{ Dir = 'lm-models';       MaxAgeHours = 220 }  # weekly cron (Sun 01:00) + slack
    @{ Dir = 'mattermost-db';   MaxAgeHours = 52 }
    @{ Dir = 'mnemory';         MaxAgeHours = 52 }
    @{ Dir = 'open-notebook';   MaxAgeHours = 52 }
    @{ Dir = 'openbrain-db';    MaxAgeHours = 52 }
    @{ Dir = 'openbrain-wiki';  MaxAgeHours = 52 }
    @{ Dir = 'openwebui';       MaxAgeHours = 52 }
    @{ Dir = 'tailscale';       MaxAgeHours = 52 }
)
function Test-BackupRecency {
    [CmdletBinding()]
    param()
    $stale = @()
    foreach ($exp in $ExpectedBackupRecency) {
        $dir = Join-Path $PROJECT_DIR "backups\$($exp.Dir)"
        if (-not (Test-Path $dir)) {
            $stale += "$($exp.Dir): backup dir missing"
            continue
        }
        $newest = Get-ChildItem $dir -File -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -notlike '*.sha256' } |
            Sort-Object LastWriteTime -Descending | Select-Object -First 1
        if (-not $newest) {
            $stale += "$($exp.Dir): no artifacts at all"
            continue
        }
        $ageH = [math]::Round(((Get-Date) - $newest.LastWriteTime).TotalHours, 1)
        if ($ageH -gt $exp.MaxAgeHours) {
            $stale += "$($exp.Dir): newest artifact $($newest.Name) is ${ageH}h old (max $($exp.MaxAgeHours)h)"
        }
    }
    # Sentinel = the outstanding-alert marker. Its presence means a STALE ping
    # was sent and never cleared; its content is the throttle key (the ';'-joined
    # stale dir names). Used by both the all-clear below and the throttle logic.
    $sentinel = Join-Path $PROJECT_DIR 'logs\.backup-recency-alert'
    if ($stale.Count -eq 0) {
        Write-LogEntry "backup recency OK ($($ExpectedBackupRecency.Count) dirs checked)" "DEBUG"
        # All-clear: if a stale alert was outstanding (sentinel present), post a
        # one-time RECOVERED notice and clear the sentinel. Without this a
        # resolved incident looks identical to an open one in #claude-code, and
        # the next stale event wouldn't re-ping until the 12h throttle lapsed.
        if (Test-Path $sentinel) {
            try {
                $prevKey = (Get-Content $sentinel -Raw -ErrorAction SilentlyContinue)
                if ($prevKey) { $prevKey = $prevKey.Trim() }
                # Same git-bash / forward-slash constraints as the STALE ping below.
                $bash = 'C:\Program Files\Git\bin\bash.exe'
                if ((Test-Path $bash) -and $prevKey) {
                    $scriptPath = ($PROJECT_DIR -replace '\\', '/') + '/scripts/notify-mattermost.sh'
                    $recovered = (($prevKey -split ';') | Sort-Object) -join ', '
                    $null | & $bash $scriptPath "RECOVERED ai-stack backup: fresh artifacts again for $recovered" 2>$null | Out-Null
                }
                Remove-Item $sentinel -Force -ErrorAction SilentlyContinue
                Write-LogEntry "backup recency RECOVERED - cleared stale alert for: $prevKey" "SUCCESS"
            } catch { Write-LogEntry "backup recency recovery notice failed: $($_.Exception.Message)" "WARN" }
        }
        return $true
    }
    foreach ($s in $stale) { Write-LogEntry "BACKUP STALE - $s" "ERROR" }
    # Mattermost alert, throttled: re-ping only if the stale set changed or the
    # last ping is older than 12h (this check runs every 10 minutes).
    try {
        $content = ($stale | Sort-Object) -join '; '
        # Throttle key = WHICH dirs are stale (not the full message: the age
        # number changes every cycle and would defeat the 12h suppression).
        $contentKey = (($stale | ForEach-Object { ($_ -split ':')[0] }) | Sort-Object) -join ';'
        $shouldPing = $true
        if (Test-Path $sentinel) {
            $prev = (Get-Content $sentinel -Raw -ErrorAction SilentlyContinue)
            if ($prev) { $prev = $prev.Trim() }
            $lastPing = (Get-Item $sentinel).LastWriteTime
            if (($prev -eq $contentKey) -and ((Get-Date) - $lastPing).TotalHours -lt 12) { $shouldPing = $false }
        }
        if ($shouldPing) {
            # Git bash EXPLICITLY: bare `Get-Command bash` resolves to WSL's
            # bash (System32), which cannot open Windows paths. Forward-slash
            # path for the same reason.
            $bash = 'C:\Program Files\Git\bin\bash.exe'
            if (Test-Path $bash) {
                $scriptPath = ($PROJECT_DIR -replace '\\', '/') + '/scripts/notify-mattermost.sh'
                # Pipe $null so bash's stdin is CLOSED: notify-mattermost.sh
                # cats stdin when it isn't a tty, and an inherited open pipe
                # (interactive/manual runs) would block it forever.
                $null | & $bash $scriptPath "WARNING ai-stack backup STALE: $content" 2>$null | Out-Null
            }
            $contentKey | Out-File $sentinel -Encoding utf8 -Force
        }
    } catch { Write-LogEntry "backup recency MM alert failed: $($_.Exception.Message)" "WARN" }
    return $false
}

# --- Out-of-band Telegram alert (DOCKER-INDEPENDENT) --------------------------
# Posts straight to the operator's phone via scripts/sysadmin-mcp/telegram_notify.py
# (plain HTTPS to the Telegram Bot API). Unlike notify-mattermost.sh (which posts
# to the Mattermost *container* on :8065), this still lands when Docker is down --
# the whole point of the out-of-band channel. Throttled per-key via a logs sentinel
# so a persistent fault doesn't spam every 60s cycle. Best-effort; never throws.
function Send-TelegramAlert {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$Message,
        [string]$ThrottleKey,
        [double]$ThrottleHours = 0.5
    )
    try {
        if ($ThrottleKey) {
            $sentinel = Join-Path $PROJECT_DIR "logs\.tg-alert-$ThrottleKey"
            if (Test-Path $sentinel) {
                if (((Get-Date) - (Get-Item $sentinel).LastWriteTime).TotalHours -lt $ThrottleHours) { return }
            }
        }
        $py = Join-Path $PROJECT_DIR '.venv\Scripts\python.exe'
        if (-not (Test-Path $py)) { $py = 'python' }
        $tg = Join-Path $SCRIPT_DIR '..\sysadmin-mcp\telegram_notify.py'
        if (Test-Path $tg) {
            & $py $tg $Message 2>$null | Out-Null
            if ($ThrottleKey) { (Get-Date -Format o) | Out-File $sentinel -Encoding utf8 -Force }
        }
    } catch { Write-LogEntry "Telegram alert failed: $($_.Exception.Message)" "WARN" }
}

# --- Docker ENGINE liveness + autonomous restart ------------------------------
# The single most important addition for the "compaction/crash stranded Docker"
# class. Every other probe in this script issues `docker ...` and assumes the
# daemon is up; this confirms that first. If the engine is DOWN it attempts an
# autonomous restart (docker desktop start, with a reset-and-retry) -- this is
# what keeps trying AFTER compact-vhdx.ps1's own 3 finally-block retries give up,
# because the watchdog is re-enabled the moment a compaction ends. On unrecovered
# failure it fires an ACTIONABLE out-of-band Telegram alert (the operator can
# reply 'docker up' / 'recover' / 'status' to the listener). Returns $true if the
# engine is up (or was recovered), $false if it is still down.
function Confirm-DockerEngine {
    [CmdletBinding()]
    param()
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'   # native docker/wsl stderr must not throw under -Stop
    try {
        & docker version --format '{{.Server.Version}}' 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Write-LogEntry "Docker engine UP" "DEBUG"
            Remove-Item (Join-Path $PROJECT_DIR 'logs\.tg-alert-engine') -Force -ErrorAction SilentlyContinue
            return $true
        }
        Write-LogEntry "Docker ENGINE is DOWN (docker version failed) -- attempting autonomous restart" "ERROR"
        $dd = Get-Process 'Docker Desktop' -ErrorAction SilentlyContinue | Where-Object { $_.Path } | Select-Object -First 1
        $ddPath = if ($dd) { $dd.Path } else { 'C:\Program Files\Docker\Docker\Docker Desktop.exe' }
        for ($attempt = 1; $attempt -le 2; $attempt++) {
            if ($attempt -gt 1) {
                & docker desktop stop 2>$null | Out-Null; Start-Sleep 5
                & wsl --shutdown 2>$null | Out-Null; Start-Sleep 8
            }
            if (-not (Get-Process 'Docker Desktop' -ErrorAction SilentlyContinue)) {
                if (Test-Path $ddPath) { Start-Process -FilePath $ddPath }
                Start-Sleep 8
            }
            Write-LogEntry "docker desktop start (attempt $attempt)" "WARN"
            & docker desktop start 2>$null | Out-Null
            for ($i = 0; $i -lt 30; $i++) {   # up to ~150s per attempt
                Start-Sleep 5
                & docker version --format '{{.Server.Version}}' 2>$null | Out-Null
                if ($LASTEXITCODE -eq 0) {
                    Write-LogEntry "Docker engine recovered on attempt $attempt" "SUCCESS"
                    Send-TelegramAlert "ai-stack: Docker engine was down; the watchdog restarted it. Verifying the stack now." -ThrottleKey 'engine-ok' -ThrottleHours 1
                    Remove-Item (Join-Path $PROJECT_DIR 'logs\.tg-alert-engine') -Force -ErrorAction SilentlyContinue
                    return $true
                }
            }
        }
        Write-LogEntry "Docker engine still DOWN after restart attempts -- needs manual intervention/reboot" "ERROR"
        Send-TelegramAlert "ALERT ai-stack Docker engine is DOWN and the watchdog could NOT restart it. Reply 'docker up' to retry, 'recover' for an ordered restart, or 'status'. May need a host reboot." -ThrottleKey 'engine' -ThrottleHours 0.25
        return $false
    } finally {
        $ErrorActionPreference = $prevEAP
    }
}

# --- Generic HOST lifeline (bridge/listener) liveness + restart ---------------
# The claude-sessions bridge (48291), the sysadmin persona bridge (48292) and the
# out-of-band Telegram listener (48293) are HOST Scheduled Tasks, not containers,
# so they stay reachable during a Docker-down window -- they are the lifelines.
# Liveness proxy: a LISTEN socket on the single-instance lock port, owned by a
# python process. Test-HostLockPort probes it; Confirm-HostTaskByPort restarts the
# owning Scheduled Task if it is not listening and alerts out-of-band on failure.
function Test-HostLockPort {
    [CmdletBinding()]
    param([Parameter(Mandatory)][int]$Port)
    try {
        $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
        if (-not $conn) { return $false }
        $owner = Get-Process -Id (@($conn)[0].OwningProcess) -ErrorAction SilentlyContinue
        if ($owner -and $owner.ProcessName -notmatch '^python') {
            Write-LogEntry "lock port $Port held by '$($owner.ProcessName)' (PID $($owner.Id)) -- not a python bridge/listener; a task restart cannot fix a foreign squatter" "ERROR"
            return $false
        }
        return $true
    } catch {
        Write-LogEntry "host lock-port $Port probe error: $($_.Exception.Message)" "WARN"
        return $false
    }
}

function Confirm-HostTaskByPort {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$TaskName,
        [Parameter(Mandatory)][int]$Port,
        [Parameter(Mandatory)][string]$Label
    )
    if (Test-HostLockPort -Port $Port) {
        Write-LogEntry "$Label alive (lock port $Port listening)" "DEBUG"
        return $true
    }
    Write-LogEntry "$Label DOWN (no listener on 127.0.0.1:$Port), restarting Scheduled Task '$TaskName'..." "WARN"
    try {
        $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        if (-not $task) {
            Write-LogEntry "Scheduled Task '$TaskName' not found -- cannot repair $Label (not registered?)" "ERROR"
        } else {
            Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
            Start-Sleep 2
            Start-ScheduledTask -TaskName $TaskName
            $waited = 0
            while ($waited -lt 30) {
                Start-Sleep 5; $waited += 5
                if (Test-HostLockPort -Port $Port) {
                    Write-LogEntry "$Label recovered after ${waited}s" "SUCCESS"
                    return $true
                }
            }
            Write-LogEntry "$Label did not come back within 30s of task restart" "ERROR"
        }
    } catch {
        Write-LogEntry "$Label recovery error: $($_.Exception.Message)" "ERROR"
    }
    # Outbound Telegram is independent of the listener, so this lands even if the
    # listener itself is the thing that is down (inbound control is then lost, but
    # the operator is at least told and can RDP in via host Tailscale).
    Send-TelegramAlert "ALERT $Label is DOWN and auto-restart FAILED (Scheduled Task '$TaskName'). Check the host." -ThrottleKey ("task-" + $Port) -ThrottleHours 1
    return $false
}

function Invoke-HealthCheck {
    Write-LogEntry "Starting comprehensive health check..."

    # Change to project directory
    Set-Location $PROJECT_DIR

    # --- Docker ENGINE liveness FIRST: every check below issues `docker ...` and
    # needs the daemon. If it is down (a compaction stranded it, or a crash), try
    # to restart it autonomously and alert out-of-band. Then short-circuit: with
    # no daemon there is nothing container-side to check -- but the HOST lifelines
    # (bridges + Telegram listener) DON'T need Docker, so verify them here instead
    # of skipping them via the early return that used to blind this window.
    if (-not (Confirm-DockerEngine)) {
        Write-LogEntry "Docker engine down and not recovered; verifying host lifelines, skipping container checks" "ERROR"
        Confirm-HostTaskByPort -TaskName 'claude-sessions-bridge'     -Port 48291 -Label 'claude-sessions bridge' | Out-Null
        Confirm-HostTaskByPort -TaskName 'sysadmin-bridge'            -Port 48292 -Label 'sysadmin bridge'        | Out-Null
        Confirm-HostTaskByPort -TaskName 'sysadmin-telegram-listener' -Port 48293 -Label 'telegram listener'      | Out-Null
        return $false
    }

    # First, validate entrypoint and detect common issues
    if (-not (Test-EntrypointHealth)) {
        Write-LogEntry "Entrypoint validation failed. Manual intervention required." "ERROR"
        return $false
    }
    
    # Check OpenWebUI health first (critical for GPU container)
    if (-not (Test-ServiceHealth "openwebui")) {
        Write-LogEntry "OpenWebUI (GPU-enabled) is not healthy, waiting for CUDA initialization..." "WARN"
        
        # For GPU containers, we need to wait longer for CUDA to initialize
        $MaxWaitTime = 180  # 3 minutes for GPU initialization
        $WaitTime = 0
        
        while ($WaitTime -lt $MaxWaitTime) {
            Start-Sleep 10
            $WaitTime += 10
            
            if (Test-ServiceHealth "openwebui") {
                Write-LogEntry "OpenWebUI became healthy after ${WaitTime}s (CUDA initialized)" "SUCCESS"
                break
            }
            
            if ($WaitTime % 30 -eq 0) {
                Write-LogEntry "Still waiting for OpenWebUI GPU initialization... (${WaitTime}s/${MaxWaitTime}s)" "INFO"
            }
        }
        
        # Final check after waiting
        if (-not (Test-ServiceHealth "openwebui")) {
            Write-LogEntry "OpenWebUI failed to become healthy within ${MaxWaitTime}s - may need manual intervention" "ERROR"
            return $false
        }
    }
    
    # Check if Tailscale container is running
    if (-not (Test-ServiceHealth "tailscale")) {
        Write-LogEntry "Tailscale container not running, starting..." "WARN"
        docker compose -f frontend\docker-compose.yml --env-file .env up -d tailscale | Out-Null
        
        # Wait longer for GPU container dependencies
        Start-Sleep 45  # Increased from 30s for GPU container startup
        
        # Verify Tailscale started and can attach to OpenWebUI network namespace
        if (-not (Test-ServiceHealth "tailscale")) {
            Write-LogEntry "Tailscale failed to start properly, may need OpenWebUI restart" "WARN"
            return $false
        }
    }
    
    # Test network connectivity
    if (-not (Test-NetworkConnectivity)) {
        Write-LogEntry "Network connectivity failed, attempting recovery..." "WARN"
        if (-not (Repair-TailscaleService)) {
            Write-LogEntry "Failed to restore network connectivity" "ERROR"
            return $false
        }
    }
    
    # Test Tailscale connection
    if (-not (Test-TailscaleConnection)) {
        Write-LogEntry "Tailscale connection failed, attempting recovery..." "WARN"
        if (-not (Repair-TailscaleService)) {
            Write-LogEntry "Failed to restore Tailscale connection" "ERROR"
            return $false
        }
    }
    
    # HOST tailscale node (operator remote access) -- independent of the
    # container node checked above; non-fatal but repairs + logs loudly.
    Test-HostTailscaleBackend | Out-Null

    # Test serve configuration. Additive repair: re-add only missing
    # mappings (never `serve reset`, which would wipe working ones --
    # including the per-service mappings the old code didn't know about).
    if (-not (Repair-TailscaleServes)) {
        Write-LogEntry "Some tailscale serve mappings could not be restored (see prior WARN/ERROR lines)" "WARN"
        # Non-fatal: openwebui main path may still work even if open_notebook
        # serves are missing; downstream checks (LlamaCpp, OpenTerminal) will
        # exercise their own paths.
    }
    
    # Test llama-cpp connectivity
    if (-not (Test-LlamaCppConnectivity)) {
        Write-LogEntry "llama-cpp connectivity failed, attempting recovery..." "WARN"
        if (-not (Repair-LlamaCppConnectivity)) {
            Write-LogEntry "Failed to restore llama-cpp connectivity" "ERROR"
            return $false
        }
    }

    # Test llama-cpp-embed connectivity independently. The main llama-cpp test
    # above does not exercise the embed endpoint, so a broken embed server can
    # silently degrade RAG and mnemory while the rest of the stack looks fine.
    # Non-fatal: main inference still works without embeddings.
    if (-not (Test-LlamaCppEmbedConnectivity)) {
        Write-LogEntry "llama-cpp-embed connectivity failed, attempting recovery..." "WARN"
        if (-not (Repair-LlamaCppEmbed)) {
            Write-LogEntry "Failed to restore llama-cpp-embed - embedding/RAG/mnemory features may be degraded" "WARN"
        }
    }

    # Test Open Terminal health
    if (-not (Test-OpenTerminalHealth)) {
        Write-LogEntry "Open Terminal is unhealthy, attempting recovery..." "WARN"
        if (-not (Repair-OpenTerminal)) {
            Write-LogEntry "Open Terminal recovery failed - terminal features may be unavailable" "WARN"
            # Non-fatal: don't return $false, system can still operate without open-terminal
        }
    }

    # Verify remaining compose containers (non-critical -- log + attempt recovery
    # but do not fail the overall health check). Order matters: mnemory depends
    # on llama-cpp + llama-cpp-embed, which are confirmed healthy above.
    Confirm-AuxiliaryContainer -ServiceName "mnemory"            -RestartWaitSeconds 20 | Out-Null
    Confirm-AuxiliaryContainer -ServiceName "mnemory-backup"      -RestartWaitSeconds 10 | Out-Null
    Confirm-AuxiliaryContainer -ServiceName "openwebui-backup"    -RestartWaitSeconds 10 | Out-Null
    # surrealdb has no HTTP healthcheck (WS-only); just verify the container is up.
    # open_notebook gets a real API probe below -- surrealdb must be up first since
    # open_notebook depends on it.
    Confirm-AuxiliaryContainer -ServiceName "surrealdb"           -RestartWaitSeconds 10 | Out-Null

    # Test open-notebook API independently (separate from running-state check --
    # the FastAPI process can be unresponsive while the container is still up).
    # Non-fatal: notebook UI is non-critical for the core LLM/RAG path.
    if (-not (Test-OpenNotebookHealth)) {
        Write-LogEntry "open-notebook API failed, attempting recovery..." "WARN"
        if (-not (Repair-OpenNotebook)) {
            Write-LogEntry "open-notebook recovery failed - notebook UI may be unavailable" "WARN"
        }
    }

    # --- Private web-search gateway plane (SearXNG-over-Tor) -- non-critical ---
    # Compose SERVICE keys differ from container names here: service tor ->
    # redis -> search-redis, gateway -> search-gateway (tor retired 2026-08-21). Probe /readyz first (covers the whole plane); only ensure the
    # individual containers if it is not ready.
    if (Test-SearchGatewayHealth) {
        Write-LogEntry "search-gateway /healthz OK" "DEBUG"
        # Deep readiness is informational only (slow/Tor-flaky); never drives a restart.
        if (-not (Get-SearchGatewayReady)) {
            Write-LogEntry "search-gateway up but /readyz not ready (vpn/searxng/redis warming or degraded)" "INFO"
        }
    } else {
        Write-LogEntry "search-gateway /healthz down, ensuring web-search plane containers..." "WARN"
        Confirm-AuxiliaryContainer -ServiceName "redis"   -RestartWaitSeconds 10 | Out-Null
        Confirm-AuxiliaryContainer -ServiceName "searxng" -RestartWaitSeconds 15 | Out-Null
        Confirm-AuxiliaryContainer -ServiceName "gateway" -RestartWaitSeconds 15 | Out-Null
    }

    # --- little-coder plane (autonomous coding agent) -- non-critical ---
    # open-terminal (checked above) is its workspace; these are the agent + its
    # MCP-as-OpenAPI bridge + the egress proxy.
    Confirm-AuxiliaryContainer -ServiceName "little-coder" -RestartWaitSeconds 15 | Out-Null
    Confirm-AuxiliaryContainer -ServiceName "lc-egress"    -RestartWaitSeconds 10 | Out-Null

    # --- mnemory MCP gateway (the bridge clients reach; mnemory itself above) ---
    Confirm-AuxiliaryContainer -ServiceName "mnemory-cloud-gateway" -RestartWaitSeconds 10 | Out-Null

    # --- inference gateway plane (LiteLLM front door + admission queue) ---
    # ALL inference flows through llm-gateway (the llama-cpp:8080 alias) and
    # llm-queue. Test-LlamaCppConnectivity above exercises the data path;
    # these catch the db/UI sidecars the path test can't see.
    Confirm-AuxiliaryContainer -ServiceName "llm-queue"      -RestartWaitSeconds 15 | Out-Null
    Confirm-AuxiliaryContainer -ServiceName "llm-gateway"    -RestartWaitSeconds 20 | Out-Null
    Confirm-AuxiliaryContainer -ServiceName "llm-gateway-db" -RestartWaitSeconds 15 | Out-Null
    Confirm-AuxiliaryContainer -ServiceName "llm-gateway-ui" -RestartWaitSeconds 15 | Out-Null

    # --- remaining main-stack backup sidecars (cron loops; mnemory-backup and
    # openwebui-backup are confirmed above; portal backups (caddy/authelia) are
    # deliberately NOT here -- the portal has its own lifecycle (portal-on/off)
    # and must not be auto-started; OB/agent-org backups live in their own
    # Invoke-*Health blocks. Test-BackupRecency below watches everyone's OUTPUT.
    Confirm-AuxiliaryContainer -ServiceName "little-coder-backup"  -RestartWaitSeconds 10 | Out-Null
    Confirm-AuxiliaryContainer -ServiceName "llm-gateway-backup"   -RestartWaitSeconds 10 | Out-Null
    Confirm-AuxiliaryContainer -ServiceName "lm-models-backup"     -RestartWaitSeconds 10 | Out-Null
    Confirm-AuxiliaryContainer -ServiceName "tailscale-backup"     -RestartWaitSeconds 10 | Out-Null
    Confirm-AuxiliaryContainer -ServiceName "open-notebook-backup" -RestartWaitSeconds 10 | Out-Null

    # --- Open Brain stack (SEPARATE compose project) incl. mcp stale-pool guard ---
    Invoke-OpenBrainHealth

    # --- agent-org stack (SEPARATE compose project) incl. bridge stale-pool +
    #     ao-git-egress stale-mount guards, + its nightly pg_dump backup sidecars ---
    Invoke-AgentOrgHealth

    # --- claude-sessions bridge (Mattermost <-> Claude, HOST Scheduled Task) ---
    # After Invoke-AgentOrgHealth so the Mattermost container it connects to has
    # just been confirmed/repaired. Non-fatal for the overall check.
    Confirm-ClaudeSessionsBridge | Out-Null

    # --- sysadmin persona bridge (#sysadmin, 48292) + out-of-band Telegram command
    # listener (48293), both HOST Scheduled Tasks. Process-liveness + task-restart:
    # the claude-bridge check above already repairs the shared Mattermost host
    # port-forward, and the listener needs no container at all. This closes the gap
    # where nothing watched the sysadmin bridge or the break-glass control channel.
    Confirm-HostTaskByPort -TaskName 'sysadmin-bridge'            -Port 48292 -Label 'sysadmin bridge'   | Out-Null
    Confirm-HostTaskByPort -TaskName 'sysadmin-telegram-listener' -Port 48293 -Label 'telegram listener' | Out-Null

    # --- backup OUTPUT recency (all 14 backups/<dir> trees, incl. portal + OB) ---
    # Non-fatal for the overall check, but logs ERROR + Mattermost-alerts:
    # a running sidecar that produces nothing is invisible to container checks.
    Test-BackupRecency | Out-Null

    Write-LogEntry "All health checks passed" "SUCCESS"
    return $true
}

# Function to run as a daemon
function Start-Daemon {
    Write-LogEntry "Starting Tailscale Health Monitor daemon (interval: ${IntervalSeconds}s)"
    
    while ($true) {
        try {
            Invoke-HealthCheck | Out-Null
            Start-Sleep $IntervalSeconds
        } catch {
            Write-LogEntry "Daemon error: $($_.Exception.Message)" "ERROR"
            Start-Sleep 30
        }
    }
}

# Function to install as a Scheduled Task (issue #36)
# A .ps1 cannot be a Windows Service: it cannot answer the Service Control
# Manager handshake, so the old install-service mode could never start
# (error 1053). The live StackWatchdog registration is a Scheduled Task.
function Install-ScheduledTask {
    # $PSCommandPath, not $MyInvocation.MyCommand (a FunctionInfo inside a function, whose Path is $null): the task would otherwise register with -File '' and never run.
    $TaskAction = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    # Mirrors the live StackWatchdog registration captured 2026-08-23: time trigger, every 10 minutes, indefinite.
    $TaskTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 10)
    $TaskSettings = New-ScheduledTaskSettingsSet
    $TaskPrincipal = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive
    
    # Refuse to clobber: the script never removes an existing task (the live one is a lifeline).
    if (Get-ScheduledTask -TaskName $InstallTaskName -ErrorAction SilentlyContinue) {
        Write-LogEntry "Scheduled task '$InstallTaskName' already exists -- remove it deliberately first, then re-run -Mode install-task" "ERROR"
        exit 1
    }
    
    Register-ScheduledTask -TaskName $InstallTaskName -Action $TaskAction -Trigger $TaskTrigger -Settings $TaskSettings -Principal $TaskPrincipal -Description "ai-stack watchdog (scripts/checks/stack-watchdog.ps1), every 10 minutes" | Out-Null
    Write-LogEntry "Scheduled task '$InstallTaskName' registered (every 10 minutes, current user)" "SUCCESS"
}

# Main execution logic
switch ($Mode.ToLower()) {
    "check" {
        $Success = Invoke-HealthCheck
        exit $(if ($Success) { 0 } else { 1 })
    }
    
    "daemon" {
        Start-Daemon
    }
    
    "install-task" {
        Install-ScheduledTask
    }
    
    "install-service" {
        Write-LogEntry "-Mode install-service is deprecated (a .ps1 cannot run as a Windows Service); registering the Scheduled Task instead -- use -Mode install-task" "WARN"
        Install-ScheduledTask
    }
    
    default {
        Write-Host "Usage: stack-watchdog.ps1 [-Mode check|daemon|install-task|install-service] [-IntervalSeconds 60]"
        Write-Host ""
        Write-Host "Modes:"
        Write-Host "  check           - Run single health check (default)"
        Write-Host "  daemon          - Run continuously as daemon"
        Write-Host "  install-task    - register the StackWatchdog Scheduled Task, every 10 min, as the current user"
        Write-Host "  install-service - deprecated alias for install-task (a .ps1 cannot run as a Windows Service)"
        exit 1
    }
}
