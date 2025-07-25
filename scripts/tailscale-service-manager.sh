#!/bin/bash
# Autonomous Tailscale Service Manager
# This script runs as a daemon to ensure Tailscale stays connected

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
LOG_FILE="$PROJECT_DIR/logs/tailscale-manager.log"
PID_FILE="$PROJECT_DIR/logs/tailscale-manager.pid"

# Ensure logs directory exists
mkdir -p "$PROJECT_DIR/logs"

# Logging function
log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $1" | tee -a "$LOG_FILE"
}

# Check if already running
if [ -f "$PID_FILE" ]; then
    if kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        log "Service manager already running with PID $(cat "$PID_FILE")"
        exit 1
    else
        rm -f "$PID_FILE"
    fi
fi

# Write PID
echo $$ > "$PID_FILE"

log "Starting Tailscale Service Manager"

# Cleanup on exit
cleanup() {
    log "Shutting down Tailscale Service Manager"
    rm -f "$PID_FILE"
    exit 0
}

trap cleanup SIGTERM SIGINT

cd "$PROJECT_DIR"

# Main monitoring loop
while true; do
    # Check if OpenWebUI container exists and is healthy
    if ! docker compose ps openwebui | grep -q "healthy"; then
        log "OpenWebUI not healthy, waiting..."
        sleep 30
        continue
    fi
    
    # Check if Tailscale container exists
    if ! docker compose ps tailscale | grep -q "Up"; then
        log "Tailscale container not running, starting..."
        docker compose up -d tailscale
        sleep 30
        continue
    fi
    
    # Test network connectivity from Tailscale container
    if ! docker compose exec -T tailscale ping -c 1 8.8.8.8 >/dev/null 2>&1; then
        log "Tailscale has no network connectivity, restarting..."
        docker compose stop tailscale
        sleep 5
        docker compose start tailscale
        sleep 30
        continue
    fi
    
    # Test Tailscale connection
    if ! docker compose exec -T tailscale tailscale --socket=/tmp/tailscaled.sock status >/dev/null 2>&1; then
        log "Tailscale not connected, attempting recovery..."
        docker compose restart tailscale
        sleep 30
        continue
    fi
    
    # Test serve configuration
    if ! docker compose exec -T tailscale tailscale --socket=/tmp/tailscaled.sock serve status | grep -q "127.0.0.1:8080"; then
        log "Tailscale serve not configured, fixing..."
        docker compose exec -T tailscale tailscale --socket=/tmp/tailscaled.sock serve reset
        docker compose exec -T tailscale tailscale --socket=/tmp/tailscaled.sock serve --https=443 --bg http://127.0.0.1:8080
        sleep 10
        continue
    fi
    
    # All checks passed
    log "All systems operational"
    sleep 60  # Check every minute
done
