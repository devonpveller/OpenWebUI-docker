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

# Configure LM Studio API at /lmstudio path
sleep 2
echo "🧠 Configuring LM Studio API access..."

# Use environment variables for LM Studio configuration
LMSTUDIO_HOST=${LMSTUDIO_HOST:-host.docker.internal}
LMSTUDIO_PORT=${LMSTUDIO_PORT:-1234}
LMSTUDIO_ENABLED=${LMSTUDIO_ENABLED:-true}
LMSTUDIO_LOCAL_PORT=8234  # Local port for proxy

if [ "$LMSTUDIO_ENABLED" = "true" ]; then
    if wget -q -T 5 -O /dev/null http://${LMSTUDIO_HOST}:${LMSTUDIO_PORT}/v1/models; then
        # If LM Studio is not on localhost, create a local proxy using socat
        if [ "$LMSTUDIO_HOST" != "127.0.0.1" ] && [ "$LMSTUDIO_HOST" != "localhost" ] && [ "$LMSTUDIO_HOST" != "host.docker.internal" ]; then
            echo "🔄 Creating local proxy for LM Studio at ${LMSTUDIO_HOST}:${LMSTUDIO_PORT}"
            
            # Kill any existing socat process on this port
            pkill -f "socat.*:${LMSTUDIO_LOCAL_PORT}" || true
            sleep 2
            
            # Check if port is available
            if netstat -ln | grep -q ":${LMSTUDIO_LOCAL_PORT} "; then
                echo "⚠️  Port ${LMSTUDIO_LOCAL_PORT} is still in use, waiting..."
                sleep 3
            fi
            
            # Start socat proxy with detailed logging
            echo "🚀 Starting socat proxy: 127.0.0.1:${LMSTUDIO_LOCAL_PORT} -> ${LMSTUDIO_HOST}:${LMSTUDIO_PORT}"
            socat -d -d TCP-LISTEN:${LMSTUDIO_LOCAL_PORT},fork,reuseaddr,keepalive TCP:${LMSTUDIO_HOST}:${LMSTUDIO_PORT} > /tmp/socat-lmstudio.log 2>&1 &
            SOCAT_PID=$!
            echo $SOCAT_PID > /tmp/socat-lmstudio.pid
            
            # Wait and verify socat started properly
            sleep 3
            if kill -0 $SOCAT_PID 2>/dev/null; then
                echo "✅ LM Studio proxy started successfully (PID: $SOCAT_PID)"
            else
                echo "❌ ERROR: socat failed to start or crashed immediately"
                echo "📋 socat log contents:"
                cat /tmp/socat-lmstudio.log 2>/dev/null || echo "No log file found"
                exit 1
            fi
            
            # Test the proxy
            echo "🧪 Testing proxy connection..."
            if wget -q -T 10 -O /dev/null http://127.0.0.1:${LMSTUDIO_LOCAL_PORT}/v1/models; then
                echo "✅ LM Studio proxy test successful"
            else
                echo "⚠️  WARNING: LM Studio proxy test failed"
                echo "📋 Checking socat status..."
                if kill -0 $SOCAT_PID 2>/dev/null; then
                    echo "🟡 socat process is still running - may be a temporary connectivity issue"
                else
                    echo "🔴 socat process has died - check logs:"
                    cat /tmp/socat-lmstudio.log 2>/dev/null || echo "No log file found"
                fi
            fi
            # Configure Tailscale serve to use the local proxy
            tailscale --socket=/tmp/tailscaled.sock serve \
              --https=443 \
              --set-path=/lmstudio \
              --bg \
              http://127.0.0.1:${LMSTUDIO_LOCAL_PORT}
            echo "✅ LM Studio API configured at /lmstudio (via proxy: ${LMSTUDIO_HOST}:${LMSTUDIO_PORT} -> 127.0.0.1:${LMSTUDIO_LOCAL_PORT})"
        else
            # Direct configuration for localhost
            tailscale --socket=/tmp/tailscaled.sock serve \
              --https=443 \
              --set-path=/lmstudio \
              --bg \
              http://${LMSTUDIO_HOST}:${LMSTUDIO_PORT}
            echo "✅ LM Studio API configured at /lmstudio (${LMSTUDIO_HOST}:${LMSTUDIO_PORT})"
        fi
    else
        echo "⚠️ LM Studio not responding on ${LMSTUDIO_HOST}:${LMSTUDIO_PORT}, skipping serve configuration"
        echo "   Make sure LM Studio is running on your host machine with server enabled"
    fi
