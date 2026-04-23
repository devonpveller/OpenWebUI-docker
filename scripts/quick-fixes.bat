@echo off
REM Quick Emergency Fixes for OpenWebUI AI Stack
REM Usage: quick-fixes.bat [namespace|rebuild|nuclear|gpu|gpu-map|status|lmstudio|restart-openwebui]

setlocal enabledelayedexpansion
set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

if "%1"=="" goto :interactive_menu
if "%1"=="namespace" goto :namespace_reset
if "%1"=="rebuild" goto :rebuild_tailscale
if "%1"=="nuclear" goto :nuclear_option
if "%1"=="gpu" goto :gpu_check
if "%1"=="gpu-map" goto :gpu_map
if "%1"=="status" goto :status_check
if "%1"=="lmstudio" goto :lmstudio_fix
if "%1"=="restart-openwebui" goto :restart_openwebui
if "%1"=="mnemory" goto :mnemory_check
if "%1"=="smolcrawl" goto :smolcrawl_check
goto :usage

:interactive_menu
echo.
echo ========================================
echo   AI Stack Quick Fixes
echo ========================================
echo.
echo Common Fixes:
echo   1. Network namespace reset (most common)
echo   2. Check system status
echo   3. GPU check and restart
echo   4. Show GPU service assignment
echo   5. Restart OpenWebUI properly
echo.
echo Advanced Fixes:
echo   6. Rebuild Tailscale container
echo   7. Fix LM Studio connectivity
echo   8. Nuclear option (full restart)
echo   9. Mnemory check and restart
echo  10. SmolCrawl pipelines check and restart
echo.
echo   0. Exit
echo.
set /p choice="Select option (1-10,0): "

if "%choice%"=="1" goto :namespace_reset
if "%choice%"=="2" goto :status_check
if "%choice%"=="3" goto :gpu_check
if "%choice%"=="4" goto :gpu_map
if "%choice%"=="5" goto :restart_openwebui
if "%choice%"=="6" goto :rebuild_tailscale
if "%choice%"=="7" goto :lmstudio_fix
if "%choice%"=="8" goto :nuclear_option
if "%choice%"=="9" goto :mnemory_check
if "%choice%"=="10" goto :smolcrawl_check
if "%choice%"=="0" goto :end
echo [ERROR] Invalid choice
timeout /t 2 /nobreak >nul
cls
goto :interactive_menu

:namespace_reset
echo.
echo ========================================
echo   Network Namespace Reset
echo ========================================
echo.
echo [DEBUG] Current directory: %CD%
echo [DEBUG] Script directory: %SCRIPT_DIR%
echo.
echo [INFO] Quick namespace reset - restarting Tailscale...
echo [DEBUG] Changing to parent directory...
cd /d "%SCRIPT_DIR%\.."
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Failed to change directory
    pause
    goto :interactive_menu
)
echo [DEBUG] Now in: %CD%
echo [DEBUG] Running docker compose restart tailscale...
docker compose restart tailscale
echo [DEBUG] Docker compose exit code: %ERRORLEVEL%
cd /d "%SCRIPT_DIR%"
echo [INFO] Waiting for Tailscale to reconnect...
timeout /t 30 /nobreak >nul
echo.
echo [INFO] Testing connectivity...
cd /d "%SCRIPT_DIR%\.."
docker compose exec tailscale ping -c 1 8.8.8.8 2>&1
set RESULT=%ERRORLEVEL%
cd /d "%SCRIPT_DIR%"
if %RESULT% EQU 0 (
    echo [SUCCESS] Namespace reset successful - network is working!
) else (
    echo [ERROR] Namespace reset failed (exit code: %RESULT%)
    echo [INFO] Try option 5 (Rebuild Tailscale) for deeper issues
)
if "%1"=="" (
    echo.
    echo Press any key to return to menu...
    pause >nul
    goto :interactive_menu
)
goto :end

