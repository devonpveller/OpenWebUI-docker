@echo off
REM AI Stack Update Manager
REM Handles manual updates for OpenWebUI and llama-cpp services
REM Usage: update-stack.bat [openwebui|llama-cpp|all|check]

setlocal enabledelayedexpansion

if "%1"=="" goto :interactive_menu
if "%1"=="openwebui" goto :update_openwebui
if "%1"=="llama-cpp" goto :update_llama_cpp
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
echo 3. Update llama-cpp services
echo 4. Update both OpenWebUI and llama-cpp
echo 5. Exit
echo.
set /p choice="Select option (1-5): "

if "%choice%"=="1" goto :check_versions
if "%choice%"=="2" goto :update_openwebui
if "%choice%"=="3" goto :update_llama_cpp
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
echo   llama-cpp   - Update llama-cpp and llama-cpp-embed to latest image
echo   all         - Update both OpenWebUI and llama-cpp
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
echo [INFO] Current llama-cpp image:
cd ..
docker compose exec -T llama-cpp curl -s http://localhost:8080/health 2>nul
echo.
docker compose ps llama-cpp llama-cpp-embed --format "table {{.Name}}\t{{.Status}}" 2>nul

echo.
echo [INFO] Current llama-cpp image in docker-compose.yml:
findstr "image: ghcr.io/ggml-org/llama.cpp" docker-compose.yml

echo.
echo [INFO] To check for latest versions:
echo   - OpenWebUI: https://github.com/open-webui/open-webui/releases/latest
echo   - llama.cpp: https://github.com/ggml-org/llama.cpp/releases/latest
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
echo [STEP 1/8] Creating data backup...
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
echo [STEP 2/8] Specify OpenWebUI version to update to
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
echo [STEP 3/8] Updating Dockerfile.openwebui-gpu...
powershell -Command "(Get-Content '..\Dockerfile.openwebui-gpu') -replace 'FROM ghcr.io/open-webui/open-webui:v[0-9.]+', 'FROM ghcr.io/open-webui/open-webui:%VERSION%' | Set-Content '..\Dockerfile.openwebui-gpu'"
echo [SUCCESS] Dockerfile updated to %VERSION%

