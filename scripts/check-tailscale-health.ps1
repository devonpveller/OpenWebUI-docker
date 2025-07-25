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

# Function to recover Tailscale service
function Repair-TailscaleService {
    Write-Log "Starting Tailscale service recovery..." "WARN"
    
    try {
        # Stop Tailscale container
        Write-Log "Stopping Tailscale container..."
        docker compose stop tailscale | Out-Null
        Start-Sleep 5
        
        # Start Tailscale container
        Write-Log "Starting Tailscale container..."
        docker compose start tailscale | Out-Null
        Start-Sleep 30
        
        # Verify recovery
        if (Test-NetworkConnectivity -and Test-TailscaleConnection) {
            Write-Log "Tailscale service recovery successful" "SUCCESS"
            return $true
        } else {
            Write-Log "Recovery failed, attempting full restart..." "WARN"
            docker compose down tailscale | Out-Null
            docker compose up -d tailscale | Out-Null
            Start-Sleep 45
            
            return (Test-NetworkConnectivity -and Test-TailscaleConnection)
        }
    } catch {
        Write-Log "Recovery failed with error: $($_.Exception.Message)" "ERROR"
        return $false
    }
}

# Function to perform comprehensive health check
function Invoke-HealthCheck {
    Write-Log "Starting comprehensive health check..."
    
    # Change to project directory
    Set-Location $ProjectDir
    
    # Check OpenWebUI health first
    if (-not (Test-ServiceHealth "openwebui")) {
        Write-Log "OpenWebUI is not healthy, waiting..." "WARN"
        return $false
    }
    
    # Check if Tailscale container is running
    if (-not (Test-ServiceHealth "tailscale")) {
        Write-Log "Tailscale container not running, starting..." "WARN"
        docker compose up -d tailscale | Out-Null
        Start-Sleep 30
    }
    
    # Test network connectivity
    if (-not (Test-NetworkConnectivity)) {
        Write-Log "Network connectivity failed, attempting recovery..." "WARN"
        if (-not (Repair-TailscaleService)) {
            Write-Log "Failed to restore network connectivity" "ERROR"
            return $false
        }
    }
    
    # Test Tailscale connection
    if (-not (Test-TailscaleConnection)) {
        Write-Log "Tailscale connection failed, attempting recovery..." "WARN"
        if (-not (Repair-TailscaleService)) {
            Write-Log "Failed to restore Tailscale connection" "ERROR"
            return $false
        }
    }
    
    # Test serve configuration
    if (-not (Test-ServeConfiguration)) {
        Write-Log "Serve configuration missing, reconfiguring..." "WARN"
        try {
            docker compose exec -T tailscale tailscale --socket=/tmp/tailscaled.sock serve reset | Out-Null
            docker compose exec -T tailscale tailscale --socket=/tmp/tailscaled.sock serve --https=443 --bg http://127.0.0.1:8080 | Out-Null
            Write-Log "Serve configuration restored"
        } catch {
            Write-Log "Failed to restore serve configuration: $($_.Exception.Message)" "ERROR"
            return $false
        }
    }
    
    Write-Log "All health checks passed" "SUCCESS"
    return $true
}

# Function to run as a daemon
function Start-Daemon {
    Write-Log "Starting Tailscale Health Monitor daemon (interval: ${IntervalSeconds}s)"
    
    while ($true) {
        try {
            Invoke-HealthCheck | Out-Null
            Start-Sleep $IntervalSeconds
        } catch {
            Write-Log "Daemon error: $($_.Exception.Message)" "ERROR"
            Start-Sleep 30
        }
    }
}

# Function to install as Windows Service
function Install-WindowsService {
    $ServicePath = "powershell.exe -File `"$($MyInvocation.MyCommand.Path)`" -Mode daemon"
    
    # Check if service already exists
    if (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue) {
        Write-Log "Service $ServiceName already exists. Removing first..."
        Stop-Service -Name $ServiceName -Force
        sc.exe delete $ServiceName
        Start-Sleep 5
    }
    
    # Create the service
    Write-Log "Installing Windows Service: $ServiceName"
    sc.exe create $ServiceName binPath= $ServicePath start= auto
    sc.exe description $ServiceName "Autonomous Tailscale Health Monitor for AI Stack"
    
    # Start the service
    Start-Service -Name $ServiceName
    Write-Log "Service installed and started successfully"
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
            Write-Log "Administrator privileges required to install service" "ERROR"
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