:rebuild_tailscale
echo.
echo ========================================
echo   Rebuild Tailscale Container
echo ========================================
echo.
echo [INFO] Rebuilding Tailscale container...
cd ..
docker compose down tailscale
docker compose build --no-cache tailscale
docker compose up -d tailscale
cd scripts
echo [INFO] Waiting for rebuild completion...
timeout /t 45 /nobreak >nul
echo.
echo [INFO] Testing connectivity...
cd ..
docker compose exec tailscale ping -c 1 8.8.8.8 >nul 2>&1
cd scripts
if %ERRORLEVEL% EQU 0 (
    echo [SUCCESS] Tailscale rebuild successful - network restored!
) else (
    echo [ERROR] Tailscale rebuild failed
    echo [INFO] Try option 7 (Nuclear option) if issues persist
)
if "%1"=="" (
    echo.
    pause
    goto :interactive_menu
)
goto :end

:nuclear_option
echo.
echo ========================================
echo   NUCLEAR OPTION - Full Stack Restart
echo ========================================
echo.
echo [WARN] This will restart ALL containers and may take several minutes
echo [WARN] This will DESTROY containers and rebuild them
echo.
echo [INFO] Pre-nuclear diagnostic check...
cd ..
docker compose exec tailscale ping -c 1 8.8.8.8 >nul 2>&1
cd scripts
if %ERRORLEVEL% EQU 0 (
    echo [SUCCESS] Wait - connectivity is actually working!
    echo [INFO] Issue may be performance/timing related, not connectivity
    echo.
    set /p continue="Are you sure you want to continue? (y/n): "
    if /i not "!continue!"=="y" (
        echo [INFO] Nuclear option cancelled
        if "%1"=="" (
            pause
            goto :interactive_menu
        )
        goto :end
    )
) else (
    echo [ERROR] Connectivity is broken - nuclear option recommended
    echo.
    set /p continue="Proceed with full restart? (y/n): "
    if /i not "!continue!"=="y" (
        echo [INFO] Nuclear option cancelled
        if "%1"=="" (
            pause
            goto :interactive_menu
        )
        goto :end
    )
)
echo.
echo [INFO] Performing full stack restart...
cd ..
docker compose down
cd scripts
timeout /t 15 /nobreak >nul
cd ..
docker compose up -d
cd scripts
echo [INFO] Waiting for complete stack initialization...
timeout /t 90 /nobreak >nul
echo.
echo [INFO] Testing final connectivity...
cd ..
docker compose exec tailscale ping -c 1 8.8.8.8 >nul 2>&1
cd scripts
if %ERRORLEVEL% EQU 0 (
    echo [SUCCESS] Nuclear option successful - all systems operational!
) else (
    echo [ERROR] Nuclear option failed - manual intervention required
    echo [INFO] Check logs: docker compose logs
)
if "%1"=="" (
    echo.
    pause
    goto :interactive_menu
)
goto :end

