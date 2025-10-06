@echo off
REM Quick Emergency Fixes for OpenWebUI AI Stack
REM Usage: quick-fixes.bat [namespace|rebuild|nuclear|gpu|status|lmstudio|restart-openwebui]

if "%1"=="" goto :usage
if "%1"=="namespace" goto :namespace_reset
if "%1"=="rebuild" goto :rebuild_tailscale
if "%1"=="nuclear" goto :nuclear_option
if "%1"=="gpu" goto :gpu_check
if "%1"=="status" goto :status_check
if "%1"=="lmstudio" goto :lmstudio_fix
if "%1"=="restart-openwebui" goto :restart_openwebui
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

echo [INFO] Pre-nuclear diagnostic check...
docker compose exec tailscale ping -c 1 8.8.8.8 >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo [SUCCESS] Wait - connectivity is actually working!
    echo [INFO] Issue may be performance/timing related, not connectivity
    echo [INFO] Try: quick-fixes.bat status  (to check detailed status)
    echo [INFO] Or just wait a bit longer for services to stabilize
    goto :end
)

echo [WARN] This will restart ALL containers and may take several minutes
echo [WARN] This will DESTROY containers and rebuild them
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
echo [INFO] Checking GPU status and restarting GPU services if needed...
echo [INFO] Testing OpenWebUI GPU access...
docker compose exec openwebui python -c "import torch; print('CUDA available:', torch.cuda.is_available())" 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo [WARN] GPU check failed, restarting GPU services...
    docker compose restart ollama openwebui
    echo [INFO] Waiting for GPU services to restart...
    timeout /t 60 /nobreak >nul
    echo [INFO] Re-testing GPU access...
    docker compose exec openwebui python -c "import torch; print('CUDA available:', torch.cuda.is_available())" 2>nul
    if %ERRORLEVEL% EQU 0 (
        echo [SUCCESS] GPU restored after restart
        echo [INFO] Testing Ollama availability...
        docker compose exec ollama ollama list >nul 2>&1
        if %ERRORLEVEL% EQU 0 (
            echo [SUCCESS] Ollama GPU integration working
        ) else (
            echo [WARN] Ollama may need additional time to initialize
        )
    ) else (
        echo [ERROR] GPU still not available - may need full rebuild
        echo [INFO] Try: quick-fixes.bat nuclear
    )
) else (
    echo [SUCCESS] OpenWebUI GPU is working correctly
    echo [INFO] Testing Ollama GPU integration...
    docker compose exec ollama ollama list >nul 2>&1
    if %ERRORLEVEL% EQU 0 (
        echo [SUCCESS] Ollama GPU integration working
    ) else (
        echo [WARN] Ollama may need restart
        docker compose restart ollama
        timeout /t 30 /nobreak >nul
    )
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
echo [INFO] Starting any missing services...
docker compose up -d watchtower >nul 2>&1
echo.
echo [INFO] OpenWebUI GPU Status:
docker compose exec openwebui python -c "import torch; print('CUDA available:', torch.cuda.is_available()); print('GPU count:', torch.cuda.device_count())" 2>nul
echo.
echo [INFO] Ollama Status:
docker compose exec ollama ollama list 2>nul
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
echo.
echo [INFO] Service Accessibility Check:
docker compose exec tailscale wget -q -T 3 -O /dev/null http://127.0.0.1:8080 2>nul
if %ERRORLEVEL% EQU 0 (
    echo [SUCCESS] OpenWebUI accessibility: OK
) else (
    echo [ERROR] OpenWebUI accessibility: FAILED
)
docker compose exec tailscale wget -q -T 3 -O /dev/null http://127.0.0.1:11434/api/version 2>nul
if %ERRORLEVEL% EQU 0 (
    echo [SUCCESS] Ollama API accessibility: OK
) else (
    echo [ERROR] Ollama API accessibility: FAILED
)
goto :end

:lmstudio_fix
echo [INFO] Fixing LM Studio Tailscale connectivity...
echo [INFO] Testing LM Studio host connectivity...
curl -s -m 5 http://169.254.83.107:5506/v1/models >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] LM Studio not accessible - make sure it's running
    goto :end
)
echo [SUCCESS] LM Studio is running
echo [INFO] Restarting socat proxy and Tailscale serve...
docker compose exec tailscale sh -c "pkill socat 2>/dev/null || true"
echo [INFO] Starting persistent socat proxy...
docker compose exec -d tailscale sh -c "socat TCP-LISTEN:8234,fork,reuseaddr,keepalive TCP:169.254.83.107:5506"
echo [INFO] Waiting for proxy to initialize...
timeout /t 8 /nobreak >nul
echo [INFO] Testing proxy connection...
docker compose exec tailscale sh -c "wget -q -T 5 -O /dev/null http://127.0.0.1:8234/v1/models && echo 'Proxy working' || echo 'Proxy failed'"
docker compose exec tailscale tailscale --socket=/tmp/tailscaled.sock serve --https=443 --set-path=/lmstudio --bg http://127.0.0.1:8234 >nul
echo [SUCCESS] LM Studio Tailscale configuration restored
echo [INFO] Access URL: https://openwebui-13.tail37f875.ts.net/lmstudio
goto :end

:restart_openwebui
echo [INFO] Restarting OpenWebUI with proper network dependency handling...
echo [WARN] This will restart OpenWebUI, Ollama, and Tailscale containers
echo [INFO] Stopping dependent containers first...
docker compose stop tailscale ollama
echo [INFO] Restarting OpenWebUI...
docker compose restart openwebui
echo [INFO] Waiting for OpenWebUI to be healthy...
:wait_openwebui_restart
docker compose ps openwebui | findstr "healthy" >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [INFO] OpenWebUI not yet healthy, waiting 10 more seconds...
    timeout /t 10 /nobreak >nul
    goto :wait_openwebui_restart
)
echo [SUCCESS] OpenWebUI healthy - restarting dependent services
echo [INFO] Starting Ollama...
docker compose up -d ollama
timeout /t 15 /nobreak >nul
echo [INFO] Starting Tailscale...
docker compose up -d tailscale
timeout /t 30 /nobreak >nul
echo [SUCCESS] OpenWebUI restart sequence complete
goto :end

:usage
echo.
echo Quick Emergency Fixes for OpenWebUI AI Stack
echo =============================================
echo.
echo Usage: quick-fixes.bat [option]
echo.
echo Options:
echo   namespace         - Quick restart of Tailscale (fixes most network issues)
echo   rebuild           - Rebuild and restart Tailscale container
echo   nuclear           - Full stack restart (use when all else fails)
echo   gpu               - Check and restart GPU functionality
echo   status            - Show detailed system status
echo   lmstudio          - Fix LM Studio Tailscale connectivity
echo   restart-openwebui - Properly restart OpenWebUI with dependent containers
echo.
echo Examples:
echo   quick-fixes.bat namespace         (most common fix)
echo   quick-fixes.bat restart-openwebui (restart OpenWebUI properly)
echo   quick-fixes.bat lmstudio          (fix LM Studio access)
echo   quick-fixes.bat status            (check everything)
echo   quick-fixes.bat nuclear           (last resort)
echo.
echo Press any key to close this window...
pause >nul
goto :end

:end
echo.
echo [INFO] For advanced recovery options, use: emergency-recovery.ps1