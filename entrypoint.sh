#!/bin/sh
set -e

# Enhanced entrypoint with autonomous recovery capabilities
echo "🚀 Starting Tailscale with autonomous management..."

# Function to check network connectivity
check_network() {
    ping -c 1 8.8.8.8 >/dev/null 2>&1
}

# Function to wait for network with retry logic
wait_for_network() {
    local max_attempts=30
    local attempt=1
    
    echo "⏳ Waiting for network connectivity..."
    while [ $attempt -le $max_attempts ]; do
        if check_network; then
            echo "✅ Network connectivity established"
            return 0
        fi
        echo "🔄 Network attempt $attempt/$max_attempts failed, retrying in 5s..."
        sleep 5
        attempt=$((attempt + 1))
    done
    
    echo "❌ Failed to establish network connectivity after $max_attempts attempts"
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
tailscale --socket=/tmp/tailscaled.sock serve \
  --https=443 \
  --bg \
  http://127.0.0.1:8080

echo "✅ Tailscale serve configured for HTTPS on port 443 -> 127.0.0.1:8080"

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
            if ! tailscale --socket=/tmp/tailscaled.sock serve status | grep -q "127.0.0.1:8080"; then
                echo "🔄 $(date): Reconfiguring serve..."
                tailscale --socket=/tmp/tailscaled.sock serve reset || true
                tailscale --socket=/tmp/tailscaled.sock serve \
                  --https=443 \
                  --bg \
                  http://127.0.0.1:8080 || true
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

# 9) Keep the container running
tail -f /dev/null