:gpu_check
echo.
echo ========================================
echo   GPU Status Check and Recovery
echo ========================================
echo.
echo [INFO] Checking GPU status and restarting GPU services if needed...
echo [INFO] Testing OpenWebUI GPU access...
cd ..
docker compose exec openwebui python -c "import torch; print('CUDA available:', torch.cuda.is_available())" 2>nul
cd scripts
if %ERRORLEVEL% NEQ 0 (
    echo [WARN] GPU check failed, restarting GPU services...
    cd ..
    docker compose restart llama-cpp llama-cpp-embed openwebui
    cd scripts
    echo [INFO] Waiting for GPU services to restart...
    timeout /t 60 /nobreak >nul
    echo.
    echo [INFO] Re-testing GPU access...
    cd ..
    docker compose exec openwebui python -c "import torch; print('CUDA available:', torch.cuda.is_available())" 2>nul
    cd scripts
    if %ERRORLEVEL% EQU 0 (
        echo [SUCCESS] GPU restored after restart
        echo.
        echo [INFO] Testing llama-cpp availability...
        cd ..
        docker compose exec llama-cpp curl -s -f http://localhost:8080/health >nul 2>&1
        cd scripts
        if %ERRORLEVEL% EQU 0 (
            echo [SUCCESS] llama-cpp GPU integration working
        ) else (
            echo [WARN] llama-cpp may need additional time to initialize
        )
    ) else (
        echo [ERROR] GPU still not available - may need full rebuild
        echo [INFO] Consider running update-stack.bat to rebuild with GPU support
    )
) else (
    echo [SUCCESS] OpenWebUI GPU is working correctly
    echo.
    echo [INFO] Testing llama-cpp GPU integration...
    cd ..
    docker compose exec llama-cpp curl -s -f http://localhost:8080/health >nul 2>&1
    cd scripts
    if %ERRORLEVEL% EQU 0 (
        echo [SUCCESS] llama-cpp GPU integration working
        echo [INFO] All GPU services operational
    ) else (
        echo [WARN] llama-cpp may need restart
        cd ..
        docker compose restart llama-cpp
        cd scripts
        timeout /t 30 /nobreak >nul
        echo [SUCCESS] llama-cpp restarted
    )
)
if "%1"=="" (
    echo.
    pause
    goto :interactive_menu
)
goto :end

:gpu_map
echo.
echo ========================================
echo   GPU Service Assignment
echo ========================================
echo.
echo [INFO] Effective container GPU environment:
cd /d "%SCRIPT_DIR%\.."
for %%S in (openwebui ollama llama-cpp llama-cpp-embed) do (
    echo.
    echo [INFO] %%S:
    docker inspect %%S --format "{{range .Config.Env}}{{println .}}{{end}}" | findstr /R /C:"^NVIDIA_VISIBLE_DEVICES=" /C:"^NVIDIA_DRIVER_CAPABILITIES="
    if !ERRORLEVEL! NEQ 0 (
        echo [WARN] No NVIDIA env detected or container not available
    )
)
cd /d "%SCRIPT_DIR%"
if "%1"=="" (
    echo.
    pause
    goto :interactive_menu
)
goto :end

