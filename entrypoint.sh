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

# Persist tailnet info to a shared file so other containers (the openwebui
# admin pipe) can read the FQDN/MagicDNS suffix and render full tailnet URLs
# without needing the tailscale CLI themselves. /var/lib/tailscale is mapped
# to ./data/tailscale on the host, which openwebui mounts read-only.
write_tailnet_info() {
    if tailscale --socket=/tmp/tailscaled.sock status --json \
        > /var/lib/tailscale/tailnet-info.json.tmp 2>/dev/null \
    && [ -s /var/lib/tailscale/tailnet-info.json.tmp ]; then
        mv /var/lib/tailscale/tailnet-info.json.tmp \
           /var/lib/tailscale/tailnet-info.json
        return 0
    fi
    rm -f /var/lib/tailscale/tailnet-info.json.tmp 2>/dev/null
    return 1
}
write_tailnet_info && echo "📝 Wrote /var/lib/tailscale/tailnet-info.json"

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

# Configure llama-cpp-embed API at /llama-cpp-embed path
sleep 2
echo "🦙 Configuring llama-cpp-embed API access..."

LLAMA_CPP_EMBED_HOST=${LLAMA_CPP_EMBED_HOST:-llama-cpp-embed}
LLAMA_CPP_EMBED_PORT=${LLAMA_CPP_EMBED_PORT:-8080}
LLAMA_CPP_EMBED_ENABLED=${LLAMA_CPP_EMBED_ENABLED:-true}
LLAMA_CPP_EMBED_LOCAL_PORT=8236  # Local port for socat proxy

# Helper function to set up the llama-cpp-embed socat proxy and tailscale serve
setup_llama_cpp_embed_serve() {
    echo "🔄 Creating local proxy for llama-cpp-embed at ${LLAMA_CPP_EMBED_HOST}:${LLAMA_CPP_EMBED_PORT}"

    # Kill any existing socat process on this port
    pkill -f "socat.*:${LLAMA_CPP_EMBED_LOCAL_PORT}" || true
    sleep 2

    # Start socat proxy
    echo "🚀 Starting socat proxy: 127.0.0.1:${LLAMA_CPP_EMBED_LOCAL_PORT} -> ${LLAMA_CPP_EMBED_HOST}:${LLAMA_CPP_EMBED_PORT}"
    socat -d -d TCP-LISTEN:${LLAMA_CPP_EMBED_LOCAL_PORT},fork,reuseaddr,keepalive TCP:${LLAMA_CPP_EMBED_HOST}:${LLAMA_CPP_EMBED_PORT} > /tmp/socat-llama-cpp-embed.log 2>&1 &
    LLAMA_CPP_EMBED_SOCAT_PID=$!
    echo $LLAMA_CPP_EMBED_SOCAT_PID > /tmp/socat-llama-cpp-embed.pid

    # Wait and verify socat started
    sleep 3
    if ! kill -0 $LLAMA_CPP_EMBED_SOCAT_PID 2>/dev/null; then
        echo "❌ ERROR: llama-cpp-embed socat failed to start"
        cat /tmp/socat-llama-cpp-embed.log 2>/dev/null || echo "No log file found"
        return 1
    fi
    echo "✅ llama-cpp-embed proxy started successfully (PID: $LLAMA_CPP_EMBED_SOCAT_PID)"

    # Configure Tailscale serve
    tailscale --socket=/tmp/tailscaled.sock serve \
      --https=443 \
      --set-path=/llama-cpp-embed \
      --bg \
      http://127.0.0.1:${LLAMA_CPP_EMBED_LOCAL_PORT}
    echo "✅ llama-cpp-embed API configured at /llama-cpp-embed (via proxy: ${LLAMA_CPP_EMBED_HOST}:${LLAMA_CPP_EMBED_PORT} -> 127.0.0.1:${LLAMA_CPP_EMBED_LOCAL_PORT})"

    # Mark as configured so the monitoring loop knows
    touch /tmp/llama-cpp-embed-serve-configured
    return 0
}

if [ "$LLAMA_CPP_EMBED_ENABLED" = "true" ]; then
    LLAMA_CPP_EMBED_ATTEMPTS=0
    LLAMA_CPP_EMBED_MAX_ATTEMPTS=12
    LLAMA_CPP_EMBED_CONFIGURED=false

    while [ $LLAMA_CPP_EMBED_ATTEMPTS -lt $LLAMA_CPP_EMBED_MAX_ATTEMPTS ]; do
        if wget -q -T 10 -O /dev/null http://${LLAMA_CPP_EMBED_HOST}:${LLAMA_CPP_EMBED_PORT}/health; then
            if setup_llama_cpp_embed_serve; then
                LLAMA_CPP_EMBED_CONFIGURED=true
            fi
            break
        fi
        LLAMA_CPP_EMBED_ATTEMPTS=$((LLAMA_CPP_EMBED_ATTEMPTS + 1))
        echo "⏳ llama-cpp-embed not ready yet (attempt ${LLAMA_CPP_EMBED_ATTEMPTS}/${LLAMA_CPP_EMBED_MAX_ATTEMPTS}), waiting 10s..."
        sleep 10
    done

    if [ "$LLAMA_CPP_EMBED_CONFIGURED" != "true" ]; then
        echo "⚠️ llama-cpp-embed not available after ${LLAMA_CPP_EMBED_MAX_ATTEMPTS} attempts — monitoring loop will configure it when it comes online"
    fi
else
    echo "🔄 llama-cpp-embed Tailscale integration disabled (LLAMA_CPP_EMBED_ENABLED=false)"
fi

# Configure open-notebook UI on a separate Tailscale HTTPS port (Streamlit
# UIs do not host cleanly under a sub-path, so expose at root on a distinct
# tailnet HTTPS port instead of using --set-path).
sleep 2
echo "📓 Configuring open-notebook UI access..."

