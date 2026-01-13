@echo off
REM AI Stack Update Manager
REM Handles manual updates for OpenWebUI and Ollama
REM Usage: update-stack.bat [openwebui|ollama|all|check]

setlocal enabledelayedexpansion

if "%1"=="" goto :interactive_menu
if "%1"=="openwebui" goto :update_openwebui
if "%1"=="ollama" goto :update_ollama
if "%1"=="all" goto :update_all
if "%1"=="check" goto :check_versions
goto :usage

:interactive_menu
echo.
echo ========================================
echo   AI Stack Update Manager
echo ========================================
echo.
echo 1. Check current versions
echo 2. Update OpenWebUI
echo 3. Update Ollama
echo 4. Update both OpenWebUI and Ollama
echo 5. Exit
echo.
set /p choice="Select option (1-5): "

if "%choice%"=="1" goto :check_versions
if "%choice%"=="2" goto :update_openwebui
if "%choice%"=="3" goto :update_ollama
if "%choice%"=="4" goto :update_all
if "%choice%"=="5" goto :end
echo [ERROR] Invalid choice
timeout /t 2 /nobreak >nul
goto :interactive_menu

:usage
echo.
echo ========================================
echo   AI Stack Update Manager
echo ========================================
echo.
echo Usage: update-stack.bat [command]
echo.
echo Commands:
echo   check       - Check current versions and available updates
echo   openwebui   - Update OpenWebUI to latest version
echo   ollama      - Update Ollama to latest version
echo   all         - Update both OpenWebUI and Ollama
echo.
echo Note: Updates require manual version specification in Dockerfile.
echo       The script will guide you through the process.
echo.
goto :end

:check_versions
echo.
echo ========================================
echo   Checking Current Versions
echo ========================================
echo.

echo [INFO] Current OpenWebUI version:
cd ..
docker compose logs openwebui 2>nul | findstr /C:"v0." | findstr /C:"building the best"
if %ERRORLEVEL% NEQ 0 (
    echo [WARNING] Could not detect version from logs
    docker compose exec -T openwebui cat /app/backend/open_webui/__init__.py 2>nul | findstr "VERSION ="
)

echo.
echo [INFO] Current Ollama version:
docker compose exec -T ollama ollama --version 2>nul

echo.
echo [INFO] Current Ollama image in docker-compose.yml:
findstr "image: ollama/ollama" docker-compose.yml

echo.
echo [INFO] To check for latest versions:
echo   - OpenWebUI: https://github.com/open-webui/open-webui/releases/latest
echo   - Ollama: https://github.com/ollama/ollama/releases/latest
echo.
echo [INFO] Current Dockerfile base image:
findstr "FROM ghcr.io/open-webui" Dockerfile.openwebui-gpu
cd scripts

if "%1"=="" (
    echo.
    pause
    goto :interactive_menu
)
goto :end

:update_openwebui
echo.
echo ========================================
echo   OpenWebUI Update Process
echo ========================================
echo.

REM Step 1: Backup data
echo [STEP 1/7] Creating data backup...
set BACKUP_DIR=..\data-backup\data-backup-%date:~-4%%date:~-7,2%%date:~-10,2%-%time:~0,2%%time:~3,2%%time:~6,2%
set BACKUP_DIR=%BACKUP_DIR: =0%
echo [INFO] Backup directory: %BACKUP_DIR%

if not exist "..\data\openwebui" (
    echo.
    echo [WARNING] data\openwebui directory not found
    echo [INFO] This may be a fresh install or data is stored elsewhere
    set /p SKIP_BACKUP="Continue without backup? (y/n): "
    if /i not "!SKIP_BACKUP!"=="y" (
        echo [INFO] Update cancelled
        if "%1"=="" (
            pause
            goto :interactive_menu
        )
        goto :end
    )
    echo [INFO] Skipping backup - continuing with update...
) else (
    if not exist "..\data-backup" mkdir "..\data-backup"
    if not exist "%BACKUP_DIR%" mkdir "%BACKUP_DIR%"
    
    echo [INFO] Backing up data\openwebui to %BACKUP_DIR%\openwebui...
    xcopy /E /I /Y /Q "..\data\openwebui" "%BACKUP_DIR%\openwebui"
    
    REM Check if backup actually succeeded by verifying files exist
    if exist "%BACKUP_DIR%\openwebui" (
        echo [SUCCESS] Data backed up to %BACKUP_DIR%
    ) else (
        echo [ERROR] Backup failed - aborting update
        echo [INFO] Make sure data\openwebui directory exists
        if "%1"=="" (
            pause
            goto :interactive_menu
        )
        goto :end
    )
)

