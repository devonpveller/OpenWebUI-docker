@echo off
REM setup-and-run.bat
REM Checks for Docker, runs PowerShell prereqs if needed, then launches Docker Compose

where docker >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Docker not found. Running PowerShell prerequisites script...
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup-prereqs.ps1"
    if %ERRORLEVEL% neq 0 (
        echo [ERROR] Prerequisite script failed. Please review the output above for details.
        pause
        exit /b 1
    )
    echo Please reboot your computer, then re-run this batch file.
    pause
    exit /b
)

REM Check if Docker Desktop is running
TASKLIST /FI "IMAGENAME eq Docker Desktop.exe" 2>NUL | find /I /N "Docker Desktop.exe">NUL
if %ERRORLEVEL% neq 0 (
    echo [WARNING] Docker Desktop is not running. Attempting to start it...
    start "" "C:\Program Files\Docker\Docker\Docker Desktop.exe"
    timeout /t 10
)

REM Test Docker daemon
for /f "delims=" %%i in ('docker info 2^>nul') do set DOCKER_OK=1
if not defined DOCKER_OK (
    echo [ERROR] Docker daemon is not running or not responding.
    echo Please ensure Docker Desktop is running and try again.
    pause
    exit /b 2
)

REM If Docker is running, launch Docker Compose
cd /d "%~dp0"
docker compose up --build || (
    echo [ERROR] Docker Compose failed to start. Please check your docker-compose.yml and Docker status.
    pause
    exit /b 3
)
