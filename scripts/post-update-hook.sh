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

echo "POST-UPDATE HOOK: Waiting for OpenWebUI to be healthy..."
timeout=60
while [ $timeout -gt 0 ]; do
    if docker compose ps openwebui | grep -q "healthy"; then
        echo "POST-UPDATE HOOK: OpenWebUI is healthy"
        break
    fi
    echo "POST-UPDATE HOOK: Waiting for OpenWebUI health... ($timeout seconds remaining)"
    sleep 2
    timeout=$((timeout - 2))
done

if [ $timeout -le 0 ]; then
    echo "POST-UPDATE HOOK: WARNING - Timeout waiting for OpenWebUI health"
    exit 1
fi

echo "POST-UPDATE HOOK: Starting Tailscale..."
docker compose up -d tailscale

echo "POST-UPDATE HOOK: Verifying Tailscale connectivity..."
sleep 10

# Verify the restart worked
if docker compose ps tailscale | grep -q "healthy\|starting"; then
    echo "POST-UPDATE HOOK: SUCCESS - Tailscale restarted successfully"
else
    echo "POST-UPDATE HOOK: WARNING - Tailscale may not have restarted properly"
    docker compose logs tailscale --tail=5
fi

echo "POST-UPDATE HOOK: Update coordination complete"