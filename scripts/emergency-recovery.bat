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
echo [INFO] Running pre-recovery diagnostics...
echo [INFO] Testing basic connectivity before destructive actions...

REM Check if containers are running
docker compose ps --format json >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Docker Compose not working properly
    goto :nuclear_option
)

REM Test OpenWebUI health
docker compose exec openwebui curl -f -s http://localhost:8080/ >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo [INFO] OpenWebUI responding...
    
    REM Test Ollama connectivity  
    docker compose exec openwebui curl -f -s http://localhost:11434/api/version >nul 2>&1
    if %ERRORLEVEL% EQU 0 (
        echo [INFO] Ollama connectivity working...
        
        REM Test external connectivity
        docker compose exec tailscale ping -c 1 8.8.8.8 >nul 2>&1
        if %ERRORLEVEL% EQU 0 (
            echo [SUCCESS] All basic checks PASSED - trying minimal recovery first
            goto :minimal_recovery
        ) else (
            echo [WARN] External connectivity failed
        )
    ) else (
        echo [WARN] Ollama connectivity failed
    )
) else (
    echo [WARN] OpenWebUI health check failed
)

echo [INFO] Basic checks failed - proceeding with full recovery

REM Phase 1: Graceful shutdown in reverse dependency order
echo [INFO] Phase 1: Graceful shutdown
echo [WARN] This will restart OpenWebUI, Ollama, and Tailscale containers
echo [INFO] Stopping Tailscale container...
docker compose stop tailscale
if %ERRORLEVEL% NEQ 0 (
    echo [WARN] Tailscale stop failed, attempting force kill...
    docker compose kill tailscale
)

echo [INFO] Stopping Ollama container...
docker compose stop ollama
if %ERRORLEVEL% NEQ 0 (
    echo [WARN] Ollama stop failed, attempting force kill...
    docker compose kill ollama
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
timeout /t 90 /nobreak >nul

echo [INFO] Starting Ollama with GPU support...
docker compose up -d ollama
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Failed to start Ollama container
    goto :nuclear_option
)

echo [INFO] Waiting for Ollama to initialize...
timeout /t 30 /nobreak >nul

echo [INFO] Starting Tailscale with shared network namespace...
docker compose up -d tailscale
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Failed to start Tailscale container
    goto :nuclear_option
)

echo [INFO] Waiting for Tailscale network connectivity...
timeout /t 60 /nobreak >nul

echo [INFO] Starting Watchtower monitoring service...
docker compose up -d watchtower

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

:minimal_recovery
echo [INFO] ==========================================
echo [INFO] MINIMAL RECOVERY - GENTLE RESTART
echo [INFO] ==========================================
echo [INFO] Attempting gentle restart without destroying containers...
docker compose restart tailscale ollama openwebui
docker compose up -d watchtower
echo [INFO] Waiting for services to stabilize...
timeout /t 60 /nobreak >nul

echo [INFO] Testing if minimal recovery worked...
docker compose exec tailscale ping -c 1 8.8.8.8 >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo [SUCCESS] Minimal recovery successful!
    goto :verify_services
) else (
    echo [WARN] Minimal recovery failed - proceeding with full recovery
    goto :full_recovery
)

:full_recovery
echo [INFO] ==========================================
echo [INFO] FULL RECOVERY - CONTAINER RESTART  
echo [INFO] ==========================================

:nuclear_option
echo [WARN] ========================================
echo [WARN] PERFORMING NUCLEAR RECOVERY
echo [WARN] ========================================

echo [INFO] Last chance diagnostic check...
docker compose exec tailscale ping -c 1 8.8.8.8 >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo [SUCCESS] Wait - connectivity actually working! Trying minimal recovery instead...
    goto :minimal_recovery
)

echo [WARN] All diagnostics failed - proceeding with nuclear option
echo [WARN] This will DESTROY and REBUILD containers - all customizations will be lost
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
echo [INFO] Ollama status:
docker compose exec ollama ollama list

echo.
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
