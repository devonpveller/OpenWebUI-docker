# setup-prereqs.ps1
# Run as Administrator

# Self-elevate if not running as administrator
if (-not ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "Script is not running as Administrator. Relaunching with elevated permissions..."
    Start-Process powershell "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`"" -Verb RunAs
    exit
}

# Fix function name to use approved verb
function Throw-Error {
    param([string]$msg)
    Write-Host "[ERROR] $msg" -ForegroundColor Red
    Write-Host "Please check your internet connection, permissions, and try running this script as Administrator."
    exit 1
}

# Check for internet connectivity
try {
    Write-Host "Checking internet connectivity..."
    $null = Invoke-WebRequest -Uri "https://www.google.com" -UseBasicParsing -TimeoutSec 10
} catch { Throw-Error "No internet connection detected. Please connect to the internet and try again."}

# Check for at least 20GB free disk space on system drive
try {
    Write-Host "Checking for sufficient disk space (at least 20GB free on system drive)..."
    $sysDrive = Get-PSDrive -Name ((Get-Location).Path.Substring(0,1))
    if ($sysDrive.Free -lt 20GB) {
        Throw-Error "Insufficient disk space. At least 20GB free space is recommended."
    }
} catch { Write-Host "[WARNING] Could not determine free disk space. Please ensure you have at least 20GB free." }

try {
    Write-Host "Enabling required Windows features..."
    $wslResult = dism.exe /online /enable-feature /featurename:Microsoft-Windows-Subsystem-Linux /all /norestart
    if ($LASTEXITCODE -ne 0) {
        Throw-Error "Failed to enable WSL feature."
    }
    $vmResult = dism.exe /online /enable-feature /featurename:VirtualMachinePlatform /all /norestart
    if ($LASTEXITCODE -ne 0) {
        Throw-Error "Failed to enable Virtual Machine Platform."
    }
} catch { Throw-Error $_ }

try {
    Write-Host "Installing WSL2 kernel update..."
    wsl --set-default-version 2
    if ($LASTEXITCODE -ne 0) {
        Throw-Error "Failed to set WSL2 as default."
    }
} catch { Throw-Error $_ }

try {
    Write-Host "Installing Ubuntu (default WSL distro)..."
    wsl --install -d Ubuntu
    if ($LASTEXITCODE -ne 0) {
        Write-Host "If Ubuntu is already installed, this step may be safely ignored."
    }
} catch { Write-Host "If Ubuntu is already installed, this step may be safely ignored." }

try {
    Write-Host "Downloading Docker Desktop installer..."
    $dockerInstaller = "$env:TEMP\DockerDesktopInstaller.exe"
    Invoke-WebRequest -Uri "https://desktop.docker.com/win/main/amd64/Docker%20Desktop%20Installer.exe" -OutFile $dockerInstaller -ErrorAction Stop
} catch { Throw-Error "Failed to download Docker Desktop installer." }

try {
    Write-Host "Installing Docker Desktop..."
    Start-Process -FilePath $dockerInstaller -Wait -ErrorAction Stop
} catch { Throw-Error "Failed to start Docker Desktop installer. Please install manually from https://www.docker.com/products/docker-desktop/" }

try {
    Write-Host "Checking for NVIDIA GPU..."
    $gpu = Get-WmiObject win32_VideoController | Where-Object { $_.Name -like '*NVIDIA*' }
    if ($gpu) {
        Write-Host "NVIDIA GPU detected. Installing NVIDIA drivers and NVIDIA Container Toolkit for WSL2..."
        $nvidiaDriverUrl = "https://us.download.nvidia.com/Windows/wsl/WSL-Driver-Latest.exe"
        $nvidiaDriverInstaller = "$env:TEMP\WSL-Driver-Latest.exe"
        try {
            Invoke-WebRequest -Uri $nvidiaDriverUrl -OutFile $nvidiaDriverInstaller -ErrorAction Stop
        } catch { Throw-Error "Failed to download NVIDIA WSL2 driver. Download manually from https://developer.nvidia.com/cuda/wsl/download" }
        try {
            Start-Process -FilePath $nvidiaDriverInstaller -Wait -ErrorAction Stop
        } catch { Throw-Error "Failed to install NVIDIA WSL2 driver. Please install manually." }
        Write-Host "To complete GPU support, run the following inside your Ubuntu WSL2 shell after rebooting:"
        Write-Host "------------------------------------------------------------"
        Write-Host "sudo apt-get update && sudo apt-get install -y nvidia-container-toolkit"
        Write-Host "sudo systemctl restart docker || sudo service docker restart"
        Write-Host "------------------------------------------------------------"
        Write-Host "If you see 'System has not been booted with systemd', use:"
        Write-Host "sudo service docker restart"
        Write-Host "For more info: https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html#wsl2"
    } else {
        Write-Host "No NVIDIA GPU detected. Skipping NVIDIA driver and toolkit installation."
    }
} catch { Write-Host "GPU detection failed. If you have an NVIDIA GPU, please install the WSL2 driver and toolkit manually." }

Write-Host "Setup complete. Please reboot your computer before running Docker Desktop."