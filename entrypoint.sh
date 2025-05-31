#!/bin/sh

/usr/local/bin/containerboot &

# Wait a bit for tailscaled
sleep 5

# Authenticate
tailscale up --authkey=${TAILSCALE_AUTH_KEY} --hostname=openwebui --state=${TS_STATE_DIR}

# Forward localhost:3000 to Open WebUI running in host.docker.internal
socat TCP4-LISTEN:3000,bind=127.0.0.1,fork TCP:host.docker.internal:3000 &

# Serve on tailnet
tailscale serve --https=443 --bg http://localhost:3000

# Keep container alive
tail -f /dev/null
