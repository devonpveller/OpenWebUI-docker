@echo off
REM Quick Emergency Fixes for OpenWebUI AI Stack
REM Usage: quick-fixes.bat [namespace|rebuild|nuclear|gpu|gpu-map|status|restart-openwebui]

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
if "%1"=="restart-openwebui" goto :restart_openwebui
if "%1"=="mnemory" goto :mnemory_check
if "%1"=="llama-cpp" goto :llama_cpp_check
if "%1"=="open-notebook" goto :open_notebook_check
if "%1"=="openbrain" goto :openbrain_check
if "%1"=="llm-gateway" goto :llm_gateway_check
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
echo   7. Nuclear option (full restart)
echo   8. Mnemory check and restart
echo   9. llama-cpp / llama-cpp-embed check and restart
echo  10. open-notebook (and surrealdb) check and restart
echo  11. Open Brain (mcp/mcpo/db/gateway/wiki) check and restart
echo  12. llm-gateway (LiteLLM front door) check and restart
echo.
echo   0. Exit
echo.
set /p choice="Select option (1-12,0): "

if "%choice%"=="1" goto :namespace_reset
if "%choice%"=="2" goto :status_check
if "%choice%"=="3" goto :gpu_check
if "%choice%"=="4" goto :gpu_map
if "%choice%"=="5" goto :restart_openwebui
if "%choice%"=="6" goto :rebuild_tailscale
if "%choice%"=="7" goto :nuclear_option
if "%choice%"=="8" goto :mnemory_check
if "%choice%"=="9" goto :llama_cpp_check
if "%choice%"=="10" goto :open_notebook_check
if "%choice%"=="11" goto :openbrain_check
if "%choice%"=="12" goto :llm_gateway_check
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
cd /d "%SCRIPT_DIR%\..\.."
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
cd /d "%SCRIPT_DIR%\..\.."
docker compose exec tailscale ping -c 1 8.8.8.8 2>&1
set RESULT=%ERRORLEVEL%
cd /d "%SCRIPT_DIR%"
if %RESULT% EQU 0 (
    echo [SUCCESS] Namespace reset successful - network is working!
) else (
    echo [ERROR] Namespace reset failed ^(exit code: %RESULT%^)
    echo [INFO] Try option 5 ^(Rebuild Tailscale^) for deeper issues
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
cd /d "%SCRIPT_DIR%\..\.."
docker compose down tailscale
docker compose build --no-cache tailscale
docker compose up -d tailscale
cd /d "%SCRIPT_DIR%"
echo [INFO] Waiting for rebuild completion...
timeout /t 45 /nobreak >nul
echo.
echo [INFO] Testing connectivity...
cd /d "%SCRIPT_DIR%\..\.."
docker compose exec tailscale ping -c 1 8.8.8.8 >nul 2>&1
cd /d "%SCRIPT_DIR%"
if %ERRORLEVEL% EQU 0 (
    echo [SUCCESS] Tailscale rebuild successful - network restored!
) else (
    echo [ERROR] Tailscale rebuild failed
    echo [INFO] Try option 7 ^(Nuclear option^) if issues persist
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
cd /d "%SCRIPT_DIR%\..\.."
docker compose exec tailscale ping -c 1 8.8.8.8 >nul 2>&1
cd /d "%SCRIPT_DIR%"
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
cd /d "%SCRIPT_DIR%\..\.."
docker compose down
cd /d "%SCRIPT_DIR%"
timeout /t 15 /nobreak >nul
cd /d "%SCRIPT_DIR%\..\.."
docker compose up -d
cd /d "%SCRIPT_DIR%"
echo [INFO] Waiting for complete stack initialization...
timeout /t 90 /nobreak >nul
echo.
echo [INFO] Testing final connectivity...
cd /d "%SCRIPT_DIR%\..\.."
docker compose exec tailscale ping -c 1 8.8.8.8 >nul 2>&1
cd /d "%SCRIPT_DIR%"
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
cd /d "%SCRIPT_DIR%\..\.."
docker compose exec openwebui python -c "import torch; print('CUDA available:', torch.cuda.is_available())" 2>nul
cd /d "%SCRIPT_DIR%"
if %ERRORLEVEL% NEQ 0 (
    echo [WARN] GPU check failed, restarting GPU services...
cd /d "%SCRIPT_DIR%\..\.."
    docker restart llama-cpp-upstream llama-cpp-embed-upstream
docker compose restart openwebui
    cd /d "%SCRIPT_DIR%"
    echo [INFO] Waiting for GPU services to restart...
    timeout /t 60 /nobreak >nul
    echo.
    echo [INFO] Re-testing GPU access...
cd /d "%SCRIPT_DIR%\..\.."
    docker compose exec openwebui python -c "import torch; print('CUDA available:', torch.cuda.is_available())" 2>nul
    cd /d "%SCRIPT_DIR%"
    if %ERRORLEVEL% EQU 0 (
        echo [SUCCESS] GPU restored after restart
        echo.
        echo [INFO] Testing llama-cpp availability...
cd /d "%SCRIPT_DIR%\..\.."
        docker exec llama-cpp-upstream curl -s -f http://localhost:8080/health >nul 2>&1
        cd /d "%SCRIPT_DIR%"
        if %ERRORLEVEL% EQU 0 (
            echo [SUCCESS] llama-cpp GPU integration working
        ) else (
            echo [WARN] llama-cpp may need additional time to initialize
        )
        echo.
        echo [INFO] Testing llama-cpp-embed availability...
cd /d "%SCRIPT_DIR%\..\.."
        docker exec llama-cpp-embed-upstream curl -s -f http://localhost:8080/health >nul 2>&1
        cd /d "%SCRIPT_DIR%"
        if %ERRORLEVEL% EQU 0 (
            echo [SUCCESS] llama-cpp-embed GPU integration working
        ) else (
            echo [WARN] llama-cpp-embed may need additional time to initialize
        )
    ) else (
        echo [ERROR] GPU still not available - may need full rebuild
        echo [INFO] Consider running update-stack.bat to rebuild with GPU support
    )
) else (
    echo [SUCCESS] OpenWebUI GPU is working correctly
    echo.
    echo [INFO] Testing llama-cpp GPU integration...
cd /d "%SCRIPT_DIR%\..\.."
    docker exec llama-cpp-upstream curl -s -f http://localhost:8080/health >nul 2>&1
    cd /d "%SCRIPT_DIR%"
    if %ERRORLEVEL% EQU 0 (
        echo [SUCCESS] llama-cpp GPU integration working
        echo.
        echo [INFO] Testing llama-cpp-embed GPU integration...
cd /d "%SCRIPT_DIR%\..\.."
        docker exec llama-cpp-embed-upstream curl -s -f http://localhost:8080/health >nul 2>&1
        cd /d "%SCRIPT_DIR%"
        if %ERRORLEVEL% EQU 0 (
            echo [SUCCESS] llama-cpp-embed GPU integration working
            echo [INFO] All GPU services operational
        ) else (
            echo [WARN] llama-cpp-embed may need restart
cd /d "%SCRIPT_DIR%\..\.."
            docker restart llama-cpp-embed-upstream
            cd /d "%SCRIPT_DIR%"
            timeout /t 30 /nobreak >nul
            echo [SUCCESS] llama-cpp-embed restarted
        )
    ) else (
        echo [WARN] llama-cpp may need restart
cd /d "%SCRIPT_DIR%\..\.."
        docker restart llama-cpp-upstream llama-cpp-embed-upstream
        cd /d "%SCRIPT_DIR%"
        timeout /t 30 /nobreak >nul
        echo [SUCCESS] llama-cpp services restarted
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
cd /d "%SCRIPT_DIR%\..\.."
for %%S in (openwebui llama-cpp-upstream llama-cpp-embed-upstream) do (
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
cd /d "%SCRIPT_DIR%\..\.."
docker compose ps
cd /d "%SCRIPT_DIR%"
echo.
echo [INFO] OpenWebUI GPU Status:
cd /d "%SCRIPT_DIR%\..\.."
docker compose exec openwebui python -c "import torch; print('CUDA available:', torch.cuda.is_available()); print('GPU count:', torch.cuda.device_count())" 2>nul
cd /d "%SCRIPT_DIR%"
echo.
echo [INFO] llama-cpp Status:
cd /d "%SCRIPT_DIR%\..\.."
docker exec llama-cpp-upstream curl -s http://localhost:8080/health 2>nul
cd /d "%SCRIPT_DIR%"
echo.
echo [INFO] llama-cpp-embed Status:
cd /d "%SCRIPT_DIR%\..\.."
docker exec llama-cpp-embed-upstream curl -s http://localhost:8080/health 2>nul
cd /d "%SCRIPT_DIR%"
echo.
echo [INFO] Network Connectivity:
cd /d "%SCRIPT_DIR%\..\.."
docker compose exec tailscale ping -c 1 8.8.8.8 >nul 2>&1
cd /d "%SCRIPT_DIR%"
if %ERRORLEVEL% EQU 0 (
    echo [SUCCESS] Network connectivity: OK
) else (
    echo [ERROR] Network connectivity: FAILED - Try option 1 ^(namespace reset^)
)
echo.
echo [INFO] Tailscale Status:
cd /d "%SCRIPT_DIR%\..\.."
docker compose exec tailscale tailscale --socket=/tmp/tailscaled.sock status 2>nul
cd /d "%SCRIPT_DIR%"
echo.
echo [INFO] Tailscale Serve Status:
cd /d "%SCRIPT_DIR%\..\.."
docker compose exec tailscale tailscale --socket=/tmp/tailscaled.sock serve status 2>nul
cd /d "%SCRIPT_DIR%"
echo.
echo [INFO] Service Accessibility Check:
cd /d "%SCRIPT_DIR%\..\.."
docker compose exec tailscale wget -q -T 3 -O /dev/null http://127.0.0.1:8080 2>nul
cd /d "%SCRIPT_DIR%"
if %ERRORLEVEL% EQU 0 (
    echo [SUCCESS] OpenWebUI accessibility: OK
) else (
    echo [ERROR] OpenWebUI accessibility: FAILED
)
cd /d "%SCRIPT_DIR%\..\.."
docker exec llama-cpp-upstream curl -s -f http://localhost:8080/health >nul 2>&1
cd /d "%SCRIPT_DIR%"
if %ERRORLEVEL% EQU 0 (
    echo [SUCCESS] llama-cpp API accessibility: OK
) else (
    echo [ERROR] llama-cpp API accessibility: FAILED
)
echo.
echo [INFO] Open Terminal Health:
cd /d "%SCRIPT_DIR%\..\.."
REM open-terminal is the little-coder workspace plane — it left openwebui's
REM network namespace, so probe it inside its OWN container.
docker compose exec -T open-terminal curl -s -o NUL -w "%%{http_code}" http://localhost:8000/health 2>nul | findstr /C:"200" >nul
cd /d "%SCRIPT_DIR%"
if %ERRORLEVEL% EQU 0 (
    echo [SUCCESS] Open Terminal health: OK
) else (
    echo [ERROR] Open Terminal health: FAILED - run: docker compose up -d open-terminal
)
echo.
echo [INFO] Mnemory Health:
cd /d "%SCRIPT_DIR%\..\.."
docker exec mnemory python -c "import urllib.request; urllib.request.urlopen('http://localhost:8051/health')" >nul 2>&1
cd /d "%SCRIPT_DIR%"
if %ERRORLEVEL% EQU 0 (
    echo [SUCCESS] Mnemory health: OK
) else (
    echo [ERROR] Mnemory health: FAILED - run: docker compose -f memory\docker-compose.yml --env-file .env up -d mnemory
)
echo.
echo.
echo [INFO] open-notebook Health (API on port 5055):
cd /d "%SCRIPT_DIR%\..\.."
docker compose exec open_notebook python3 -c "import urllib.request,sys; urllib.request.urlopen('http://localhost:5055/api/config', timeout=5); sys.exit(0)" >nul 2>&1
cd /d "%SCRIPT_DIR%"
if %ERRORLEVEL% EQU 0 (
    echo [SUCCESS] open-notebook health: OK
) else (
    echo [ERROR] open-notebook health: FAILED - run: quick-fixes.bat open-notebook
)
echo.
echo [INFO] surrealdb (open-notebook DB) running state:
cd /d "%SCRIPT_DIR%\..\.."
docker compose ps surrealdb --format "table {{.Service}}\t{{.Status}}" 2>nul
cd /d "%SCRIPT_DIR%"
echo.
echo [INFO] Web-search gateway health (:8085/healthz):
curl -s -f -m 5 http://127.0.0.1:8085/healthz >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo [SUCCESS] search-gateway healthz: OK
) else (
    echo [ERROR] search-gateway healthz: FAILED - run: docker compose -f search\docker-compose.yml --env-file .env up -d
)
echo.
echo [INFO] Extended planes running state (search / little-coder / mnemory-cloud-gateway):
echo        ^(compose service keys: gateway=search-gateway, vpn=search-vpn^)
cd /d "%SCRIPT_DIR%\..\.."
docker compose -f search\docker-compose.yml --env-file .env ps --format "table {{.Service}}\t{{.Status}}" 2>nul
docker compose ps little-coder lc-egress --format "table {{.Service}}\t{{.Status}}" 2>nul
cd /d "%SCRIPT_DIR%"
echo.
echo [INFO] Open Brain stack (mcp/mcpo/db/gateway/wiki - SEPARATE compose project):
powershell -ExecutionPolicy Bypass -NoProfile -File "%SCRIPT_DIR%check-openbrain-health.ps1"
echo.
echo [INFO] Backup schedulers (no health endpoints - running state only):
cd /d "%SCRIPT_DIR%\..\.."
docker compose ps openwebui-backup --format "table {{.Service}}\t{{.Status}}" 2>nul
docker compose -f memory\docker-compose.yml --env-file .env ps mnemory-backup --format "table {{.Service}}\t{{.Status}}" 2>nul
cd /d "%SCRIPT_DIR%"
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
cd /d "%SCRIPT_DIR%\..\.."
docker compose -f inference\docker-compose.yml --env-file .env stop --timeout 30
docker compose -f memory\docker-compose.yml --env-file .env stop --timeout 30
docker compose stop tailscale openwebui-backup open_notebook surrealdb
cd /d "%SCRIPT_DIR%"
echo [INFO] Restarting OpenWebUI...
cd /d "%SCRIPT_DIR%\..\.."
docker compose restart openwebui
cd /d "%SCRIPT_DIR%"
echo [INFO] Waiting for OpenWebUI to be healthy...
:wait_openwebui_restart
cd /d "%SCRIPT_DIR%\..\.."
docker compose ps openwebui | findstr "healthy" >nul 2>&1
cd /d "%SCRIPT_DIR%"
if %ERRORLEVEL% NEQ 0 (
    echo [INFO] OpenWebUI not yet healthy, waiting 10 more seconds...
    timeout /t 10 /nobreak >nul
    goto :wait_openwebui_restart
)
echo [SUCCESS] OpenWebUI healthy - restarting dependent services
echo.
echo [INFO] Starting llama-cpp...
cd /d "%SCRIPT_DIR%\..\.."
docker compose start llama-cpp-upstream
if %ERRORLEVEL% NEQ 0 (
    echo [WARN] llama-cpp start failed, trying up -d...
    docker compose -f inference\docker-compose.yml --env-file .env up -d llama-cpp-upstream
)
cd /d "%SCRIPT_DIR%"
echo [INFO] Starting llama-cpp-embed...
cd /d "%SCRIPT_DIR%\..\.."
docker compose start llama-cpp-embed-upstream
if %ERRORLEVEL% NEQ 0 (
    echo [WARN] llama-cpp-embed start failed, trying up -d...
    docker compose -f inference\docker-compose.yml --env-file .env up -d llama-cpp-embed-upstream
)
cd /d "%SCRIPT_DIR%"
echo [INFO] Waiting for llama-cpp to initialize...
timeout /t 15 /nobreak >nul
echo.
echo [INFO] Starting Tailscale...
cd /d "%SCRIPT_DIR%\..\.."
docker compose start tailscale
if %ERRORLEVEL% NEQ 0 (
    echo [WARN] Tailscale start failed, trying up -d...
    docker compose up -d tailscale
)
cd /d "%SCRIPT_DIR%"
echo [INFO] Waiting for Tailscale to connect...
timeout /t 30 /nobreak >nul
echo.
echo [INFO] Starting Mnemory...
cd /d "%SCRIPT_DIR%\..\.."
docker compose start mnemory
if %ERRORLEVEL% NEQ 0 (
    echo [WARN] Mnemory start failed, trying up -d...
    docker compose -f memory\docker-compose.yml --env-file .env up -d mnemory
)
docker compose -f memory\docker-compose.yml --env-file .env up -d mnemory-backup
docker compose up -d openwebui-backup
cd /d "%SCRIPT_DIR%"
cd /d "%SCRIPT_DIR%"
echo [INFO] Starting surrealdb (open-notebook DB)...
cd /d "%SCRIPT_DIR%\..\.."
docker compose start surrealdb
if %ERRORLEVEL% NEQ 0 (
    echo [WARN] surrealdb start failed, trying up -d...
    docker compose up -d surrealdb
)
cd /d "%SCRIPT_DIR%"
echo [INFO] Starting open-notebook...
cd /d "%SCRIPT_DIR%\..\.."
docker compose start open_notebook
if %ERRORLEVEL% NEQ 0 (
    echo [WARN] open-notebook start failed, trying up -d...
    docker compose up -d open_notebook
)
cd /d "%SCRIPT_DIR%"
echo.
echo [INFO] Verifying services are running...
cd /d "%SCRIPT_DIR%\..\.."
docker compose ps --format "table {{.Service}}\t{{.Status}}"
cd /d "%SCRIPT_DIR%"
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
cd /d "%SCRIPT_DIR%\..\.."
docker compose -f memory\docker-compose.yml --env-file .env ps mnemory --format "table {{.Service}}\t{{.Status}}"
echo.
echo [INFO] Testing Mnemory health endpoint...
docker exec mnemory python -c "import urllib.request; urllib.request.urlopen('http://localhost:8051/health')" >nul 2>&1
set RESULT=%ERRORLEVEL%
cd /d "%SCRIPT_DIR%"
if %RESULT% EQU 0 (
    echo [SUCCESS] Mnemory is healthy and running
) else (
    echo [WARN] Mnemory health check failed, restarting...
    cd /d "%SCRIPT_DIR%\..\.."
    docker restart mnemory
    cd /d "%SCRIPT_DIR%"
    echo [INFO] Waiting for Mnemory to start...
    timeout /t 30 /nobreak >nul
    cd /d "%SCRIPT_DIR%\..\.."
    docker exec mnemory python -c "import urllib.request; urllib.request.urlopen('http://localhost:8051/health')" >nul 2>&1
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
cd /d "%SCRIPT_DIR%\..\.."
docker compose -f memory\docker-compose.yml --env-file .env ps mnemory-backup --format "table {{.Service}}\t{{.Status}}"
cd /d "%SCRIPT_DIR%"
if "%1"=="" (
    echo.
    pause
    goto :interactive_menu
)
goto :end

:llama_cpp_check
echo.
echo ========================================
echo   llama-cpp Health Check and Recovery
echo ========================================
echo.
echo [INFO] Checking llama-cpp service status...
cd /d "%SCRIPT_DIR%\..\.."
docker ps --filter "name=llama-cpp-upstream" --filter "name=llama-cpp-embed-upstream" --format "table {{.Names}}\t{{.Status}}"
echo.
echo [INFO] Testing llama-cpp health endpoint...
docker exec llama-cpp-upstream curl -s -f http://localhost:8080/health >nul 2>&1
set RESULT=%ERRORLEVEL%
if %RESULT% EQU 0 (
    echo [SUCCESS] llama-cpp is healthy and running
) else (
    echo [WARN] llama-cpp health check failed, restarting...
    docker restart llama-cpp-upstream
    echo [INFO] Waiting for llama-cpp to start ^(large model, may take up to 2 minutes^)...
    timeout /t 60 /nobreak >nul
    docker exec llama-cpp-upstream curl -s -f http://localhost:8080/health >nul 2>&1
    set RESULT=!ERRORLEVEL!
    if !RESULT! EQU 0 (
        echo [SUCCESS] llama-cpp restored after restart
    ) else (
        echo [ERROR] llama-cpp-upstream still not healthy - check logs: docker compose logs llama-cpp-upstream
    )
)
echo.
echo [INFO] Testing llama-cpp-embed health endpoint...
docker exec llama-cpp-embed-upstream curl -s -f http://localhost:8080/health >nul 2>&1
set RESULT=%ERRORLEVEL%
if %RESULT% EQU 0 (
    echo [SUCCESS] llama-cpp-embed is healthy and running
) else (
    echo [WARN] llama-cpp-embed health check failed, restarting...
    docker restart llama-cpp-embed-upstream
    echo [INFO] Waiting for llama-cpp-embed to start...
    timeout /t 30 /nobreak >nul
    docker exec llama-cpp-embed-upstream curl -s -f http://localhost:8080/health >nul 2>&1
    set RESULT=!ERRORLEVEL!
    if !RESULT! EQU 0 (
        echo [SUCCESS] llama-cpp-embed restored after restart
    ) else (
        echo [ERROR] llama-cpp-embed-upstream still not healthy - check logs: docker compose logs llama-cpp-embed-upstream
    )
)
cd /d "%SCRIPT_DIR%"
if "%1"=="" (
    echo.
    pause
    goto :interactive_menu
)
goto :end

:llm_gateway_check
echo.
echo ========================================
echo   llm-gateway (LiteLLM) Health Check and Recovery
echo ========================================
echo.
echo [INFO] Checking llm-gateway / llm-gateway-db status...
cd /d "%SCRIPT_DIR%\..\.."
docker ps --filter "name=llm-gateway" --filter "name=llm-gateway-db" --format "table {{.Names}}\t{{.Status}}"
echo.
echo [INFO] Testing gateway liveliness (the wolfi image has no curl - use python)...
docker exec llm-gateway python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8080/health/liveliness').status==200 else 1)" >nul 2>&1
set RESULT=%ERRORLEVEL%
if %RESULT% EQU 0 (
    echo [SUCCESS] llm-gateway is healthy ^(front door is up^)
) else (
    echo [WARN] llm-gateway liveliness failed, restarting db then gateway...
    docker compose -f inference\docker-compose.yml --env-file .env up -d llm-gateway-db
    timeout /t 5 /nobreak >nul
    docker restart llm-gateway
    echo [INFO] Waiting for gateway ^(first boot runs prisma migrations, ~60-90s^)...
    timeout /t 60 /nobreak >nul
    docker exec llm-gateway python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8080/health/liveliness').status==200 else 1)" >nul 2>&1
    set RESULT=!ERRORLEVEL!
    if !RESULT! EQU 0 (
        echo [SUCCESS] llm-gateway restored after restart
    ) else (
        echo [ERROR] llm-gateway still not healthy - check logs: docker compose logs llm-gateway
        echo [HINT] The gateway needs llama-cpp-upstream + llama-cpp-embed-upstream healthy first ^(option 11^).
    )
)
echo.
echo [INFO] Recent spend-ledger rows (confirms callers are routing through the gateway):
docker exec llm-gateway-db psql -U litellm -d litellm -c "SELECT api_key, model, count(*) FROM \"LiteLLM_SpendLogs\" GROUP BY 1,2 ORDER BY 3 DESC LIMIT 5;" 2>nul
cd /d "%SCRIPT_DIR%"
if "%1"=="" (
    echo.
    pause
    goto :interactive_menu
)
goto :end

:open_notebook_check
echo.
echo ========================================
echo   open-notebook Health Check and Recovery
echo ========================================
echo.
echo [INFO] Checking surrealdb (open-notebook DB) status...
cd /d "%SCRIPT_DIR%\..\.."
docker compose ps surrealdb --format "table {{.Service}}\t{{.Status}}"
docker compose ps surrealdb --format json 2>nul | findstr /C:"\"State\":\"running\"" >nul 2>&1
set RESULT=%ERRORLEVEL%
if not %RESULT% EQU 0 (
    echo [WARN] surrealdb not running, starting...
    docker compose up -d surrealdb
    timeout /t 10 /nobreak >nul
)
echo.
echo [INFO] Checking open_notebook service status...
docker compose ps open_notebook --format "table {{.Service}}\t{{.Status}}"
echo.
echo [INFO] Testing open-notebook API endpoint (port 5055)...
docker compose exec open_notebook python3 -c "import urllib.request,sys; urllib.request.urlopen('http://localhost:5055/api/config', timeout=5); sys.exit(0)" >nul 2>&1
set RESULT=%ERRORLEVEL%
if %RESULT% EQU 0 (
    echo [SUCCESS] open-notebook API is healthy and running
) else (
    echo [WARN] open-notebook API health check failed, restarting...
    docker compose restart open_notebook
    echo [INFO] Waiting for open-notebook to start ^(Next.js waits for FastAPI, may take up to 90 seconds^)...
    timeout /t 60 /nobreak >nul
    docker compose exec open_notebook python3 -c "import urllib.request,sys; urllib.request.urlopen('http://localhost:5055/api/config', timeout=5); sys.exit(0)" >nul 2>&1
    set RESULT=!ERRORLEVEL!
    if !RESULT! EQU 0 (
        echo [SUCCESS] open-notebook restored after restart
    ) else (
        echo [ERROR] open-notebook still not healthy - check logs: docker compose logs open_notebook
    )
)
cd /d "%SCRIPT_DIR%"
if "%1"=="" (
    echo.
    pause
    goto :interactive_menu
)
goto :end

:openbrain_check
echo.
echo ========================================
echo   Open Brain Health Check and Recovery
echo ========================================
echo.
echo [INFO] Open Brain is a SEPARATE compose project (open-brain) - a plain
echo [INFO] 'docker compose' from this stack cannot see it.
echo [INFO] Running canonical probe with auto-repair (incl. the openbrain-mcp
echo [INFO] stale-DB-pool guard that fixes OWUI tool 500s / 'Broken pipe')...
echo.
powershell -ExecutionPolicy Bypass -NoProfile -File "%SCRIPT_DIR%check-openbrain-health.ps1" -Repair
echo.
echo [INFO] If mcp/mcpo tools still 500 in Open WebUI after this, check logs:
echo [INFO]   docker logs --tail 50 openbrain-mcp
echo [INFO]   docker logs --tail 50 openbrain-mcpo
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
echo   mnemory           - Check Mnemory health and restart if needed
echo   llama-cpp         - Check llama-cpp and llama-cpp-embed health and restart if needed
echo   open-notebook     - Check open-notebook (and surrealdb) health and restart if needed
echo   openbrain         - Check Open Brain (mcp/mcpo/db/gateway/wiki) health and restart if needed
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