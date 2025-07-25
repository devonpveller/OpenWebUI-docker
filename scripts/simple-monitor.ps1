# Simple Tailscale Monitor Launcher
# This script provides an alternative to Windows Service installation

param(
    [Parameter(Mandatory=$false)]
    [string]$Action = "start",  # "start", "stop", "status", "restart"
    
    [Parameter(Mandatory=$false)]
    [int]$IntervalSeconds = 60
)

$ProcessName = "TailscaleHealthMonitor"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$HealthScript = Join-Path $ScriptDir "check-tailscale-health.ps1"
$ProjectDir = Split-Path -Parent $ScriptDir
$PidFile = Join-Path $ProjectDir "logs\tailscale-monitor.pid"

# Create logs directory if it doesn't exist
$LogDir = Split-Path -Parent $PidFile
if (!(Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}

# Function to check if monitor is running
function Test-MonitorRunning {
    if (Test-Path $PidFile) {
        $ProcessId = Get-Content $PidFile -ErrorAction SilentlyContinue
        if ($ProcessId) {
            $Process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
            if ($Process -and $Process.ProcessName -eq "powershell") {
                return $Process
            }
        }
    }
    return $null
}

# Function to start the monitor
function Start-Monitor {
    $RunningProcess = Test-MonitorRunning
    if ($RunningProcess) {
        Write-Host "Tailscale Health Monitor is already running (PID: $($RunningProcess.Id))" -ForegroundColor Yellow
        return
    }
    
    Write-Host "Starting Tailscale Health Monitor..." -ForegroundColor Green
    Write-Host "Monitor will check every $IntervalSeconds seconds" -ForegroundColor Gray
    Write-Host "Press Ctrl+C to stop the monitor" -ForegroundColor Gray
    Write-Host ""
    
    # Start the health monitor in daemon mode
    $Job = Start-Process -FilePath "powershell.exe" -ArgumentList @(
        "-ExecutionPolicy", "Bypass",
        "-File", "`"$HealthScript`"",
        "-Mode", "daemon", 
        "-IntervalSeconds", $IntervalSeconds
    ) -WindowStyle Hidden -PassThru
    
    # Save PID
    $Job.Id | Out-File -FilePath $PidFile
    
    Write-Host "Monitor started successfully (PID: $($Job.Id))" -ForegroundColor Green
    Write-Host "Log file: logs\tailscale-health.log" -ForegroundColor Gray
    Write-Host ""
    Write-Host "Commands:" -ForegroundColor Cyan
    Write-Host "  Status:  .\simple-monitor.ps1 -Action status"
    Write-Host "  Stop:    .\simple-monitor.ps1 -Action stop"
    Write-Host "  Restart: .\simple-monitor.ps1 -Action restart"
}

# Function to stop the monitor
function Stop-Monitor {
    $RunningProcess = Test-MonitorRunning
    if ($RunningProcess) {
        Write-Host "Stopping Tailscale Health Monitor (PID: $($RunningProcess.Id))..." -ForegroundColor Yellow
        $RunningProcess | Stop-Process -Force
        Remove-Item $PidFile -ErrorAction SilentlyContinue
        Write-Host "Monitor stopped successfully" -ForegroundColor Green
    } else {
        Write-Host "Tailscale Health Monitor is not running" -ForegroundColor Yellow
    }
}

# Function to show monitor status
function Show-MonitorStatus {
    $RunningProcess = Test-MonitorRunning
    if ($RunningProcess) {
        Write-Host "Tailscale Health Monitor Status" -ForegroundColor Cyan
        Write-Host "  Status: Running" -ForegroundColor Green
        Write-Host "  PID: $($RunningProcess.Id)"
        Write-Host "  Started: $($RunningProcess.StartTime)"
        Write-Host "  CPU Time: $($RunningProcess.TotalProcessorTime)"
        Write-Host "  Memory: $([Math]::Round($RunningProcess.WorkingSet64 / 1MB, 2)) MB"
    } else {
        Write-Host "Tailscale Health Monitor Status" -ForegroundColor Cyan
        Write-Host "  Status: Not Running" -ForegroundColor Red
    }
    
    # Show recent log entries
    $LogFile = Join-Path $ProjectDir "logs\tailscale-health.log"
    if (Test-Path $LogFile) {
        Write-Host ""
        Write-Host "Recent Log Entries (last 5):" -ForegroundColor Cyan
        Get-Content $LogFile -Tail 5 | ForEach-Object {
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
}

# Function to restart the monitor
function Restart-Monitor {
    Write-Host "Restarting Tailscale Health Monitor..." -ForegroundColor Yellow
    Stop-Monitor
    Start-Sleep 2
    Start-Monitor
}

# Main execution
switch ($Action.ToLower()) {
    "start" {
        Start-Monitor
    }
    
    "stop" {
        Stop-Monitor
    }
    
    "status" {
        Show-MonitorStatus
    }
    
    "restart" {
        Restart-Monitor
    }
    
    default {
        Write-Host "Tailscale Health Monitor - Simple Launcher" -ForegroundColor Cyan
        Write-Host ""
        Write-Host "Usage: .\simple-monitor.ps1 [-Action start|stop|status|restart] [-IntervalSeconds 60]"
        Write-Host ""
        Write-Host "Actions:" -ForegroundColor Yellow
        Write-Host "  start    - Start the health monitor (default)"
        Write-Host "  stop     - Stop the running monitor"
        Write-Host "  status   - Show monitor status and recent logs"
        Write-Host "  restart  - Restart the monitor"
        Write-Host ""
        Write-Host "Options:" -ForegroundColor Yellow
        Write-Host "  -IntervalSeconds  - Health check interval in seconds (default: 60)"
        Write-Host ""
        Write-Host "Examples:" -ForegroundColor Green
        Write-Host "  .\simple-monitor.ps1                    # Start monitor"
        Write-Host "  .\simple-monitor.ps1 -IntervalSeconds 30  # Start with 30s interval"
        Write-Host "  .\simple-monitor.ps1 -Action status       # Check status"
        Write-Host "  .\simple-monitor.ps1 -Action stop         # Stop monitor"
    }
}