REM Step 4: Rebuild custom GPU image (CRITICAL - must use custom Dockerfile)
echo.
echo [STEP 4/8] Rebuilding OpenWebUI with GPU support (custom CUDA PyTorch)...
echo [INFO] This builds from Dockerfile.openwebui-gpu with CUDA-enabled PyTorch
echo [INFO] This may take several minutes...
cd ..
docker compose build --no-cache openwebui
set BUILD_RESULT=%ERRORLEVEL%
cd scripts
if %BUILD_RESULT% NEQ 0 (
    echo [ERROR] Custom GPU image build FAILED - check logs above
    echo [ERROR] Without this build, OpenWebUI will lack CUDA PyTorch and GPU health checks will timeout
    echo [INFO] Common causes: network issues downloading PyTorch, invalid base image version
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
echo [SUCCESS] Custom GPU image built successfully

REM Step 5: Verify GPU support in built image before starting
echo.
echo [STEP 5/8] Verifying CUDA PyTorch in built image...
cd ..
docker compose run --rm --no-deps --entrypoint python openwebui -c "import torch; assert torch.cuda.is_available(), 'CUDA NOT AVAILABLE'; print('CUDA available:', torch.cuda.is_available()); print('PyTorch version:', torch.__version__)"
set CUDA_VERIFY_RESULT=%ERRORLEVEL%
cd scripts
if %CUDA_VERIFY_RESULT% NEQ 0 (
    echo [ERROR] Built image does NOT have working CUDA PyTorch!
    echo [ERROR] The health check will timeout waiting for GPU initialization
    echo [INFO] Check Dockerfile.openwebui-gpu has correct PyTorch CUDA install commands
    echo [INFO] Verify: pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121
    set /p FORCE_CONTINUE="Continue anyway? (y/n): "
    if /i not "!FORCE_CONTINUE!"=="y" (
        echo [INFO] Update aborted - fix Dockerfile.openwebui-gpu and retry
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
    echo [WARNING] Continuing without verified GPU support...
) else (
    echo [SUCCESS] CUDA PyTorch verified in built image
)

REM Step 6: Restart services
echo.
echo [STEP 6/8] Restarting services...
cd ..
docker compose up -d openwebui
echo [INFO] Waiting for OpenWebUI CUDA initialization (up to 90s)...
timeout /t 30 /nobreak >nul

REM Wait for OpenWebUI health check to pass
set HEALTH_WAIT=0
set HEALTH_MAX=90
:health_loop
cd ..
docker compose ps openwebui --format "{{.Health}}" 2>nul | findstr /C:"healthy" >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    cd scripts
    echo [SUCCESS] OpenWebUI is healthy after approximately %HEALTH_WAIT%s
    goto :health_done
)
cd scripts
set /a HEALTH_WAIT+=10
if %HEALTH_WAIT% GEQ %HEALTH_MAX% (
    echo [WARNING] OpenWebUI not yet healthy after %HEALTH_MAX%s - continuing with dependent services
    goto :health_done
)
timeout /t 10 /nobreak >nul
goto :health_loop

:health_done
cd ..
docker compose up -d llama-cpp llama-cpp-embed tailscale
cd scripts

REM Step 7: Final verification
echo.
echo [STEP 7/8] Final verification...
timeout /t 15 /nobreak >nul

echo [INFO] Checking GPU availability in running container...
cd ..
docker compose exec -T openwebui python -c "import torch; print('CUDA available:', torch.cuda.is_available()); print('GPU count:', torch.cuda.device_count()); print('GPU name:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'None')" 2>nul
set GPU_CHECK_RESULT=%ERRORLEVEL%
cd scripts
if %GPU_CHECK_RESULT% NEQ 0 (
    echo [ERROR] GPU check failed in running container!
    echo [ERROR] OpenWebUI may not have CUDA-enabled PyTorch
    echo [INFO] This will cause the health monitor to timeout on GPU initialization
    echo [INFO] Try: docker compose build --no-cache openwebui
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
echo [SUCCESS] GPU verified in running container

echo.
echo [INFO] Checking service health...
cd ..
docker compose ps
cd scripts

REM Step 8: Resume monitoring (skip if part of full update)
if not defined FULL_UPDATE (
    echo.
    echo [STEP 8/8] Resuming monitoring services...
    sc query "TailscaleMonitor" >nul 2>&1
    if %ERRORLEVEL% EQU 0 (
        echo [INFO] Starting TailscaleMonitor service...
        sc start TailscaleMonitor >nul 2>&1
        timeout /t 2 /nobreak >nul
    )
    echo [SUCCESS] Monitoring resumed
)

echo.
echo [SUCCESS] OpenWebUI update complete with GPU support verified!
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

:update_llama_cpp
echo.
echo ========================================
echo   llama-cpp Update Process
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
echo [STEP 1/3] Pulling latest llama-cpp server-cuda image...
docker pull ghcr.io/ggml-org/llama.cpp:server-cuda
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Pull failed - check internet connection
    if "%1"=="" (
        if not defined FULL_UPDATE pause
        goto :interactive_menu
    )
    goto :end
)
echo [SUCCESS] Latest llama-cpp image pulled

echo.
echo [STEP 2/3] Restarting llama-cpp services...
cd ..
docker compose up -d llama-cpp
echo [INFO] Waiting for llama-cpp to load model (this may take a few minutes)...
timeout /t 60 /nobreak >nul

REM Wait for llama-cpp health check
set LLAMA_WAIT=0
set LLAMA_MAX=180
:llama_health_loop
docker compose ps llama-cpp --format "{{.Health}}" 2>nul | findstr /C:"healthy" >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    cd scripts
    echo [SUCCESS] llama-cpp is healthy after approximately %LLAMA_WAIT%s
    goto :llama_health_done
)
set /a LLAMA_WAIT+=10
if %LLAMA_WAIT% GEQ %LLAMA_MAX% (
    cd scripts
    echo [WARNING] llama-cpp not yet healthy after %LLAMA_MAX%s - continuing
    goto :llama_health_done
)
timeout /t 10 /nobreak >nul
goto :llama_health_loop

:llama_health_done
cd ..
docker compose up -d llama-cpp-embed
echo [INFO] Waiting for llama-cpp-embed to initialize...
timeout /t 30 /nobreak >nul
cd scripts

echo.
echo [STEP 3/3] Verifying llama-cpp services...
cd ..
docker compose exec -T llama-cpp curl -s http://localhost:8080/health 2>nul
echo.
docker compose ps llama-cpp llama-cpp-embed --format "table {{.Name}}\t{{.Status}}" 2>nul
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
echo [SUCCESS] llama-cpp update complete!
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
echo [WARNING] This will update both OpenWebUI and llama-cpp
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

REM Only update llama-cpp if OpenWebUI succeeded
if "%OPENWEBUI_UPDATE_SUCCESS%"=="1" (
    echo [INFO] OpenWebUI update successful - proceeding with llama-cpp update...
    echo.
    call :update_llama_cpp
) else (
    echo [ERROR] OpenWebUI update failed - skipping llama-cpp update
    echo [INFO] Fix OpenWebUI issues before updating llama-cpp
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
    echo [WARNING] Partial update - OpenWebUI failed, llama-cpp skipped
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