OPEN_NOTEBOOK_HOST=${OPEN_NOTEBOOK_HOST:-open_notebook}
OPEN_NOTEBOOK_PORT=${OPEN_NOTEBOOK_PORT:-8502}
OPEN_NOTEBOOK_ENABLED=${OPEN_NOTEBOOK_ENABLED:-true}
OPEN_NOTEBOOK_TS_PORT=${OPEN_NOTEBOOK_TS_PORT:-8443}
OPEN_NOTEBOOK_LOCAL_PORT=8237  # Local port for UI socat proxy
OPEN_NOTEBOOK_API_PORT=${OPEN_NOTEBOOK_API_PORT:-5055}
OPEN_NOTEBOOK_API_TS_PORT=${OPEN_NOTEBOOK_API_TS_PORT:-5055}
OPEN_NOTEBOOK_API_LOCAL_PORT=8238  # Local port for API socat proxy

# Open-notebook's frontend uses runtime auto-detection of the API URL based on
# the request's Host/X-Forwarded-Proto headers and constructs <proto>://<host>:5055.
# So when accessed via the tailnet, the browser expects the API to be reachable
# at https://<tailnet-host>:5055. We therefore expose port 5055 on the tailnet
# in addition to the UI port — without it, every API call from the browser
# fails with "Failed to fetch".
setup_open_notebook_api_serve() {
    echo "🔄 Creating local proxy for open-notebook API at ${OPEN_NOTEBOOK_HOST}:${OPEN_NOTEBOOK_API_PORT}"

    pkill -f "socat.*:${OPEN_NOTEBOOK_API_LOCAL_PORT}" || true
    sleep 2

    echo "🚀 Starting socat proxy: 127.0.0.1:${OPEN_NOTEBOOK_API_LOCAL_PORT} -> ${OPEN_NOTEBOOK_HOST}:${OPEN_NOTEBOOK_API_PORT}"
    socat -d -d TCP-LISTEN:${OPEN_NOTEBOOK_API_LOCAL_PORT},fork,reuseaddr,keepalive TCP:${OPEN_NOTEBOOK_HOST}:${OPEN_NOTEBOOK_API_PORT} > /tmp/socat-open-notebook-api.log 2>&1 &
    OPEN_NOTEBOOK_API_SOCAT_PID=$!
    echo $OPEN_NOTEBOOK_API_SOCAT_PID > /tmp/socat-open-notebook-api.pid

    sleep 3
    if ! kill -0 $OPEN_NOTEBOOK_API_SOCAT_PID 2>/dev/null; then
        echo "❌ ERROR: open-notebook API socat failed to start"
        cat /tmp/socat-open-notebook-api.log 2>/dev/null || echo "No log file found"
        return 1
    fi
    echo "✅ open-notebook API proxy started successfully (PID: $OPEN_NOTEBOOK_API_SOCAT_PID)"

    tailscale --socket=/tmp/tailscaled.sock serve \
      --https=${OPEN_NOTEBOOK_API_TS_PORT} \
      --bg \
      http://127.0.0.1:${OPEN_NOTEBOOK_API_LOCAL_PORT}
    echo "✅ open-notebook API configured on tailnet HTTPS port ${OPEN_NOTEBOOK_API_TS_PORT} (via proxy: ${OPEN_NOTEBOOK_HOST}:${OPEN_NOTEBOOK_API_PORT} -> 127.0.0.1:${OPEN_NOTEBOOK_API_LOCAL_PORT})"

    touch /tmp/open-notebook-api-serve-configured
    return 0
}

setup_open_notebook_serve() {
    echo "🔄 Creating local proxy for open-notebook at ${OPEN_NOTEBOOK_HOST}:${OPEN_NOTEBOOK_PORT}"

    pkill -f "socat.*:${OPEN_NOTEBOOK_LOCAL_PORT}" || true
    sleep 2

    echo "🚀 Starting socat proxy: 127.0.0.1:${OPEN_NOTEBOOK_LOCAL_PORT} -> ${OPEN_NOTEBOOK_HOST}:${OPEN_NOTEBOOK_PORT}"
    socat -d -d TCP-LISTEN:${OPEN_NOTEBOOK_LOCAL_PORT},fork,reuseaddr,keepalive TCP:${OPEN_NOTEBOOK_HOST}:${OPEN_NOTEBOOK_PORT} > /tmp/socat-open-notebook.log 2>&1 &
    OPEN_NOTEBOOK_SOCAT_PID=$!
    echo $OPEN_NOTEBOOK_SOCAT_PID > /tmp/socat-open-notebook.pid

    sleep 3
    if ! kill -0 $OPEN_NOTEBOOK_SOCAT_PID 2>/dev/null; then
        echo "❌ ERROR: open-notebook socat failed to start"
        cat /tmp/socat-open-notebook.log 2>/dev/null || echo "No log file found"
        return 1
    fi
    echo "✅ open-notebook proxy started successfully (PID: $OPEN_NOTEBOOK_SOCAT_PID)"

    tailscale --socket=/tmp/tailscaled.sock serve \
      --https=${OPEN_NOTEBOOK_TS_PORT} \
      --bg \
      http://127.0.0.1:${OPEN_NOTEBOOK_LOCAL_PORT}
    echo "✅ open-notebook UI configured on tailnet HTTPS port ${OPEN_NOTEBOOK_TS_PORT} (via proxy: ${OPEN_NOTEBOOK_HOST}:${OPEN_NOTEBOOK_PORT} -> 127.0.0.1:${OPEN_NOTEBOOK_LOCAL_PORT})"

    touch /tmp/open-notebook-serve-configured
    return 0
}

