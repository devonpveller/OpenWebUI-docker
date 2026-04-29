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

# Function to test serve configuration
function Test-ServeConfiguration {
    [CmdletBinding()]
    param()
    
    try {
        $Result = docker compose exec -T tailscale tailscale --socket=/tmp/tailscaled.sock serve status 2>$null
        return ($Result -like "*127.0.0.1:8080*")
    }
    catch {
        return $false
    }
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
        
        # Check for common Docker build issues in logs
        $Logs = docker compose logs tailscale --tail=5 2>$null
        if ($Logs -match "no such file or directory" -and $Logs -match "entrypoint") {
            Write-LogEntry "CRITICAL: Entrypoint script not found in container. Rebuild required." "ERROR"
            Write-LogEntry "Run: docker compose build --no-cache tailscale" "INFO"
            return $false
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
        # Check if llama-cpp container is running
        if (-not (Test-ServiceHealth "llama-cpp")) {
            Write-LogEntry "llama-cpp container not running, starting..." "WARN"
            docker compose up -d llama-cpp | Out-Null
            Start-Sleep 30
            
            if (-not (Test-ServiceHealth "llama-cpp")) {
                Write-LogEntry "Failed to start llama-cpp container" "ERROR"
                return $false
            }
        }
        
        # Also check llama-cpp-embed
        if (-not (Test-ServiceHealth "llama-cpp-embed")) {
            Write-LogEntry "llama-cpp-embed container not running, starting..." "WARN"
            docker compose up -d llama-cpp-embed | Out-Null
            Start-Sleep 15
        }
        
        # Wait for llama-cpp API to become available
        Write-LogEntry "Waiting for llama-cpp API to become ready..."
        $MaxWaitTime = 120
        $WaitTime = 0
        
        while ($WaitTime -lt $MaxWaitTime) {
            try {
                docker compose exec -T llama-cpp curl -s -f --max-time 5 http://localhost:8080/health | Out-Null
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
            Write-LogEntry "llama-cpp API ready but health check still failing, trying container restart" "WARN"
            
            # Restart both services
            docker compose restart openwebui | Out-Null
            Start-Sleep 45  # Wait for GPU initialization
            
            docker compose restart llama-cpp llama-cpp-embed | Out-Null
            Start-Sleep 30
            
            # Final connectivity test
            if (Test-LlamaCppConnectivity) {
                Write-LogEntry "Container restart restored llama-cpp connectivity" "SUCCESS"
                return $true
            } else {
                Write-LogEntry "Failed to restore llama-cpp connectivity after restart" "ERROR"
                return $false
            }
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
        $Response = docker compose exec -T openwebui curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/health 2>$null
        if ($LASTEXITCODE -eq 0 -and $Response -eq "200") {
            Write-LogEntry "Open Terminal health check passed" "DEBUG"
            return $true
        } else {
            Write-LogEntry "Open Terminal is not responding on localhost:8000 (HTTP $Response)" "WARN"
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
    try {
        Write-LogEntry "Testing llama-cpp connectivity..." "DEBUG"

        # Test if llama-cpp service is running and accessible
        $LlamaCppResponse = docker compose exec -T llama-cpp curl -s -f --max-time 10 http://localhost:8080/health 2>$null

        if ($LASTEXITCODE -eq 0 -and $LlamaCppResponse) {
            Write-LogEntry "llama-cpp connectivity verified" "DEBUG"
            return $true
        } else {
            Write-LogEntry "llama-cpp not responding on localhost:8080" "WARN"
            return $false
        }
    } catch {
        Write-LogEntry "Failed to test llama-cpp connectivity: $($_.Exception.Message)" "ERROR"
        return $false
    }
}

# Function to test llama-cpp-embed connectivity (independent of main llama-cpp).
# Embed has its own model/process and can fail while main llama-cpp is healthy.
function Test-LlamaCppEmbedConnectivity {
    try {
        Write-LogEntry "Testing llama-cpp-embed connectivity..." "DEBUG"
        $EmbedResponse = docker compose exec -T llama-cpp-embed curl -s -f --max-time 10 http://localhost:8080/health 2>$null
        if ($LASTEXITCODE -eq 0 -and $EmbedResponse) {
            Write-LogEntry "llama-cpp-embed connectivity verified" "DEBUG"
            return $true
        } else {
            Write-LogEntry "llama-cpp-embed not responding on localhost:8080" "WARN"
            return $false
        }
    } catch {
        Write-LogEntry "Failed to test llama-cpp-embed connectivity: $($_.Exception.Message)" "ERROR"
        return $false
    }
}

# Function to repair llama-cpp-embed (start if missing, restart otherwise).
function Repair-LlamaCppEmbed {
    Write-LogEntry "Starting llama-cpp-embed recovery..." "WARN"
    try {
        if (-not (Test-ServiceHealth "llama-cpp-embed")) {
            Write-LogEntry "llama-cpp-embed container not running, starting..." "WARN"
            docker compose up -d llama-cpp-embed | Out-Null
        } else {
            Write-LogEntry "llama-cpp-embed running but unresponsive, restarting..." "WARN"
            docker compose restart llama-cpp-embed | Out-Null
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
    
    # Test serve configuration
    if (-not (Test-ServeConfiguration)) {
        Write-LogEntry "Serve configuration missing, reconfiguring..." "WARN"
        try {
            docker compose exec -T tailscale tailscale --socket=/tmp/tailscaled.sock serve reset | Out-Null
            docker compose exec -T tailscale tailscale --socket=/tmp/tailscaled.sock serve --https=443 --bg http://127.0.0.1:8080 | Out-Null
            Write-LogEntry "Serve configuration restored"
        } catch {
            Write-LogEntry "Failed to restore serve configuration: $($_.Exception.Message)" "ERROR"
            return $false
        }
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
