# LM Studio Tailscale Recovery PowerShell Script
# For use from Windows host to fix LM Studio connectivity

[CmdletBinding()]
param(
    [switch]$Force,
    [string]$LMStudioHost = "169.254.83.107",
    [string]$LMStudioPort = "5506"
)

Write-Host "LM Studio Tailscale Recovery" -ForegroundColor Cyan
Write-Host "=================================" -ForegroundColor Cyan

# Test if containers are running
Write-Host "Checking container status..." -ForegroundColor Yellow
$containers = docker compose ps --format json | ConvertFrom-Json
$tailscaleRunning = $containers | Where-Object { $_.Service -eq "tailscale" -and $_.State -eq "running" }

if (-not $tailscaleRunning) {
    Write-Host "❌ Tailscale container not running - starting it..." -ForegroundColor Red
    docker compose up -d tailscale
    Start-Sleep 15
}

# Test LM Studio connectivity from host
Write-Host "Testing LM Studio connectivity from host..." -ForegroundColor Yellow
try {
    $response = Invoke-RestMethod -Uri "http://${LMStudioHost}:${LMStudioPort}/v1/models" -TimeoutSec 5
    Write-Host "✅ LM Studio is accessible from host" -ForegroundColor Green
} catch {
    Write-Host "❌ LM Studio not accessible from host at ${LMStudioHost}:${LMStudioPort}" -ForegroundColor Red
    Write-Host "   Make sure LM Studio is running with server enabled" -ForegroundColor Yellow
    exit 1
}

# Fix the proxy and Tailscale configuration
Write-Host "Fixing LM Studio proxy and Tailscale serve..." -ForegroundColor Yellow

$fixScript = @"
#!/bin/bash
set -e

LMSTUDIO_HOST=${LMStudioHost}
LMSTUDIO_PORT=${LMStudioPort}
LMSTUDIO_LOCAL_PORT=8234

echo "Cleaning up existing processes..."
pkill -f "socat.*:\${LMSTUDIO_LOCAL_PORT}" 2>/dev/null || true

echo "Starting socat proxy with improved stability..."
nohup socat TCP-LISTEN:\${LMSTUDIO_LOCAL_PORT},fork,reuseaddr,keepalive TCP:\${LMSTUDIO_HOST}:\${LMSTUDIO_PORT} > /tmp/socat-lmstudio.log 2>&1 &

# Wait and test
sleep 3
if wget -q -T 5 -O /dev/null http://127.0.0.1:\${LMSTUDIO_LOCAL_PORT}/v1/models; then
    echo "Proxy working"
else
    echo "Proxy failed - check /tmp/socat-lmstudio.log"
    cat /tmp/socat-lmstudio.log || true
    exit 1
fi

echo "Configuring Tailscale serve..."
tailscale --socket=/tmp/tailscaled.sock serve --https=443 --set-path=/lmstudio --bg http://127.0.0.1:\${LMSTUDIO_LOCAL_PORT}

echo "Configuration complete"
tailscale --socket=/tmp/tailscaled.sock serve status
"@

# Write script to temporary file and execute
$fixScript | Out-File -FilePath "fix-lmstudio.sh" -Encoding ascii
docker cp "fix-lmstudio.sh" "tailscale:/tmp/fix-lmstudio.sh"
Remove-Item "fix-lmstudio.sh"

# Execute the fix
$result = docker compose exec tailscale sh /tmp/fix-lmstudio.sh

Write-Host ""
Write-Host "LM Studio Recovery Complete!" -ForegroundColor Green
Write-Host ""
Write-Host "Access URLs:" -ForegroundColor Cyan
Write-Host "  - LM Studio API: https://openwebui-13.tail37f875.ts.net/lmstudio" -ForegroundColor White
Write-Host "  - Test endpoint: https://openwebui-13.tail37f875.ts.net/lmstudio/v1/models" -ForegroundColor White
Write-Host ""
Write-Host "To test from command line:" -ForegroundColor Yellow
Write-Host "curl -k https://openwebui-13.tail37f875.ts.net/lmstudio/v1/models" -ForegroundColor Gray