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
# the WHOLE TREE traversable/writable from both planes, not just the mount
# point. Recursive (live 2026-07-14): dotnet build artifacts under vendor/
# were owned by a foreign uid, the ot-plane wipe couldn't delete them, and
# every re-clone failed on the non-empty dir — a worker restart must
# self-heal any such leftovers. Runs as root, so ownership never blocks it.
chmod -R 0777 /workspace 2>/dev/null || true

# models.json override — points the llamacpp provider at ai-stack's llama-swap
# and registers the model ids it actually serves (see config/models.json).
mkdir -p /home/lc/.config/little-coder
cp /app/config/models.json /home/lc/.config/little-coder/models.json 2>/dev/null || true
# J.1 missed-caller #7 (2026-08-23): the node agent uses models.json apiKey VERBATIM —
# the "LLAMACPP_API_KEY" string is a placeholder that must be substituted at boot from
# the environment (compose feeds it from LC_LLAMA_API_KEY). Without this, every LLM
# call sends the literal placeholder as its bearer (fine in the old permissive gateway,
# 401 since the master-key flip). Leaves the placeholder when the env is unset.
if [ -n "${LLAMACPP_API_KEY:-}" ]; then
  sed -i "s|\"LLAMACPP_API_KEY\"|\"${LLAMACPP_API_KEY}\"|" /home/lc/.config/little-coder/models.json 2>/dev/null || true
fi

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
  # Remove extensions whose execution/egress would land OUTSIDE the open-terminal
  # workspace plane (control-plane→workspace invariant). `bash` (our override →
  # ot-exec → open-terminal → git-proxy) must be the sole execution path — the
  # agent was observed escaping the git-proxy via ShellSession.
  #   shell-session            — in-container shell (git-proxy bypass)
  #   browser / browser-extract-retention — playwright launches chromium
  #                              IN-PROCESS here (1.9.x), not in open-terminal;
  #                              also egress. Excluded until/unless routed.
  # The `--exclude-tools` denylist in config/little-coder.config.yaml is the
  # declarative backstop (survives an upstream dir rename); this rm is the
  # belt-and-braces. Removal is logged per-dir so a silent miss is visible.
  for ext in shell-session browser browser-extract-retention; do
    if [ -d "$EXT_DIR/$ext" ]; then
      rm -rf "$EXT_DIR/$ext" && echo "[entrypoint] removed extension: $ext"
    else
      echo "[entrypoint] NOTE: extension '$ext' not present (renamed upstream? check --exclude-tools)"
    fi
  done
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
