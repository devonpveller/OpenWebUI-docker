# Install Tailscale Health Monitor as Windows Service
# Run this script as Administrator

param(
    [Parameter(Mandatory=$false)]
    [string]$Action = "install",  # "install", "uninstall", "restart", "status"
    
    [Parameter(Mandatory=$false)]
    [int]$IntervalSeconds = 60
)

$ServiceName = "TailscaleHealthMonitor"
$ServiceDisplayName = "Tailscale Health Monitor"
$ServiceDescription = "Autonomous Tailscale Health Monitor for AI Stack - monitors and recovers Tailscale connectivity issues"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$HealthScript = Join-Path $ScriptDir "check-tailscale-health.ps1"

# Check if running as Administrator
function Test-IsAdmin {
    return ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole] "Administrator")
}

# Function to install the service
function Install-Service {
    if (-not (Test-IsAdmin)) {
        Write-Host "ERROR: Administrator privileges required to install service" -ForegroundColor Red
        Write-Host "Please run PowerShell as Administrator and try again."
        exit 1
    }
    
    # Check if script exists
    if (-not (Test-Path $HealthScript)) {
        Write-Host "ERROR: Health check script not found at: $HealthScript" -ForegroundColor Red
        exit 1
    }
    
    # Remove existing service if it exists
    $ExistingService = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if ($ExistingService) {
        Write-Host "Removing existing service..." -ForegroundColor Yellow
        Stop-Service -Name $ServiceName -Force -ErrorAction SilentlyContinue
        sc.exe delete $ServiceName | Out-Null
        Start-Sleep 3
    }
    
    # Create the service
    $ServicePath = "powershell.exe -ExecutionPolicy Bypass -File `"$HealthScript`" -Mode daemon -IntervalSeconds $IntervalSeconds"
    
    Write-Host "Installing service: $ServiceDisplayName" -ForegroundColor Green
    Write-Host "Service Path: $ServicePath" -ForegroundColor Gray
    
    # Use New-Service cmdlet instead of sc.exe for better PowerShell integration
    try {
        New-Service -Name $ServiceName -BinaryPathName $ServicePath -DisplayName $ServiceDisplayName -StartupType Automatic -Description $ServiceDescription
        $ServiceCreated = $true
    } catch {
        Write-Host "New-Service failed, trying sc.exe method..." -ForegroundColor Yellow
        sc.exe create $ServiceName binPath= $ServicePath start= auto DisplayName= $ServiceDisplayName
        $ServiceCreated = ($LASTEXITCODE -eq 0)
    }
    
    if ($ServiceCreated) {
    if ($ServiceCreated) {
        Write-Host "Service installed successfully!" -ForegroundColor Green
        
        # Set description if using sc.exe method
        sc.exe description $ServiceName $ServiceDescription | Out-Null
        
        Write-Host "Starting service..." -ForegroundColor Green
        Start-Service -Name $ServiceName
        
        if ((Get-Service -Name $ServiceName).Status -eq "Running") {
            Write-Host "Service started successfully!" -ForegroundColor Green
            Write-Host ""
            Write-Host "Service Details:" -ForegroundColor Cyan
            Write-Host "  Name: $ServiceName"
            Write-Host "  Display Name: $ServiceDisplayName"
            Write-Host "  Check Interval: $IntervalSeconds seconds"
            Write-Host "  Log Location: logs\tailscale-health.log"
            Write-Host ""
            Write-Host "Management Commands:" -ForegroundColor Cyan
            Write-Host "  Status:    .\install-service.ps1 -Action status"
            Write-Host "  Restart:   .\install-service.ps1 -Action restart"
            Write-Host "  Uninstall: .\install-service.ps1 -Action uninstall"
        } else {
            Write-Host "WARNING: Service installed but failed to start" -ForegroundColor Yellow
        }
    } else {
        Write-Host "ERROR: Failed to install service" -ForegroundColor Red
        Write-Host "Make sure you're running PowerShell as Administrator" -ForegroundColor Yellow
        exit 1
    }
}

# Function to uninstall the service
function Uninstall-Service {
    if (-not (Test-IsAdmin)) {
        Write-Host "ERROR: Administrator privileges required to uninstall service" -ForegroundColor Red
        exit 1
    }
    
    $ExistingService = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if ($ExistingService) {
        Write-Host "Stopping and removing service: $ServiceDisplayName" -ForegroundColor Yellow
        Stop-Service -Name $ServiceName -Force -ErrorAction SilentlyContinue
        sc.exe delete $ServiceName | Out-Null
        
        if ($LASTEXITCODE -eq 0) {
            Write-Host "Service uninstalled successfully!" -ForegroundColor Green
        } else {
            Write-Host "ERROR: Failed to uninstall service" -ForegroundColor Red
            exit 1
        }
    } else {
        Write-Host "Service not found: $ServiceName" -ForegroundColor Yellow
    }
}

# Function to restart the service
function Restart-Service {
    $ExistingService = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if ($ExistingService) {
        Write-Host "Restarting service: $ServiceDisplayName" -ForegroundColor Yellow
        Restart-Service -Name $ServiceName -Force
        
        Start-Sleep 3
        $Status = (Get-Service -Name $ServiceName).Status
        if ($Status -eq "Running") {
            Write-Host "Service restarted successfully!" -ForegroundColor Green
        } else {
            Write-Host "WARNING: Service restart may have failed. Status: $Status" -ForegroundColor Yellow
        }
    } else {
        Write-Host "Service not found: $ServiceName" -ForegroundColor Red
        Write-Host "Use -Action install to install the service first."
    }
}

# Function to show service status
function Show-ServiceStatus {
    $ExistingService = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if ($ExistingService) {
        Write-Host "Service Status for: $ServiceDisplayName" -ForegroundColor Cyan
        Write-Host "  Name: $($ExistingService.Name)"
        Write-Host "  Status: $($ExistingService.Status)" -ForegroundColor $(if ($ExistingService.Status -eq "Running") { "Green" } else { "Red" })
        Write-Host "  Start Type: $($ExistingService.StartType)"
        
        # Show recent log entries
        $LogFile = Join-Path (Split-Path -Parent $ScriptDir) "logs\tailscale-health.log"
        if (Test-Path $LogFile) {
            Write-Host ""
            Write-Host "Recent Log Entries (last 10):" -ForegroundColor Cyan
            Get-Content $LogFile -Tail 10 | ForEach-Object {
                if ($_ -match "\[ERROR\]") {
                    Write-Host $_ -ForegroundColor Red
                } elseif ($_ -match "\[WARN\]") {
                    Write-Host $_ -ForegroundColor Yellow
                } elseif ($_ -match "\[SUCCESS\]") {
                    Write-Host $_ -ForegroundColor Green
                } else {
                    Write-Host $_
                }
            }
        }
    } else {
        Write-Host "Service not installed: $ServiceName" -ForegroundColor Red
        Write-Host "Use -Action install to install the service."
    }
}

# Main execution
switch ($Action.ToLower()) {
    "install" {
        Install-Service
    }
    
    "uninstall" {
        Uninstall-Service
    }
    
    "restart" {
        Restart-Service
    }
    
    "status" {
        Show-ServiceStatus
    }
    
    default {
        Write-Host "Tailscale Health Monitor Service Manager" -ForegroundColor Cyan
        Write-Host ""
        Write-Host "Usage: .\install-service.ps1 [-Action install|uninstall|restart|status] [-IntervalSeconds 60]"
        Write-Host ""
        Write-Host "Actions:" -ForegroundColor Yellow
        Write-Host "  install    - Install service (requires Administrator)"
        Write-Host "  uninstall  - Remove service (requires Administrator)"
        Write-Host "  restart    - Restart running service"
        Write-Host "  status     - Show service status and recent logs"
        Write-Host ""
        Write-Host "Options:" -ForegroundColor Yellow
        Write-Host "  -IntervalSeconds  - Health check interval in seconds (default: 60)"
        Write-Host ""
        Write-Host "Examples:" -ForegroundColor Green
        Write-Host "  .\install-service.ps1 -Action install"
        Write-Host "  .\install-service.ps1 -Action install -IntervalSeconds 30"
        Write-Host "  .\install-service.ps1 -Action status"
    }
}
