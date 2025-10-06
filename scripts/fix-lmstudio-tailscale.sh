#!/bin/bash
# LM Studio Tailscale Fix Script
# Run this script when LM Studio is not accessible via Tailscale

echo "🔧 LM Studio Tailscale Recovery Script"
echo "======================================"

# Get environment variables
LMSTUDIO_HOST=${LMSTUDIO_HOST:-169.254.83.107}
LMSTUDIO_PORT=${LMSTUDIO_PORT:-5506}
LMSTUDIO_LOCAL_PORT=8234

echo "📋 Configuration:"
echo "  LM Studio: ${LMSTUDIO_HOST}:${LMSTUDIO_PORT}"
echo "  Local proxy port: ${LMSTUDIO_LOCAL_PORT}"
echo ""

# Test LM Studio connectivity
echo "🧪 Testing LM Studio connectivity..."
if wget -q -T 5 -O /dev/null http://${LMSTUDIO_HOST}:${LMSTUDIO_PORT}/v1/models; then
    echo "✅ LM Studio is accessible"
else
    echo "❌ LM Studio is not accessible - make sure it's running"
    exit 1
fi

# Kill existing socat processes
echo "🧹 Cleaning up existing proxy processes..."
pkill -f "socat.*:${LMSTUDIO_LOCAL_PORT}" 2>/dev/null || true

# Start socat proxy with nohup to keep it running
echo "🚀 Starting persistent socat proxy..."
nohup socat TCP-LISTEN:${LMSTUDIO_LOCAL_PORT},fork,reuseaddr TCP:${LMSTUDIO_HOST}:${LMSTUDIO_PORT} > /tmp/socat.log 2>&1 &
sleep 2

# Test proxy
echo "🧪 Testing proxy connectivity..."
if wget -q -T 5 -O /dev/null http://127.0.0.1:${LMSTUDIO_LOCAL_PORT}/v1/models; then
    echo "✅ Proxy is working"
else
    echo "❌ Proxy failed to start"
    echo "📋 Socat log:"
    cat /tmp/socat.log || echo "No log available"
    exit 1
fi

# Configure Tailscale serve
echo "🌐 Configuring Tailscale serve..."
tailscale --socket=/tmp/tailscaled.sock serve --https=443 --set-path=/lmstudio --bg http://127.0.0.1:${LMSTUDIO_LOCAL_PORT}

echo ""
echo "✅ LM Studio Tailscale setup complete!"
echo ""
echo "🔗 Access URL: https://openwebui-13.tail37f875.ts.net/lmstudio"
echo ""
echo "📊 Current serve status:"
tailscale --socket=/tmp/tailscaled.sock serve status