if [ "$OPEN_NOTEBOOK_ENABLED" = "true" ]; then
    OPEN_NOTEBOOK_ATTEMPTS=0
    OPEN_NOTEBOOK_MAX_ATTEMPTS=18
    OPEN_NOTEBOOK_CONFIGURED=false
    OPEN_NOTEBOOK_API_CONFIGURED=false

    while [ $OPEN_NOTEBOOK_ATTEMPTS -lt $OPEN_NOTEBOOK_MAX_ATTEMPTS ]; do
        if wget -q -T 10 -O /dev/null http://${OPEN_NOTEBOOK_HOST}:${OPEN_NOTEBOOK_PORT}/; then
            if setup_open_notebook_serve; then
                OPEN_NOTEBOOK_CONFIGURED=true
            fi
            if wget -q -T 10 -O /dev/null http://${OPEN_NOTEBOOK_HOST}:${OPEN_NOTEBOOK_API_PORT}/api/config; then
                if setup_open_notebook_api_serve; then
                    OPEN_NOTEBOOK_API_CONFIGURED=true
                fi
            fi
            break
        fi
        OPEN_NOTEBOOK_ATTEMPTS=$((OPEN_NOTEBOOK_ATTEMPTS + 1))
        echo "⏳ open-notebook not ready yet (attempt ${OPEN_NOTEBOOK_ATTEMPTS}/${OPEN_NOTEBOOK_MAX_ATTEMPTS}), waiting 10s..."
        sleep 10
    done

    if [ "$OPEN_NOTEBOOK_CONFIGURED" != "true" ]; then
        echo "⚠️ open-notebook UI not available after ${OPEN_NOTEBOOK_MAX_ATTEMPTS} attempts — monitoring loop will configure it when it comes online"
    fi
    if [ "$OPEN_NOTEBOOK_API_CONFIGURED" != "true" ]; then
        echo "⚠️ open-notebook API not available — monitoring loop will configure it when it comes online"
    fi
else
    echo "🔄 open-notebook Tailscale integration disabled (OPEN_NOTEBOOK_ENABLED=false)"
fi

# Configure the OB1 Quartz wiki viewer on its own Tailscale HTTPS port.
# Quartz (like Streamlit) serves a site at root and does not host cleanly
# under a sub-path, so expose it at root on a distinct tailnet HTTPS port.
# The viewer is the openbrain-wiki-viewer container on the OB1 stack; it
# joins ai-stack_app-net so this netns (shared with openwebui) reaches it
# by name. OB1 starts AFTER the main stack, so the viewer is usually not up
# yet at boot — the monitoring loop performs deferred setup when it appears.
sleep 2
echo "📚 Configuring OB1 Quartz wiki viewer access..."

QUARTZ_HOST=${QUARTZ_HOST:-openbrain-wiki-viewer}
QUARTZ_PORT=${QUARTZ_PORT:-8080}
QUARTZ_ENABLED=${QUARTZ_ENABLED:-true}
QUARTZ_TS_PORT=${QUARTZ_TS_PORT:-8444}
QUARTZ_LOCAL_PORT=8239  # Local port for socat proxy

setup_quartz_serve() {
    echo "🔄 Creating local proxy for Quartz wiki viewer at ${QUARTZ_HOST}:${QUARTZ_PORT}"

    pkill -f "socat.*:${QUARTZ_LOCAL_PORT}" || true
    sleep 2

    echo "🚀 Starting socat proxy: 127.0.0.1:${QUARTZ_LOCAL_PORT} -> ${QUARTZ_HOST}:${QUARTZ_PORT}"
    socat -d -d TCP-LISTEN:${QUARTZ_LOCAL_PORT},fork,reuseaddr,keepalive TCP:${QUARTZ_HOST}:${QUARTZ_PORT} > /tmp/socat-quartz.log 2>&1 &
    QUARTZ_SOCAT_PID=$!
    echo $QUARTZ_SOCAT_PID > /tmp/socat-quartz.pid

    sleep 3
    if ! kill -0 $QUARTZ_SOCAT_PID 2>/dev/null; then
        echo "❌ ERROR: Quartz wiki viewer socat failed to start"
        cat /tmp/socat-quartz.log 2>/dev/null || echo "No log file found"
        return 1
    fi
    echo "✅ Quartz wiki viewer proxy started successfully (PID: $QUARTZ_SOCAT_PID)"

    tailscale --socket=/tmp/tailscaled.sock serve \
      --https=${QUARTZ_TS_PORT} \
      --bg \
      http://127.0.0.1:${QUARTZ_LOCAL_PORT}
    echo "✅ Quartz wiki viewer configured on tailnet HTTPS port ${QUARTZ_TS_PORT} (via proxy: ${QUARTZ_HOST}:${QUARTZ_PORT} -> 127.0.0.1:${QUARTZ_LOCAL_PORT})"

    touch /tmp/quartz-serve-configured
    return 0
}

if [ "$QUARTZ_ENABLED" = "true" ]; then
    # Single boot attempt — OB1 usually isn't up yet, so don't block startup
    # on a long retry; the monitoring loop's deferred setup handles it.
    if wget -q -T 10 -O /dev/null http://${QUARTZ_HOST}:${QUARTZ_PORT}/; then
        setup_quartz_serve || echo "⚠️ Quartz wiki viewer setup failed — monitoring loop will retry"
    else
        echo "⚠️ Quartz wiki viewer not reachable yet (OB1 starts after the main stack) — monitoring loop will configure it when it comes online"
    fi
else
    echo "🔄 Quartz wiki viewer Tailscale integration disabled (QUARTZ_ENABLED=false)"
fi

# Configure the LiteLLM Admin-UI sidecar (llm-gateway-ui) on its own Tailscale
# HTTPS port. This is the SEPARATE master-key'd LiteLLM instance that serves the
# analytics dashboard at /ui (the permissive main gateway can't — LiteLLM 1.88.1
# requires a master_key for the UI to log in). Like Quartz/Streamlit it serves
# at root, so expose it at root on a distinct tailnet HTTPS port. The container
# is on the main stack (starts with us) so it's usually up; a short retry plus
# the monitoring loop's deferred setup handle any lag.
sleep 2
echo "🔑 Configuring LiteLLM Admin UI (llm-gateway-ui) access..."

