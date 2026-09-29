# Enhanced Tailscale Health Check and Recovery Service for Windows
# This script provides autonomous management of Tailscale connectivity

[CmdletBinding()]
param(
    [Parameter(Mandatory=$false)]
    [ValidateSet("check", "loops", "daemon", "install-service")]
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
$PROJECT_DIR = Split-Path -Parent (Split-Path -Parent $PSCommandPath) | Split-Path -Parent
$LOG_FILE = Join-Path $PROJECT_DIR "logs\tailscale-health.log"
$SERVICE_NAME = "TailscaleHealthMonitor"

# --- docker compose stderr guard (added 2026-06-05) ---------------------------
# The caddy service references ${WORKBENCH_KEY} (docker-compose.yml). When that
# variable is absent from THIS process's environment, every `docker compose ...`
# call prints "The \"WORKBENCH_KEY\" variable is not set..." to stderr. Combined
# with $ErrorActionPreference='Stop' above, the first docker call that redirects
# stderr (e.g. `docker compose logs ... 2>$null` in Test-EntrypointHealth) turns
# that benign warning into a TERMINATING error (PS 5.1 native-stderr gotcha) and
# the whole health check aborts at step 1 - the window just flashes and exits 1,
# checking/repairing nothing. Defining the var here silences the warning at the
# source for every docker invocation this script makes. This only
# affects this script's own process env; it does NOT modify .env or any container.
#
# The value is a non-empty PLACEHOLDER, not the real key: Windows cannot store an
# empty env var (PowerShell deletes it on `=''`), and docker only suppresses the
# "is not set" warning for a DEFINED, non-empty value. This monitor never creates
# or recreates the caddy service (the sole consumer of WORKBENCH_KEY - it is not
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

# --- repair ROUTING: which compose project actually owns this container? -----
# (2026-08-28) Part K split the stack into per-plane compose PROJECTS and left
# the root `ai-stack` project a PURE NETWORK ANCHOR with ZERO services
# (`docker compose config --services` at the repo root prints nothing). The
# DETECTION half of this monitor was migrated at K.10 - Test-ServiceHealth below
# does a name-based `docker inspect` - but the REMEDIATION half was not: every
# repair still issued a bare `docker compose up -d <name>`, which resolves to the
# anchor and exits 1 with "no such service".
#
# That failure was SILENT for three compounding reasons, all verified 2026-08-28:
#   1. the command targets a project that cannot see the container, so nothing
#      is started;
#   2. an UN-REDIRECTED native stderr write does NOT throw under
#      $ErrorActionPreference = "Stop" in PS 5.1, so the surrounding catch block
#      never fired; and
#   3. the old code piped stdout to Out-Null and never read $LASTEXITCODE, so
#      the log only ever said "recovery failed" with no hint that the repair
#      command had not run at all.
# 22 self-heal paths were dead this way between 2026-08-21 and 2026-08-28.
#
# The container -> project mapping is NOT duplicated here. scripts\lib\stack-services.json
# is the canonical inventory and its (container -> project) rows are machine-verified
# against the rendered compose configs by the pre-commit check
# scripts\checks\check-project-configs.ps1, so it cannot drift silently. A container
# missing from it fails LOUDLY (an ERROR naming the container) instead of no-op'ing.
$Script:RepairTargets = $null

function Get-RepairTargetMap {
    [CmdletBinding()]
    param()
    if ($null -ne $Script:RepairTargets) { return $Script:RepairTargets }

    $Map = @{}
    $InvPath = Join-Path $PROJECT_DIR 'scripts\lib\stack-services.json'
    try {
        $Inv = Get-Content -Path $InvPath -Raw -ErrorAction Stop | ConvertFrom-Json
    } catch {
        Write-LogEntry "Service inventory unreadable ($InvPath): $($_.Exception.Message) - container repairs cannot be routed" "ERROR"
        $Script:RepairTargets = $Map
        return $Map
    }

    $Projects = @{}
    foreach ($Prop in $Inv.projects.PSObject.Properties) { $Projects[$Prop.Name] = $Prop.Value }

    foreach ($Plane in $Inv.planes.PSObject.Properties) {
        foreach ($Row in $Plane.Value) {
            if (-not $Row.container -or -not $Row.project) { continue }
            if (-not $Projects.ContainsKey($Row.project)) { continue }
            $Proj = $Projects[$Row.project]
            # file = null marks a project that owns no services (the anchor).
            # Such a project can never start anything, so it is not a repair target.
            if (-not $Proj.file) { continue }

            # Absolute paths: this function must not depend on the caller's CWD.
            # Invoke-HealthCheck does Set-Location $PROJECT_DIR, but a repair
            # helper that only works from one directory is a trap for the next
            # caller.
            $ComposeArgs = @('-f', (Join-Path $PROJECT_DIR ($Proj.file.Replace('/', [string][char]92))))
            if ($Proj.env_file) {
                $ComposeArgs += @('--env-file', (Join-Path $PROJECT_DIR ($Proj.env_file.Replace('/', [string][char]92))))
            }
            # 'service' is present only where the compose SERVICE key differs
            # from the container name (search: redis -> search-redis,
            # gateway -> search-gateway). `docker compose` wants the service key.
            $Service = if ($Row.service) { [string]$Row.service } else { [string]$Row.container }

            $Map[[string]$Row.container] = [pscustomobject]@{
                Container   = [string]$Row.container
                Project     = [string]$Row.project
                Service     = $Service
                ComposeArgs = $ComposeArgs
            }
        }
    }
    $Script:RepairTargets = $Map
    return $Map
}

# Run a compose verb against the project that OWNS $Container, by CONTAINER name.
# $Action is the verb plus its flags, e.g. @('up','-d') or @('restart'); the
# resolved service key is appended. Returns $true only when docker exits 0.
function Invoke-PlaneCompose {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$Container,
        [Parameter(Mandatory)][string[]]$Action
    )
    $Target = (Get-RepairTargetMap)[$Container]
    if (-not $Target) {
        Write-LogEntry ("Cannot repair '{0}': no compose project owns it in scripts\lib\stack-services.json. Add the row (documentation\runbooks\SERVICE-LIFECYCLE.md step 8) - a repair cannot be routed without it." -f $Container) "ERROR"
        return $false
    }

    $Argv = @('compose') + $Target.ComposeArgs + $Action + @($Target.Service)
    # stderr is deliberately NOT redirected: under $ErrorActionPreference="Stop"
    # a REDIRECTED native stderr write becomes a terminating NativeCommandError
    # in PS 5.1. $LASTEXITCODE is the reliable signal, and reading it is exactly
    # what the pre-2026-08-28 code failed to do.
    & docker @Argv | Out-Null
    $Code = $LASTEXITCODE

    if ($Code -ne 0) {
        Write-LogEntry ("compose '{0}' FAILED for '{1}' (project {2}, service {3}) - exit {4}: docker {5}" -f `
            ($Action -join ' '), $Container, $Target.Project, $Target.Service, $Code, ($Argv -join ' ')) "ERROR"
        return $false
    }
    Write-LogEntry ("compose '{0}' issued for '{1}' (project {2}, service {3})" -f `
        ($Action -join ' '), $Container, $Target.Project, $Target.Service) "DEBUG"
    return $true
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

function Test-TailscaleDeployed {
    [CmdletBinding()]
    param()
    # Is the `tailscale` profile part of THIS deployment? The frontend plane is
    # profile-gated since 2026-09-19 (stack-layers 2.5 / D8): `stock` is Open
    # WebUI alone, `gpu,tailscale` is this host. A deployment without the
    # tailscale profile has no tailscale container, and repairing a container
    # that is not meant to exist is its own kind of outage.
    #
    # WHICH SOURCE: the RENDERED project (`config --services`), not a parse of
    # frontend/.env. That is compose's own answer after applying COMPOSE_PROFILES
    # from --env-file, from the process environment, and its own precedence
    # rules; reimplementing that here would drift the moment any of them moves.
    #
    # IT FAILS OPEN in BOTH unclear cases, deliberately:
    #   - the render cannot be read (docker down, bad env file) -> assume
    #     deployed, because a checker that goes quiet on its own error is the
    #     failure mode this stack keeps paying for;
    #   - the render says no tailscale while a container NAMED tailscale is
    #     RUNNING -> that is this host with COMPOSE_PROFILES missing from frontend/.env.
    #     Keep checking and log why, rather than silently dropping the tailnet
    #     checks that exist because a 94-minute outage went unnoticed.
    # Cached PER CYCLE, not for the life of the process: the answer cannot
    # change inside one cycle, but it certainly can between them - the operator
    # edits frontend/.env precisely to fix this - and daemon mode runs weeks. The
    # cache is cleared at the top of Invoke-HealthCheck; re-probing costs one
    # `compose config` (~550 ms measured), cheap once a minute and silly
    # several times inside one cycle.
    if ($null -ne $script:TailscaleDeployedCache) { return $script:TailscaleDeployedCache }
    $deployed = $true
    try {
        # cmd /c so compose's stderr warnings cannot become PS 5.1
        # NativeCommandErrors under this script's EAP=Stop.
        $svc = (cmd /c "docker compose -f frontend\docker-compose.yml config --services 2>nul") -join "`n"
        if (-not $svc) {
            # A render ERROR is not an answer, and must never pass for one. The
            # trigger is real: the compose file carries a fail-loud
            # WEBUI_SECRET_KEY guard, so a frontend/.env missing or lacking that
            # key renders NOTHING. Say so, and check as usual.
            Write-LogEntry "The frontend plane rendered NOTHING (docker down, or frontend/.env missing/incomplete - the compose file's WEBUI_SECRET_KEY guard hard-fails the render) - cannot tell whether tailscale is deployed, so treating it as DEPLOYED and keeping the tailnet checks ON" "WARN"
        }
        elseif ($svc -notmatch '(?m)^tailscale\s*$') {
            # Substring filter + an exact-match pass: `--filter name=^tailscale$`
            # cannot survive cmd /c (cmd eats the `^`, so stt-tts-tailscale
            # matches too - verified 2026-09-19).
            $live = @(@(cmd /c "docker ps --filter name=tailscale --format {{.Names}} 2>nul") | Where-Object { $_ -eq 'tailscale' })
            if ($live.Count -gt 0) {
                Write-LogEntry "tailscale is RUNNING but absent from the frontend render - frontend\.env is probably missing the frontend profiles from COMPOSE_PROFILES (per-plane since sl-env-split; the whole correct value there is gpu,tailscale, and inference\.env carries local separately). If frontend\.env is absent, this host has not been migrated: documentation\runbooks\env-split-migration.md. Keeping the tailnet checks ON" "WARN"
            } else {
                $deployed = $false
            }
        }
    }
    catch {
        Write-LogEntry "Could not read the frontend render to decide whether tailscale is deployed ($($_.Exception.Message)) - assuming it IS, and checking as usual" "WARN"
    }
    $script:TailscaleDeployedCache = $deployed
    return $deployed
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
        $EntrypointPath = Join-Path $PROJECT_DIR "frontend\entrypoint.sh"
        if (Test-Path $EntrypointPath) {
            $Content = Get-Content $EntrypointPath -Raw
            if ($Content -match "`r`n") {
                Write-LogEntry "WARNING: entrypoint.sh has Windows line endings (CRLF). This can cause container startup failures." "WARN"
                Write-LogEntry "Run: (Get-Content .\frontend\entrypoint.sh -Raw) -replace '`r`n', '`n' | Set-Content .\frontend\entrypoint.sh -NoNewline" "INFO"
                return $false
            }
        }
        
        # Check for common Docker build issues in logs. Isolated in its own
        # try/catch: a failure to READ the logs (docker stderr, daemon hiccup)
        # must NOT be misread as "entrypoint invalid" and abort the whole health
        # check - that exact misclassification (a docker stderr warning bubbling
        # up under -Stop) is what crashed every run before 2026-06-05.
        try {
            # cmd /c merges the streams BEFORE PowerShell sees them - the tailscale
            # container logs to stderr, which PS 5.1 would otherwise wrap into a
            # NativeCommandError (the WARN the operator saw 2026-08-22).
            $Logs = cmd /c "docker logs tailscale --tail 5 2>&1" | Out-String
            if ($Logs -match "no such file or directory" -and $Logs -match "entrypoint") {
                Write-LogEntry "CRITICAL: Entrypoint script not found in container. Rebuild required." "ERROR"
                Write-LogEntry "Run: docker compose -f frontend\docker-compose.yml build --no-cache tailscale" "INFO"
                # (the plane file is REQUIRED: tailscale lives in the frontend
                #  project, and a bare `docker compose build` would hit the root
                #  anchor, which declares no services - the operator would be
                #  handed a command that cannot work, on the one CRITICAL path
                #  where they most need it to.)
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
            docker compose -f inference\docker-compose.yml up -d llama-cpp-upstream | Out-Null
            Start-Sleep 30

            if (-not (Test-ServiceHealth "llama-cpp-upstream")) {
                Write-LogEntry "Failed to start llama-cpp-upstream container" "ERROR"
                return $false
            }
        }

        # Also check llama-cpp-embed-upstream
        if (-not (Test-ServiceHealth "llama-cpp-embed-upstream")) {
            Write-LogEntry "llama-cpp-embed-upstream container not running, starting..." "WARN"
            docker compose -f inference\docker-compose.yml up -d llama-cpp-embed-upstream | Out-Null
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
        docker compose -f frontend\docker-compose.yml stop tailscale | Out-Null
        Start-Sleep 5
        
        # Ensure OpenWebUI is still healthy before restarting Tailscale
        if (-not (Test-ServiceHealth "openwebui")) {
            Write-LogEntry "OpenWebUI became unhealthy during restart, aborting gentle restart" "ERROR"
            return $false
        }
        
        docker compose -f frontend\docker-compose.yml start tailscale | Out-Null
        Start-Sleep 45  # Increased wait time for GPU container dependencies
        
        # Verify gentle restart worked
        if ((Test-NetworkConnectivity) -and (Test-TailscaleConnection)) {
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
        docker compose -f frontend\docker-compose.yml stop tailscale | Out-Null
        docker compose -f frontend\docker-compose.yml rm -f tailscale | Out-Null
        Start-Sleep 5  # Give OpenWebUI time to stabilize
        docker compose -f frontend\docker-compose.yml up -d tailscale | Out-Null
        Start-Sleep 60  # Increased wait for GPU container + network namespace reattachment
        
        # Final verification
        if ((Test-NetworkConnectivity) -and (Test-TailscaleConnection)) {
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
        # open-terminal is the little-coder workspace plane - it left openwebui's
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
        if (-not (Invoke-PlaneCompose -Container 'open-terminal' -Action @('up','-d'))) {
            Write-LogEntry "Open Terminal recovery could not be ATTEMPTED (see the ERROR above) - not waiting on a command that never ran" "ERROR"
            return $false
        }
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
# Used for mnemory and the backup sidecars -
# none are required for the core OpenWebUI/Tailscale/LLM path, so failures
# are logged but do not fail the overall health check.
#
# THE PARAMETER IS A CONTAINER NAME, NOT A COMPOSE SERVICE KEY (renamed
# 2026-08-28). It was called -ServiceName while Test-ServiceHealth looked it up
# with `docker inspect`, which takes CONTAINER names - and the misnomer did real
# damage: two call sites passed the search plane's compose SERVICE keys, `redis`
# and `gateway`, whose containers are named search-redis and search-gateway.
# `docker inspect redis` returns "no such object", so those two could never read
# healthy and their repair could never have worked. Where the two differ, the
# service key comes from stack-services.json via Invoke-PlaneCompose.
function Confirm-AuxiliaryContainer {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$Container,
        [int]$RestartWaitSeconds = 15
    )
    if (Test-ServiceHealth $Container) {
        Write-LogEntry "$Container container healthy" "DEBUG"
        return $true
    }
    Write-LogEntry "$Container is unhealthy or stopped, attempting recovery..." "WARN"
    try {
        if (-not (Invoke-PlaneCompose -Container $Container -Action @('up','-d'))) {
            Write-LogEntry "$Container recovery could not be ATTEMPTED (see the ERROR above) - not waiting on a command that never ran" "WARN"
            return $false
        }
        Start-Sleep $RestartWaitSeconds
        if (Test-ServiceHealth $Container) {
            Write-LogEntry "$Container recovered successfully" "SUCCESS"
            return $true
        }
        Write-LogEntry "$Container recovery did not converge - feature may be degraded" "WARN"
        return $false
    } catch {
        Write-LogEntry "$Container recovery error: $($_.Exception.Message)" "WARN"
        return $false
    }
}

# Function to test llama-cpp connectivity
function Test-LlamaCppConnectivity {
    # Skip the exec probe if the container isn't running - `docker compose exec`
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

# --- Inference serving depth: /health answers with an EMPTY model store -----
# The 2026-09-19 outage in one sentence: llama-cpp-upstream was recreated with
# LM_MODELS_DIR unset, compose bound its default `../../data/models/gguf` (an
# empty directory) at /models, llama-swap kept answering /health because it does
# not load a model to do so, and the FIRST REAL COMPLETION 500'd. Nothing in this
# watchdog could see it. This counts .gguf files in the mount and names the HOST
# path when there are none, because the host path is the thing an operator fixes.
#
# Deliberately NOT a completion: this cycle runs every few minutes and a cold
# load is minutes long (257 s measured 2026-09-21). The completion belongs to
# `python scripts/stack/stack.py health`, which an operator runs once after a
# recreate. Here we only need the condition that was invisible.
#
# Read-only, and it targets the upstream directly - which CLAUDE.md permits for
# exactly this class ("only health/GPU/recovery probes may target *-upstream").
function Test-InferenceServingDepth {
    [CmdletBinding()]
    param()
    if (-not (Test-ServiceHealth "llama-cpp-upstream")) {
        Write-LogEntry "serving depth: llama-cpp-upstream is not running - skipping the model-store check" "DEBUG"
        return $true
    }
    try {
        $count = docker exec llama-cpp-upstream sh -c "find /models -maxdepth 4 -name '*.gguf' 2>/dev/null | head -n 5 | wc -l" 2>$null
        $found = 0
        if ($count) { [void][int]::TryParse(($count | Select-Object -Last 1).ToString().Trim(), [ref]$found) }
        if ($found -gt 0) {
            Write-LogEntry "serving depth OK - llama-cpp-upstream's /models holds GGUF files" "DEBUG"
            Resolve-Catastrophe -Key 'inference-models' -Message "llama-cpp-upstream's /models has models again."
            return $true
        }
        $bind = docker inspect llama-cpp-upstream --format '{{range .Mounts}}{{if eq .Destination "/models"}}{{.Source}}{{end}}{{end}}' 2>$null
        if (-not $bind) { $bind = '<no /models mount>' }
        Write-LogEntry "INFERENCE MODELS EMPTY - llama-cpp-upstream's /models has no .gguf files (host bind: $bind). /health still answers; the next real completion will 500. Set LM_MODELS_DIR in inference\.env and recreate llama-cpp-upstream + lm-models-backup." "ERROR"
        Send-CatastropheAlert -Key 'inference-models' -Message "INFERENCE cannot serve: llama-cpp-upstream's /models is EMPTY (host bind: $bind). Health checks pass and every chat will 500. Fix LM_MODELS_DIR in inference/.env and recreate the upstream."
        return $false
    }
    catch {
        Write-LogEntry "serving-depth check failed to run: $($_.Exception.Message)" "WARN"
        return $true
    }
}

# Function to test llama-cpp-embed connectivity (independent of main llama-cpp).
# Embed has its own model/process and can fail while main llama-cpp is healthy.
#
# IMPORTANT: llama.cpp server's HTTP handler stalls /health and /v1/models while
# embedding requests are in flight (verified: /health times out at 30s, but
# /v1/embeddings keeps returning 200 in the logs). So a /health timeout does
# NOT mean the container is dead - it just means it's busy. We use a two-stage
# probe: try /health quickly; if it stalls, fall back to scanning recent logs
# for active embedding traffic. Docker's healthcheck has the same blind spot
# and frequently marks this container "unhealthy" while it is in fact serving.
function Test-LlamaCppEmbedConnectivity {
    # Stage 0: container must be running. Note we deliberately DO NOT require
    # Health -ne "unhealthy" here (Test-ServiceHealth does), because the docker
    # healthcheck false-positives under load - see comment above.
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

    # Stage 1: quick /health probe. Short timeout - we don't want to block the
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
    # traffic - if the server has served an embedding request in the last 2 min
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
            Invoke-PlaneCompose -Container 'llama-cpp-embed-upstream' -Action @('up','-d') | Out-Null
        } else {
            Write-LogEntry "llama-cpp-embed-upstream running but unresponsive, restarting..." "WARN"
            Invoke-PlaneCompose -Container 'llama-cpp-embed-upstream' -Action @('restart') | Out-Null
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
# probe scripts\check-agent-org-health.ps1 - same pattern as Open Brain. It
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
# tray app was not running and unattended mode was not yet enabled) - the
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
            Resolve-Catastrophe -Key 'mattermost' -Message "Mattermost is reachable again."
            return $true
        }
        Write-LogEntry "claude-sessions bridge process is alive but its Mattermost endpoint $CLAUDE_BRIDGE_MM_URL is unreachable -- bridge is deaf (not registering chats)" "WARN"
        if (Repair-ClaudeBridgeMattermostForward) {
            Remove-Item $mmSentinel -Force -ErrorAction SilentlyContinue
            return $true
        }
        # CATASTROPHE: Mattermost IS the normal channel, so an alert about it
        # cannot be delivered through it. This is the definitive out-of-band case.
        Send-CatastropheAlert -Key 'mattermost' -Message "MATTERMOST is unreachable at $CLAUDE_BRIDGE_MM_URL and auto-repair failed - the normal channel is DOWN, which is why this is reaching you here. @bot-claude and @bot-sysadmin cannot respond. Reply 'mm' to bring it up, or 'status'."
        $script:HealthIssues += 'mattermost'

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
#
# Since 2026-09-21 a stale row also carries the REASON, when the sidecar wrote
# one down. backup/generic-tar-backup.sh prints `<PREFIX> PRECHECK SKIP: <why>`
# and exits 0, and until now that sentence lived only in `docker logs` - so
# lm-models read as "350h old" for two days when its own log said `/data is
# empty`, and the age sent the reader hunting for a dead cron instead of a
# wrong mount. Get-BackupSkipReason reads it back out.
#
# NOTE (measured 2026-09-21, not fixed here): this list is THIRTEEN dirs and
# scripts/sysadmin-mcp/check_backups.py's is FIFTEEN - the two ao-worker journal
# sidecars are watched there and not here. Adding them here would also add a
# false STALE whenever the agent-org `workers` profile is down, because this
# list has no container gate the way check_backups.py does; closing that
# properly means gating per row, which is its own change.
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
# The sidecar's own explanation, read back out of its log. Returns '' when it
# has not declined, or when the decline has already been superseded by a later
# line - both live ao-worker journal sidecars carry a first-boot `/data is empty`
# in the same tail as last night's successful tar, and reporting on the marker
# alone would call two healthy sidecars broken.
# The min-age guard's `<PREFIX> SKIP:` (no PRECHECK) is deliberately not matched:
# it means a fresh artifact already exists.
function Get-BackupSkipReason {
    [CmdletBinding()]
    param([string]$Container)
    try {
        $log = cmd /c "docker logs --tail 60 $Container 2>&1" | Out-String
        if (-not $log) { return '' }
        $skipTs = $null; $skipWhy = ''; $newestTs = $null
        foreach ($line in ($log -split "`r?`n")) {
            if ($line -match '^\[(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)\]') {
                $ts = $matches[1]
                if (-not $newestTs -or $ts -gt $newestTs) { $newestTs = $ts }
                if ($line -match 'PRECHECK SKIP:\s*(.+?)\s*$') { $skipTs = $ts; $skipWhy = $matches[1] }
            }
        }
        if (-not $skipWhy) { return '' }
        # Superseded by a later line from the same container (ISO-8601 Z strings
        # sort lexically, which is why they are compared as strings).
        if ($newestTs -and $skipTs -and ($skipTs -lt $newestTs)) { return '' }
        return $skipWhy
    }
    catch { return '' }
}

function Test-BackupRecency {
    [CmdletBinding()]
    param()
    $stale = @()
    foreach ($exp in $ExpectedBackupRecency) {
        $dir = Join-Path $PROJECT_DIR "backups\$($exp.Dir)"
        # The reason, if the sidecar left one. Sidecar name = <dir>-backup for
        # every row in this list.
        $why = Get-BackupSkipReason -Container "$($exp.Dir)-backup"
        $because = if ($why) { " -- the sidecar declined its last run: PRECHECK SKIP: $why" } else { '' }
        # An empty or absent DATA_DIR is the wrong-mount signature and does not
        # heal on its own, so it is STALE on its own terms - even while the last
        # artifact from before the mount moved still looks fresh. That is the
        # lm-models shape exactly, and an age-only check cannot see it.
        if ($why -match '^(\S+) (is empty|does not exist)$') {
            $stale += "$($exp.Dir): MOUNT $($matches[1]) is empty or absent inside $($exp.Dir)-backup - it is producing nothing (PRECHECK SKIP: $why)"
            continue
        }
        if (-not (Test-Path $dir)) {
            $stale += "$($exp.Dir): backup dir missing$because"
            continue
        }
        $newest = Get-ChildItem $dir -File -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -notlike '*.sha256' } |
            Sort-Object LastWriteTime -Descending | Select-Object -First 1
        if (-not $newest) {
            $stale += "$($exp.Dir): no artifacts at all$because"
            continue
        }
        $ageH = [math]::Round(((Get-Date) - $newest.LastWriteTime).TotalHours, 1)
        if ($ageH -gt $exp.MaxAgeHours) {
            $stale += "$($exp.Dir): newest artifact $($newest.Name) is ${ageH}h old (max $($exp.MaxAgeHours)h)$because"
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

# --- CATASTROPHE tier: alerts that must reach the operator OFF-STACK ----------
# Added 2026-09-16 after a 94-minute tailnet outage (expired tailscale node key)
# produced nothing but WARN lines in this log: Send-TelegramAlert was wired ONLY
# to a full Docker-down, so a single dead container - even one carrying EVERY
# remote route, Mattermost included - alerted nowhere.
#
# Tier (operator decision 2026-09-16): total loss of REMOTE ACCESS, of the COMMS
# CHANNEL itself, or of INFERENCE - and only AFTER an automatic repair has
# already FAILED, so a self-healed blip stays quiet. Re-alerts hourly while the
# fault persists (Send-TelegramAlert throttle); Resolve-Catastrophe sends the
# all-clear and re-arms the key.
function Send-CatastropheAlert {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$Key,
        [Parameter(Mandatory)][string]$Message
    )
    Write-LogEntry "CATASTROPHE [$Key] $Message" "ERROR"
    # Decide HERE whether this send actually goes out, rather than letting
    # Send-TelegramAlert decide silently: the "firing" marker must be written
    # only when a message really left, or a flapping fault re-arms the all-clear
    # every cycle and pages the operator 6x/hour (found in review 2026-09-16).
    $sentinel = Join-Path $PROJECT_DIR "logs\.tg-alert-$Key"
    $willSend = $true
    try {
        if (Test-Path $sentinel) {
            if (((Get-Date) - (Get-Item $sentinel).LastWriteTime).TotalHours -lt 1) { $willSend = $false }
        }
    } catch { }
    if ($willSend) {
        Send-TelegramAlert ("ALERT ai-stack: " + $Message) -ThrottleKey $Key -ThrottleHours 1
        try { 'firing' | Out-File (Join-Path $PROJECT_DIR "logs\.tg-state-$Key") -Encoding ascii -Force } catch { }
    } else {
        Write-LogEntry "CATASTROPHE [$Key] suppressed by the 1h throttle (still firing)" "DEBUG"
    }
    # Mirror into Mattermost as well, best-effort. When Mattermost IS the outage
    # this no-ops - which is precisely why Telegram is the primary path here.
    try {
        $bash = 'C:\Program Files\Git\bin\bash.exe'
        if (Test-Path $bash) {
            $scriptPath = ($PROJECT_DIR -replace '\\', '/') + '/scripts/notify-mattermost.sh'
            $null | & $bash $scriptPath "ALERT $Message" 2>$null | Out-Null
        }
    } catch { }
}

# Clears a catastrophe key and pings the all-clear, but ONLY if that key was
# actually firing - so a healthy stack stays silent instead of sending an
# "all good" every cycle.
function Resolve-Catastrophe {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$Key,
        [Parameter(Mandatory)][string]$Message
    )
    try {
        $state = Join-Path $PROJECT_DIR "logs\.tg-state-$Key"
        if (Test-Path $state) {
            Remove-Item $state -Force -ErrorAction SilentlyContinue
            Send-TelegramAlert ("RESOLVED ai-stack: " + $Message)
            Write-LogEntry "CATASTROPHE RESOLVED [$Key] $Message" "SUCCESS"
        }
        # DELIBERATELY does not delete logs\.tg-alert-$Key. That sentinel is the
        # 1h throttle floor; deleting it here re-armed the alert instantly, so a
        # service flapping fail/recover each 10-minute cycle sent ALERT+RESOLVED
        # pairs indefinitely. Leaving it caps each key at one ALERT + one RESOLVED
        # per hour. Trade-off, accepted 2026-09-16: a genuinely NEW outage of the
        # same key inside that hour is logged (and visible in WITH ISSUES) but not
        # re-paged.
    } catch { }
}

# Running vs running-but-unhealthy. Test-ServiceHealth collapses BOTH to $false,
# which is how the 2026-09-16 outage logged "Tailscale container not running"
# about a container that was running the whole time - and then "fixed" it with
# `compose up -d`, a NO-OP on an already-running container with unchanged
# config. The distinction decides start vs recreate.
function Get-ContainerState {
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$Container)
    $out = [pscustomobject]@{ Exists = $false; Running = $false; State = 'missing'; Health = 'none' }
    try {
        $json = docker inspect $Container --format '{{json .State}}' 2>$null
        if (-not $json) { return $out }
        $st = $json | ConvertFrom-Json
        $out.Exists  = $true
        $out.State   = $st.Status
        $out.Running = ($st.Status -eq "running")
        if ($st.Health) { $out.Health = $st.Health.Status }
    } catch { }
    return $out
}

# Tailnet NODE state - login + node-key expiry.
# Nothing in this script looked at login state before 2026-09-16, so an expired
# node key was invisible: the container stayed up, tailscaled stayed alive, and
# every `tailscale serve` failed with "Logged out." The ExpiresInDays field is
# the PREVENTIVE half - node keys expire on a schedule, so this gives notice
# instead of an outage.
function Test-TailscaleNodeState {
    [CmdletBinding()]
    param()
    $out = [pscustomobject]@{ Reachable = $false; LoggedIn = $false; State = 'unknown'; ExpiresInDays = $null }
    try {
        # `docker exec ... 2>$null` is a TRAP here: under $ErrorActionPreference
        # = "Stop" a single stderr byte from a redirected native call becomes a
        # TERMINATING error (the gotcha this file already documents elsewhere).
        # The empty catch below would then hand back Reachable=$false and the
        # logout alert would silently never fire - the exact class of bug this
        # check exists to catch. Route stderr through cmd instead.
        $raw = cmd /c "docker exec tailscale tailscale --socket=/tmp/tailscaled.sock status --json 2>&1"
        $txt = (($raw -join "`n")).Trim()
        if (-not $txt -or $txt -notmatch '^\s*\{') {
            Write-LogEntry "Tailscale node state UNAVAILABLE - 'status --json' returned no JSON; the logout/expiry check cannot run this cycle" "WARN"
            return $out
        }
        $st = $txt | ConvertFrom-Json
        $out.Reachable = $true
        $out.State     = "$($st.BackendState)"
        $out.LoggedIn  = ($st.BackendState -eq 'Running')
        if ($st.Self -and $st.Self.KeyExpiry) {
            $exp = [datetime]$st.Self.KeyExpiry
            $out.ExpiresInDays = [math]::Round(($exp.ToUniversalTime() - (Get-Date).ToUniversalTime()).TotalDays, 1)
        }
        # Log on SUCCESS too. Without this, a permanently blind check looks
        # exactly like a healthy one: clean logs, no alert, no evidence it ran.
        Write-LogEntry "Tailscale node state: BackendState=$($out.State) keyExpiresInDays=$($out.ExpiresInDays)" "DEBUG"
    } catch {
        Write-LogEntry "Tailscale node state check FAILED: $($_.Exception.Message)" "WARN"
    }
    return $out
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

# --- Bridge FUNCTIONAL health (beyond lock-port liveness) ---------------------
# 2026-08-23 incident: BOTH bridge personas held their lock ports for weeks while
# every turn died with WinError 2 -- the claude.exe path cached at startup had
# been deleted by VS Code extension pruning. Liveness said healthy; turns were
# dead; nothing alerted. bridge.py now writes state/health.json (60s heartbeat:
# ts, claude_bin, bin_exists, consecutive_failures) and self-heals a pruned
# path. This check reads the beacon and acts on what liveness cannot see.
function Confirm-BridgeFunctionalHealth {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$TaskName,
        [Parameter(Mandatory)][int]$Port,
        [Parameter(Mandatory)][string]$Label,
        [Parameter(Mandatory)][string]$HealthPath
    )
    if (-not (Test-HostLockPort -Port $Port)) { return }  # liveness repair owns that case
    if (-not (Test-Path $HealthPath)) {
        Write-LogEntry "$Label : no health beacon at $HealthPath (pre-beacon build, or still waiting for Mattermost)" "DEBUG"
        return
    }
    try { $h = Get-Content $HealthPath -Raw | ConvertFrom-Json } catch {
        Write-LogEntry "$Label : health beacon unreadable: $($_.Exception.Message)" "WARN"; return
    }
    # Epoch-to-epoch ON PURPOSE. (Get-Date "1970-01-01Z") carries the offset in
    # force at the EPOCH (EST, -5), so a local-time subtraction reads exactly one
    # DST hour stale all summer -- every cycle would see "60m" and restart both
    # bridges. Comparing unix seconds to unix seconds has no timezone in it.
    $ageMin = [int](([int64][System.DateTimeOffset]::UtcNow.ToUnixTimeSeconds() - [int64]$h.ts) / 60)
    if ($ageMin -gt 15) {
        Write-LogEntry "$Label : health beacon STALE (${ageMin}m) while lock port is held -- poll loop wedged; restarting task '$TaskName'" "WARN"
        try {
            Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue; Start-Sleep 2
            Start-ScheduledTask -TaskName $TaskName
        } catch { Write-LogEntry "$Label wedge-restart error: $($_.Exception.Message)" "ERROR" }
        Send-TelegramAlert "ALERT $Label heartbeat was stale ${ageMin}m (process alive, loop wedged) -- task restarted." -ThrottleKey ("wedge-" + $Port) -ThrottleHours 1
        return
    }
    if (-not $h.bin_exists) {
        # The bridge self-heals a pruned path; bin_exists=false in a FRESH beacon
        # means re-resolution itself failed -- a restart cannot fix that.
        Write-LogEntry "$Label : claude binary UNRESOLVABLE ($($h.claude_bin) missing and no replacement found) -- needs reinstall or BRIDGE_CLAUDE_BIN" "ERROR"
        Send-TelegramAlert "ALERT $Label cannot resolve any claude.exe (last: $($h.claude_bin)). Reinstall the VS Code extension or set BRIDGE_CLAUDE_BIN." -ThrottleKey ("nobin-" + $Port) -ThrottleHours 1
        return
    }
    if ([int]$h.consecutive_failures -ge 2) {
        Write-LogEntry "$Label : $($h.consecutive_failures) consecutive failed turns; last error: $($h.last_err)" "ERROR"
        Send-TelegramAlert "ALERT $Label : $($h.consecutive_failures) consecutive failed turns. Last error: $($h.last_err)" -ThrottleKey ("turns-" + $Port) -ThrottleHours 1
        return
    }
    Write-LogEntry "$Label functionally healthy (beacon ${ageMin}m old, bin ok, fails=$($h.consecutive_failures))" "DEBUG"
}

# === CONTAINER LOOPS: crash loops + orphaned network namespaces ===============
# Ported 2026-09-28 (closeout-followups item cf-watchdog) from the archived
# work/crashloop branch (100366c; plan store journal/archive/branches/). Only
# the detection functions and the bounded docker calls came across; that
# branch's verifier did not.
#
# WHY (2026-09-08/09): stt-tts-tailscale was restarted 5,091 times over 33 hours
# and nothing here noticed, because every other check in this script asks a
# FIXED LIST of containers whether they are healthy and it was not on the list.
# This section enumerates whatever Docker has, in any compose project, so a new
# container is covered without editing this file. Its twin, stt-tts-server,
# reported healthy through the same outage: it had joined the netns of a
# container that restarted after it, so it was listening in a namespace nobody
# routes to, and a healthcheck on its own loopback could not see that. That is
# checked from outside, by comparing start times.
#
# Neither check REPAIRS anything. A restart loop is nearly always a credential
# or config fault (an expired auth key, that time); restarting it again resets
# the counter and hides the evidence. They alert through the catastrophe path
# (Telegram + the Mattermost mirror) behind a per-key cooldown, so a loop that
# runs all night pages once, not every ten-minute pass.

# Restarts accumulated over consecutive passes before it is called a loop. The
# StackWatchdog task runs every 10 minutes (PT10M); a container that exits at
# once settles near one Docker-backoff restart a minute, ~10 a pass, so it trips
# on the second pass. One restart per pass trips on the fourth.
$RestartLoopThreshold = 3
# One page per key per window. The catastrophe path's own Telegram throttle is
# 1h and its Mattermost mirror has none, so the cooldown is enforced here.
$LoopAlertCooldownHours = 6
# SLOW loops: the fast rule needs restarts on consecutive passes, so a
# container that crashes less often than once a pass (every other pass is
# ~72 a day) never trips it. This rule counts every restart seen in the last
# $SlowLoopWindowHours and pages at $SlowLoopThreshold: 6 in 6 hours is a
# sustained one an hour, well above a container restarted by hand now and
# then, and a crash every 20 minutes reaches it in about 2 hours.
$SlowLoopWindowHours = 6
$SlowLoopThreshold = 6
# SETTLED: stopped, or running this long since its last start, with no new
# restart this pass. A settled container gets the all-clear AND its restart
# history is dropped, so restarts from before it settled can never page it as
# a slow loop later (attempt-2 finding W-1: a fixed fast loop was re-paged
# after the cooldown and its all-clear withheld). One rule for both, and it is
# an hour on purpose: the window rule can only reach 6 in 6h when the gaps
# between restarts AVERAGE under an hour, so a container up for a full hour
# has stopped looping by the same measure - while a 10-minute rule would have
# wiped the history of every loop slower than a pass.
$LoopSettledMinutes = 60
# The docker executable. A variable only so the bounded-call test can point it
# at a stub that never returns.
$WatchdogDockerExe = 'docker'
# Per-call bounds. PS 5.1 has no timeout on a native call and docker offers
# none, so a wedged daemon lock would otherwise hang the pass - and the task
# runs MultipleInstances=IgnoreNew with ExecutionTimeLimit=PT72H, so ONE hang
# silently drops every later run for up to three days.
$DockerCallTimeoutSeconds = 25   # the batched ps / inspect
$DockerProbeTimeoutSeconds = 8   # one container's inspect
$DockerLogsTimeoutSeconds = 10   # docker logs for an alert's fault line
# Per-PASS bounds for the loops that make one call per container.
$FallbackBudgetSeconds = 120
$NetnsConfirmBudgetSeconds = 30
# A projection of `docker inspect` in the SAME shape as the full JSON, so one
# parser reads both this and a recorded `docker inspect` document.
$ContainerFactsFormat = '{"Name":{{json .Name}},"Id":{{json .Id}},"RestartCount":{{json .RestartCount}},"State":{"Status":{{json .State.Status}},"StartedAt":{{json .State.StartedAt}}},"HostConfig":{"NetworkMode":{{json .HostConfig.NetworkMode}},"RestartPolicy":{{json .HostConfig.RestartPolicy}}}}'

# Docker restarts a container FOREVER under always / unless-stopped, and under
# on-failure with MaximumRetryCount 0 ("no limit", not "never"). Under 'no' and
# a bounded on-failure the count cannot run away.
$UnboundedRestartPolicies = @('always', 'unless-stopped')
function Test-UnboundedRestartPolicy {
    [CmdletBinding()]
    param([string]$Policy, [int]$MaxRetries)
    if ($UnboundedRestartPolicies -contains $Policy) { return $true }
    if ($Policy -eq 'on-failure' -and $MaxRetries -le 0) { return $true }
    return $false
}

# Quote ONE argument for a Windows command line. The repo root has a space in
# it, and an unquoted argument built from it splits (measured on the archived
# branch: both alert senders died that way). ProcessStartInfo.Arguments takes
# one string, so the quoting is ours.
function ConvertTo-ProcessArgument {
    [CmdletBinding()]
    param([Parameter(Mandatory)][AllowEmptyString()][string]$Value)
    if ($Value -eq '') { return '""' }
    if ($Value -notmatch '[\s"]') { return $Value }
    $escaped = [regex]::Replace($Value, '(\\*)"', '$1$1\"')
    $escaped = [regex]::Replace($escaped, '(\\+)$', '$1$1')
    return '"' + $escaped + '"'
}

# A Windows JOB OBJECT for each bounded call, created with KILL_ON_JOB_CLOSE:
# the child is assigned to it right after Start(), everything the child starts
# from then on is in it too - including a great-grandchild whose own parent has
# already exited, which neither `taskkill /T` nor a ParentProcessId walk can
# reach (attempt-2 finding W-4) - and closing the handle kills whatever is left.
# No WMI query, so nothing on this path waits on the WMI service (W-3).
# $WatchdogUseJobObject exists so the test can exercise the fallback.
$WatchdogUseJobObject = $true
function New-WatchdogJob {
    [CmdletBinding()]
    param()
    if (-not $WatchdogUseJobObject) { return [IntPtr]::Zero }
    try {
        if (-not ('AiStackWatchdogJob' -as [type])) {
            Add-Type -TypeDefinition @"
using System; using System.Runtime.InteropServices;
public static class AiStackWatchdogJob {
  [DllImport("kernel32.dll", CharSet = CharSet.Unicode)] static extern IntPtr CreateJobObject(IntPtr attrs, string name);
  [DllImport("kernel32.dll")] static extern bool SetInformationJobObject(IntPtr job, int infoClass, IntPtr info, uint length);
  [DllImport("kernel32.dll")] static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
  [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);
  [StructLayout(LayoutKind.Sequential)] struct Basic { public long PerProcessUserTimeLimit; public long PerJobUserTimeLimit; public uint LimitFlags;
    public UIntPtr MinimumWorkingSetSize; public UIntPtr MaximumWorkingSetSize; public uint ActiveProcessLimit; public UIntPtr Affinity;
    public uint PriorityClass; public uint SchedulingClass; }
  [StructLayout(LayoutKind.Sequential)] struct Io { public ulong R, W, O, RB, WB, OB; }
  [StructLayout(LayoutKind.Sequential)] struct Extended { public Basic BasicLimit; public Io IoInfo; public UIntPtr ProcessMemoryLimit;
    public UIntPtr JobMemoryLimit; public UIntPtr PeakProcessMemoryUsed; public UIntPtr PeakJobMemoryUsed; }
  public static IntPtr Create() {
    IntPtr job = CreateJobObject(IntPtr.Zero, null);
    if (job == IntPtr.Zero) return IntPtr.Zero;
    Extended e = new Extended(); e.BasicLimit.LimitFlags = 0x2000; // JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    int len = Marshal.SizeOf(typeof(Extended)); IntPtr p = Marshal.AllocHGlobal(len);
    try { Marshal.StructureToPtr(e, p, false); if (!SetInformationJobObject(job, 9, p, (uint)len)) { CloseHandle(job); return IntPtr.Zero; } }
    finally { Marshal.FreeHGlobal(p); }
    return job;
  }
  public static bool Assign(IntPtr job, IntPtr process) { return AssignProcessToJobObject(job, process); }
  public static void Close(IntPtr job) { if (job != IntPtr.Zero) CloseHandle(job); }
}
"@
        }
        return [AiStackWatchdogJob]::Create()
    } catch {
        Write-LogEntry "job object unavailable ($($_.Exception.Message)) - bounded calls fall back to taskkill /T" "WARN"
        return [IntPtr]::Zero
    }
}

# Run an external command with a hard wall-clock bound. Returns its output
# lines, or $null when the call did not complete cleanly. $null means one of:
#   timed out          -> $script:BoundedFailureReason = "did not answer within Ns"
#   exited non-zero    -> "exited N: <first stderr line>"; whatever it DID print
#                         is kept in $script:BoundedFailureLines
#   threw before start -> the reason is empty
# Callers report the reason rather than assuming a timeout: "did not answer" is
# this section's signature for a wedged daemon, and a 0.1s "No such container"
# must not read like one. Both are reset at the top of every call.
# .NET Process + WaitForExit(ms) + `taskkill /T /F`, not Start-Job (whose
# Stop-Job/Remove-Job block until the child exits, so it bounded the WAIT and
# not the call) and not Start-Process -PassThru (which returned $null
# intermittently and left ExitCode empty on 5.1).
function Invoke-BoundedProcess {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$FilePath,
        [string[]]$ProcArgs = @(),
        [int]$TimeoutSeconds = 0
    )
    $ErrorActionPreference = 'Continue'   # function-local: native stderr must not throw
    $script:BoundedFailureReason = ''
    $script:BoundedFailureLines = @()
    if ($TimeoutSeconds -le 0) { $TimeoutSeconds = $DockerCallTimeoutSeconds }
    if (-not $TimeoutSeconds -or $TimeoutSeconds -le 0) { $TimeoutSeconds = 25 }

    $proc = New-Object System.Diagnostics.Process
    $job = [IntPtr]::Zero
    try {
        $psi = $proc.StartInfo
        $psi.FileName = $FilePath
        $psi.Arguments = ((@($ProcArgs) | ForEach-Object { ConvertTo-ProcessArgument $_ }) -join ' ')
        $psi.UseShellExecute = $false
        $psi.CreateNoWindow = $true
        $psi.RedirectStandardOutput = $true
        $psi.RedirectStandardError = $true
        $psi.RedirectStandardInput = $true
        $sw = [System.Diagnostics.Stopwatch]::StartNew()
        [void]$proc.Start()
        # Into the job at once. (A process the child starts in the few
        # microseconds before this line would escape it; docker does not.)
        $job = New-WatchdogJob
        if ($job -ne [IntPtr]::Zero -and -not [AiStackWatchdogJob]::Assign($job, $proc.Handle)) {
            [AiStackWatchdogJob]::Close($job); $job = [IntPtr]::Zero
        }
        # Both streams asynchronously BEFORE waiting: reading one to the end
        # while the child fills the other is the classic deadlock.
        $outTask = $proc.StandardOutput.ReadToEndAsync()
        $errTask = $proc.StandardError.ReadToEndAsync()
        # An empty stdin, not an inherited one.
        $proc.StandardInput.Close()

        if (-not $proc.WaitForExit($TimeoutSeconds * 1000)) {
            $script:BoundedFailureReason = "did not answer within ${TimeoutSeconds}s"
            # The whole tree: what hangs is often a grandchild, and .NET
            # Framework's Kill() has no entireProcessTree overload. With a job
            # the finally block's Close kills the tree, orphans included;
            # without one, taskkill /T reaches what still has a live parent.
            if ($job -eq [IntPtr]::Zero) { & taskkill.exe /PID $proc.Id /T /F 2>$null | Out-Null }
            return $null
        }
        # The parameterless wait after the timed one is what makes ExitCode
        # readable (it came back empty after WaitForExit([int]) alone).
        $proc.WaitForExit()
        $code = $proc.ExitCode
        # The CHILD exiting is not the end of its output: a descendant that
        # inherited the pipe holds it open, and reading to EOF would wait for
        # THAT process (measured by the attempt-1 tester: 29.7s under a 2s
        # bound). Give the readers only what is left of the bound; closing the
        # job (finally) then kills the descendant holding the pipe.
        $leftMs = [int][Math]::Max(0, ($TimeoutSeconds * 1000) - $sw.ElapsedMilliseconds)
        if (-not [System.Threading.Tasks.Task]::WaitAll(@($outTask, $errTask), $leftMs)) {
            $script:BoundedFailureReason = "did not answer within ${TimeoutSeconds}s (it exited, but a process it started kept its output open)"
            return $null
        }
        $outLines = @(@($outTask.Result -split "`r?`n") | Where-Object { $null -ne $_ -and $_ -ne '' })
        $errLines = @(@($errTask.Result -split "`r?`n") | Where-Object { $null -ne $_ -and $_ -ne '' })
        $lines = @($outLines) + @($errLines)
        # The EXIT CODE decides, never "produced output".
        if ($code -ne 0) {
            $errFirst = @($errLines | Where-Object { $_ -and $_.Trim() }) | Select-Object -First 1
            $first = if ($errFirst) { $errFirst } else {
                @($lines | Where-Object { $_ -and $_.Trim() }) | Select-Object -First 1
            }
            $script:BoundedFailureReason = "exited $code" + $(if ($first) { ": $first" } else { "" })
            # `docker inspect a b ghost` exits 1 and still prints a good row for
            # a and b; keep them for a caller that salvages. `= @(...)`, not
            # `= , $lines`: an assignment does not unroll, so the comma would
            # nest the array one level too deep.
            $script:BoundedFailureLines = @($lines)
            Write-LogEntry ("bounded process '{0}' {1}" -f $FilePath, $script:BoundedFailureReason) "DEBUG"
            return $null
        }
        return , $lines
    } catch {
        Write-LogEntry "bounded process '$FilePath' failed: $($_.Exception.Message)" "WARN"
        return $null
    } finally {
        if ($proc) {
            try { if (-not $proc.HasExited) { $proc.Kill() } } catch { }
            try { $proc.Dispose() } catch { }
        }
        # KILL_ON_JOB_CLOSE: ends anything the call left behind, on every path.
        if ($job -ne [IntPtr]::Zero) { try { [AiStackWatchdogJob]::Close($job) } catch { } }
    }
}

# Bounded `docker <args>`. ASSIGN its result, never pipe it: it returns
# `, @(...)` so an empty answer stays distinguishable from $null, and a pipeline
# would unroll that.
function Invoke-BoundedDocker {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string[]]$DockerArgs,
        [int]$TimeoutSeconds = 0
    )
    return Invoke-BoundedProcess -FilePath $WatchdogDockerExe -ProcArgs $DockerArgs -TimeoutSeconds $TimeoutSeconds
}

# One container's facts from one `docker inspect` record - either a full
# recorded document or the $ContainerFactsFormat projection, which has the same
# shape. $null for anything that is not such a record.
function ConvertTo-ContainerFact {
    [CmdletBinding()]
    param($Record)
    if ($null -eq $Record) { return $null }
    if ($Record -is [string]) {
        $t = $Record.Trim()
        if (-not $t.StartsWith('{')) { return $null }
        try { $Record = $t | ConvertFrom-Json } catch { return $null }
    }
    if (-not $Record.Id -or -not $Record.Name -or -not $Record.State -or -not $Record.HostConfig) { return $null }
    $rc = 0; [void][int]::TryParse([string]$Record.RestartCount, [ref]$rc)
    $policy = ''; $mr = 0
    if ($Record.HostConfig.RestartPolicy) {
        $policy = [string]$Record.HostConfig.RestartPolicy.Name
        [void][int]::TryParse([string]$Record.HostConfig.RestartPolicy.MaximumRetryCount, [ref]$mr)
    }
    return [pscustomobject]@{
        Name          = ([string]$Record.Name -replace '^/', '')
        Id            = [string]$Record.Id
        RestartCount  = $rc
        Status        = [string]$Record.State.Status
        StartedAt     = [string]$Record.State.StartedAt
        RestartPolicy = $policy
        MaxRetries    = $mr
        NetworkMode   = [string]$Record.HostConfig.NetworkMode
    }
}

# Facts for every container Docker knows about: one batched inspect on the
# happy path. When the batch fails it salvages what the batch printed, then
# probes the rest one by one inside a per-pass budget, and NAMES what it could
# not read - an empty array would read as "no containers".
function Get-ContainerRuntimeFacts {
    try {
        $rawNames = Invoke-BoundedDocker -DockerArgs @('ps', '-a', '--format', '{{.Names}}')
        if ($null -eq $rawNames) {
            Write-LogEntry "docker ps $script:BoundedFailureReason - container loop checks skipped this pass" "WARN"
            return @()
        }
        $names = @($rawNames | Where-Object { $_ -and $_.Trim() } | ForEach-Object { $_.Trim() })
        if ($names.Count -eq 0) {
            Write-LogEntry "docker ps returned no containers - nothing to check" "DEBUG"
            return @()
        }
        $rows = Invoke-BoundedDocker -DockerArgs (@('inspect', '--format', $ContainerFactsFormat) + $names)
        if ($null -ne $rows) {
            $out = @()
            foreach ($row in @($rows)) {
                $f = ConvertTo-ContainerFact -Record $row
                if ($f) { $out += $f }
            }
            Resolve-Catastrophe -Key 'docker-unreadable' -Message "docker can describe every container again."
            return $out
        }

        Write-LogEntry ("batched docker inspect {0} - falling back to per-container probes" -f `
            $(if ($script:BoundedFailureReason) { $script:BoundedFailureReason } else { "did not complete" })) "WARN"
        $out = @()
        $seen = @{}
        foreach ($row in @($script:BoundedFailureLines)) {
            $f = ConvertTo-ContainerFact -Record $row
            if ($f) { $out += $f; $seen[$f.Name] = $true }
        }
        $unreadable = @()
        $vanished = @()
        $skipped = @()
        $budget = [System.Diagnostics.Stopwatch]::StartNew()
        foreach ($n in $names) {
            if ($seen[$n]) { continue }
            if ($budget.Elapsed.TotalSeconds -gt $FallbackBudgetSeconds) { $skipped += $n; continue }
            $row = Invoke-BoundedDocker -DockerArgs @('inspect', '--format', $ContainerFactsFormat, $n) -TimeoutSeconds $DockerProbeTimeoutSeconds
            if ($null -eq $row) {
                # Removed between `ps` and `inspect` is ordinary churn (a compose
                # recreate, a worker teardown), not a wedged daemon.
                if ($script:BoundedFailureReason -match '(?i)no such (object|container)') {
                    $vanished += $n
                } else {
                    $unreadable += [pscustomobject]@{ Name = $n; Why = $script:BoundedFailureReason }
                }
                continue
            }
            $f = ConvertTo-ContainerFact -Record (@($row) | Select-Object -First 1)
            if ($f) { $out += $f }
            else { $unreadable += [pscustomobject]@{ Name = $n; Why = "answered, but the row did not parse" } }
        }
        $budget.Stop()
        if ($skipped.Count -gt 0) {
            Write-LogEntry ("per-container fallback ran out of budget after {0}s - {1} container(s) not probed this pass: {2}" -f `
                [int]$budget.Elapsed.TotalSeconds, $skipped.Count, ($skipped -join ', ')) "WARN"
        }
        if ($vanished.Count -gt 0) {
            Write-LogEntry ("{0} container(s) removed between docker ps and docker inspect - not a fault: {1}" -f `
                $vanished.Count, ($vanished -join ', ')) "DEBUG"
        }
        if ($unreadable.Count -gt 0) {
            $detail = (@($unreadable | ForEach-Object { "$($_.Name) ($($_.Why))" }) -join ', ')
            Send-LoopAlert -Key 'docker-unreadable' -Message ("docker cannot describe $($unreadable.Count) container(s) within ${DockerProbeTimeoutSeconds}s: $detail. " +
                "Their control plane is wedged even if the service still serves; a docker restart on one may also hang.") | Out-Null
        }
        Write-LogEntry "per-container fallback read $($out.Count) of $($names.Count) container(s)" "WARN"
        return $out
    } catch {
        Write-LogEntry "container facts unavailable: $($_.Exception.Message)" "WARN"
        return @()
    }
}

# The most useful line of a container's recent log, for the alert: "restarting
# a lot" alone sends the operator to a terminal. Bounded like everything else -
# this is the one call made while an incident is already in progress.
function Get-ContainerFaultLine {
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$Name)
    try {
        $raw = Invoke-BoundedDocker -DockerArgs @('logs', '--tail', '60', $Name) -TimeoutSeconds $DockerLogsTimeoutSeconds
        if ($null -eq $raw) { return "(log unavailable: docker logs $script:BoundedFailureReason)" }
        $lines = @($raw | ForEach-Object { [string]$_ } | Where-Object { $_.Trim() })
        if ($lines.Count -eq 0) { return "(no log output)" }
        $pattern = '(?i)(error|fail|invalid|denied|refused|unable|cannot|fatal|panic|exit status|unauthori)'
        $pick = $lines[$lines.Count - 1]
        for ($i = $lines.Count - 1; $i -ge 0; $i--) {
            if ($lines[$i] -match $pattern) { $pick = $lines[$i]; break }
        }
        # Collapse FIRST, then measure the collapsed string.
        $collapsed = $pick.Trim() -replace '\s+', ' '
        return $collapsed.Substring(0, [Math]::Min(280, $collapsed.Length))
    } catch {
        return "(log unavailable: $($_.Exception.Message))"
    }
}

# RFC3339 from Docker -> a UTC instant, or $null.
function ConvertTo-UtcInstant {
    [CmdletBinding()]
    param([string]$Value)
    if (-not $Value) { return $null }
    try { return ([datetimeoffset]::Parse($Value, [cultureinfo]::InvariantCulture)).UtcDateTime }
    catch { return $null }
}

# One page per key per $LoopAlertCooldownHours, through the catastrophe path.
# Returns $true when it sent. The cooldown sentinel is separate from the
# catastrophe path's own .tg-alert-* throttle, which is 1h and does not cover
# its Mattermost mirror at all.
function Send-LoopAlert {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$Key,
        [Parameter(Mandatory)][string]$Message
    )
    $sentinel = Join-Path $PROJECT_DIR "logs\.loop-alert-$Key"
    try {
        if (Test-Path $sentinel) {
            if (((Get-Date) - (Get-Item $sentinel).LastWriteTime).TotalHours -lt $LoopAlertCooldownHours) {
                Write-LogEntry "LOOP [$Key] still firing; not re-paged inside the ${LoopAlertCooldownHours}h cooldown: $Message" "WARN"
                return $false
            }
        }
    } catch { }
    Send-CatastropheAlert -Key $Key -Message $Message
    try { (Get-Date -Format o) | Out-File $sentinel -Encoding ascii -Force } catch { }
    return $true
}

# Crash loops by RESTART-COUNT DELTA between passes, never the absolute count:
# a container carrying 5,000 historical restarts that is now stable stays quiet.
# A container seen for the first time (or recreated - a new Id) only records a
# baseline. State lives in logs\.watchdog-restart-state.json. Returns $true when
# nothing is looping.
function Test-ContainerRestartLoops {
    [CmdletBinding()]
    param([Parameter(Mandatory)][AllowEmptyCollection()][object[]]$Facts)

    $statePath = Join-Path $PROJECT_DIR "logs\.watchdog-restart-state.json"
    $prev = @{}
    try {
        if (Test-Path $statePath) {
            $raw = Get-Content $statePath -Raw -ErrorAction Stop
            if ($raw -and $raw.Trim()) {
                $obj = $raw | ConvertFrom-Json
                foreach ($p in $obj.PSObject.Properties) { $prev[$p.Name] = $p.Value }
            }
        }
    } catch {
        Write-LogEntry "restart-state unreadable ($($_.Exception.Message)); rebuilding baseline" "WARN"
        $prev = @{}
    }

    $next = @{}
    $seen = @{}
    $looping = @()
    $quiet = @()
    $nowEpoch = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    foreach ($f in $Facts) {
        if (-not (Test-UnboundedRestartPolicy -Policy $f.RestartPolicy -MaxRetries $f.MaxRetries)) { continue }
        $key = $f.Name
        $streak = 0
        $accum = 0
        $delta = 0
        $hist = @()
        # Parse the state defensively: one hand-edited or truncated value must
        # cost one quiet pass, not throw out of the whole health pass.
        $p = $prev[$key]
        if ($p -and $p.Id -eq $f.Id) {
            # Restart history for the window rule: "epoch:count" strings, one
            # per pass that saw restarts, pruned to $SlowLoopWindowHours.
            foreach ($h in @($p.Hist)) {
                $hp = ([string]$h) -split ':'
                $he = [int64]0; $hn = 0
                # Inside the window on BOTH sides: an entry dated in the future
                # (clock skew, a hand edit) would otherwise never age out (W-2),
                # and a non-positive count is not a restart.
                if ($hp.Count -eq 2 -and [int64]::TryParse($hp[0], [ref]$he) -and [int]::TryParse($hp[1], [ref]$hn) -and $hn -gt 0 -and
                    $he -ge ($nowEpoch - [int64]($SlowLoopWindowHours * 3600)) -and $he -le ($nowEpoch + 300)) { $hist += "${he}:${hn}" }
            }
            $prevCount = 0; $prevStreak = 0; $prevAccum = 0
            $okCount = [int]::TryParse([string]$p.Count, [ref]$prevCount)
            [void][int]::TryParse([string]$p.Streak, [ref]$prevStreak)
            [void][int]::TryParse([string]$p.Accum, [ref]$prevAccum)
            if ($okCount) {
                $delta = $f.RestartCount - $prevCount
                if ($delta -gt 0) {
                    $streak = $prevStreak + 1
                    $accum = $prevAccum + $delta
                    $hist += "${nowEpoch}:${delta}"
                } elseif ($f.Status -eq 'restarting') {
                    # No new restart since the last pass, but Docker is sitting
                    # in its restart backoff (up to a minute): the loop has NOT
                    # stopped - a pass run soon after the previous one lands
                    # here. Keep the streak rather than reset it.
                    $streak = $prevStreak
                    $accum = $prevAccum
                }
            }
        }
        # Settled FIRST, so the window below never counts restarts from
        # before the container settled.
        $started = ConvertTo-UtcInstant $f.StartedAt
        $settled = ($delta -le 0) -and ($f.Status -ne 'restarting') -and
            ($f.Status -ne 'running' -or ($started -and ((Get-Date).ToUniversalTime() - $started).TotalMinutes -ge $LoopSettledMinutes))
        if ($settled) { $hist = @(); $streak = 0; $accum = 0 }
        $inWindow = 0
        foreach ($h in $hist) { $inWindow += [int](($h -split ':')[1]) }
        $next[$key] = @{ Count = $f.RestartCount; Id = $f.Id; Streak = $streak; Accum = $accum; Missed = 0; Hist = @($hist) }
        $seen[$key] = $true
        if ($accum -ge $RestartLoopThreshold) {
            $looping += [pscustomobject]@{ Fact = $f; Accum = $accum; Streak = $streak; Window = 0 }
        } elseif ($inWindow -ge $SlowLoopThreshold) {
            # SLOW loop: quiet passes in between reset the streak above, but
            # the restarts keep adding up over the window.
            $looping += [pscustomobject]@{ Fact = $f; Accum = $accum; Streak = $streak; Window = $inWindow }
        } elseif ($settled) {
            # The all-clear (a no-op unless the key is firing). One quiet pass
            # alone can be shorter than the gap between two crashes.
            $quiet += $key
        }
    }

    # Carry forward a baseline this pass simply did not see (the facts can be
    # partial by design), for at most six passes - an hour - after which a name
    # Docker stopped reporting is dropped.
    foreach ($k in $prev.Keys) {
        if ($seen[$k]) { continue }
        $missed = 0
        [void][int]::TryParse([string]$prev[$k].Missed, [ref]$missed)
        if ($missed -ge 6) { continue }
        $next[$k] = @{
            Count  = $prev[$k].Count
            Id     = $prev[$k].Id
            Streak = $prev[$k].Streak
            Accum  = $prev[$k].Accum
            Hist   = @($prev[$k].Hist)
            Missed = $missed + 1
        }
    }

    try {
        $dir = Split-Path -Parent $statePath
        if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
        ($next | ConvertTo-Json -Depth 4) | Out-File $statePath -Encoding utf8 -Force
    } catch { Write-LogEntry "could not persist restart-state: $($_.Exception.Message)" "WARN" }

    # All-clear for a loop that has stopped (a no-op unless its key is firing).
    foreach ($k in $quiet) {
        Resolve-Catastrophe -Key ("crashloop-" + $k) -Message "container '$k' is no longer restarting."
    }

    if ($looping.Count -eq 0) {
        Write-LogEntry "no container restart loops (watched $($seen.Count))" "DEBUG"
        return $true
    }
    foreach ($l in $looping) {
        $name = $l.Fact.Name
        $why = Get-ContainerFaultLine -Name $name
        $rate = if ($l.Window -gt 0) { "$($l.Window) restart(s) in the last ${SlowLoopWindowHours}h (a slow loop)" }
                else { "$($l.Accum) restart(s) over $($l.Streak) watchdog pass(es)" }
        Send-LoopAlert -Key ("crashloop-" + $name) -Message ("container '$name' is CRASH-LOOPING: $rate, " +
            "total $($l.Fact.RestartCount). Not restarted by the watchdog (a loop is usually a credential or config fault). Last fault line: $why") | Out-Null
    }
    return $false
}

# A running container joined to another's namespace (network_mode
# "service:x" -> HostConfig.NetworkMode "container:<id>") is stranded when its
# OWNER restarts after it started, or when the owner is gone. Returns $true when
# no joiner is orphaned.
function Test-NetnsJoinedContainers {
    [CmdletBinding()]
    param([Parameter(Mandatory)][AllowEmptyCollection()][object[]]$Facts)

    $byId = @{}
    foreach ($f in $Facts) { $byId[$f.Id] = $f }
    $ok = $true
    $confirmBudget = [System.Diagnostics.Stopwatch]::StartNew()
    foreach ($f in $Facts) {
        if ($f.NetworkMode -notlike 'container:*') { continue }
        if ($f.Status -ne 'running') { continue }
        $ownerId = $f.NetworkMode.Substring('container:'.Length)
        $owner = $byId[$ownerId]
        if (-not $owner) {
            # Absent from the facts is NOT absent from Docker - the facts can be
            # partial. Ask Docker, on a short leash; only a definite "no such
            # container" is a page.
            if ($confirmBudget.Elapsed.TotalSeconds -gt $NetnsConfirmBudgetSeconds) {
                Write-LogEntry "$($f.Name) netns owner $ownerId not confirmed - the ${NetnsConfirmBudgetSeconds}s confirmation budget is spent this pass" "WARN"
                continue
            }
            $probe = Invoke-BoundedDocker -DockerArgs @('inspect', '--format', $ContainerFactsFormat, $ownerId) -TimeoutSeconds $DockerProbeTimeoutSeconds
            if ($null -ne $probe) {
                $owner = ConvertTo-ContainerFact -Record (@($probe) | Select-Object -First 1)
            } elseif ($script:BoundedFailureReason -match '(?i)no such (object|container)') {
                Send-LoopAlert -Key ("netns-" + $f.Name) -Message ("container '$($f.Name)' shares the network namespace of a container that NO LONGER EXISTS ($ownerId) - " +
                    "it has no working network. Recreate it.") | Out-Null
                $ok = $false
                continue
            }
            if (-not $owner) {
                Write-LogEntry ("$($f.Name) netns owner $ownerId could not be confirmed" +
                    $(if ($script:BoundedFailureReason) { " ($script:BoundedFailureReason)" } else { "" }) +
                    " - not alerting on an unconfirmed absence") "WARN"
                continue
            }
        }
        # An owner that is NOT RUNNING - stopped, exited, dead, or sitting in
        # restart backoff - leaves the joiner running with only `lo` (measured
        # in test, 2026-09-28: the joiner stays `running` and `ip -o addr`
        # inside it lists lo alone), whatever the start times say. `paused`
        # keeps its namespace and is not a fault.
        if ($owner.Status -notin @('running', 'paused')) {
            $why = Get-ContainerFaultLine -Name $owner.Name
            Send-LoopAlert -Key ("netns-" + $f.Name) -Message ("container '$($f.Name)' is ORPHANED: the owner of its network namespace, '$($owner.Name)', is $($owner.Status), " +
                "so '$($f.Name)' is running with no network. Its own healthcheck cannot see this. Start the owner, then restart '$($f.Name)'. Owner's last fault line: $why") | Out-Null
            $ok = $false
            continue
        }
        $joinerStart = ConvertTo-UtcInstant $f.StartedAt
        $ownerStart = ConvertTo-UtcInstant $owner.StartedAt
        if ($null -eq $joinerStart -or $null -eq $ownerStart) {
            Write-LogEntry "$($f.Name)/$($owner.Name) netns check skipped - unparseable start time" "DEBUG"
            continue
        }
        if ($ownerStart -gt $joinerStart) {
            $why = Get-ContainerFaultLine -Name $owner.Name
            Send-LoopAlert -Key ("netns-" + $f.Name) -Message ("container '$($f.Name)' is ORPHANED in a dead network namespace: its owner '$($owner.Name)' restarted at $($owner.StartedAt), " +
                "AFTER '$($f.Name)' started at $($f.StartedAt). Its own healthcheck cannot see this. Restart the owner first, then '$($f.Name)'. Owner's last fault line: $why") | Out-Null
            $ok = $false
        } else {
            Write-LogEntry "$($f.Name) netns owner $($owner.Name) intact" "DEBUG"
            Resolve-Catastrophe -Key ("netns-" + $f.Name) -Message "container '$($f.Name)' shares a live network namespace again."
        }
    }
    return $ok
}

# One census feeds both checks. $true = nothing found (or nothing readable,
# which is logged but is not itself a finding).
function Invoke-ContainerLoopCheck {
    [CmdletBinding()]
    param()
    $facts = @(Get-ContainerRuntimeFacts)
    if ($facts.Count -eq 0) {
        Write-LogEntry "container facts empty - skipping the crash-loop and netns checks this pass" "WARN"
        return $true
    }
    $loopsOk = [bool](@(Test-ContainerRestartLoops -Facts $facts) | Select-Object -Last 1)
    $netnsOk = [bool](@(Test-NetnsJoinedContainers -Facts $facts) | Select-Object -Last 1)
    return ($loopsOk -and $netnsOk)
}
# === END CONTAINER LOOPS ======================================================

function Invoke-HealthCheck {
    Write-LogEntry "Starting comprehensive health check..."
    # Faults found this cycle. Checks RECORD into this and carry on rather
    # than returning early: before 2026-09-16 a single failed repair (e.g.
    # tailscale) aborted the whole cycle, so inference, backups and the
    # bridges went unchecked for as long as the fault lasted.
    $script:HealthIssues = @()
    # Per-cycle, not per-process: a daemon started before the operator fixed
    # frontend/.env must notice the fix on the NEXT cycle, not the next restart.
    $script:TailscaleDeployedCache = $null

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

    # First, validate entrypoint and detect common issues. Record and CONTINUE:
    # this is a validation probe, and aborting the cycle on it skipped every
    # other check (the 2026-09-16 blind-spot class).
    if (-not (Test-EntrypointHealth)) {
        Write-LogEntry "Entrypoint validation failed. Manual intervention required." "ERROR"
        $script:HealthIssues += 'entrypoint'
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
        
        # Final check after waiting. Escalate out-of-band, then CONTINUE: the
        # rest of this cycle (inference, bridges, backups) does not depend on
        # OpenWebUI, and the old `return $false` here blinded all of it.
        if (-not (Test-ServiceHealth "openwebui")) {
            Write-LogEntry "OpenWebUI failed to become healthy within ${MaxWaitTime}s - may need manual intervention" "ERROR"
            Send-CatastropheAlert -Key 'openwebui' -Message "OpenWebUI is UNHEALTHY and did not recover within ${MaxWaitTime}s. The main chat UI is down on both :3000 and the tailnet. Reply 'status' or 'recover'."
            $script:HealthIssues += 'openwebui'
        } else {
            Resolve-Catastrophe -Key 'openwebui' -Message "OpenWebUI is healthy again."
        }
    } else {
        # The common recovery case: OWUI came back BETWEEN cycles, so the branch
        # above never runs. Without this the 'openwebui' key stays armed forever -
        # no all-clear is ever sent, and the NEXT outage inside the hour is
        # throttled away (found in review 2026-09-16).
        Resolve-Catastrophe -Key 'openwebui' -Message "OpenWebUI is healthy again."
    }
    
    # HOST tailscale node (operator remote access) - a SEPARATE tailnet node
    # from the container one, and deliberately OUTSIDE the profile guard
    # below: it is the Windows Tailscale app, not a container this compose
    # project owns, so a frontend deployment without the tailscale profile
    # says nothing about it. (The function returns early when tailscale.exe
    # is not installed, so it is a no-op on a host that has no host node.)
    Test-HostTailscaleBackend | Out-Null

    # --- CONTAINER tailscale: only when this deployment HAS one -----------
    # The frontend plane is profile-gated since 2026-09-19 (stack-layers 2.5
    # / D8). Without the `tailscale` profile there is no tailscale container,
    # and everything from here to the serve-route repair would be checking -
    # and trying to repair - a container that is not meant to exist. See
    # Test-TailscaleDeployed for which source answers the question and why it
    # fails OPEN in both unclear cases.
    if (Test-TailscaleDeployed) {
        # Tailscale container. Test-ServiceHealth is $false for BOTH "not running"
        # and "running but unhealthy", so the old code announced "not running" about
        # a container that was up the whole time, then "fixed" it with `compose up
        # -d` - a NO-OP on an already-running container with unchanged config. That
        # is exactly how the 2026-09-16 outage retried a no-op 8 times across 94
        # minutes and never recovered. Split the two cases: RECREATE when it is up
        # but unhealthy, so a changed frontend/.env (a fresh TAILSCALE_AUTH_KEY) is
        # actually picked up. --no-deps leaves openwebui - which OWNS the netns
        # tailscale joins - untouched.
        if (-not (Test-ServiceHealth "tailscale")) {
            $tsState = Get-ContainerState "tailscale"
            if ($tsState.Running) {
                # Recreate is DESTRUCTIVE (it flaps all 8 serve routes), so cap it at
                # once an hour. The container healthcheck also probes 127.0.0.1:8080,
                # which is OPENWEBUI's port over the shared netns - so an OWUI outage
                # marks tailscale unhealthy, and an uncapped recreate would rebuild
                # tailscale every 10 minutes over a fault that is not its own
                # (found in review 2026-09-16).
                $rcCooldown = Join-Path $PROJECT_DIR 'logs\.ts-recreate-cooldown'
                $mayRecreate = $true
                try {
                    if (Test-Path $rcCooldown) {
                        if (((Get-Date) - (Get-Item $rcCooldown).LastWriteTime).TotalHours -lt 1) { $mayRecreate = $false }
                    }
                } catch { }
                if ($mayRecreate) {
                    Write-LogEntry "Tailscale container is RUNNING but health=$($tsState.Health) - recreating (up -d would be a no-op)..." "WARN"
                    docker compose -f frontend\docker-compose.yml up -d --force-recreate --no-deps tailscale | Out-Null
                    try { (Get-Date -Format o) | Out-File $rcCooldown -Encoding ascii -Force } catch { }
                } else {
                    Write-LogEntry "Tailscale container still health=$($tsState.Health) but a recreate ran within the hour - not recreating again (check whether OpenWebUI, whose :8080 the healthcheck probes, is the real fault)" "WARN"
                }
            } else {
                Write-LogEntry "Tailscale container not running (state=$($tsState.State)), starting..." "WARN"
                docker compose -f frontend\docker-compose.yml up -d tailscale | Out-Null
            }

            # Wait for the container plus its netns reattachment.
            Start-Sleep 45

            if (-not (Test-ServiceHealth "tailscale")) {
                # CATASTROPHE: a tailscale container that will not come healthy means
                # EVERY tailnet route is gone - OpenWebUI, Mattermost :8446, the wiki,
                # the LiteLLM UI. Alert out-of-band and CARRY ON with the cycle.
                Send-CatastropheAlert -Key 'tailscale-container' -Message "the TAILNET is down - the tailscale container will not become healthy and auto-repair failed. Remote access to OpenWebUI, Mattermost (:8446), the wiki and the LiteLLM UI is GONE. Reply 'status', or check 'docker logs tailscale'."
                $script:HealthIssues += 'tailscale-container'
            } else {
                Write-LogEntry "Tailscale container recovered" "SUCCESS"
                Resolve-Catastrophe -Key 'tailscale-container' -Message "the tailscale container is healthy again."
            }
        } else {
            Resolve-Catastrophe -Key 'tailscale-container' -Message "the tailscale container is healthy again."
        }

        # Test network connectivity. CATASTROPHE + continue: egress being dead
        # inside the tailscale netns breaks every tailnet route, but it tells us
        # nothing about inference or the bridges - which the old early return
        # stopped us from checking at all.
        if (-not (Test-NetworkConnectivity)) {
            Write-LogEntry "Network connectivity failed, attempting recovery..." "WARN"
            if (-not (Repair-TailscaleService)) {
                Write-LogEntry "Failed to restore network connectivity" "ERROR"
                Send-CatastropheAlert -Key 'tailnet-connectivity' -Message "no network egress from the tailscale container and auto-repair failed - every tailnet route (OpenWebUI, Mattermost :8446, wiki, LiteLLM UI) is unreachable. Reply 'status' or 'recover'."
                $script:HealthIssues += 'tailnet-connectivity'
            } else {
                Resolve-Catastrophe -Key 'tailnet-connectivity' -Message "tailnet connectivity is restored."
            }
        } else {
            Resolve-Catastrophe -Key 'tailnet-connectivity' -Message "tailnet connectivity is restored."
        }
    
        # Test the tailscale daemon itself. CATASTROPHE + continue, as above.
        if (-not (Test-TailscaleConnection)) {
            Write-LogEntry "Tailscale connection failed, attempting recovery..." "WARN"
            if (-not (Repair-TailscaleService)) {
                Write-LogEntry "Failed to restore Tailscale connection" "ERROR"
                Send-CatastropheAlert -Key 'tailscale-daemon' -Message "the tailscale daemon is not answering and auto-repair failed - the tailnet is down (OpenWebUI, Mattermost :8446, wiki, LiteLLM UI). Reply 'status', or check 'docker logs tailscale'."
                $script:HealthIssues += 'tailscale-daemon'
            } else {
                Resolve-Catastrophe -Key 'tailscale-daemon' -Message "the tailscale daemon is answering again."
            }
        } else {
            Resolve-Catastrophe -Key 'tailscale-daemon' -Message "the tailscale daemon is answering again."
        }
    
        # Test serve configuration. Additive repair: re-add only missing
        # mappings (never `serve reset`, which would wipe working ones --
        # including the per-service mappings the old code didn't know about).
        # Tailnet NODE login state + key expiry. A logged-out node keeps the
        # container running and tailscaled alive while EVERY serve route silently
        # fails with "Logged out." - the 2026-09-16 failure mode, which nothing
        # here looked for. The expiry warning is the preventive half: node keys
        # expire on a schedule, so this gives notice instead of an outage.
        # Only DEFINITIVE logged-out states page. BackendState is one of NoState /
        # NeedsMachineAuth / NeedsLogin / Stopped / Starting / Running, and treating
        # "anything but Running" as logged out would page on every restart, since the
        # container has a 60s start_period (found in review 2026-09-16).
        $tsNode = Test-TailscaleNodeState
        if ($tsNode.Reachable -and ($tsNode.State -eq 'NeedsLogin')) {
            Send-CatastropheAlert -Key 'tailscale-logout' -Message "the tailscale node is LOGGED OUT (NeedsLogin) - every serve route is gone (OpenWebUI, Mattermost :8446, wiki, LiteLLM UI). Fix: put a fresh TAILSCALE_AUTH_KEY in frontend/.env, then 'docker compose -f frontend/docker-compose.yml up -d --force-recreate --no-deps tailscale'."
            $script:HealthIssues += 'tailscale-logout'
        } elseif ($tsNode.Reachable -and ($tsNode.State -eq 'NeedsMachineAuth')) {
            Send-CatastropheAlert -Key 'tailscale-logout' -Message "the tailscale node needs DEVICE APPROVAL (NeedsMachineAuth) - every serve route is down until it is approved. Approve this machine in the Tailscale admin console; a new auth key will NOT fix this one."
            $script:HealthIssues += 'tailscale-machineauth'
        } elseif ($tsNode.Reachable -and -not $tsNode.LoggedIn) {
            # Starting / NoState / Stopped: transient or mid-restart. Record, do not page.
            Write-LogEntry "Tailscale node is not Running yet (BackendState=$($tsNode.State)) - not paging; the container-health check covers a persistent failure" "WARN"
        } elseif ($tsNode.LoggedIn) {
            Resolve-Catastrophe -Key 'tailscale-logout' -Message "the tailscale node is logged in again."
            if (($null -ne $tsNode.ExpiresInDays) -and ($tsNode.ExpiresInDays -lt 14)) {
                Write-LogEntry "Tailscale node key expires in $($tsNode.ExpiresInDays) days" "WARN"
                Send-TelegramAlert "WARNING ai-stack: the tailscale NODE KEY expires in $($tsNode.ExpiresInDays) days. When it does, every tailnet route - Mattermost included - goes down. Disable key expiry for this node, or re-auth it now." -ThrottleKey 'tailscale-key-expiry' -ThrottleHours 24
            }
        }

        if (-not (Repair-TailscaleServes)) {
            Write-LogEntry "Some tailscale serve mappings could not be restored (see prior WARN/ERROR lines)" "WARN"
            # Non-fatal: openwebui main path may still work even if open_notebook
            # serves are missing; downstream checks (LlamaCpp, OpenTerminal) will
            # exercise their own paths.
        }
    } else {
        Write-LogEntry "tailscale is not part of this deployment (no 'tailscale' service in the frontend render and no container by that name) - skipping the tailscale container, connectivity, node-state and serve-route checks" "INFO"
    }
    
    # Test llama-cpp connectivity
    if (-not (Test-LlamaCppConnectivity)) {
        Write-LogEntry "llama-cpp connectivity failed, attempting recovery..." "WARN"
        if (-not (Repair-LlamaCppConnectivity)) {
            Write-LogEntry "Failed to restore llama-cpp connectivity" "ERROR"
            Send-CatastropheAlert -Key 'inference' -Message "INFERENCE is down - llama-cpp is unreachable through the gateway and auto-repair failed. Every LLM path (OpenWebUI, mnemory, research, the agent org) is dead. Reply 'status' or 'recover'."
            $script:HealthIssues += 'llama-cpp'
        } else {
            Resolve-Catastrophe -Key 'inference' -Message "inference (llama-cpp) is reachable again."
        }
    } else {
        Resolve-Catastrophe -Key 'inference' -Message "inference (llama-cpp) is reachable again."
    }

    # SERVING DEPTH, separate from reachability on purpose. Test-LlamaCppConnectivity
    # above is satisfied by llama-swap's /health, which answers without loading a
    # model - so it passed for thirty hours (2026-09-19 18:54 -> 09-21 00:57) while
    # every chat returned `500 upstream command exited prematurely`, because a
    # recreate had bound the compose default (an empty directory) at /models.
    # Reachable is not the same as able to serve, and only this check can tell.
    if (-not (Test-InferenceServingDepth)) {
        $script:HealthIssues += 'inference-models-empty'
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

    # Verify remaining compose containers (non-critical - log + attempt recovery
    # but do not fail the overall health check). Order matters: mnemory depends
    # on llama-cpp + llama-cpp-embed, which are confirmed healthy above.
    Confirm-AuxiliaryContainer -Container "mnemory"            -RestartWaitSeconds 20 | Out-Null
    Confirm-AuxiliaryContainer -Container "mnemory-backup"      -RestartWaitSeconds 10 | Out-Null
    Confirm-AuxiliaryContainer -Container "openwebui-backup"    -RestartWaitSeconds 10 | Out-Null
    # surrealdb has no HTTP healthcheck (WS-only); just verify the container is up.
    # open_notebook gets a real API probe below - surrealdb must be up first since
    # open_notebook depends on it.
    Confirm-AuxiliaryContainer -Container "surrealdb"           -RestartWaitSeconds 10 | Out-Null

    # Test open-notebook API independently (separate from running-state check -
    # the FastAPI process can be unresponsive while the container is still up).
    # Non-fatal: notebook UI is non-critical for the core LLM/RAG path.
    if (-not (Test-OpenNotebookHealth)) {
        Write-LogEntry "open-notebook API failed, attempting recovery..." "WARN"
        if (-not (Repair-OpenNotebook)) {
            Write-LogEntry "open-notebook recovery failed - notebook UI may be unavailable" "WARN"
        }
    }

    # --- Private web-search gateway plane (SearXNG-over-Tor) - non-critical ---
    # Compose SERVICE keys differ from CONTAINER names here (redis ->
    # search-redis, gateway -> search-gateway; tor retired 2026-08-21). These
    # calls take CONTAINER names - until 2026-08-28 they passed the service keys
    # instead, so `docker inspect` never resolved them. The service key is looked
    # up from stack-services.json when the repair actually runs. Probe /readyz
    # first (covers the whole plane); only ensure the individual containers if it
    # is not ready.
    if (Test-SearchGatewayHealth) {
        Write-LogEntry "search-gateway /healthz OK" "DEBUG"
        # Deep readiness is informational only (slow/Tor-flaky); never drives a restart.
        if (-not (Get-SearchGatewayReady)) {
            Write-LogEntry "search-gateway up but /readyz not ready (vpn/searxng/redis warming or degraded)" "INFO"
        }
    } else {
        Write-LogEntry "search-gateway /healthz down, ensuring web-search plane containers..." "WARN"
        Confirm-AuxiliaryContainer -Container "search-redis"   -RestartWaitSeconds 10 | Out-Null
        Confirm-AuxiliaryContainer -Container "searxng"        -RestartWaitSeconds 15 | Out-Null
        Confirm-AuxiliaryContainer -Container "search-gateway" -RestartWaitSeconds 15 | Out-Null
    }

    # --- little-coder plane (autonomous coding agent) - non-critical ---
    # open-terminal (checked above) is its workspace; these are the agent + its
    # MCP-as-OpenAPI bridge + the egress proxy.
    Confirm-AuxiliaryContainer -Container "little-coder" -RestartWaitSeconds 15 | Out-Null
    Confirm-AuxiliaryContainer -Container "lc-egress"    -RestartWaitSeconds 10 | Out-Null

    # --- mnemory MCP gateway (the bridge clients reach; mnemory itself above) ---
    Confirm-AuxiliaryContainer -Container "mnemory-cloud-gateway" -RestartWaitSeconds 10 | Out-Null

    # --- inference gateway plane (LiteLLM front door + admission queue) ---
    # ALL inference flows through llm-gateway (the llama-cpp:8080 alias) and
    # llm-queue. Test-LlamaCppConnectivity above exercises the data path;
    # these catch the db/UI sidecars the path test can't see.
    # CATASTROPHE tier: llm-gateway is the ONLY front door for inference (it
    # carries the llama-cpp alias) and llm-queue is its admission controller.
    # If either will not come back, every LLM caller is refused even when the
    # upstream servers are perfectly healthy. Capture the result instead of
    # discarding it - Write-LogEntry writes to the INFORMATION stream, so the
    # success stream here is just the boolean; @()/Select -Last 1 guards that.
    $queueOk   = [bool](@(Confirm-AuxiliaryContainer -Container "llm-queue"   -RestartWaitSeconds 15) | Select-Object -Last 1)
    $gatewayOk = [bool](@(Confirm-AuxiliaryContainer -Container "llm-gateway" -RestartWaitSeconds 20) | Select-Object -Last 1)
    if (-not ($queueOk -and $gatewayOk)) {
        $dead = @()
        if (-not $gatewayOk) { $dead += 'llm-gateway' }
        if (-not $queueOk)   { $dead += 'llm-queue' }
        Send-CatastropheAlert -Key 'llm-gateway' -Message ("the INFERENCE FRONT DOOR is down - " + ($dead -join ' + ') + " did not recover. All LLM traffic is refused (gateway-only routing). Reply 'status' or 'recover'.")
        $script:HealthIssues += ($dead -join '+')
    } else {
        Resolve-Catastrophe -Key 'llm-gateway' -Message "llm-gateway and llm-queue are healthy again."
    }
    Confirm-AuxiliaryContainer -Container "llm-gateway-db" -RestartWaitSeconds 15 | Out-Null
    Confirm-AuxiliaryContainer -Container "llm-gateway-ui" -RestartWaitSeconds 15 | Out-Null

    # --- remaining main-stack backup sidecars (cron loops; mnemory-backup and
    # openwebui-backup are confirmed above; portal backups (caddy/authelia) are
    # deliberately NOT here - the portal has its own lifecycle (portal-on/off)
    # and must not be auto-started; OB/agent-org backups live in their own
    # Invoke-*Health blocks. Test-BackupRecency below watches everyone's OUTPUT.
    Confirm-AuxiliaryContainer -Container "little-coder-backup"  -RestartWaitSeconds 10 | Out-Null
    Confirm-AuxiliaryContainer -Container "llm-gateway-backup"   -RestartWaitSeconds 10 | Out-Null
    Confirm-AuxiliaryContainer -Container "lm-models-backup"     -RestartWaitSeconds 10 | Out-Null
    Confirm-AuxiliaryContainer -Container "tailscale-backup"     -RestartWaitSeconds 10 | Out-Null
    Confirm-AuxiliaryContainer -Container "open-notebook-backup" -RestartWaitSeconds 10 | Out-Null

    # --- EVERY container, not a list: crash loops + orphaned network
    # namespaces (see the CONTAINER LOOPS section). Reports, never repairs.
    if (-not [bool](@(Invoke-ContainerLoopCheck) | Select-Object -Last 1)) {
        $script:HealthIssues += 'container-loop'
    }

    # --- Open Brain stack (SEPARATE compose project) incl. mcp stale-pool guard ---
    Invoke-OpenBrainHealth

    # --- agent-org stack (SEPARATE compose project) incl. bridge stale-pool +
    #     ao-git-egress stale-mount guards, + its nightly pg_dump backup sidecars ---
    Invoke-AgentOrgHealth

    # --- SYSADMIN FIRST (operator directive 2026-08-23: "the sysadmin above all
    # else doesn't go down"). Liveness + FUNCTIONAL beacon for the sysadmin
    # persona bridge (48292), then the break-glass Telegram listener (48293),
    # then the claude-sessions bridge -- in that priority order.
    Confirm-HostTaskByPort -TaskName 'sysadmin-bridge' -Port 48292 -Label 'sysadmin bridge' | Out-Null
    Confirm-BridgeFunctionalHealth -TaskName 'sysadmin-bridge' -Port 48292 -Label 'sysadmin bridge' `
        -HealthPath (Join-Path $PROJECT_DIR 'scripts\sysadmin-mcp\bridge-state\health.json')
    Confirm-HostTaskByPort -TaskName 'sysadmin-telegram-listener' -Port 48293 -Label 'telegram listener' | Out-Null

    # --- claude-sessions bridge (Mattermost <-> Claude, HOST Scheduled Task) ---
    # After Invoke-AgentOrgHealth so the Mattermost container it connects to has
    # just been confirmed/repaired. Non-fatal for the overall check.
    Confirm-ClaudeSessionsBridge | Out-Null
    Confirm-BridgeFunctionalHealth -TaskName 'claude-sessions-bridge' -Port 48291 -Label 'claude-sessions bridge' `
        -HealthPath (Join-Path $PROJECT_DIR 'scripts\claude-sessions-bridge\state\health.json')

    # --- backup OUTPUT recency (the 13 backups/<dir> trees in $ExpectedBackupRecency,
    #     portal + OB included; counted 2026-09-21, and see that list's note on the
    #     two ao-worker journal dirs check_backups.py watches and this does not) ---
    # Non-fatal for the overall check, but logs ERROR + Mattermost-alerts:
    # a running sidecar that produces nothing is invisible to container checks.
    # Feed the result into the cycle summary. NOT catastrophe tier (operator
    # decision 2026-09-16: stale backups are not a page-the-phone event), but
    # it must stop the log claiming "All health checks passed".
    if (-not [bool](@(Test-BackupRecency) | Select-Object -Last 1)) {
        $script:HealthIssues += 'backup-stale'
    }

    # Honest summary. This used to print "All health checks passed" even when
    # checks above had logged ERROR (BACKUP STALE, for one), so the log read
    # green during real faults.
    if ($script:HealthIssues.Count -gt 0) {
        $issues = ($script:HealthIssues | Select-Object -Unique) -join ', '
        Write-LogEntry "Health check completed WITH ISSUES: $issues" "ERROR"
        return $false
    }
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
    
    "loops" {
        # REPORT-ONLY: the container-loop census and nothing else. No repair,
        # no compose, no Docker Desktop restart (Confirm-DockerEngine is NOT
        # called - it can stop Docker Desktop and shut WSL down), no host task
        # restarts. Only read-only docker calls (version, ps, inspect, logs),
        # each bounded, and alerts through the catastrophe path.
        $engine = Invoke-BoundedDocker -DockerArgs @('version', '--format', '{{.Server.Version}}')
        if ($null -eq $engine) {
            Write-LogEntry "loops mode: docker engine did not answer ($script:BoundedFailureReason) - nothing checked, nothing repaired" "ERROR"
            exit 2
        }
        $Success = [bool](@(Invoke-ContainerLoopCheck) | Select-Object -Last 1)
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
        Write-Host "Usage: stack-watchdog.ps1 [-Mode check|loops|daemon|install-service] [-IntervalSeconds 60]"
        Write-Host ""
        Write-Host "Modes:"
        Write-Host "  check           - Run single health check (default)"
        Write-Host "  loops           - Report-only: crash loops + orphaned netns across every container, no repair"
        Write-Host "  daemon          - Run continuously as daemon"
        Write-Host "  install-service - Install as Windows Service (requires admin)"
        exit 1
    }
}