REM Step 2: Get version input
echo.
echo [STEP 2/7] Specify OpenWebUI version to update to
echo [INFO] Check releases: https://github.com/open-webui/open-webui/releases
set /p VERSION="Enter version tag (e.g., v0.6.41): "

if "%VERSION%"=="" (
    echo [ERROR] No version specified - aborting
    if "%1"=="" (
        pause
        goto :interactive_menu
    )
    goto :end
)

REM Validate version exists in registry
echo.
echo [INFO] Validating version %VERSION%...
docker manifest inspect ghcr.io/open-webui/open-webui:%VERSION% >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Version %VERSION% not found in registry
    echo [INFO] Please check https://github.com/open-webui/open-webui/releases
    if "%1"=="" (
        pause
        goto :interactive_menu
    )
    goto :end
)
echo [SUCCESS] Version %VERSION% validated

REM Pause monitoring now that update is confirmed (skip if part of full update)
if not defined FULL_UPDATE (
    echo.
    echo [INFO] Pausing monitoring services...
    powershell -Command "Get-Process -Name 'powershell' -ErrorAction SilentlyContinue | Where-Object {$_.MainWindowTitle -like '*simple-monitor*'} | Stop-Process -Force" 2>nul
    sc query "TailscaleMonitor" >nul 2>&1
    if %ERRORLEVEL% EQU 0 (
        sc stop TailscaleMonitor >nul 2>&1
    )
    echo [SUCCESS] Monitoring paused
)

REM Step 3: Update Dockerfile
echo.
echo [STEP 3/7] Updating Dockerfile.openwebui-gpu...
powershell -Command "(Get-Content '..\Dockerfile.openwebui-gpu') -replace 'FROM ghcr.io/open-webui/open-webui:v[0-9.]+', 'FROM ghcr.io/open-webui/open-webui:%VERSION%' | Set-Content '..\Dockerfile.openwebui-gpu'"
echo [SUCCESS] Dockerfile updated to %VERSION%

REM Step 4: Rebuild image
echo.
echo [STEP 4/7] Rebuilding OpenWebUI with GPU support...
echo [INFO] This may take several minutes...
cd ..
docker compose build --no-cache openwebui
cd scripts
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Build failed - check logs above
    echo [INFO] To rollback: Restore backup and rebuild with previous version
    set OPENWEBUI_UPDATE_SUCCESS=0
    if "%1"=="" (
        if not defined FULL_UPDATE (
            pause
            goto :interactive_menu
        )
    )
    if not defined FULL_UPDATE goto :end
    goto :eof
)
echo [SUCCESS] Build completed

REM Step 5: Restart services
echo.
echo [STEP 5/7] Restarting services...
cd ..
docker compose up -d openwebui
timeout /t 15 /nobreak >nul
docker compose up -d ollama tailscale
cd scripts

REM Step 6: Verify
echo.
echo [STEP 6/7] Verifying update...
timeout /t 10 /nobreak >nul

echo [INFO] Checking GPU availability...
cd ..
docker compose exec -T openwebui python -c "import torch; print('CUDA available:', torch.cuda.is_available())" 2>nul
cd scripts
if %ERRORLEVEL% NEQ 0 (
    echo [WARNING] GPU check failed - verify manually
)

echo.
echo [INFO] Checking service health...
cd ..
docker compose ps
cd scripts

REM Step 7: Resume monitoring (skip if part of full update)
if not defined FULL_UPDATE (
    echo.
    echo [STEP 7/7] Resuming monitoring services...
    sc query "TailscaleMonitor" >nul 2>&1
    if %ERRORLEVEL% EQU 0 (
        echo [INFO] Starting TailscaleMonitor service...
        sc start TailscaleMonitor >nul 2>&1
        timeout /t 2 /nobreak >nul
    )
    echo [SUCCESS] Monitoring resumed
)

echo.
echo [SUCCESS] OpenWebUI update complete!
echo [INFO] Access OpenWebUI at http://localhost:3000
echo [INFO] Backup location: %BACKUP_DIR%
set OPENWEBUI_UPDATE_SUCCESS=1
echo.
if "%1"=="" (
    if not defined FULL_UPDATE (
        pause
        goto :interactive_menu
    )
)
if not defined FULL_UPDATE goto :end
goto :eof

:update_ollama
echo.
echo ========================================
echo   Ollama Update Process
echo ========================================
echo.

REM Pause monitoring before update (skip if part of full update)
if not defined FULL_UPDATE (
    echo [INFO] Pausing monitoring services...
    powershell -Command "Get-Process -Name 'powershell' -ErrorAction SilentlyContinue | Where-Object {$_.MainWindowTitle -like '*simple-monitor*'} | Stop-Process -Force" 2>nul
    sc query "TailscaleMonitor" >nul 2>&1
    if %ERRORLEVEL% EQU 0 (
        sc stop TailscaleMonitor >nul 2>&1
    )
    echo [SUCCESS] Monitoring paused
)

