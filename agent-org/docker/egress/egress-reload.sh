#!/bin/sh
# ao-git-egress reload wrapper (agent-org). The agent-bridge OWNS the allowlist file — it writes
# /egress/egress-allowlist.txt whenever a project is onboarded (/project add) or a host is allowed
# (/egress). This wrapper runs tinyproxy and SIGHUPs it whenever that file changes, so the worker
# git-egress SCOPE is remotely mutable via Mattermost with no container rebuild (governance §5).
set -eu

DIR=/egress
FILTER="$DIR/egress-allowlist.txt"

# The bridge runs non-root; make the shared volume writable for it, and seed a minimal fail-closed
# allowlist so tinyproxy can boot before the bridge's first write.
mkdir -p "$DIR"
chmod 0777 "$DIR" 2>/dev/null || true
if [ ! -s "$FILTER" ]; then
  printf '%s\n' \
    '# seeded by ao-git-egress until agent-bridge writes the real allowlist' \
    '^(.*\.)?github\.com$' \
    '^(.*\.)?githubusercontent\.com$' > "$FILTER"
fi
chmod 0666 "$FILTER" 2>/dev/null || true

tinyproxy -d -c /etc/tinyproxy/tinyproxy.conf &
TP=$!

# Poll the allowlist mtime and HUP tinyproxy to reload its filter when the bridge rewrites it.
# Polling (vs inotify) keeps this dependency-free across busybox builds; a ~3s lag is fine.
last=""
while kill -0 "$TP" 2>/dev/null; do
  cur=$(stat -c %Y "$FILTER" 2>/dev/null || echo 0)
  if [ -n "$last" ] && [ "$cur" != "$last" ]; then
    kill -HUP "$TP" 2>/dev/null || true
  fi
  last="$cur"
  sleep 3
done
wait "$TP"