else
    echo "🔄 LM Studio integration disabled (LMSTUDIO_ENABLED=false)"
fi

# Configure llama-cpp API at /llama-cpp path
sleep 2
echo "🦙 Configuring llama-cpp API access..."

LLAMA_CPP_HOST=${LLAMA_CPP_HOST:-llama-cpp}
LLAMA_CPP_PORT=${LLAMA_CPP_PORT:-8080}
LLAMA_CPP_ENABLED=${LLAMA_CPP_ENABLED:-true}
LLAMA_CPP_LOCAL_PORT=8235  # Local port for socat proxy

# Helper function to set up the llama-cpp socat proxy and tailscale serve
setup_llama_cpp_serve() {
    echo "🔄 Creating local proxy for llama-cpp at ${LLAMA_CPP_HOST}:${LLAMA_CPP_PORT}"

    # Kill any existing socat process on this port
    pkill -f "socat.*:${LLAMA_CPP_LOCAL_PORT}" || true
    sleep 2

    # Start socat proxy
    echo "🚀 Starting socat proxy: 127.0.0.1:${LLAMA_CPP_LOCAL_PORT} -> ${LLAMA_CPP_HOST}:${LLAMA_CPP_PORT}"
    socat -d -d TCP-LISTEN:${LLAMA_CPP_LOCAL_PORT},fork,reuseaddr,keepalive TCP:${LLAMA_CPP_HOST}:${LLAMA_CPP_PORT} > /tmp/socat-llama-cpp.log 2>&1 &
    LLAMA_CPP_SOCAT_PID=$!
    echo $LLAMA_CPP_SOCAT_PID > /tmp/socat-llama-cpp.pid

    # Wait and verify socat started
    sleep 3
    if ! kill -0 $LLAMA_CPP_SOCAT_PID 2>/dev/null; then
        echo "❌ ERROR: llama-cpp socat failed to start"
        cat /tmp/socat-llama-cpp.log 2>/dev/null || echo "No log file found"
        return 1
    fi
    echo "✅ llama-cpp proxy started successfully (PID: $LLAMA_CPP_SOCAT_PID)"

    # Configure Tailscale serve
    tailscale --socket=/tmp/tailscaled.sock serve \
      --https=443 \
      --set-path=/llama-cpp \
      --bg \
      http://127.0.0.1:${LLAMA_CPP_LOCAL_PORT}
    echo "✅ llama-cpp API configured at /llama-cpp (via proxy: ${LLAMA_CPP_HOST}:${LLAMA_CPP_PORT} -> 127.0.0.1:${LLAMA_CPP_LOCAL_PORT})"

    # Mark as configured so the monitoring loop knows
    touch /tmp/llama-cpp-serve-configured
    return 0
}

