#!/bin/sh
set -e

# 1) Clean up any old socket
rm -f /tmp/tailscaled.sock

# 2) Start tailscaled directly with persistent state
/usr/local/bin/tailscaled \
  --socket=/tmp/tailscaled.sock \
  --statedir=/var/lib/tailscale \
  --tun=userspace-networking &

# 3) Wait up to 10s for the socket
timeout=10
while [ ! -S /tmp/tailscaled.sock ] && [ $timeout -gt 0 ]; do
  sleep 1
  timeout=$((timeout - 1))
done
[ -S /tmp/tailscaled.sock ] || { echo >&2 "Error: /tmp/tailscaled.sock not found"; exit 1; }

# 4) Join your tailnet (reusing existing device identity)
tailscale --socket=/tmp/tailscaled.sock up \
  --auth-key="${TAILSCALE_AUTH_KEY}" \
  --hostname="${TS_HOSTNAME:-openwebui}" \
  --accept-dns="${TS_ACCEPT_DNS:-false}"

# 5) Wait for the node to be fully connected
sleep 3

# 6) Configure serve for HTTPS access
# Clear any existing serve config and set up fresh
tailscale --socket=/tmp/tailscaled.sock serve reset
tailscale --socket=/tmp/tailscaled.sock serve \
  --https=443 \
  --bg \
  http://127.0.0.1:8080

echo "✅ Tailscale serve configured for HTTPS on port 443 -> 127.0.0.1:8080"

# 6) Keep the container running
tail -f /dev/null
