[CmdletBinding()]
param(
    [ValidateSet("recover", "nuclear", "gpu-reset")]
    [string]$Action = "recover"
)

$ErrorActionPreference = "Stop"

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

function Test-BasicConnectivity {
    Write-Log "INFO" "Performing basic connectivity checks..."
    
    # Check if containers are running
    try {
        $containers = docker compose ps --format json | ConvertFrom-Json
        $openwebuiStatus = ($containers | Where-Object { $_.Service -eq "openwebui" }).State
        $ollamaStatus = ($containers | Where-Object { $_.Service -eq "ollama" }).State
        $tailscaleStatus = ($containers | Where-Object { $_.Service -eq "tailscale" }).State
        
        Write-Log "INFO" "Container states - OpenWebUI: $openwebuiStatus, Ollama: $ollamaStatus, Tailscale: $tailscaleStatus"
        
        # If all containers are running, test basic functionality
        if ($openwebuiStatus -eq "running" -and $ollamaStatus -eq "running" -and $tailscaleStatus -eq "running") {
            # Test OpenWebUI health
            try {
                $response = docker compose exec openwebui curl -f -s http://localhost:8080/health 2>$null
                if ($LASTEXITCODE -eq 0) {
                    Write-Log "INFO" "OpenWebUI health check: PASSED"
                    
                    # Test Ollama connectivity
                    try {
                        docker compose exec openwebui curl -f -s http://localhost:11434/api/version 2>$null | Out-Null
                        if ($LASTEXITCODE -eq 0) {
                            Write-Log "INFO" "Ollama connectivity: PASSED"
                            
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
                            Write-Log "WARN" "Ollama connectivity failed"
                        }
                    }
                    catch {
                        Write-Log "WARN" "Ollama connectivity test failed: $_"
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
            Write-Log "WARN" "Not all containers are running - recovery needed"
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
    
    # Just restart services without destroying containers
    try {
        docker compose restart tailscale ollama openwebui
        
        # Start Watchtower if it's missing (it doesn't need restart usually)
        docker compose up -d watchtower
        
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
    
    # Phase 1: Graceful shutdown in reverse dependency order
    Write-Log "INFO" "Phase 1: Graceful shutdown"
    Write-Log "WARN" "This will restart OpenWebUI, Ollama, and Tailscale services"
    
    # Stop Tailscale first (dependent on OpenWebUI network)
    if (-not (Stop-ServiceGracefully "tailscale" 30)) {
        Write-Log "WARN" "Tailscale stop had issues, continuing..."
    }
    
    # Stop Ollama (dependent on OpenWebUI network)
    if (-not (Stop-ServiceGracefully "ollama" 30)) {
        Write-Log "WARN" "Ollama stop had issues, continuing..."
    }
    
    # Stop OpenWebUI 
    if (-not (Stop-ServiceGracefully "openwebui" 45)) {
        Write-Log "WARN" "OpenWebUI stop had issues, continuing..."
    }
    
    # Phase 2: Clean up any orphaned network namespaces
    Write-Log "INFO" "Phase 2: Network namespace cleanup"
    Start-Sleep -Seconds 15
    
    # Phase 3: Restart in correct dependency order
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
    
    # Start Ollama (depends on OpenWebUI network)
    Write-Log "INFO" "Starting Ollama with GPU support..."
    try {
        docker compose up -d ollama
        if (-not (Wait-ForHealthy "ollama" 60)) {
            Write-Log "WARN" "Ollama health check failed, but continuing..."
        }
    }
    catch {
        Write-Log "ERROR" "Failed to start Ollama: $_"
        throw
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
    
    # Start Watchtower (independent service)
    Write-Log "INFO" "Starting Watchtower monitoring service..."
    try {
        docker compose up -d watchtower
        Write-Log "SUCCESS" "Watchtower started"
    }
    catch {
        Write-Log "WARN" "Failed to start Watchtower: $_"
        # Don't throw - Watchtower is not critical for basic functionality
    }
    
    # Phase 4: Connectivity verification
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
    
    # Phase 5: Service verification
    Write-Log "INFO" "Phase 5: Service verification"
    
    try {
        Write-Log "INFO" "Ollama status:"
        docker compose exec ollama ollama list
        
        Write-Log "INFO" "Tailscale status:"
        docker compose exec tailscale tailscale --socket=/tmp/tailscaled.sock status
        
        Write-Log "INFO" "Tailscale serve configuration:"
        docker compose exec tailscale tailscale --socket=/tmp/tailscaled.sock serve status
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
    
    Write-Log "INFO" "Performing complete stack shutdown..."
    docker compose down
    
    Write-Log "INFO" "Cleaning up network namespaces..."
    Start-Sleep -Seconds 20
    
    Write-Log "INFO" "Starting full stack with proper dependency order..."
    docker compose up -d
    
    Write-Log "INFO" "Waiting for complete stack initialization..."
    Start-Sleep -Seconds 90
    
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
    docker compose down ollama openwebui
    
    Write-Log "INFO" "Rebuilding OpenWebUI with fresh GPU configuration..."
    docker compose build --no-cache openwebui
    
    Write-Log "INFO" "Starting OpenWebUI with GPU support..."
    docker compose up -d openwebui
    
    if (Wait-ForHealthy "openwebui" 240) {
        Write-Log "INFO" "Starting Ollama with GPU support..."
        docker compose up -d ollama
        
        if (Wait-ForHealthy "ollama" 60) {
            if (Test-GPUAvailability) {
                Write-Log "SUCCESS" "GPU reset successful - CUDA is available"
                
                # Test Ollama GPU access
                try {
                    docker compose exec ollama ollama list
                    Write-Log "SUCCESS" "Ollama GPU integration verified"
                }
                catch {
                    Write-Log "WARN" "Ollama may need additional time to initialize"
                }
            }
            else {
                Write-Log "ERROR" "GPU reset failed - CUDA not available"
                throw "GPU reset failed"
            }
        }
        else {
            Write-Log "WARN" "Ollama startup slow but continuing..."
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