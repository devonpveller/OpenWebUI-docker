@echo off
REM Quick Emergency Fixes for OpenWebUI AI Stack
REM Usage: quick-fixes.bat [namespace|rebuild|nuclear|gpu|status]

if "%1"=="" goto :usage
if "%1"=="namespace" goto :namespace_reset
if "%1"=="rebuild" goto :rebuild_tailscale
if "%1"=="nuclear" goto :nuclear_option
if "%1"=="gpu" goto :gpu_check
if "%1"=="status" goto :status_check
goto :usage

:namespace_reset
echo [INFO] Quick namespace reset - restarting Tailscale...
docker compose restart tailscale
echo [INFO] Waiting for Tailscale to reconnect...
timeout /t 30 /nobreak >nul
echo [INFO] Testing connectivity...
docker compose exec tailscale ping -c 1 8.8.8.8 >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo [SUCCESS] Namespace reset successful
) else (
    echo [ERROR] Namespace reset failed
)
goto :end

:rebuild_tailscale
echo [INFO] Rebuilding Tailscale container...
docker compose down tailscale
docker compose build --no-cache tailscale
docker compose up -d tailscale
echo [INFO] Waiting for rebuild completion...
timeout /t 45 /nobreak >nul
echo [INFO] Testing connectivity...
docker compose exec tailscale ping -c 1 8.8.8.8 >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo [SUCCESS] Tailscale rebuild successful
) else (
    echo [ERROR] Tailscale rebuild failed
)
goto :end

:nuclear_option
echo [WARN] ==========================================
echo [WARN] NUCLEAR OPTION - FULL STACK RESTART
echo [WARN] ==========================================
echo [WARN] This will restart ALL containers and may take several minutes
pause
echo [INFO] Performing full stack restart...
docker compose down
timeout /t 15 /nobreak >nul
docker compose up -d
echo [INFO] Waiting for complete stack initialization...
timeout /t 90 /nobreak >nul
echo [INFO] Testing final connectivity...
docker compose exec tailscale ping -c 1 8.8.8.8 >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo [SUCCESS] Nuclear option successful
) else (
    echo [ERROR] Nuclear option failed - manual intervention required
)
goto :end

:gpu_check
echo [INFO] Checking GPU status and restarting OpenWebUI if needed...
docker compose exec openwebui python -c "import torch; print('CUDA available:', torch.cuda.is_available())" 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo [WARN] GPU check failed, restarting OpenWebUI...
    docker compose restart openwebui
    timeout /t 45 /nobreak >nul
    docker compose exec openwebui python -c "import torch; print('CUDA available:', torch.cuda.is_available())" 2>nul
    if %ERRORLEVEL% EQU 0 (
        echo [SUCCESS] GPU restored after restart
    ) else (
        echo [ERROR] GPU still not available - may need full rebuild
        echo [INFO] Try: quick-fixes.bat nuclear
    )
) else (
    echo [SUCCESS] GPU is working correctly
)
goto :end

:status_check
echo [INFO] ==========================================
echo [INFO] SYSTEM STATUS CHECK
echo [INFO] ==========================================
echo.
echo [INFO] Container Status:
docker compose ps
echo.
echo [INFO] GPU Status:
docker compose exec openwebui python -c "import torch; print('CUDA available:', torch.cuda.is_available()); print('GPU count:', torch.cuda.device_count())" 2>nul
echo.
echo [INFO] Network Connectivity:
docker compose exec tailscale ping -c 1 8.8.8.8 >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo [SUCCESS] Network connectivity: OK
) else (
    echo [ERROR] Network connectivity: FAILED
)
echo.
echo [INFO] Tailscale Status:
docker compose exec tailscale tailscale --socket=/tmp/tailscaled.sock status 2>nul
echo.
echo [INFO] Tailscale Serve Status:
docker compose exec tailscale tailscale --socket=/tmp/tailscaled.sock serve status 2>nul
goto :end

:usage
echo.
echo Quick Emergency Fixes for OpenWebUI AI Stack
echo =============================================
echo.
echo Usage: quick-fixes.bat [option]
echo.
echo Options:
echo   namespace  - Quick restart of Tailscale (fixes most network issues)
echo   rebuild    - Rebuild and restart Tailscale container
echo   nuclear    - Full stack restart (use when all else fails)
echo   gpu        - Check and restart GPU functionality
echo   status     - Show detailed system status
echo.
echo Examples:
echo   quick-fixes.bat namespace     (most common fix)
echo   quick-fixes.bat status        (check everything)
echo   quick-fixes.bat nuclear       (last resort)
echo.

:end
echo.
echo [INFO] For advanced recovery options, use: emergency-recovery.ps1