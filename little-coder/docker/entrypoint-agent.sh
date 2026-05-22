#!/bin/sh
# Entrypoint for the little-coder agent image (and lc-mcpo, same image).
# Named volumes mount root-owned; hand them to the unprivileged user, install
# the pi extension into that user's pi config, then drop privileges.
set -e

mkdir -p /var/lib/little-coder/journals \
         /var/lib/little-coder/skill \
         /var/lib/little-coder/cohorts \
         /var/lib/little-coder/polyglot \
         /workspace
chown -R lc:lc /var/lib/little-coder /workspace 2>/dev/null || true
# The workspace volume is shared with open-terminal (a different uid); make
# the mount point traversable/writable from both planes.
chmod 0777 /workspace 2>/dev/null || true

# models.json override — points the llamacpp provider at ai-stack's llama-swap
# and registers the model ids it actually serves (see config/models.json).
mkdir -p /home/lc/.config/little-coder
cp /app/config/models.json /home/lc/.config/little-coder/models.json 2>/dev/null || true
chown -R lc:lc /home/lc/.config

# Route the agent's shell into open-terminal (design §1.5, §3.4): install the
# bash-override pi extension into little-coder's OWN extensions dir, so pi
# discovers it and its imports resolve. OFF by default — set LC_ROUTE_EXEC=1
# once validated (see pi-extension/README.md). Without it the agent uses pi's
# built-in bash, which runs in THIS container — network-isolated and contained,
# but outside the open-terminal plane / git-proxy.
if [ "${LC_ROUTE_EXEC:-0}" = "1" ]; then
  PKG_EXT="$(npm root -g)/little-coder/.pi/extensions/open-terminal-exec"
  mkdir -p "$PKG_EXT"
  if cp /opt/little-coder/pi-extensions/open-terminal-exec/index.ts \
        "$PKG_EXT/index.ts" 2>/dev/null; then
    echo "[entrypoint] open-terminal exec routing ENABLED"
  else
    echo "[entrypoint] WARN: could not install open-terminal-exec extension"
  fi
else
  echo "[entrypoint] exec routing disabled (LC_ROUTE_EXEC!=1) — built-in bash"
fi

exec gosu lc "$@"
