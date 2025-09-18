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
function Repair-TailscaleService {
    Write-LogEntry "Starting Tailscale service recovery..." "WARN"
    
    try {
        # First try gentle restart (preserves network namespace)
        Write-LogEntry "Attempting gentle restart..."
        docker compose stop tailscale | Out-Null
        Start-Sleep 5
        
        docker compose start tailscale | Out-Null
        Start-Sleep 30
        
        # Verify gentle restart worked
        if (Test-NetworkConnectivity -and Test-TailscaleConnection) {
            Write-LogEntry "Gentle restart successful" "SUCCESS"
            return $true
        }
        
        # If gentle restart failed, try network namespace recovery
        Write-LogEntry "Gentle restart failed, attempting network namespace recovery..." "WARN"
        
        # Use the proper network namespace recovery method
        docker compose down tailscale | Out-Null
        Start-Sleep 3
        docker compose up -d tailscale | Out-Null
        Start-Sleep 45
        
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
    
    # Check OpenWebUI health first
    if (-not (Test-ServiceHealth "openwebui")) {
        Write-LogEntry "OpenWebUI is not healthy, waiting..." "WARN"
        return $false
    }
    
    # Check if Tailscale container is running
    if (-not (Test-ServiceHealth "tailscale")) {
        Write-LogEntry "Tailscale container not running, starting..." "WARN"
        docker compose up -d tailscale | Out-Null
        Start-Sleep 30
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
