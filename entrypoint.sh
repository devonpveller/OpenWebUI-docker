#!/bin/sh
set -euo pipefail  # Strict error handling

# Enhanced entrypoint with autonomous recovery capabilities
echo "🚀 Starting Tailscale with autonomous management..."

# Constants
readonly SOCKET_PATH="/tmp/tailscaled.sock"
readonly STATE_DIR="/var/lib/tailscale"
readonly MAX_NETWORK_ATTEMPTS=30
readonly SOCKET_TIMEOUT=15

# Function to log with timestamp
log_info() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') [INFO] $*"
}

log_warn() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') [WARN] $*" >&2
}

log_error() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') [ERROR] $*" >&2
}

# Function to check network connectivity with validation
check_network() {
    ping -c 1 -W 5 8.8.8.8 >/dev/null 2>&1
}

# Function to wait for network with retry logic
wait_for_network() {
    local attempt=1
    
    log_info "Waiting for network connectivity..."
    while [ $attempt -le $MAX_NETWORK_ATTEMPTS ]; do
        if check_network; then
            log_info "Network connectivity established"
            return 0
        fi
        log_warn "Network attempt $attempt/$MAX_NETWORK_ATTEMPTS failed, retrying in 5s..."
        sleep 5
        attempt=$((attempt + 1))
    done
    
    log_error "Failed to establish network connectivity after $MAX_NETWORK_ATTEMPTS attempts"
    return 1
}

# 1) Clean up any old socket
rm -f /tmp/tailscaled.sock

# 2) Wait for network to be available (handles container restart scenarios)
if ! wait_for_network; then
    echo "💀 Cannot proceed without network connectivity"
    exit 1
fi

# 3) Start tailscaled directly with persistent state
/usr/local/bin/tailscaled \
  --socket=/tmp/tailscaled.sock \
  --statedir=/var/lib/tailscale \
  --tun=userspace-networking &

# 4) Wait up to 15s for the socket with better error handling
timeout=15
echo "⏳ Waiting for tailscaled socket..."
while [ ! -S /tmp/tailscaled.sock ] && [ $timeout -gt 0 ]; do
  sleep 1
  timeout=$((timeout - 1))
done

if [ ! -S /tmp/tailscaled.sock ]; then
    echo "❌ Error: /tmp/tailscaled.sock not found after 15 seconds"
    echo "📋 Debug info:"
    ps aux | grep tailscaled || true
    ls -la /tmp/ || true
    exit 1
fi

echo "✅ Tailscaled socket ready"

# 5) Join your tailnet (reusing existing device identity)
echo "🔗 Connecting to Tailscale network..."
tailscale --socket=/tmp/tailscaled.sock up \
  --auth-key="${TAILSCALE_AUTH_KEY}" \
  --hostname="${TS_HOSTNAME:-openwebui}" \
  --accept-dns="${TS_ACCEPT_DNS:-false}"

# 6) Wait for connection to be established
echo "⏳ Waiting for Tailscale connection..."
connection_timeout=30
while [ $connection_timeout -gt 0 ]; do
    if tailscale --socket=/tmp/tailscaled.sock status >/dev/null 2>&1; then
        echo "✅ Tailscale connected successfully"
        break
    fi
    sleep 2
    connection_timeout=$((connection_timeout - 2))
done

if [ $connection_timeout -le 0 ]; then
    echo "⚠️ Warning: Tailscale connection timeout, but continuing..."
fi

# 7) Configure serve for HTTPS access
echo "🌐 Configuring Tailscale serve..."
# Clear any existing serve config and set up fresh
tailscale --socket=/tmp/tailscaled.sock serve reset

# Configure OpenWebUI at root path
tailscale --socket=/tmp/tailscaled.sock serve \
  --https=443 \
  --bg \
  http://127.0.0.1:8080

