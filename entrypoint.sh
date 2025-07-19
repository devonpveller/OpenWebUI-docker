#!/bin/sh
set -e

# 1) Clean up any old socket
rm -f /tmp/tailscaled.sock

# 2) Launch the daemon
/usr/local/bin/containerboot &

# 3) Wait up to 5s for the socket
timeout=5
while [ ! -S /tmp/tailscaled.sock ] && [ $timeout -gt 0 ]; do
  sleep 1
  timeout=$((timeout - 1))
done
[ -S /tmp/tailscaled.sock ] || {
  echo >&2 "Error: /tmp/tailscaled.sock not found"
  exit 1
}

# 4) Join your tailnet, wiping any broken state
tailscale --socket=/tmp/tailscaled.sock up --reset \
  --auth-key="${TS_AUTHKEY}" \
  --hostname="openwebui" \
  --accept-dns=false

# 5) Expose Open WebUI via Tailscale (IPv4)
tailscale --socket=/tmp/tailscaled.sock serve \
  --https=443 --bg http://127.0.0.1:8080

# 6) Keep the container running
tail -f /dev/null
