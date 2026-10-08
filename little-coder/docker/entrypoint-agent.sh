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
# The global npm root, resolved ONCE as root with root's own HOME, so no npm config in the agent
# user's home can move where the entrypoint installs or removes extensions.
NPM_GLOBAL_ROOT="$(HOME=/root npm root -g)"
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
  EXT_DIR="$NPM_GLOBAL_ROOT/little-coder/.pi/extensions"
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
  #   bg-shell                 — (1.16.0+) ShellStart/ShellLog/ShellList/
  #                              ShellSend/ShellStop: background jobs spawned
  #                              IN THIS container, a second git-proxy bypass
  #                              exactly like shell-session.
  # The `--exclude-tools` denylist in config/little-coder.config.yaml is the
  # declarative backstop (survives an upstream dir rename); this rm is the
  # belt-and-braces. Removal is logged per-dir so a silent miss is visible.
  for ext in shell-session bg-shell browser browser-extract-retention; do
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

# Load-surface lock (cf-lc-upgrade) - AFTER the chown above, which would undo it. The agent runs
# as `lc`, so anything `lc` can write the model can write with its ordinary `write` tool. pi and
# the little-coder launcher read CODE or CONFIG from $HOME at startup (~/.pi/agent settings:
# `packages` + `npmCommand`; config values starting with `!`, which pi runs as a shell command;
# models.json / auth.json / trust.json; ~/.agents/skills; the user extension dir, also pinned
# away by LITTLE_CODER_EXTENSIONS_DIR; Node's ~/.node_modules fallback). So $HOME itself is
# root-owned and read-only (a root-owned dir inside an lc-owned home can still be RENAMED away
# by lc - measured), and so is everything in it except the data dirs below.
# Stays writable, as DATA: ~/.little-coder (upstream's pre-edit checkpoints), ~/.cache, ~/.npm,
# ~/.lc-quarantine, ~/.pi/agent/sessions (pi's default session dir: an operator's direct
# `docker exec -u lc little-coder little-coder --print ...` run creates its session there; pi only
# JSON.parses session lines), /tmp, /workspace and the named volumes. What makes each of those
# data and not a load path is checked per path in the cf-lc-upgrade findings ("Load surface"):
# e.g. /tmp holds pi's jiti transpile cache, which IS executed when enabled - hence
# JITI_FS_CACHE=false in the image and in agent.py. A writable dir is only safe while nothing
# loads code or config from it; re-check that on every upstream bump.
# Anything else in $HOME from an earlier run of THIS container (a restart, not a recreate,
# keeps the writable layer) is moved to ~/.lc-quarantine/<ts>/ - never loaded, never deleted.
LC_HOME=/home/lc
HOME_DATA_DIRS=".little-coder .cache .npm .lc-quarantine"
HOME_SKELETON=".bashrc .profile .bash_logout"
PI_SESSIONS="$LC_HOME/.pi/agent/sessions"
PI_PKG="$NPM_GLOBAL_ROOT/little-coder/node_modules/@earendil-works/pi-coding-agent/package.json"
PI_VERSION="$(node -p "require('$PI_PKG').version" 2>/dev/null || echo unknown)"
QUAR="$LC_HOME/.lc-quarantine/$(date +%Y%m%dT%H%M%S)"
in_list() { case " $2 " in *" $1 "*) return 0;; esac; return 1; }
# Our own generated files are regenerated below; drop them first so a restart does not
# quarantine (and so copy around) models.json, which carries the substituted API key.
MODELS_TMP="$(mktemp)"
cp "$LC_HOME/.config/little-coder/models.json" "$MODELS_TMP" 2>/dev/null || true
rm -f "$LC_HOME/.config/little-coder/models.json" "$LC_HOME/.pi/agent/settings.json" \
      "$LC_HOME/.pi/agent/auth.json"
# pi's default session dir is data (the operator's direct-run sessions): carry it across the
# quarantine sweep instead of moving it aside with the rest of ~/.pi.
SESS_TMP=""
if [ -d "$PI_SESSIONS" ] && [ ! -L "$PI_SESSIONS" ]; then
  SESS_TMP="$(mktemp -d)" && mv "$PI_SESSIONS" "$SESS_TMP/sessions"
fi
for entry in "$LC_HOME"/.[!.]* "$LC_HOME"/*; do
  [ -e "$entry" ] || continue
  name="$(basename "$entry")"
  in_list "$name" "$HOME_DATA_DIRS $HOME_SKELETON" && continue
  # a leftover dir holding only (empty) dirs is our own earlier lock - nothing to keep
  if [ -d "$entry" ] && [ -z "$(find "$entry" ! -type d 2>/dev/null | head -n 1)" ]; then
    rm -rf "$entry"; continue
  fi
  mkdir -p "$QUAR" && mv "$entry" "$QUAR/$name" \
    && echo "[entrypoint] quarantined earlier content: ~/$name -> $QUAR/$name"
done
mkdir -p "$LC_HOME/.pi/agent" "$LC_HOME/.config/little-coder" "$LC_HOME/.agents" \
         "$LC_HOME/.node_modules" "$LC_HOME/.node_libraries"
# settings.json pre-stamped with exactly what the launcher's step 8 merges in, so it never
# needs to write; auth.json present so pi never tries to create it.
printf '{\n  "quietStartup": true,\n  "lastChangelogVersion": "%s"\n}\n' "$PI_VERSION" \
  > "$LC_HOME/.pi/agent/settings.json"
printf '{}\n' > "$LC_HOME/.pi/agent/auth.json"
cp "$MODELS_TMP" "$LC_HOME/.config/little-coder/models.json" 2>/dev/null || true
rm -f "$MODELS_TMP"
for d in $HOME_DATA_DIRS; do mkdir -p "$LC_HOME/$d"; chown -R lc:lc "$LC_HOME/$d"; done
for entry in "$LC_HOME"/.[!.]* "$LC_HOME"/*; do
  [ -e "$entry" ] || continue
  in_list "$(basename "$entry")" "$HOME_DATA_DIRS" && continue
  chown -R root:root "$entry"
  find "$entry" -type d -exec chmod 0555 {} +
  find "$entry" -type f -exec chmod 0444 {} +
done
chown root:root "$LC_HOME" && chmod 0755 "$LC_HOME"
# The session dir is created INSIDE the locked ~/.pi/agent (root 0555), so lc can write session
# files in it but can neither rename it nor put anything else beside it.
if [ -n "$SESS_TMP" ]; then mv "$SESS_TMP/sessions" "$PI_SESSIONS"; rmdir "$SESS_TMP"; fi
mkdir -p "$PI_SESSIONS" && chown -R lc:lc "$PI_SESSIONS" && chmod 0755 "$PI_SESSIONS"
echo "[entrypoint] load surface locked: ~ root-owned read-only except ~/.little-coder ~/.cache ~/.npm ~/.lc-quarantine ~/.pi/agent/sessions; user extensions dir: ${LITTLE_CODER_EXTENSIONS_DIR:-UNSET}; jiti fs cache: ${JITI_FS_CACHE:-UNSET}"

# ao-dauth round 3: the control daemon starts as ROOT and drops to `lc` itself, right after reading
# its token from the root-only secret file /etc/lc-secret/token (littlecoder/daemon_auth.py,
# take_root_secret). The token is never in any environment, and the daemon is never readable by lc
# while it holds the file's content. Everything else (lc-mcpo, a direct command) drops here as before.
if [ "$1" = "lc-daemon" ]; then
  exec "$@"
fi
exec gosu lc "$@"