:status_check
echo.
echo ========================================
echo   System Status Check
echo ========================================
echo.
echo [INFO] Container Status:
cd ..
docker compose ps
cd scripts
echo.
echo [INFO] Starting any missing services...
cd ..
docker compose up -d watchtower >nul 2>&1
cd scripts
echo.
echo [INFO] OpenWebUI GPU Status:
cd ..
docker compose exec openwebui python -c "import torch; print('CUDA available:', torch.cuda.is_available()); print('GPU count:', torch.cuda.device_count())" 2>nul
cd scripts
echo.
echo [INFO] llama-cpp Status:
cd ..
docker compose exec llama-cpp curl -s http://localhost:8080/health 2>nul
cd scripts
echo.
echo [INFO] Network Connectivity:
cd ..
docker compose exec tailscale ping -c 1 8.8.8.8 >nul 2>&1
cd scripts
if %ERRORLEVEL% EQU 0 (
    echo [SUCCESS] Network connectivity: OK
) else (
    echo [ERROR] Network connectivity: FAILED - Try option 1 (namespace reset)
)
echo.
echo [INFO] Tailscale Status:
cd ..
docker compose exec tailscale tailscale --socket=/tmp/tailscaled.sock status 2>nul
cd scripts
echo.
echo [INFO] Tailscale Serve Status:
cd ..
docker compose exec tailscale tailscale --socket=/tmp/tailscaled.sock serve status 2>nul
cd scripts
echo.
echo [INFO] Service Accessibility Check:
cd ..
docker compose exec tailscale wget -q -T 3 -O /dev/null http://127.0.0.1:8080 2>nul
cd scripts
if %ERRORLEVEL% EQU 0 (
    echo [SUCCESS] OpenWebUI accessibility: OK
) else (
    echo [ERROR] OpenWebUI accessibility: FAILED
)
cd ..
docker compose exec llama-cpp curl -s -f http://localhost:8080/health >nul 2>&1
cd scripts
if %ERRORLEVEL% EQU 0 (
    echo [SUCCESS] llama-cpp API accessibility: OK
) else (
    echo [ERROR] llama-cpp API accessibility: FAILED
)
echo.
echo [INFO] Open Terminal Health:
cd ..
docker compose exec -T openwebui curl -s -o NUL -w "%%{http_code}" http://localhost:8000/health 2>nul | findstr /C:"200" >nul
cd scripts
if %ERRORLEVEL% EQU 0 (
    echo [SUCCESS] Open Terminal health: OK
) else (
    echo [ERROR] Open Terminal health: FAILED - run: docker compose up -d open-terminal
)
echo.
echo [INFO] Mnemory Health:
cd ..
docker compose exec mnemory python -c "import urllib.request; urllib.request.urlopen('http://localhost:8051/health')" >nul 2>&1
cd scripts
if %ERRORLEVEL% EQU 0 (
    echo [SUCCESS] Mnemory health: OK
) else (
    echo [ERROR] Mnemory health: FAILED - run: docker compose up -d mnemory
)
echo.
echo [INFO] SmolCrawl Pipelines Health:
cd ..
docker compose exec smolcrawl-pipelines curl -f -s http://localhost:9099/ >nul 2>&1
cd scripts
if %ERRORLEVEL% EQU 0 (
    echo [SUCCESS] SmolCrawl Pipelines health: OK
) else (
    echo [ERROR] SmolCrawl Pipelines health: FAILED - run: docker compose up -d smolcrawl-pipelines
)
if "%1"=="" (
    echo.
    pause
    goto :interactive_menu
)
goto :end

:lmstudio_fix
echo.
echo ========================================
echo   LM Studio Connectivity Fix
echo ========================================
echo.
echo [INFO] Fixing LM Studio Tailscale connectivity...
echo [INFO] Testing LM Studio host connectivity...
curl -s -m 5 http://169.254.83.107:5506/v1/models >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] LM Studio not accessible - make sure it's running
    echo [INFO] Start LM Studio and ensure the server is running on port 5506
    if "%1"=="" (
        pause
        goto :interactive_menu
    )
    goto :end
)
echo [SUCCESS] LM Studio is running
echo.
echo [INFO] Restarting socat proxy and Tailscale serve...
cd ..
docker compose exec tailscale sh -c "pkill socat 2>/dev/null || true"
cd scripts
echo [INFO] Starting persistent socat proxy...
cd ..
docker compose exec -d tailscale sh -c "socat TCP-LISTEN:8234,fork,reuseaddr,keepalive TCP:169.254.83.107:5506"
cd scripts
echo [INFO] Waiting for proxy to initialize...
timeout /t 8 /nobreak >nul
echo.
echo [INFO] Testing proxy connection...
cd ..
docker compose exec tailscale sh -c "wget -q -T 5 -O /dev/null http://127.0.0.1:8234/v1/models && echo 'Proxy working' || echo 'Proxy failed'"
docker compose exec tailscale tailscale --socket=/tmp/tailscaled.sock serve --https=443 --set-path=/lmstudio --bg http://127.0.0.1:8234 >nul
cd scripts
echo [SUCCESS] LM Studio Tailscale configuration restored
echo [INFO] Access URL: https://openwebui-13.tail37f875.ts.net/lmstudio
if "%1"=="" (
    echo.
    pause
    goto :interactive_menu
)
goto :end