if [ "$LLAMA_CPP_ENABLED" = "true" ]; then
    # Retry up to 3 minutes (18 x 10s) for llama-cpp to become healthy
    # llama-cpp has a 120s start_period for model loading, so we wait patiently
    LLAMA_CPP_ATTEMPTS=0
    LLAMA_CPP_MAX_ATTEMPTS=18
    LLAMA_CPP_CONFIGURED=false

    while [ $LLAMA_CPP_ATTEMPTS -lt $LLAMA_CPP_MAX_ATTEMPTS ]; do
        if wget -q -T 10 -O /dev/null http://${LLAMA_CPP_HOST}:${LLAMA_CPP_PORT}/health; then
            if setup_llama_cpp_serve; then
                LLAMA_CPP_CONFIGURED=true
            fi
            break
        fi
        LLAMA_CPP_ATTEMPTS=$((LLAMA_CPP_ATTEMPTS + 1))
        echo "⏳ llama-cpp not ready yet (attempt ${LLAMA_CPP_ATTEMPTS}/${LLAMA_CPP_MAX_ATTEMPTS}), waiting 10s..."
        sleep 10
    done

    if [ "$LLAMA_CPP_CONFIGURED" != "true" ]; then
        echo "⚠️ llama-cpp not available after ${LLAMA_CPP_MAX_ATTEMPTS} attempts — monitoring loop will configure it when it comes online"
    fi
else
    echo "🔄 llama-cpp Tailscale integration disabled (LLAMA_CPP_ENABLED=false)"
fi

echo "✅ Tailscale serve configured:"
echo "  - OpenWebUI: HTTPS port 443 -> 127.0.0.1:8080"
echo "  - Ollama API: HTTPS port 443/ollama -> 127.0.0.1:11434"
if [ "$LMSTUDIO_ENABLED" = "true" ]; then
    echo "  - LM Studio API: HTTPS port 443/lmstudio -> ${LMSTUDIO_HOST}:${LMSTUDIO_PORT}"
fi
if [ "$LLAMA_CPP_ENABLED" = "true" ]; then
    echo "  - llama-cpp API: HTTPS port 443/llama-cpp -> ${LLAMA_CPP_HOST}:${LLAMA_CPP_PORT}"
fi