LITELLM_UI_HOST=${LITELLM_UI_HOST:-llm-gateway-ui}
LITELLM_UI_PORT=${LITELLM_UI_PORT:-8080}
LITELLM_UI_ENABLED=${LITELLM_UI_ENABLED:-true}
LITELLM_UI_TS_PORT=${LITELLM_UI_TS_PORT:-8445}
LITELLM_UI_LOCAL_PORT=8240  # Local port for socat proxy

setup_litellm_ui_serve() {
    echo "🔄 Creating local proxy for LiteLLM Admin UI at ${LITELLM_UI_HOST}:${LITELLM_UI_PORT}"

    pkill -f "socat.*:${LITELLM_UI_LOCAL_PORT}" || true
    sleep 2

    echo "🚀 Starting socat proxy: 127.0.0.1:${LITELLM_UI_LOCAL_PORT} -> ${LITELLM_UI_HOST}:${LITELLM_UI_PORT}"
    socat -d -d TCP-LISTEN:${LITELLM_UI_LOCAL_PORT},fork,reuseaddr,keepalive TCP:${LITELLM_UI_HOST}:${LITELLM_UI_PORT} > /tmp/socat-litellm-ui.log 2>&1 &
    LITELLM_UI_SOCAT_PID=$!
    echo $LITELLM_UI_SOCAT_PID > /tmp/socat-litellm-ui.pid

    sleep 3
    if ! kill -0 $LITELLM_UI_SOCAT_PID 2>/dev/null; then
        echo "❌ ERROR: LiteLLM Admin UI socat failed to start"
        cat /tmp/socat-litellm-ui.log 2>/dev/null || echo "No log file found"
        return 1
    fi
    echo "✅ LiteLLM Admin UI proxy started successfully (PID: $LITELLM_UI_SOCAT_PID)"

    tailscale --socket=/tmp/tailscaled.sock serve \
      --https=${LITELLM_UI_TS_PORT} \
      --bg \
      http://127.0.0.1:${LITELLM_UI_LOCAL_PORT}
    echo "✅ LiteLLM Admin UI configured on tailnet HTTPS port ${LITELLM_UI_TS_PORT} (via proxy: ${LITELLM_UI_HOST}:${LITELLM_UI_PORT} -> 127.0.0.1:${LITELLM_UI_LOCAL_PORT})"

    touch /tmp/litellm-ui-serve-configured
    return 0
}

if [ "$LITELLM_UI_ENABLED" = "true" ]; then
    LITELLM_UI_ATTEMPTS=0
    LITELLM_UI_MAX_ATTEMPTS=12
    LITELLM_UI_CONFIGURED=false

    while [ $LITELLM_UI_ATTEMPTS -lt $LITELLM_UI_MAX_ATTEMPTS ]; do
        if wget -q -T 10 -O /dev/null http://${LITELLM_UI_HOST}:${LITELLM_UI_PORT}/health/liveliness; then
            if setup_litellm_ui_serve; then
                LITELLM_UI_CONFIGURED=true
            fi
            break
        fi
        LITELLM_UI_ATTEMPTS=$((LITELLM_UI_ATTEMPTS + 1))
        echo "⏳ llm-gateway-ui not ready yet (attempt ${LITELLM_UI_ATTEMPTS}/${LITELLM_UI_MAX_ATTEMPTS}), waiting 10s..."
        sleep 10
    done

    if [ "$LITELLM_UI_CONFIGURED" != "true" ]; then
        echo "⚠️ llm-gateway-ui not available after ${LITELLM_UI_MAX_ATTEMPTS} attempts — monitoring loop will configure it when it comes online"
    fi
else
    echo "🔄 LiteLLM Admin UI Tailscale integration disabled (LITELLM_UI_ENABLED=false)"
fi

# Configure the Mattermost chat server (agent-org project) on its own Tailscale
# HTTPS port. Mattermost is a full web app (root path + websockets) so it can't
# host under a sub-path — expose it at root on a distinct tailnet HTTPS port. It
# lives on the SEPARATE agent-org compose project but joins llm-net, so this netns
# (shared with openwebui) reaches it by name. agent-org usually starts AFTER the
# main stack, so it may not be up at boot — the monitoring loop's deferred setup
# handles it. (For full function over the tailnet set MM_SERVICESETTINGS_SITEURL to
# the tailnet URL — agent-org/docker/.env MM_SITE_URL — else MM rejects websockets.)
sleep 2
echo "💬 Configuring Mattermost (agent-org) access..."

MATTERMOST_HOST=${MATTERMOST_HOST:-mattermost}
MATTERMOST_PORT=${MATTERMOST_PORT:-8065}
MATTERMOST_ENABLED=${MATTERMOST_ENABLED:-true}
MATTERMOST_TS_PORT=${MATTERMOST_TS_PORT:-8446}
MATTERMOST_LOCAL_PORT=8241  # Local port for socat proxy (8240 reserved by llm-gateway-ui)

