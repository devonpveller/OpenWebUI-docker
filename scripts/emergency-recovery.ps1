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
    
    # Phase 1: Graceful shutdown in reverse dependency order
    Write-Log "INFO" "Phase 1: Graceful shutdown"
    Write-Log "WARN" "This will restart OpenWebUI and Tailscale services"
    
    # Stop Tailscale first (dependent on OpenWebUI network)
    if (-not (Stop-ServiceGracefully "tailscale" 30)) {
        Write-Log "WARN" "Tailscale stop had issues, continuing..."
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
        if (-not (Wait-ForHealthy "openwebui" 120)) {
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
        Write-Log "INFO" "Tailscale status:"
        docker compose exec tailscale tailscale --socket=/tmp/tailscaled.sock status
        
        Write-Log "INFO" "Tailscale serve configuration:"
        docker compose exec tailscale tailscale --socket=/tmp/tailscaled.sock serve status
    }
    catch {
        Write-Log "WARN" "Unable to verify Tailscale configuration: $_"
    }
    
    Write-Log "SUCCESS" "========================================="
    Write-Log "SUCCESS" "EMERGENCY RECOVERY COMPLETED"
    Write-Log "SUCCESS" "========================================="
}

function Invoke-NuclearRecovery {
    Write-Log "WARN" "========================================="
    Write-Log "WARN" "NUCLEAR RECOVERY - FULL STACK RESTART"
    Write-Log "WARN" "========================================="
    
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
    Write-Log "INFO" "GPU RECOVERY - REBUILDING OPENWEBUI"
    Write-Log "INFO" "========================================="
    
    Write-Log "INFO" "Stopping OpenWebUI for GPU reset..."
    docker compose down openwebui
    
    Write-Log "INFO" "Rebuilding OpenWebUI with fresh GPU configuration..."
    docker compose build --no-cache openwebui
    
    Write-Log "INFO" "Starting OpenWebUI with GPU support..."
    docker compose up -d openwebui
    
    if (Wait-ForHealthy "openwebui" 180) {
        if (Test-GPUAvailability) {
            Write-Log "SUCCESS" "GPU reset successful - CUDA is available"
        }
        else {
            Write-Log "ERROR" "GPU reset failed - CUDA not available"
            throw "GPU reset failed"
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