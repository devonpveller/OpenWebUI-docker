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

# Route the agent's shell into open-terminal (design §1.5, §3.4): install the
# bash-override pi extension into little-coder's OWN extensions dir, so pi
# discovers it and its imports resolve. OFF by default — set LC_ROUTE_EXEC=1
# once validated (see pi-extension/README.md). Without it the agent uses pi's
# built-in bash, which runs in THIS container — network-isolated and contained,
# but outside the open-terminal plane / git-proxy.
if [ "${LC_ROUTE_EXEC:-0}" = "1" ]; then
  EXT_DIR="$(npm root -g)/little-coder/.pi/extensions"
  mkdir -p "$EXT_DIR/open-terminal-exec"
  if cp /opt/little-coder/pi-extensions/open-terminal-exec/index.ts \
        "$EXT_DIR/open-terminal-exec/index.ts" 2>/dev/null; then
    echo "[entrypoint] open-terminal exec routing ENABLED"
  else
    echo "[entrypoint] WARN: could not install open-terminal-exec extension"
  fi
  # Remove tools that execute or egress OUTSIDE the routed path. `bash` (our
  # override → ot-exec → open-terminal → git-proxy) must be the sole execution
  # tool — the agent was observed escaping the git-proxy via ShellSession.
  rm -rf "$EXT_DIR/shell-session" "$EXT_DIR/browser" 2>/dev/null || true
  echo "[entrypoint] removed shell-session + browser extensions (no exec bypass)"
else
  echo "[entrypoint] exec routing disabled (LC_ROUTE_EXEC!=1) — built-in bash"
fi

# `/home/lc` ownership defense — done LAST so anything earlier in the
# entrypoint (npm cache, models.json copy, etc.) that may have written
# into the home as root gets corrected back to lc. Also covers the case
# where a prior `docker exec little-coder ...` run as root (operator
# debugging) created `/home/lc/.pi/agent/` owned by root, which then
# EACCES-blocks the lc user's next direct pi CLI run.
chown -R lc:lc /home/lc

exec gosu lc "$@"