setup_mattermost_serve() {
    echo "🔄 Creating local proxy for Mattermost at ${MATTERMOST_HOST}:${MATTERMOST_PORT}"

    pkill -f "socat.*:${MATTERMOST_LOCAL_PORT}" || true
    sleep 2

    echo "🚀 Starting socat proxy: 127.0.0.1:${MATTERMOST_LOCAL_PORT} -> ${MATTERMOST_HOST}:${MATTERMOST_PORT}"
    socat -d -d TCP-LISTEN:${MATTERMOST_LOCAL_PORT},fork,reuseaddr,keepalive TCP:${MATTERMOST_HOST}:${MATTERMOST_PORT} > /tmp/socat-mattermost.log 2>&1 &
    MATTERMOST_SOCAT_PID=$!
    echo $MATTERMOST_SOCAT_PID > /tmp/socat-mattermost.pid

    sleep 3
    if ! kill -0 $MATTERMOST_SOCAT_PID 2>/dev/null; then
        echo "❌ ERROR: Mattermost socat failed to start"
        cat /tmp/socat-mattermost.log 2>/dev/null || echo "No log file found"
        return 1
    fi
    echo "✅ Mattermost proxy started successfully (PID: $MATTERMOST_SOCAT_PID)"

    if ! tailscale --socket=/tmp/tailscaled.sock serve \
      --https=${MATTERMOST_TS_PORT} \
      --bg \
      http://127.0.0.1:${MATTERMOST_LOCAL_PORT}; then
        echo "❌ ERROR: tailscale serve --https=${MATTERMOST_TS_PORT} failed — leaving unconfigured so the monitoring loop retries"
        return 1
    fi
    # Confirm the mapping actually landed before stamping the flag: a flag written
    # after a serve that did not apply permanently disables the deferred-setup
    # retry (this exact failure stranded Mattermost off the tailnet, 2026-07-05).
    if ! tailscale --socket=/tmp/tailscaled.sock serve status 2>/dev/null | grep -q ":${MATTERMOST_TS_PORT} "; then
        echo "❌ ERROR: serve mapping :${MATTERMOST_TS_PORT} missing after configuration — leaving unconfigured so the monitoring loop retries"
        return 1
    fi
    echo "✅ Mattermost configured on tailnet HTTPS port ${MATTERMOST_TS_PORT} (via proxy: ${MATTERMOST_HOST}:${MATTERMOST_PORT} -> 127.0.0.1:${MATTERMOST_LOCAL_PORT})"

    touch /tmp/mattermost-serve-configured
    return 0
}

if [ "$MATTERMOST_ENABLED" = "true" ]; then
    # Single boot attempt — agent-org usually isn't up yet (starts after the main
    # stack), so don't block startup on a long retry; the monitoring loop's
    # deferred setup configures it once Mattermost is reachable.
    if wget -q -T 10 -O /dev/null http://${MATTERMOST_HOST}:${MATTERMOST_PORT}/api/v4/system/ping; then
        setup_mattermost_serve || echo "⚠️ Mattermost setup failed — monitoring loop will retry"
    else
        echo "⚠️ Mattermost not reachable yet (agent-org starts after the main stack) — monitoring loop will configure it when it comes online"
    fi
else
    echo "🔄 Mattermost Tailscale integration disabled (MATTERMOST_ENABLED=false)"
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
if [ "$LLAMA_CPP_EMBED_ENABLED" = "true" ]; then
    echo "  - llama-cpp-embed API: HTTPS port 443/llama-cpp-embed -> ${LLAMA_CPP_EMBED_HOST}:${LLAMA_CPP_EMBED_PORT}"
fi
if [ "$OPEN_NOTEBOOK_ENABLED" = "true" ]; then
    echo "  - open-notebook UI: HTTPS port ${OPEN_NOTEBOOK_TS_PORT} -> ${OPEN_NOTEBOOK_HOST}:${OPEN_NOTEBOOK_PORT}"
    echo "  - open-notebook API: HTTPS port ${OPEN_NOTEBOOK_API_TS_PORT} -> ${OPEN_NOTEBOOK_HOST}:${OPEN_NOTEBOOK_API_PORT}"
fi
if [ "$QUARTZ_ENABLED" = "true" ]; then
    echo "  - Quartz wiki viewer: HTTPS port ${QUARTZ_TS_PORT} -> ${QUARTZ_HOST}:${QUARTZ_PORT}"