# 8) Background monitoring loop for autonomous recovery
(
    echo "🔍 Starting autonomous monitoring..."
    while true; do
        sleep 60  # Check every minute
        
        # Check socat health if LM Studio is enabled
        if [ "$LMSTUDIO_ENABLED" = "true" ] && [ "$LMSTUDIO_HOST" != "127.0.0.1" ] && [ "$LMSTUDIO_HOST" != "localhost" ] && [ "$LMSTUDIO_HOST" != "host.docker.internal" ]; then
            if [ -f /tmp/socat-lmstudio.pid ]; then
                SOCAT_PID=$(cat /tmp/socat-lmstudio.pid)
                if ! kill -0 $SOCAT_PID 2>/dev/null; then
                    echo "⚠️ $(date): LM Studio socat proxy (PID: $SOCAT_PID) has died, restarting..."
                    
                    # Restart socat proxy
                    LMSTUDIO_LOCAL_PORT=8234
                    pkill -f "socat.*:${LMSTUDIO_LOCAL_PORT}" || true
                    sleep 2
                    
                    socat -d -d TCP-LISTEN:${LMSTUDIO_LOCAL_PORT},fork,reuseaddr,keepalive TCP:${LMSTUDIO_HOST}:${LMSTUDIO_PORT} > /tmp/socat-lmstudio.log 2>&1 &
                    NEW_SOCAT_PID=$!
                    echo $NEW_SOCAT_PID > /tmp/socat-lmstudio.pid
                    sleep 3
                    
                    if kill -0 $NEW_SOCAT_PID 2>/dev/null; then
                        echo "✅ $(date): LM Studio proxy restarted successfully (PID: $NEW_SOCAT_PID)"
                    else
                        echo "❌ $(date): Failed to restart LM Studio proxy"
                        cat /tmp/socat-lmstudio.log 2>/dev/null || echo "No log available"
                    fi
                fi
            fi
        fi

        # Check llama-cpp: deferred setup + socat health
        if [ "$LLAMA_CPP_ENABLED" = "true" ]; then
            if [ ! -f /tmp/llama-cpp-serve-configured ]; then
                # Serve was never configured — try deferred setup
                if wget -q -T 10 -O /dev/null http://${LLAMA_CPP_HOST}:${LLAMA_CPP_PORT}/health; then
                    echo "🦙 $(date): llama-cpp is now online, performing deferred setup..."
                    setup_llama_cpp_serve || echo "❌ $(date): Deferred llama-cpp setup failed, will retry next cycle"
                fi
            elif [ -f /tmp/socat-llama-cpp.pid ]; then
                # Serve is configured — keep the socat proxy alive
                LLAMA_CPP_PID=$(cat /tmp/socat-llama-cpp.pid)
                if ! kill -0 $LLAMA_CPP_PID 2>/dev/null; then
                    echo "⚠️ $(date): llama-cpp socat proxy (PID: $LLAMA_CPP_PID) has died, restarting..."

                    LLAMA_CPP_LOCAL_PORT=8235
                    pkill -f "socat.*:${LLAMA_CPP_LOCAL_PORT}" || true
                    sleep 2

                    socat -d -d TCP-LISTEN:${LLAMA_CPP_LOCAL_PORT},fork,reuseaddr,keepalive TCP:${LLAMA_CPP_HOST}:${LLAMA_CPP_PORT} > /tmp/socat-llama-cpp.log 2>&1 &
                    NEW_LLAMA_PID=$!
                    echo $NEW_LLAMA_PID > /tmp/socat-llama-cpp.pid
                    sleep 3

                    if kill -0 $NEW_LLAMA_PID 2>/dev/null; then
                        echo "✅ $(date): llama-cpp proxy restarted successfully (PID: $NEW_LLAMA_PID)"
                    else
                        echo "❌ $(date): Failed to restart llama-cpp proxy"
                        cat /tmp/socat-llama-cpp.log 2>/dev/null || echo "No log available"
                    fi
                fi
            fi
        fi
        
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
                
                # Reconfigure LM Studio API if available and enabled
                if [ "$LMSTUDIO_ENABLED" = "true" ] && wget -q -T 5 -O /dev/null http://${LMSTUDIO_HOST}:${LMSTUDIO_PORT}/v1/models; then
                    if [ "$LMSTUDIO_HOST" != "127.0.0.1" ] && [ "$LMSTUDIO_HOST" != "localhost" ] && [ "$LMSTUDIO_HOST" != "host.docker.internal" ]; then
                        # Recreate proxy if needed
                        LMSTUDIO_LOCAL_PORT=8234
                        echo "🔄 Recreating LM Studio proxy during monitoring..."
                        pkill -f "socat.*:${LMSTUDIO_LOCAL_PORT}" || true
                        sleep 2
                        
                        socat -d -d TCP-LISTEN:${LMSTUDIO_LOCAL_PORT},fork,reuseaddr,keepalive TCP:${LMSTUDIO_HOST}:${LMSTUDIO_PORT} > /tmp/socat-lmstudio.log 2>&1 &
                        SOCAT_PID=$!
                        echo $SOCAT_PID > /tmp/socat-lmstudio.pid
                        sleep 3
                        
                        if kill -0 $SOCAT_PID 2>/dev/null; then
                            echo "✅ LM Studio proxy recreated successfully (PID: $SOCAT_PID)"
                        else
                            echo "❌ Failed to recreate LM Studio proxy"
                            cat /tmp/socat-lmstudio.log 2>/dev/null || echo "No log available"
                        fi
                        sleep 2
                        tailscale --socket=/tmp/tailscaled.sock serve \
                          --https=443 \
                          --set-path=/lmstudio \
                          --bg \
                          http://127.0.0.1:${LMSTUDIO_LOCAL_PORT} || true
                    else
                        tailscale --socket=/tmp/tailscaled.sock serve \
                          --https=443 \
                          --set-path=/lmstudio \
                          --bg \
                          http://${LMSTUDIO_HOST}:${LMSTUDIO_PORT} || true
                    fi
                fi

                # Reconfigure llama-cpp API if available and enabled
                if [ "$LLAMA_CPP_ENABLED" = "true" ] && wget -q -T 10 -O /dev/null http://${LLAMA_CPP_HOST}:${LLAMA_CPP_PORT}/health; then
                    echo "🔄 Reconfiguring llama-cpp serve after reconnection..."
                    rm -f /tmp/llama-cpp-serve-configured
                    setup_llama_cpp_serve || echo "❌ Failed to reconfigure llama-cpp serve"
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
            elif ! echo "$serve_status" | grep -q "127.0.0.1:8234\|${LMSTUDIO_HOST}:${LMSTUDIO_PORT}"; then
                # LM Studio serve is missing, try to add it
                if [ "$LMSTUDIO_ENABLED" = "true" ]; then
                    echo "🔄 $(date): Adding missing LM Studio serve configuration..."
                    if wget -q -T 5 -O /dev/null http://${LMSTUDIO_HOST}:${LMSTUDIO_PORT}/v1/models; then
                        if [ "$LMSTUDIO_HOST" != "127.0.0.1" ] && [ "$LMSTUDIO_HOST" != "localhost" ] && [ "$LMSTUDIO_HOST" != "host.docker.internal" ]; then
                            LMSTUDIO_LOCAL_PORT=8234
                            echo "🔄 Recreating LM Studio proxy for missing serve config..."
                            pkill -f "socat.*:${LMSTUDIO_LOCAL_PORT}" || true
                            sleep 2
                            
                            socat -d -d TCP-LISTEN:${LMSTUDIO_LOCAL_PORT},fork,reuseaddr,keepalive TCP:${LMSTUDIO_HOST}:${LMSTUDIO_PORT} > /tmp/socat-lmstudio.log 2>&1 &
                            SOCAT_PID=$!
                            echo $SOCAT_PID > /tmp/socat-lmstudio.pid
                            sleep 3
                            
                            if kill -0 $SOCAT_PID 2>/dev/null; then
                                echo "✅ LM Studio proxy recreated for serve config (PID: $SOCAT_PID)"
                            else
                                echo "❌ Failed to recreate LM Studio proxy for serve config"
                                cat /tmp/socat-lmstudio.log 2>/dev/null || echo "No log available"
                            fi
                            tailscale --socket=/tmp/tailscaled.sock serve \
                              --https=443 \
                              --set-path=/lmstudio \
                              --bg \
                              http://127.0.0.1:${LMSTUDIO_LOCAL_PORT} || true
                        else
                            tailscale --socket=/tmp/tailscaled.sock serve \
                              --https=443 \
                              --set-path=/lmstudio \
                              --bg \
                              http://${LMSTUDIO_HOST}:${LMSTUDIO_PORT} || true
                        fi
                    fi
                fi
            elif [ "$LLAMA_CPP_ENABLED" = "true" ] && ! echo "$serve_status" | grep -q "127.0.0.1:${LLAMA_CPP_LOCAL_PORT:-8235}"; then
                # llama-cpp serve is missing, try to add it
                echo "🔄 $(date): Adding missing llama-cpp serve configuration..."
                if wget -q -T 10 -O /dev/null http://${LLAMA_CPP_HOST}:${LLAMA_CPP_PORT}/health; then
                    rm -f /tmp/llama-cpp-serve-configured
                    setup_llama_cpp_serve || echo "❌ Failed to add llama-cpp serve"
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
if [ "$LMSTUDIO_ENABLED" = "true" ]; then
    echo "  - LM Studio API: https://$(tailscale --socket=/tmp/tailscaled.sock status --json | grep '\"Name\"' | cut -d'"' -f4 2>/dev/null || echo 'your-hostname').tail[...].ts.net/lmstudio"
fi
if [ "$LLAMA_CPP_ENABLED" = "true" ]; then
    echo "  - llama-cpp API: https://$(tailscale --socket=/tmp/tailscaled.sock status --json | grep '\"Name\"' | cut -d'"' -f4 2>/dev/null || echo 'your-hostname').tail[...].ts.net/llama-cpp/v1"
fi

# 9) Keep the container running
tail -f /dev/null
