@echo off
REM Manual Tailscale Health Check and Fix
REM Run this if you notice Tailscale is disconnected

echo Checking Tailscale connectivity...

REM Test internet connectivity from Tailscale container
docker compose exec tailscale ping -c 1 8.8.8.8 >nul 2>&1

if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Tailscale has no internet connectivity
    echo [INFO] Restarting Tailscale container...
    
    docker compose down tailscale
    if %ERRORLEVEL% NEQ 0 (
        echo [ERROR] Failed to stop Tailscale container
        pause
        exit /b 1
    )
    
    docker compose up -d tailscale
    if %ERRORLEVEL% NEQ 0 (
        echo [ERROR] Failed to start Tailscale container
        pause
        exit /b 1
    )
    
    echo [INFO] Waiting for Tailscale to start...
    timeout /t 20 /nobreak >nul
    
    REM Test again
    docker compose exec tailscale ping -c 1 8.8.8.8 >nul 2>&1
    if %ERRORLEVEL% NEQ 0 (
        echo [ERROR] Tailscale still has connectivity issues
        echo [INFO] You may need to check Docker network settings
        pause
        exit /b 1
    ) else (
        echo [SUCCESS] Tailscale connectivity restored!
    )
) else (
    echo [SUCCESS] Tailscale connectivity is working
)

echo.
echo Checking Tailscale status...
docker compose exec tailscale tailscale --socket=/tmp/tailscaled.sock status

echo.
echo Checking serve configuration...
docker compose exec tailscale tailscale --socket=/tmp/tailscaled.sock serve status

echo.
echo Health check completed!
pause