fi
if [ "$LITELLM_UI_ENABLED" = "true" ]; then
    echo "  - LiteLLM Admin UI: HTTPS port ${LITELLM_UI_TS_PORT} -> ${LITELLM_UI_HOST}:${LITELLM_UI_PORT}"
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

        # Check open-notebook API: deferred setup + socat health
        if [ "$OPEN_NOTEBOOK_ENABLED" = "true" ]; then
            if [ ! -f /tmp/open-notebook-api-serve-configured ]; then
                if wget -q -T 10 -O /dev/null http://${OPEN_NOTEBOOK_HOST}:${OPEN_NOTEBOOK_API_PORT}/api/config; then
                    echo "📓 $(date): open-notebook API is now online, performing deferred setup..."
                    setup_open_notebook_api_serve || echo "❌ $(date): Deferred open-notebook API setup failed, will retry next cycle"
                fi
            elif [ -f /tmp/socat-open-notebook-api.pid ]; then
                OPEN_NOTEBOOK_API_PID=$(cat /tmp/socat-open-notebook-api.pid)
                if ! kill -0 $OPEN_NOTEBOOK_API_PID 2>/dev/null; then
                    echo "⚠️ $(date): open-notebook API socat proxy (PID: $OPEN_NOTEBOOK_API_PID) has died, restarting..."

                    OPEN_NOTEBOOK_API_LOCAL_PORT=8238
                    pkill -f "socat.*:${OPEN_NOTEBOOK_API_LOCAL_PORT}" || true
                    sleep 2

                    socat -d -d TCP-LISTEN:${OPEN_NOTEBOOK_API_LOCAL_PORT},fork,reuseaddr,keepalive TCP:${OPEN_NOTEBOOK_HOST}:${OPEN_NOTEBOOK_API_PORT} > /tmp/socat-open-notebook-api.log 2>&1 &
                    NEW_OPEN_NOTEBOOK_API_PID=$!
                    echo $NEW_OPEN_NOTEBOOK_API_PID > /tmp/socat-open-notebook-api.pid
                    sleep 3

                    if kill -0 $NEW_OPEN_NOTEBOOK_API_PID 2>/dev/null; then
                        echo "✅ $(date): open-notebook API proxy restarted successfully (PID: $NEW_OPEN_NOTEBOOK_API_PID)"
                    else
                        echo "❌ $(date): Failed to restart open-notebook API proxy"
                        cat /tmp/socat-open-notebook-api.log 2>/dev/null || echo "No log available"
                    fi
                fi
            fi
        fi

        # Check open-notebook: deferred setup + socat health
        if [ "$OPEN_NOTEBOOK_ENABLED" = "true" ]; then
            if [ ! -f /tmp/open-notebook-serve-configured ]; then
                if wget -q -T 10 -O /dev/null http://${OPEN_NOTEBOOK_HOST}:${OPEN_NOTEBOOK_PORT}/; then
                    echo "📓 $(date): open-notebook is now online, performing deferred setup..."
                    setup_open_notebook_serve || echo "❌ $(date): Deferred open-notebook setup failed, will retry next cycle"
                fi
            elif [ -f /tmp/socat-open-notebook.pid ]; then
                OPEN_NOTEBOOK_PID=$(cat /tmp/socat-open-notebook.pid)
                if ! kill -0 $OPEN_NOTEBOOK_PID 2>/dev/null; then
                    echo "⚠️ $(date): open-notebook socat proxy (PID: $OPEN_NOTEBOOK_PID) has died, restarting..."

                    OPEN_NOTEBOOK_LOCAL_PORT=8237
                    pkill -f "socat.*:${OPEN_NOTEBOOK_LOCAL_PORT}" || true
                    sleep 2

                    socat -d -d TCP-LISTEN:${OPEN_NOTEBOOK_LOCAL_PORT},fork,reuseaddr,keepalive TCP:${OPEN_NOTEBOOK_HOST}:${OPEN_NOTEBOOK_PORT} > /tmp/socat-open-notebook.log 2>&1 &
                    NEW_OPEN_NOTEBOOK_PID=$!
                    echo $NEW_OPEN_NOTEBOOK_PID > /tmp/socat-open-notebook.pid
                    sleep 3

                    if kill -0 $NEW_OPEN_NOTEBOOK_PID 2>/dev/null; then
                        echo "✅ $(date): open-notebook proxy restarted successfully (PID: $NEW_OPEN_NOTEBOOK_PID)"
                    else
                        echo "❌ $(date): Failed to restart open-notebook proxy"
                        cat /tmp/socat-open-notebook.log 2>/dev/null || echo "No log available"
                    fi
                fi
            fi
        fi

        # Check llama-cpp-embed: deferred setup + socat health
        if [ "$LLAMA_CPP_EMBED_ENABLED" = "true" ]; then
            if [ ! -f /tmp/llama-cpp-embed-serve-configured ]; then
                # Serve was never configured — try deferred setup
                if wget -q -T 10 -O /dev/null http://${LLAMA_CPP_EMBED_HOST}:${LLAMA_CPP_EMBED_PORT}/health; then
                    echo "🦙 $(date): llama-cpp-embed is now online, performing deferred setup..."
                    setup_llama_cpp_embed_serve || echo "❌ $(date): Deferred llama-cpp-embed setup failed, will retry next cycle"
                fi
            elif [ -f /tmp/socat-llama-cpp-embed.pid ]; then
                # Serve is configured — keep the socat proxy alive
                LLAMA_CPP_EMBED_PID=$(cat /tmp/socat-llama-cpp-embed.pid)
                if ! kill -0 $LLAMA_CPP_EMBED_PID 2>/dev/null; then
                    echo "⚠️ $(date): llama-cpp-embed socat proxy (PID: $LLAMA_CPP_EMBED_PID) has died, restarting..."

                    LLAMA_CPP_EMBED_LOCAL_PORT=8236
                    pkill -f "socat.*:${LLAMA_CPP_EMBED_LOCAL_PORT}" || true
                    sleep 2

                    socat -d -d TCP-LISTEN:${LLAMA_CPP_EMBED_LOCAL_PORT},fork,reuseaddr,keepalive TCP:${LLAMA_CPP_EMBED_HOST}:${LLAMA_CPP_EMBED_PORT} > /tmp/socat-llama-cpp-embed.log 2>&1 &
                    NEW_EMBED_PID=$!
                    echo $NEW_EMBED_PID > /tmp/socat-llama-cpp-embed.pid
                    sleep 3

                    if kill -0 $NEW_EMBED_PID 2>/dev/null; then
                        echo "✅ $(date): llama-cpp-embed proxy restarted successfully (PID: $NEW_EMBED_PID)"
                    else
                        echo "❌ $(date): Failed to restart llama-cpp-embed proxy"
                        cat /tmp/socat-llama-cpp-embed.log 2>/dev/null || echo "No log available"
                    fi
                fi
            fi
        fi
        
        # Check Quartz wiki viewer: deferred setup + socat health
        if [ "$QUARTZ_ENABLED" = "true" ]; then
            if [ ! -f /tmp/quartz-serve-configured ]; then
                # Serve was never configured (OB1 starts after the main stack) —
                # try deferred setup once the viewer is reachable.
                if wget -q -T 10 -O /dev/null http://${QUARTZ_HOST}:${QUARTZ_PORT}/; then
                    echo "📚 $(date): Quartz wiki viewer is now online, performing deferred setup..."
                    setup_quartz_serve || echo "❌ $(date): Deferred Quartz setup failed, will retry next cycle"
                fi
            elif [ -f /tmp/socat-quartz.pid ]; then
                QUARTZ_PID=$(cat /tmp/socat-quartz.pid)
                if ! kill -0 $QUARTZ_PID 2>/dev/null; then
                    echo "⚠️ $(date): Quartz wiki viewer socat proxy (PID: $QUARTZ_PID) has died, restarting..."

                    QUARTZ_LOCAL_PORT=8239
                    pkill -f "socat.*:${QUARTZ_LOCAL_PORT}" || true
                    sleep 2

                    socat -d -d TCP-LISTEN:${QUARTZ_LOCAL_PORT},fork,reuseaddr,keepalive TCP:${QUARTZ_HOST}:${QUARTZ_PORT} > /tmp/socat-quartz.log 2>&1 &
                    NEW_QUARTZ_PID=$!
                    echo $NEW_QUARTZ_PID > /tmp/socat-quartz.pid
                    sleep 3

                    if kill -0 $NEW_QUARTZ_PID 2>/dev/null; then
                        echo "✅ $(date): Quartz wiki viewer proxy restarted successfully (PID: $NEW_QUARTZ_PID)"
                    else
                        echo "❌ $(date): Failed to restart Quartz wiki viewer proxy"
                        cat /tmp/socat-quartz.log 2>/dev/null || echo "No log available"
                    fi
                fi
            fi
        fi

        # Check LiteLLM Admin UI: deferred setup + socat health
        if [ "$LITELLM_UI_ENABLED" = "true" ]; then
            if [ ! -f /tmp/litellm-ui-serve-configured ]; then
                # Serve was never configured — try deferred setup
                if wget -q -T 10 -O /dev/null http://${LITELLM_UI_HOST}:${LITELLM_UI_PORT}/health/liveliness; then
                    echo "🔑 $(date): llm-gateway-ui is now online, performing deferred setup..."
                    setup_litellm_ui_serve || echo "❌ $(date): Deferred LiteLLM Admin UI setup failed, will retry next cycle"
                fi
            elif [ -f /tmp/socat-litellm-ui.pid ]; then
                # Serve is configured — keep the socat proxy alive
                LITELLM_UI_PID=$(cat /tmp/socat-litellm-ui.pid)
                if ! kill -0 $LITELLM_UI_PID 2>/dev/null; then
                    echo "⚠️ $(date): LiteLLM Admin UI socat proxy (PID: $LITELLM_UI_PID) has died, restarting..."

                    LITELLM_UI_LOCAL_PORT=8240
                    pkill -f "socat.*:${LITELLM_UI_LOCAL_PORT}" || true
                    sleep 2

                    socat -d -d TCP-LISTEN:${LITELLM_UI_LOCAL_PORT},fork,reuseaddr,keepalive TCP:${LITELLM_UI_HOST}:${LITELLM_UI_PORT} > /tmp/socat-litellm-ui.log 2>&1 &
                    NEW_LITELLM_UI_PID=$!
                    echo $NEW_LITELLM_UI_PID > /tmp/socat-litellm-ui.pid
                    sleep 3

                    if kill -0 $NEW_LITELLM_UI_PID 2>/dev/null; then
                        echo "✅ $(date): LiteLLM Admin UI proxy restarted successfully (PID: $NEW_LITELLM_UI_PID)"
                    else
                        echo "❌ $(date): Failed to restart LiteLLM Admin UI proxy"
                        cat /tmp/socat-litellm-ui.log 2>/dev/null || echo "No log available"
                    fi
                fi
            fi
        fi

        # Check Mattermost (agent-org): deferred setup + socat health
        if [ "$MATTERMOST_ENABLED" = "true" ]; then
            if [ ! -f /tmp/mattermost-serve-configured ]; then
                # Serve was never configured (agent-org came up after us) — try deferred setup
                if wget -q -T 10 -O /dev/null http://${MATTERMOST_HOST}:${MATTERMOST_PORT}/api/v4/system/ping; then
                    echo "💬 $(date): Mattermost is now online, performing deferred setup..."
                    setup_mattermost_serve || echo "❌ $(date): Deferred Mattermost setup failed, will retry next cycle"
                fi
            elif [ -f /tmp/socat-mattermost.pid ]; then
                # Serve is configured — keep the socat proxy alive
                MATTERMOST_PID=$(cat /tmp/socat-mattermost.pid)
                if ! kill -0 $MATTERMOST_PID 2>/dev/null; then
                    echo "⚠️ $(date): Mattermost socat proxy (PID: $MATTERMOST_PID) has died, restarting..."

                    pkill -f "socat.*:${MATTERMOST_LOCAL_PORT}" || true
                    sleep 2

                    socat -d -d TCP-LISTEN:${MATTERMOST_LOCAL_PORT},fork,reuseaddr,keepalive TCP:${MATTERMOST_HOST}:${MATTERMOST_PORT} > /tmp/socat-mattermost.log 2>&1 &
                    NEW_MATTERMOST_PID=$!
                    echo $NEW_MATTERMOST_PID > /tmp/socat-mattermost.pid
                    sleep 3

                    if kill -0 $NEW_MATTERMOST_PID 2>/dev/null; then
                        echo "✅ $(date): Mattermost proxy restarted successfully (PID: $NEW_MATTERMOST_PID)"
                    else
                        echo "❌ $(date): Failed to restart Mattermost proxy"
                        cat /tmp/socat-mattermost.log 2>/dev/null || echo "No log available"
                    fi
                fi

                # Re-verify the tailnet serve mapping still exists: a stranded flag
                # with no serve leaves Mattermost silently unreachable from the
                # tailnet (2026-07-05). Dropping the flag makes the next cycle rerun
                # the full deferred setup (socat + serve, both now verified).
                if ! tailscale --socket=/tmp/tailscaled.sock serve status 2>/dev/null | grep -q ":${MATTERMOST_TS_PORT} "; then
                    echo "⚠️ $(date): Mattermost serve mapping :${MATTERMOST_TS_PORT} missing — clearing flag to reconfigure next cycle"
                    rm -f /tmp/mattermost-serve-configured
                fi
            fi
        fi

        # Refresh tailnet-info.json so the openwebui admin pipe always sees
        # current FQDN / peer state. Cheap (single status call) and resilient.
        write_tailnet_info >/dev/null 2>&1 || true

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

                # Reconfigure llama-cpp-embed API if available and enabled
                if [ "$LLAMA_CPP_EMBED_ENABLED" = "true" ] && wget -q -T 10 -O /dev/null http://${LLAMA_CPP_EMBED_HOST}:${LLAMA_CPP_EMBED_PORT}/health; then
                    echo "🔄 Reconfiguring llama-cpp-embed serve after reconnection..."
                    rm -f /tmp/llama-cpp-embed-serve-configured
                    setup_llama_cpp_embed_serve || echo "❌ Failed to reconfigure llama-cpp-embed serve"
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
            elif [ "$LLAMA_CPP_EMBED_ENABLED" = "true" ] && ! echo "$serve_status" | grep -q "127.0.0.1:${LLAMA_CPP_EMBED_LOCAL_PORT:-8236}"; then
                # llama-cpp-embed serve is missing, try to add it
                echo "🔄 $(date): Adding missing llama-cpp-embed serve configuration..."
                if wget -q -T 10 -O /dev/null http://${LLAMA_CPP_EMBED_HOST}:${LLAMA_CPP_EMBED_PORT}/health; then
                    rm -f /tmp/llama-cpp-embed-serve-configured
                    setup_llama_cpp_embed_serve || echo "❌ Failed to add llama-cpp-embed serve"
                fi
            elif [ "$OPEN_NOTEBOOK_ENABLED" = "true" ] && ! echo "$serve_status" | grep -q "127.0.0.1:${OPEN_NOTEBOOK_LOCAL_PORT:-8237}"; then
                # open-notebook serve is missing, try to add it
                echo "🔄 $(date): Adding missing open-notebook serve configuration..."
                if wget -q -T 10 -O /dev/null http://${OPEN_NOTEBOOK_HOST}:${OPEN_NOTEBOOK_PORT}/; then
                    rm -f /tmp/open-notebook-serve-configured
                    setup_open_notebook_serve || echo "❌ Failed to add open-notebook serve"
                fi
            elif [ "$OPEN_NOTEBOOK_ENABLED" = "true" ] && ! echo "$serve_status" | grep -q "127.0.0.1:${OPEN_NOTEBOOK_API_LOCAL_PORT:-8238}"; then
                # open-notebook API serve is missing, try to add it
                echo "🔄 $(date): Adding missing open-notebook API serve configuration..."
                if wget -q -T 10 -O /dev/null http://${OPEN_NOTEBOOK_HOST}:${OPEN_NOTEBOOK_API_PORT}/api/config; then
                    rm -f /tmp/open-notebook-api-serve-configured
                    setup_open_notebook_api_serve || echo "❌ Failed to add open-notebook API serve"
                fi
            elif [ "$QUARTZ_ENABLED" = "true" ] && ! echo "$serve_status" | grep -q "127.0.0.1:${QUARTZ_LOCAL_PORT:-8239}"; then
                # Quartz wiki viewer serve is missing, try to add it
                echo "🔄 $(date): Adding missing Quartz wiki viewer serve configuration..."
                if wget -q -T 10 -O /dev/null http://${QUARTZ_HOST}:${QUARTZ_PORT}/; then
                    rm -f /tmp/quartz-serve-configured
                    setup_quartz_serve || echo "❌ Failed to add Quartz wiki viewer serve"
                fi
            elif [ "$LITELLM_UI_ENABLED" = "true" ] && ! echo "$serve_status" | grep -q "127.0.0.1:${LITELLM_UI_LOCAL_PORT:-8240}"; then
                # LiteLLM Admin UI serve is missing, try to add it
                echo "🔄 $(date): Adding missing LiteLLM Admin UI serve configuration..."
                if wget -q -T 10 -O /dev/null http://${LITELLM_UI_HOST}:${LITELLM_UI_PORT}/health/liveliness; then
                    rm -f /tmp/litellm-ui-serve-configured
                    setup_litellm_ui_serve || echo "❌ Failed to add LiteLLM Admin UI serve"
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
if [ "$LLAMA_CPP_EMBED_ENABLED" = "true" ]; then
    echo "  - llama-cpp-embed API: https://$(tailscale --socket=/tmp/tailscaled.sock status --json | grep '"Name"' | cut -d'"' -f4 2>/dev/null || echo 'your-hostname').tail[...].ts.net/llama-cpp-embed/v1"
