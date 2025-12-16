@echo off
REM AI Stack Update Manager
REM Handles manual updates for OpenWebUI and Ollama
REM Usage: update-stack.bat [openwebui|ollama|all|check]

setlocal enabledelayedexpansion

if "%1"=="" goto :usage
if "%1"=="openwebui" goto :update_openwebui
if "%1"=="ollama" goto :update_ollama
if "%1"=="all" goto :update_all
if "%1"=="check" goto :check_versions
goto :usage

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
docker compose logs openwebui 2>nul | findstr /C:"v0." | findstr /C:"building the best"
if %ERRORLEVEL% NEQ 0 (
    echo [WARNING] Could not detect version from logs
    docker compose exec -T openwebui cat /app/backend/open_webui/__init__.py 2>nul | findstr "VERSION ="
)

echo.
echo [INFO] Current Ollama version:
docker compose exec -T ollama ollama --version 2>nul

echo.
echo [INFO] To check for latest versions:
echo   - OpenWebUI: https://github.com/open-webui/open-webui/releases/latest
echo   - Ollama: https://github.com/ollama/ollama/releases/latest
echo.
echo [INFO] Current Dockerfile base image:
findstr "FROM ghcr.io/open-webui" Dockerfile.openwebui-gpu

goto :end

:update_openwebui
echo.
echo ========================================
echo   OpenWebUI Update Process
echo ========================================
echo.

REM Step 1: Backup data
echo [STEP 1/6] Creating data backup...
set BACKUP_DIR=data-backup-%date:~-4%%date:~-7,2%%date:~-10,2%-%time:~0,2%%time:~3,2%%time:~6,2%
set BACKUP_DIR=%BACKUP_DIR: =0%
echo [INFO] Backup directory: %BACKUP_DIR%

if not exist "%BACKUP_DIR%" (
    mkdir "%BACKUP_DIR%"
)

xcopy /E /I /Y "data\openwebui" "%BACKUP_DIR%\openwebui" >nul
if %ERRORLEVEL% EQU 0 (
    echo [SUCCESS] Data backed up to %BACKUP_DIR%
) else (
    echo [ERROR] Backup failed - aborting update
    goto :end
)

REM Step 2: Get version input
echo.
echo [STEP 2/6] Specify OpenWebUI version to update to
echo [INFO] Check releases: https://github.com/open-webui/open-webui/releases
set /p VERSION="Enter version tag (e.g., v0.6.41): "

if "%VERSION%"=="" (
    echo [ERROR] No version specified - aborting
    goto :end
)

REM Step 3: Update Dockerfile
echo.
echo [STEP 3/6] Updating Dockerfile.openwebui-gpu...
powershell -Command "(Get-Content 'Dockerfile.openwebui-gpu') -replace 'FROM ghcr.io/open-webui/open-webui:v[0-9.]+', 'FROM ghcr.io/open-webui/open-webui:%VERSION%' | Set-Content 'Dockerfile.openwebui-gpu'"
echo [SUCCESS] Dockerfile updated to %VERSION%

REM Step 4: Rebuild image
echo.
echo [STEP 4/6] Rebuilding OpenWebUI with GPU support...
echo [INFO] This may take several minutes...
docker compose build --no-cache openwebui
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Build failed - check logs above
    echo [INFO] To rollback: Restore backup and rebuild with previous version
    goto :end
)
echo [SUCCESS] Build completed

REM Step 5: Restart services
echo.
echo [STEP 5/6] Restarting services...
docker compose up -d openwebui
timeout /t 15 /nobreak >nul
docker compose up -d ollama tailscale

REM Step 6: Verify
echo.
echo [STEP 6/6] Verifying update...
timeout /t 10 /nobreak >nul

echo [INFO] Checking GPU availability...
docker compose exec -T openwebui python -c "import torch; print('CUDA available:', torch.cuda.is_available())" 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo [WARNING] GPU check failed - verify manually
)

echo.
echo [INFO] Checking service health...
docker compose ps

echo.
echo [SUCCESS] OpenWebUI update complete!
echo [INFO] Access OpenWebUI at http://localhost:3000
echo [INFO] Backup location: %BACKUP_DIR%
echo.
goto :end

:update_ollama
echo.
echo ========================================
echo   Ollama Update Process
echo ========================================
echo.

echo [STEP 1/3] Pulling latest Ollama image...
docker pull ollama/ollama:latest
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Pull failed - check internet connection
    goto :end
)

echo.
echo [STEP 2/3] Restarting Ollama...
docker compose up -d ollama

echo.
echo [STEP 3/3] Verifying Ollama...
timeout /t 10 /nobreak >nul
docker compose exec -T ollama ollama --version

echo.
echo [SUCCESS] Ollama update complete!
goto :end

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
    goto :end
)

call :update_openwebui
echo.
echo ========================================
echo.
call :update_ollama

echo.
echo ========================================
echo [SUCCESS] Full stack update complete!
echo ========================================
goto :end

:end
endlocal
