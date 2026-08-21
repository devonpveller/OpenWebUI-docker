#!/bin/sh
# Watchtower post-update hook for OpenWebUI
# Proactively restarts Tailscale when OpenWebUI gets a new container ID

set -e

echo "POST-UPDATE HOOK: OpenWebUI container updated"
echo "POST-UPDATE HOOK: Restarting Tailscale to reattach to new network namespace"

# Change to the compose directory
cd /compose-dir

# Gracefully restart Tailscale to pick up new OpenWebUI network namespace
echo "POST-UPDATE HOOK: Stopping Tailscale..."
docker compose stop tailscale

echo "POST-UPDATE HOOK: Waiting for OpenWebUI (GPU-enabled) to be healthy..."
timeout=180  # Increased timeout for GPU container CUDA initialization
while [ $timeout -gt 0 ]; do
    if docker compose ps openwebui | grep -q "healthy"; then
        echo "POST-UPDATE HOOK: OpenWebUI is healthy (CUDA initialized)"
        break
    fi
    echo "POST-UPDATE HOOK: Waiting for OpenWebUI GPU initialization... ($timeout seconds remaining)"
    sleep 5  # Check more frequently but with longer total timeout
    timeout=$((timeout - 5))
done

if [ $timeout -le 0 ]; then
    echo "POST-UPDATE HOOK: ERROR - Timeout waiting for OpenWebUI GPU initialization"
    echo "POST-UPDATE HOOK: OpenWebUI may need manual intervention for CUDA setup"
    exit 1
fi

# Additional wait for GPU container to fully stabilize
echo "POST-UPDATE HOOK: Allowing additional time for GPU container stabilization..."
sleep 15

echo "POST-UPDATE HOOK: Starting Tailscale..."
docker compose up -d tailscale

echo "POST-UPDATE HOOK: Verifying Tailscale connectivity..."
sleep 20  # Increased wait time for GPU container dependencies

# Verify the restart worked
if docker compose ps tailscale | grep -q "healthy\|starting"; then
    echo "POST-UPDATE HOOK: SUCCESS - Tailscale restarted successfully"
else
    echo "POST-UPDATE HOOK: WARNING - Tailscale may not have restarted properly"
    docker compose logs tailscale --tail=5
fi

echo "POST-UPDATE HOOK: Update coordination complete"