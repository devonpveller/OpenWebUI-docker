# Enhanced Tailscale Health Check and Recovery Service for Windows
# This script provides autonomous management of Tailscale connectivity

[CmdletBinding()]
param(
    [Parameter(Mandatory=$false)]
    [ValidateSet("check", "daemon", "install-service")]
    [string]$Mode = "check",
    
    [Parameter(Mandatory=$false)]
    [ValidateRange(10, 3600)]
    [int]$IntervalSeconds = 60
)

# Set strict error handling
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

# Constants
$SCRIPT_DIR = Split-Path -Parent $PSCommandPath
$PROJECT_DIR = Split-Path -Parent $SCRIPT_DIR
$LOG_FILE = Join-Path $PROJECT_DIR "logs\tailscale-health.log"
$SERVICE_NAME = "TailscaleHealthMonitor"

# --- docker compose stderr guard (added 2026-06-05) ---------------------------
# The caddy service references ${WORKBENCH_KEY} (docker-compose.yml). When that
# variable is absent from THIS process's environment, every `docker compose ...`
# call prints "The \"WORKBENCH_KEY\" variable is not set..." to stderr. Combined
# with $ErrorActionPreference='Stop' above, the first docker call that redirects
# stderr (e.g. `docker compose logs ... 2>$null` in Test-EntrypointHealth) turns
# that benign warning into a TERMINATING error (PS 5.1 native-stderr gotcha) and
# the whole health check aborts at step 1 — the window just flashes and exits 1,
# checking/repairing nothing. Defining the var here silences the warning at the
# source for every docker invocation this script makes. This only
# affects this script's own process env; it does NOT modify .env or any container.
#
# The value is a non-empty PLACEHOLDER, not the real key: Windows cannot store an
# empty env var (PowerShell deletes it on `=''`), and docker only suppresses the
# "is not set" warning for a DEFINED, non-empty value. This monitor never creates
# or recreates the caddy service (the sole consumer of WORKBENCH_KEY — it is not
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
        $Status = docker compose ps $ServiceName --format json | ConvertFrom-Json
        
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
        $null = docker compose exec -T tailscale ping -c 1 8.8.8.8 2>$null
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
        $null = docker compose exec -T tailscale tailscale --socket=/tmp/tailscaled.sock status 2>$null
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
                docker compose exec -T tailscale tailscale --socket=/tmp/tailscaled.sock serve --https=$($m.TailscalePort) --bg "http://127.0.0.1:$($m.LocalPort)" | Out-Null
            } else {
                docker compose exec -T tailscale tailscale --socket=/tmp/tailscaled.sock serve --https=$($m.TailscalePort) --set-path=$($m.TailscalePath) --bg "http://127.0.0.1:$($m.LocalPort)" | Out-Null
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
        # check — that exact misclassification (a docker stderr warning bubbling
        # up under -Stop) is what crashed every run before 2026-06-05.
        try {
            $Logs = docker compose logs tailscale --tail=5 2>$null
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
            docker compose up -d llama-cpp-upstream | Out-Null
            Start-Sleep 30

            if (-not (Test-ServiceHealth "llama-cpp-upstream")) {
                Write-LogEntry "Failed to start llama-cpp-upstream container" "ERROR"
                return $false
            }
        }

        # Also check llama-cpp-embed-upstream
        if (-not (Test-ServiceHealth "llama-cpp-embed-upstream")) {
            Write-LogEntry "llama-cpp-embed-upstream container not running, starting..." "WARN"
            docker compose up -d llama-cpp-embed-upstream | Out-Null
            Start-Sleep 15
        }
        
        # Wait for llama-cpp API to become available
        Write-LogEntry "Waiting for llama-cpp API to become ready..."
        $MaxWaitTime = 120
        $WaitTime = 0
        
        while ($WaitTime -lt $MaxWaitTime) {
            try {
                docker compose exec -T llama-cpp-upstream curl -s -f --max-time 5 http://localhost:8080/health | Out-Null
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
        docker compose stop tailscale | Out-Null
        Start-Sleep 5
        
        # Ensure OpenWebUI is still healthy before restarting Tailscale
        if (-not (Test-ServiceHealth "openwebui")) {
            Write-LogEntry "OpenWebUI became unhealthy during restart, aborting gentle restart" "ERROR"
            return $false
        }
        
        docker compose start tailscale | Out-Null
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
        docker compose down tailscale | Out-Null
        Start-Sleep 5  # Give OpenWebUI time to stabilize
        docker compose up -d tailscale | Out-Null
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
        # open-terminal is the little-coder workspace plane — it left openwebui's
        # network namespace (it is on lc-net / llm-net now), so probe it INSIDE
        # its own container, not via openwebui's localhost:8000.
        $Response = docker compose exec -T open-terminal curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/health 2>$null
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
# Used for mnemory, smolcrawl-pipelines, watchtower, and the backup sidecars —
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
    # Skip the exec probe if the container isn't running — `docker compose exec`
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
        $LlamaCppResponse = docker compose exec -T llama-cpp-upstream curl -s -f --max-time 10 http://localhost:8080/health 2>$null
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
# NOT mean the container is dead — it just means it's busy. We use a two-stage
# probe: try /health quickly; if it stalls, fall back to scanning recent logs
# for active embedding traffic. Docker's healthcheck has the same blind spot
# and frequently marks this container "unhealthy" while it is in fact serving.
function Test-LlamaCppEmbedConnectivity {
    # Stage 0: container must be running. Note we deliberately DO NOT require
    # Health -ne "unhealthy" here (Test-ServiceHealth does), because the docker
    # healthcheck false-positives under load — see comment above.
    $Status = $null
    try {
        $Status = docker compose ps llama-cpp-embed-upstream --format json 2>$null | ConvertFrom-Json
    } catch { }
    if (-not $Status -or $Status.State -ne "running") {
        Write-LogEntry "llama-cpp-embed container is not running" "DEBUG"
        return $false
    }

    # Stage 1: quick /health probe. Short timeout — we don't want to block the
    # monitor for 30 s on every cycle when the server is busy.
    try {
        Write-LogEntry "Testing llama-cpp-embed connectivity..." "DEBUG"
        $EmbedResponse = docker compose exec -T llama-cpp-embed-upstream curl -s -f --max-time 5 http://localhost:8080/health 2>$null
        if ($LASTEXITCODE -eq 0 -and $EmbedResponse) {
            Write-LogEntry "llama-cpp-embed /health OK" "DEBUG"
            return $true
        }
    } catch { }

    # Stage 2: /health didn't answer. Scan recent logs for active embedding
    # traffic — if the server has served an embedding request in the last 2 min
    # it is alive, just blocked on inference. Patterns match llama.cpp server's
    # request-completion lines ("done request: POST /v1/embeddings ... 200")
    # and slot lifecycle markers.
    try {
        $RecentLog = docker compose logs --tail=40 --since=2m llama-cpp-embed-upstream 2>$null | Out-String
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
        docker compose exec -T open_notebook python3 -c "import urllib.request,sys; urllib.request.urlopen('http://localhost:5055/api/config', timeout=5); sys.exit(0)" 2>$null | Out-Null
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
            docker compose up -d surrealdb 2>&1 | Out-Null
            Start-Sleep 10
        }

        if (-not (Test-ServiceHealth "open_notebook")) {
            Write-LogEntry "open_notebook container not running, starting..." "WARN"
            docker compose up -d open_notebook 2>&1 | Out-Null
        } else {
            Write-LogEntry "open_notebook running but API unresponsive, restarting..." "WARN"
            docker compose restart open_notebook 2>&1 | Out-Null
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
# little-coder, mnemory-gateway, and the SEPARATE "open-brain" compose project
# (including the openbrain-mcp stale-DB-pool guard that caused Open WebUI tool
# 500s / "Broken pipe" on 2026-06-05).
# ---------------------------------------------------------------------------

# search-gateway /healthz is fast process liveness (always 200 if the event loop
# is serving). We gate restarts on THIS, not /readyz: /readyz does a deep check
# (SearXNG through the Tor chain) that is slow and Tor-flaky, so it would
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

# Informational only: is the full web-search path actually ready (redis + tor +
# searxng reachable)? Slow/Tor-dependent, so logged but never used to restart.
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
# probe scripts\check-agent-org-health.ps1 — same pattern as Open Brain. It
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
function Invoke-HealthCheck {
    Write-LogEntry "Starting comprehensive health check..."
    
    # Change to project directory
    Set-Location $PROJECT_DIR
    
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
        docker compose up -d tailscale | Out-Null
        
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

    # Verify remaining compose containers (non-critical — log + attempt recovery
    # but do not fail the overall health check). Order matters: mnemory depends
    # on llama-cpp + llama-cpp-embed, which are confirmed healthy above.
    Confirm-AuxiliaryContainer -ServiceName "mnemory"            -RestartWaitSeconds 20 | Out-Null
    Confirm-AuxiliaryContainer -ServiceName "smolcrawl-pipelines" -RestartWaitSeconds 20 | Out-Null
    Confirm-AuxiliaryContainer -ServiceName "watchtower"          -RestartWaitSeconds 10 | Out-Null
    Confirm-AuxiliaryContainer -ServiceName "mnemory-backup"      -RestartWaitSeconds 10 | Out-Null
    Confirm-AuxiliaryContainer -ServiceName "openwebui-backup"    -RestartWaitSeconds 10 | Out-Null
    # surrealdb has no HTTP healthcheck (WS-only); just verify the container is up.
    # open_notebook gets a real API probe below — surrealdb must be up first since
    # open_notebook depends on it.
    Confirm-AuxiliaryContainer -ServiceName "surrealdb"           -RestartWaitSeconds 10 | Out-Null

    # Test open-notebook API independently (separate from running-state check —
    # the FastAPI process can be unresponsive while the container is still up).
    # Non-fatal: notebook UI is non-critical for the core LLM/RAG path.
    if (-not (Test-OpenNotebookHealth)) {
        Write-LogEntry "open-notebook API failed, attempting recovery..." "WARN"
        if (-not (Repair-OpenNotebook)) {
            Write-LogEntry "open-notebook recovery failed - notebook UI may be unavailable" "WARN"
        }
    }

    # --- Private web-search gateway plane (SearXNG-over-Tor) — non-critical ---
    # Compose SERVICE keys differ from container names here: service tor ->
    # search-tor, redis -> search-redis, gateway -> search-gateway, mcpo ->
    # search-mcpo. Probe /readyz first (covers the whole plane); only ensure the
    # individual containers if it is not ready.
    if (Test-SearchGatewayHealth) {
        Write-LogEntry "search-gateway /healthz OK" "DEBUG"
        # Deep readiness is informational only (slow/Tor-flaky); never drives a restart.
        if (-not (Get-SearchGatewayReady)) {
            Write-LogEntry "search-gateway up but /readyz not ready (tor/searxng/redis warming or degraded)" "INFO"
        }
    } else {
        Write-LogEntry "search-gateway /healthz down, ensuring web-search plane containers..." "WARN"
        Confirm-AuxiliaryContainer -ServiceName "tor"     -RestartWaitSeconds 15 | Out-Null
        Confirm-AuxiliaryContainer -ServiceName "redis"   -RestartWaitSeconds 10 | Out-Null
        Confirm-AuxiliaryContainer -ServiceName "searxng" -RestartWaitSeconds 15 | Out-Null
        Confirm-AuxiliaryContainer -ServiceName "gateway" -RestartWaitSeconds 15 | Out-Null
        Confirm-AuxiliaryContainer -ServiceName "mcpo"    -RestartWaitSeconds 10 | Out-Null
    }

    # --- little-coder plane (autonomous coding agent) — non-critical ---
    # open-terminal (checked above) is its workspace; these are the agent + its
    # MCP-as-OpenAPI bridge + the egress proxy.
    Confirm-AuxiliaryContainer -ServiceName "little-coder" -RestartWaitSeconds 15 | Out-Null
    Confirm-AuxiliaryContainer -ServiceName "lc-mcpo"      -RestartWaitSeconds 10 | Out-Null
    Confirm-AuxiliaryContainer -ServiceName "lc-egress"    -RestartWaitSeconds 10 | Out-Null

    # --- mnemory MCP gateway (the bridge clients reach; mnemory itself above) ---
    Confirm-AuxiliaryContainer -ServiceName "mnemory-gateway" -RestartWaitSeconds 10 | Out-Null

    # --- Open Brain stack (SEPARATE compose project) incl. mcp stale-pool guard ---
    Invoke-OpenBrainHealth

    # --- agent-org stack (SEPARATE compose project) incl. bridge stale-pool +
    #     ao-git-egress stale-mount guards, + its nightly pg_dump backup sidecars ---
    Invoke-AgentOrgHealth

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

# Function to install as Windows Service
function Install-WindowsService {
    $ServicePath = "powershell.exe -File `"$($MyInvocation.MyCommand.Path)`" -Mode daemon"
    
    # Check if service already exists
    if (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue) {
        Write-LogEntry "Service $ServiceName already exists. Removing first..."
        Stop-Service -Name $ServiceName -Force
        sc.exe delete $ServiceName
        Start-Sleep 5
    }
    
    # Create the service
    Write-LogEntry "Installing Windows Service: $ServiceName"
    sc.exe create $ServiceName binPath= $ServicePath start= auto
    sc.exe description $ServiceName "Autonomous Tailscale Health Monitor for AI Stack"
    
    # Start the service
    Start-Service -Name $ServiceName
    Write-LogEntry "Service installed and started successfully"
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
    
    "install-service" {
        if (-not ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole] "Administrator")) {
            Write-LogEntry "Administrator privileges required to install service" "ERROR"
            exit 1
        }
        Install-WindowsService
    }
    
    default {
        Write-Host "Usage: check-tailscale-health.ps1 [-Mode check|daemon|install-service] [-IntervalSeconds 60]"
        Write-Host ""
        Write-Host "Modes:"
        Write-Host "  check           - Run single health check (default)"
        Write-Host "  daemon          - Run continuously as daemon"
        Write-Host "  install-service - Install as Windows Service (requires admin)"
        exit 1
    }
}