echo.
echo [STEP 1/4] Pulling latest Ollama image...
docker pull ollama/ollama:latest
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Pull failed - check internet connection
    if "%1"=="" (
        if not defined FULL_UPDATE pause
        goto :interactive_menu
    )
    goto :end
)

echo.
echo [STEP 2/4] Detecting Ollama version...
for /f "tokens=*" %%i in ('docker run --rm ollama/ollama:latest ollama --version 2^>nul') do set OLLAMA_VERSION=%%i
echo [INFO] Detected version: %OLLAMA_VERSION%

REM Extract version number (format: "ollama version is X.Y.Z")
for /f "tokens=4" %%v in ("%OLLAMA_VERSION%") do set OLLAMA_TAG=%%v
if not defined OLLAMA_TAG (
    echo [WARNING] Could not detect version, using 'latest' tag
    set OLLAMA_TAG=latest
) else (
    echo [INFO] Pinning to version: %OLLAMA_TAG%
    
    REM Update docker-compose.yml to pin Ollama version
    echo [INFO] Updating docker-compose.yml...
    cd ..
    powershell -Command "(Get-Content 'docker-compose.yml') -replace 'image: ollama/ollama:.*', 'image: ollama/ollama:%OLLAMA_TAG%' | Set-Content 'docker-compose.yml'"
    cd scripts
    echo [SUCCESS] Ollama pinned to version %OLLAMA_TAG%
)

echo.
echo [STEP 3/4] Restarting Ollama...
cd ..
docker compose up -d ollama
cd scripts

echo.
echo [STEP 4/4] Verifying Ollama...
timeout /t 10 /nobreak >nul
cd ..
docker compose exec -T ollama ollama --version
cd scripts

REM Resume monitoring (skip if part of full update)
if not defined FULL_UPDATE (
    echo.
    echo [INFO] Resuming monitoring services...
    sc query "TailscaleMonitor" >nul 2>&1
    if %ERRORLEVEL% EQU 0 (
        sc start TailscaleMonitor >nul 2>&1
        timeout /t 2 /nobreak >nul
    )
    echo [SUCCESS] Monitoring resumed
)

echo.
echo [SUCCESS] Ollama update complete!
if "%1"=="" (
    if not defined FULL_UPDATE (
        echo.
        pause
        goto :interactive_menu
    )
)
if not defined FULL_UPDATE goto :end
goto :eof

:update_all
echo.
echo ========================================
echo   Full Stack Update
echo ========================================
echo.
echo [WARNING] This will update both OpenWebUI and Ollama
echo.
set /p CONFIRM="Continue? (y/n): "
if /i not "%CONFIRM%"=="y" (
    echo [INFO] Update cancelled
    if "%1"=="" (
        pause
        goto :interactive_menu
    )
    goto :end
)

REM Pause monitoring now that full update is confirmed
set FULL_UPDATE=1
echo.
echo [INFO] Pausing monitoring services for full update...
powershell -Command "Get-Process -Name 'powershell' -ErrorAction SilentlyContinue | Where-Object {$_.MainWindowTitle -like '*simple-monitor*'} | Stop-Process -Force" 2>nul
sc query "TailscaleMonitor" >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    sc stop TailscaleMonitor >nul 2>&1
)
echo [SUCCESS] Monitoring paused

call :update_openwebui
echo.
echo ========================================
echo.

REM Only update Ollama if OpenWebUI succeeded
if "%OPENWEBUI_UPDATE_SUCCESS%"=="1" (
    echo [INFO] OpenWebUI update successful - proceeding with Ollama update...
    echo.
    call :update_ollama
) else (
    echo [ERROR] OpenWebUI update failed - skipping Ollama update
    echo [INFO] Fix OpenWebUI issues before updating Ollama
)

REM Resume monitoring after full update
echo.
echo [INFO] Resuming monitoring services...
sc query "TailscaleMonitor" >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    sc start TailscaleMonitor >nul 2>&1
    timeout /t 2 /nobreak >nul
)
echo [SUCCESS] Monitoring resumed

echo.
echo ========================================
if "%OPENWEBUI_UPDATE_SUCCESS%"=="1" (
    echo [SUCCESS] Full stack update complete!
) else (
    echo [WARNING] Partial update - OpenWebUI failed, Ollama skipped
)
echo ========================================
if "%1"=="" (
    echo.
    pause
    goto :interactive_menu
)
goto :end

:end
pause
endlocal