fi
if [ "$OPEN_NOTEBOOK_ENABLED" = "true" ]; then
    echo "  - open-notebook UI: https://$(tailscale --socket=/tmp/tailscaled.sock status --json | grep '"Name"' | cut -d'"' -f4 2>/dev/null || echo 'your-hostname').tail[...].ts.net:${OPEN_NOTEBOOK_TS_PORT}/"
    echo "  - open-notebook API: https://$(tailscale --socket=/tmp/tailscaled.sock status --json | grep '"Name"' | cut -d'"' -f4 2>/dev/null || echo 'your-hostname').tail[...].ts.net:${OPEN_NOTEBOOK_API_TS_PORT}/api"
fi
if [ "$QUARTZ_ENABLED" = "true" ]; then
    echo "  - Quartz wiki viewer: https://$(tailscale --socket=/tmp/tailscaled.sock status --json | grep '"Name"' | cut -d'"' -f4 2>/dev/null || echo 'your-hostname').tail[...].ts.net:${QUARTZ_TS_PORT}/"
fi
if [ "$LITELLM_UI_ENABLED" = "true" ]; then
    echo "  - LiteLLM Admin UI: https://$(tailscale --socket=/tmp/tailscaled.sock status --json | grep '"Name"' | cut -d'"' -f4 2>/dev/null || echo 'your-hostname').tail[...].ts.net:${LITELLM_UI_TS_PORT}/ui"
fi
# 9) Keep the container running
tail -f /dev/null