:restart_openwebui
echo.
echo ========================================
echo   OpenWebUI Restart Sequence
echo ========================================
echo.
echo [INFO] Restarting OpenWebUI with proper network dependency handling...
echo [WARN] This will restart OpenWebUI, llama-cpp, llama-cpp-embed, and Tailscale containers
echo.
echo [INFO] Stopping dependent containers first...
cd ..
docker compose stop tailscale llama-cpp llama-cpp-embed mnemory mnemory-backup smolcrawl-pipelines
cd scripts
echo [INFO] Restarting OpenWebUI...
cd ..
docker compose restart openwebui
cd scripts
echo [INFO] Waiting for OpenWebUI to be healthy...
:wait_openwebui_restart
cd ..
docker compose ps openwebui | findstr "healthy" >nul 2>&1
cd scripts
if %ERRORLEVEL% NEQ 0 (
    echo [INFO] OpenWebUI not yet healthy, waiting 10 more seconds...
    timeout /t 10 /nobreak >nul
    goto :wait_openwebui_restart
)
echo [SUCCESS] OpenWebUI healthy - restarting dependent services
echo.
echo [INFO] Starting llama-cpp...
cd ..
docker compose start llama-cpp
if %ERRORLEVEL% NEQ 0 (
    echo [WARN] llama-cpp start failed, trying up -d...
    docker compose up -d llama-cpp
)
cd scripts
echo [INFO] Starting llama-cpp-embed...
cd ..
docker compose start llama-cpp-embed
if %ERRORLEVEL% NEQ 0 (
    echo [WARN] llama-cpp-embed start failed, trying up -d...
    docker compose up -d llama-cpp-embed
)
cd scripts
echo [INFO] Waiting for llama-cpp to initialize...
timeout /t 15 /nobreak >nul
echo.
echo [INFO] Starting Tailscale...
cd ..
docker compose start tailscale
if %ERRORLEVEL% NEQ 0 (
    echo [WARN] Tailscale start failed, trying up -d...
    docker compose up -d tailscale
)
cd scripts
echo [INFO] Waiting for Tailscale to connect...
timeout /t 30 /nobreak >nul
echo.
echo [INFO] Starting Mnemory...
cd ..
docker compose start mnemory
if %ERRORLEVEL% NEQ 0 (
    echo [WARN] Mnemory start failed, trying up -d...
    docker compose up -d mnemory
)
docker compose up -d mnemory-backup
cd scripts
echo [INFO] Starting SmolCrawl Pipelines...
cd ..
docker compose start smolcrawl-pipelines
if %ERRORLEVEL% NEQ 0 (
    echo [WARN] SmolCrawl Pipelines start failed, trying up -d...
    docker compose up -d smolcrawl-pipelines
)
cd scripts
echo.
echo [INFO] Verifying services are running...
cd ..
docker compose ps --format "table {{.Service}}\t{{.Status}}"
cd scripts
echo.
echo [SUCCESS] OpenWebUI restart sequence complete
if "%1"=="" (
    echo.
    pause
    goto :interactive_menu
)
goto :end

:mnemory_check
echo.
echo ========================================
echo   Mnemory Health Check and Recovery
echo ========================================
echo.
echo [INFO] Checking Mnemory service status...
cd /d "%SCRIPT_DIR%\.."
docker compose ps mnemory --format "table {{.Service}}\t{{.Status}}"
echo.
echo [INFO] Testing Mnemory health endpoint...
docker compose exec mnemory python -c "import urllib.request; urllib.request.urlopen('http://localhost:8051/health')" >nul 2>&1
set RESULT=%ERRORLEVEL%
cd /d "%SCRIPT_DIR%"
if %RESULT% EQU 0 (
    echo [SUCCESS] Mnemory is healthy and running
) else (
    echo [WARN] Mnemory health check failed, restarting...
    cd /d "%SCRIPT_DIR%\.."
    docker compose restart mnemory
    cd /d "%SCRIPT_DIR%"
    echo [INFO] Waiting for Mnemory to start...
    timeout /t 30 /nobreak >nul
    cd /d "%SCRIPT_DIR%\.."
    docker compose exec mnemory python -c "import urllib.request; urllib.request.urlopen('http://localhost:8051/health')" >nul 2>&1
    set RESULT=%ERRORLEVEL%
    cd /d "%SCRIPT_DIR%"
    if %RESULT% EQU 0 (
        echo [SUCCESS] Mnemory restored after restart
    ) else (
        echo [ERROR] Mnemory still not healthy - check logs: docker compose logs mnemory
    )
)
echo.
echo [INFO] Mnemory backup service status:
cd /d "%SCRIPT_DIR%\.."
docker compose ps mnemory-backup --format "table {{.Service}}\t{{.Status}}"
cd /d "%SCRIPT_DIR%"
if "%1"=="" (
    echo.
    pause
    goto :interactive_menu
)
goto :end