# Wait a moment and then configure Ollama API at /ollama path
sleep 2
echo "🤖 Configuring Ollama API access..."
if wget -q -T 5 -O /dev/null http://127.0.0.1:11434/api/version; then
    tailscale --socket=/tmp/tailscaled.sock serve \
      --https=443 \
      --set-path=/ollama \
      --bg \
      http://127.0.0.1:11434
    echo "✅ Ollama API configured at /ollama"
else
    echo "⚠️ Ollama not responding, skipping serve configuration"
fi

echo "✅ Tailscale serve configured:"
echo "  - OpenWebUI: HTTPS port 443 -> 127.0.0.1:8080"
echo "  - Ollama API: HTTPS port 443/ollama -> 127.0.0.1:11434"

# 8) Background monitoring loop for autonomous recovery
(
    echo "🔍 Starting autonomous monitoring..."
    while true; do
        sleep 60  # Check every minute
        
        # Check if we can reach the internet
        if ! check_network; then
            echo "⚠️ $(date): Network connectivity lost, container may need restart"
            continue
        fi
        
        # Check if Tailscale is still connected
        if ! tailscale --socket=/tmp/tailscaled.sock status >/dev/null 2>&1; then
            echo "⚠️ $(date): Tailscale disconnected, attempting reconnection..."
            
            # Try to reconnect
            tailscale --socket=/tmp/tailscaled.sock up \
              --auth-key="${TAILSCALE_AUTH_KEY}" \
              --hostname="${TS_HOSTNAME:-openwebui}" \
              --accept-dns="${TS_ACCEPT_DNS:-false}" || true
            
            sleep 10
            
            # Reconfigure serve if needed
            serve_status=$(tailscale --socket=/tmp/tailscaled.sock serve status)
            if ! echo "$serve_status" | grep -q "127.0.0.1:8080"; then
                echo "🔄 $(date): Reconfiguring serve..."
                tailscale --socket=/tmp/tailscaled.sock serve reset || true
                
                # Reconfigure OpenWebUI
                tailscale --socket=/tmp/tailscaled.sock serve \
                  --https=443 \
                  --bg \
                  http://127.0.0.1:8080 || true
                
                # Reconfigure Ollama API if available
                if wget -q -T 5 -O /dev/null http://127.0.0.1:11434/api/version; then
                    tailscale --socket=/tmp/tailscaled.sock serve \
                      --https=443 \
                      --set-path=/ollama \
                      --bg \
                      http://127.0.0.1:11434 || true
                fi
            elif ! echo "$serve_status" | grep -q "127.0.0.1:11434"; then
                # Ollama serve is missing, try to add it
                echo "🔄 $(date): Adding missing Ollama serve configuration..."
                if wget -q -T 5 -O /dev/null http://127.0.0.1:11434/api/version; then
                    tailscale --socket=/tmp/tailscaled.sock serve \
                      --https=443 \
                      --set-path=/ollama \
                      --bg \
                      http://127.0.0.1:11434 || true
                fi
            fi
        fi
    done
) &

echo "🎉 Tailscale autonomous setup complete!"
echo "📊 Status:"
tailscale --socket=/tmp/tailscaled.sock status || echo "⚠️ Status check failed"
echo ""
echo "🌐 Serve configuration:"
tailscale --socket=/tmp/tailscaled.sock serve status || echo "⚠️ Serve status check failed"
echo ""
echo "🔗 Access URLs (via Tailnet):"
echo "  - OpenWebUI: https://$(tailscale --socket=/tmp/tailscaled.sock status --json | grep '\"Name\"' | cut -d'"' -f4 2>/dev/null || echo 'your-hostname').tail[...].ts.net/"
echo "  - Ollama API: https://$(tailscale --socket=/tmp/tailscaled.sock status --json | grep '\"Name\"' | cut -d'"' -f4 2>/dev/null || echo 'your-hostname').tail[...].ts.net/ollama"

# 9) Keep the container running
tail -f /dev/null
