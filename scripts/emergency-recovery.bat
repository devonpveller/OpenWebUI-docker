@echo off
REM Emergency Tailscale Network Recovery - Legacy Batch Version
REM For PowerShell version with better GPU support, use: emergency-recovery.ps1

echo ========================================
echo EMERGENCY TAILSCALE NETWORK RECOVERY
echo ========================================
echo.

echo [INFO] Checking current container status...
docker compose ps

echo.
echo [INFO] Attempting emergency network namespace recovery...
echo [WARN] This will restart both OpenWebUI and Tailscale containers

REM Phase 1: Graceful shutdown in reverse dependency order
echo [INFO] Phase 1: Graceful shutdown
echo [INFO] Stopping Tailscale container...
docker compose stop tailscale
if %ERRORLEVEL% NEQ 0 (
    echo [WARN] Tailscale stop failed, attempting force kill...
    docker compose kill tailscale
)

echo [INFO] Stopping OpenWebUI container...
docker compose stop openwebui
if %ERRORLEVEL% NEQ 0 (
    echo [WARN] OpenWebUI stop failed, attempting force kill...
    docker compose kill openwebui
)

echo [INFO] Waiting for cleanup...
timeout /t 15 /nobreak >nul

REM Phase 2: Restart with proper timing for GPU/network dependencies
echo [INFO] Phase 2: Service restart
echo [INFO] Starting OpenWebUI with GPU support...
docker compose up -d openwebui
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Failed to start OpenWebUI container
    goto :nuclear_option
)

echo [INFO] Waiting for OpenWebUI to be healthy (may take longer with GPU initialization)...
timeout /t 45 /nobreak >nul

echo [INFO] Starting Tailscale with shared network namespace...
docker compose up -d tailscale
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Failed to start Tailscale container
    goto :nuclear_option
)

echo [INFO] Waiting for Tailscale network connectivity...
timeout /t 60 /nobreak >nul

REM Phase 3: Connectivity verification
echo [INFO] Phase 3: Testing connectivity...
docker compose exec tailscale ping -c 1 8.8.8.8 >nul 2>&1

if %ERRORLEVEL% NEQ 0 (
    echo [WARN] Basic recovery failed, attempting nuclear option...
    goto :nuclear_option
) else (
    echo [SUCCESS] Emergency recovery successful!
    goto :verify_services
)

:nuclear_option
echo [WARN] ========================================
echo [WARN] PERFORMING NUCLEAR RECOVERY
echo [WARN] ========================================
echo [INFO] Full stack restart with network namespace reset...
docker compose down
timeout /t 15 /nobreak >nul
docker compose up -d
timeout /t 90 /nobreak >nul

echo [INFO] Testing post-nuclear connectivity...
docker compose exec tailscale ping -c 1 8.8.8.8 >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Nuclear recovery failed - manual intervention required
    echo [INFO] Try: docker system prune -f && docker compose build --no-cache
    pause
    exit /b 1
) else (
    echo [SUCCESS] Nuclear recovery successful!
)

:verify_services
echo.
echo [INFO] Verifying services...
echo [INFO] Tailscale status:
docker compose exec tailscale tailscale --socket=/tmp/tailscaled.sock status

echo.
echo [INFO] Tailscale serve configuration:
docker compose exec tailscale tailscale --socket=/tmp/tailscaled.sock serve status

echo.
echo [INFO] OpenWebUI GPU status:
docker compose exec openwebui python -c "import torch; print('CUDA available:', torch.cuda.is_available())" 2>nul

echo.
echo ========================================
echo EMERGENCY RECOVERY COMPLETED
echo ========================================
echo [INFO] For advanced recovery options, use: emergency-recovery.ps1
pause