:smolcrawl_check
echo.
echo ========================================
echo   SmolCrawl Pipelines Health Check
echo ========================================
echo.
echo [INFO] Checking SmolCrawl Pipelines service status...
cd /d "%SCRIPT_DIR%\.."
docker compose ps smolcrawl-pipelines --format "table {{.Service}}\t{{.Status}}"
echo.
echo [INFO] Testing SmolCrawl Pipelines health endpoint...
docker compose exec smolcrawl-pipelines curl -f -s http://localhost:9099/ >nul 2>&1
set RESULT=%ERRORLEVEL%
cd /d "%SCRIPT_DIR%"
if %RESULT% EQU 0 (
    echo [SUCCESS] SmolCrawl Pipelines is healthy and running
) else (
    echo [WARN] SmolCrawl Pipelines health check failed, restarting...
    cd /d "%SCRIPT_DIR%\.."
    docker compose restart smolcrawl-pipelines
    cd /d "%SCRIPT_DIR%"
    echo [INFO] Waiting for SmolCrawl Pipelines to start...
    timeout /t 30 /nobreak >nul
    cd /d "%SCRIPT_DIR%\.."
    docker compose exec smolcrawl-pipelines curl -f -s http://localhost:9099/ >nul 2>&1
    set RESULT=%ERRORLEVEL%
    cd /d "%SCRIPT_DIR%"
    if %RESULT% EQU 0 (
        echo [SUCCESS] SmolCrawl Pipelines restored after restart
    ) else (
        echo [ERROR] SmolCrawl Pipelines still not healthy - check logs: docker compose logs smolcrawl-pipelines
    )
)
if "%1"=="" (
    echo.
    pause
    goto :interactive_menu
)
goto :end

:usage
echo.
echo ========================================
echo   AI Stack Quick Fixes
echo ========================================
echo.
echo Usage: quick-fixes.bat [option]
echo.
echo Options:
echo   namespace         - Quick restart of Tailscale (fixes most network issues)
echo   status            - Show detailed system status
echo   gpu               - Check and restart GPU functionality
echo   gpu-map           - Show current GPU assignment per service
echo   restart-openwebui - Properly restart OpenWebUI with dependent containers
echo   rebuild           - Rebuild and restart Tailscale container
echo   lmstudio          - Fix LM Studio Tailscale connectivity
echo   mnemory           - Check Mnemory health and restart if needed
echo   smolcrawl         - Check SmolCrawl Pipelines health and restart if needed
echo   nuclear           - Full stack restart (use when all else fails)
echo.
echo Examples:
echo   quick-fixes.bat namespace         (most common fix)
echo   quick-fixes.bat status            (check everything)
echo   quick-fixes.bat restart-openwebui (restart OpenWebUI properly)
echo.
echo Or run without arguments for interactive menu
echo.
pause
goto :end

:end
if "%1"=="" (
    echo.
    pause
    cls
    goto :interactive_menu
)
echo.
echo [INFO] For advanced recovery options, use: emergency-recovery.ps1
pause
endlocal
exit /b

:error_exit
echo.
echo [ERROR] Script encountered an error
echo [DEBUG] Last error level: %ERRORLEVEL%
pause
endlocal
exit /b 1