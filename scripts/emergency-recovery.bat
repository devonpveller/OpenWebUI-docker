@echo off
REM Emergency Tailscale Network Recovery
REM Use this when normal restart methods fail due to network namespace issues

echo ========================================
echo EMERGENCY TAILSCALE NETWORK RECOVERY
echo ========================================
echo.

echo [INFO] Checking current container status...
docker compose ps

echo.
echo [INFO] Attempting emergency network namespace recovery...
echo [WARN] This will restart both OpenWebUI and Tailscale containers

REM Stop both containers to break the network namespace sharing
echo [INFO] Stopping Tailscale container...
docker compose stop tailscale
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Failed to stop Tailscale container
)

echo [INFO] Stopping OpenWebUI container...
docker compose stop openwebui
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Failed to stop OpenWebUI container
)

echo [INFO] Waiting for cleanup...
timeout /t 10 /nobreak >nul

echo [INFO] Starting OpenWebUI container first...
docker compose start openwebui
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Failed to start OpenWebUI container
    pause
    exit /b 1
)

echo [INFO] Waiting for OpenWebUI to be healthy...
timeout /t 20 /nobreak >nul

echo [INFO] Starting Tailscale container...
docker compose start tailscale
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Failed to start Tailscale container
    pause
    exit /b 1
)

echo [INFO] Waiting for Tailscale to establish network connectivity...
timeout /t 45 /nobreak >nul

echo.
echo [INFO] Testing connectivity...
docker compose exec tailscale ping -c 1 8.8.8.8 >nul 2>&1

if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Emergency recovery failed
    echo [INFO] Network namespace is severely broken
    echo [INFO] Consider full stack restart: docker compose down && docker compose up -d
    pause
    exit /b 1
) else (
    echo [SUCCESS] Emergency recovery successful!
)

echo.
echo [INFO] Verifying Tailscale status...
docker compose exec tailscale tailscale --socket=/tmp/tailscaled.sock status

echo.
echo [INFO] Checking serve configuration...
docker compose exec tailscale tailscale --socket=/tmp/tailscaled.sock serve status

echo.
echo ========================================
echo EMERGENCY RECOVERY COMPLETED
echo ========================================
pause
