#!/bin/sh
# Tailscale container entrypoint (rewrite of entrypoint.sh, CLEANUP-PLAN v3 D.2).
# Starts tailscaled + joins the tailnet, publishes every HTTPS serve route from
# ONE data-driven route table (each container-target route backed by a local
# socat proxy), and runs a 60s monitor that heals dead socats, performs
# deferred setup for late-starting services, and recovers the tailnet
# connection. Retired routes (ollama, LM Studio) are gone.
#
# Runs under busybox ash (tailscale/tailscale image) -- POSIX sh only.
# shellcheck disable=SC2034  # route fields are read positionally; not every consumer uses every field
# shellcheck disable=SC3040  # busybox ash supports pipefail
set -euo pipefail

TS_SOCK=/tmp/tailscaled.sock
STATE_DIR=/var/lib/tailscale

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*"; }
ts() { tailscale --socket="$TS_SOCK" "$@"; }
probe() { wget -q -T 10 -O /dev/null "http://$1:$2$3"; }
check_network() { ping -c 1 -W 5 8.8.8.8 >/dev/null 2>&1; }

wait_for_network() {
    n=1
    log "Waiting for network connectivity..."
    while [ "$n" -le 30 ]; do
        if check_network; then
            log "Network connectivity established"
            return 0
        fi
        log "WARN: network attempt $n/30 failed, retrying in 5s..."
        sleep 5
        n=$((n + 1))
    done
    log "ERROR: no network connectivity after 30 attempts"
    return 1
}

# Persist tailnet info so other containers (the openwebui admin pipe) can read
# the FQDN/MagicDNS suffix -- STATE_DIR maps to ./data/tailscale on the host,
# which openwebui mounts read-only.
write_tailnet_info() {
    if ts status --json > "$STATE_DIR/tailnet-info.json.tmp" 2>/dev/null \
        && [ -s "$STATE_DIR/tailnet-info.json.tmp" ]; then
        mv "$STATE_DIR/tailnet-info.json.tmp" "$STATE_DIR/tailnet-info.json"
        return 0
    fi
    rm -f "$STATE_DIR/tailnet-info.json.tmp" 2>/dev/null
    return 1
}

# ---------------------------------------------------------------------------
# Route configuration (env-overridable; historical names and defaults kept)
# ---------------------------------------------------------------------------
LLAMA_CPP_HOST=${LLAMA_CPP_HOST:-llama-cpp}
LLAMA_CPP_PORT=${LLAMA_CPP_PORT:-8080}
LLAMA_CPP_ENABLED=${LLAMA_CPP_ENABLED:-true}
LLAMA_CPP_EMBED_HOST=${LLAMA_CPP_EMBED_HOST:-llama-cpp-embed}
LLAMA_CPP_EMBED_PORT=${LLAMA_CPP_EMBED_PORT:-8080}
LLAMA_CPP_EMBED_ENABLED=${LLAMA_CPP_EMBED_ENABLED:-true}
OPEN_NOTEBOOK_HOST=${OPEN_NOTEBOOK_HOST:-open_notebook}
OPEN_NOTEBOOK_PORT=${OPEN_NOTEBOOK_PORT:-8502}
OPEN_NOTEBOOK_ENABLED=${OPEN_NOTEBOOK_ENABLED:-true}
OPEN_NOTEBOOK_TS_PORT=${OPEN_NOTEBOOK_TS_PORT:-8443}
OPEN_NOTEBOOK_API_PORT=${OPEN_NOTEBOOK_API_PORT:-5055}
OPEN_NOTEBOOK_API_TS_PORT=${OPEN_NOTEBOOK_API_TS_PORT:-5055}
QUARTZ_HOST=${QUARTZ_HOST:-openbrain-wiki-viewer}
QUARTZ_PORT=${QUARTZ_PORT:-8080}
QUARTZ_ENABLED=${QUARTZ_ENABLED:-true}
QUARTZ_TS_PORT=${QUARTZ_TS_PORT:-8444}
LITELLM_UI_HOST=${LITELLM_UI_HOST:-llm-gateway-ui}
LITELLM_UI_PORT=${LITELLM_UI_PORT:-8080}
LITELLM_UI_ENABLED=${LITELLM_UI_ENABLED:-true}
LITELLM_UI_TS_PORT=${LITELLM_UI_TS_PORT:-8445}
MATTERMOST_HOST=${MATTERMOST_HOST:-mattermost}
MATTERMOST_PORT=${MATTERMOST_PORT:-8065}
MATTERMOST_ENABLED=${MATTERMOST_ENABLED:-true}
MATTERMOST_TS_PORT=${MATTERMOST_TS_PORT:-8446}

# Route table. Fields:
#   name|enabled|target_host|target_port|ts_port|ts_path|local_port|probe_path|boot_attempts|verify
#   ts_path "/"   = root serve on its own tailnet HTTPS port; anything else = --set-path on :443
#   local_port    = the 127.0.0.1 socat listener the serve mapping points at
#   boot_attempts = probes x 10s at startup; 1 = single try, monitor does deferred setup
#   verify "y"    = confirm the :ts_port mapping in `serve status` after setup and
#                   every monitor cycle (stranded-flag fix, 2026-07-05)
# Per-route state files: /tmp/<name>-serve-configured, /tmp/socat-<name>.{pid,log}
routes() {
    grep -v '^#' <<EOF
# llama-cpp: LiteLLM gateway alias on llm-gateway -- must stay the alias, never a *-upstream real server; 18 attempts covers the 120s model-load start_period
llama-cpp|$LLAMA_CPP_ENABLED|$LLAMA_CPP_HOST|$LLAMA_CPP_PORT|443|/llama-cpp|8235|/health|18|n
# llama-cpp-embed: LiteLLM gateway alias on llm-gateway -- must stay the alias, never a *-upstream real server
llama-cpp-embed|$LLAMA_CPP_EMBED_ENABLED|$LLAMA_CPP_EMBED_HOST|$LLAMA_CPP_EMBED_PORT|443|/llama-cpp-embed|8236|/health|12|n
# open-notebook: Streamlit UI hosts only at root -> own tailnet port
open-notebook|$OPEN_NOTEBOOK_ENABLED|$OPEN_NOTEBOOK_HOST|$OPEN_NOTEBOOK_PORT|$OPEN_NOTEBOOK_TS_PORT|/|8237|/|18|n
# open-notebook-api: the browser derives <proto>://<host>:5055, so the tailnet port must be 5055 (shares OPEN_NOTEBOOK_ENABLED)
open-notebook-api|$OPEN_NOTEBOOK_ENABLED|$OPEN_NOTEBOOK_HOST|$OPEN_NOTEBOOK_API_PORT|$OPEN_NOTEBOOK_API_TS_PORT|/|8238|/api/config|1|n
# quartz: OB1 wiki viewer, separate compose project that starts after us -- deferred setup is the normal path
quartz|$QUARTZ_ENABLED|$QUARTZ_HOST|$QUARTZ_PORT|$QUARTZ_TS_PORT|/|8239|/|1|n
# litellm-ui: master-key'd Admin-UI sidecar, analytics dashboard only -- serves no inference
litellm-ui|$LITELLM_UI_ENABLED|$LITELLM_UI_HOST|$LITELLM_UI_PORT|$LITELLM_UI_TS_PORT|/|8240|/health/liveliness|12|n
# mattermost: agent-org compose project on llm-net, starts after us -- verify serve landed (a lying flag stranded it off the tailnet, 2026-07-05)
mattermost|$MATTERMOST_ENABLED|$MATTERMOST_HOST|$MATTERMOST_PORT|$MATTERMOST_TS_PORT|/|8241|/api/v4/system/ping|1|y
EOF
}

# Start (or restart) the socat proxy for a route and verify it stayed up.
start_socat() { # $1 name  $2 host  $3 port  $4 local_port
    pkill -f "socat.*:$4" || true
    sleep 2
    log "Starting socat proxy: 127.0.0.1:$4 -> $2:$3"
    socat -d -d "TCP-LISTEN:$4,fork,reuseaddr,keepalive" "TCP:$2:$3" > "/tmp/socat-$1.log" 2>&1 &
    echo $! > "/tmp/socat-$1.pid"
    sleep 3
    if ! kill -0 "$(cat "/tmp/socat-$1.pid")" 2>/dev/null; then
        log "ERROR: socat for $1 failed to start"
        cat "/tmp/socat-$1.log" 2>/dev/null || echo "No log file found"
        return 1
    fi
    log "$1 proxy started (PID: $(cat "/tmp/socat-$1.pid"))"
}

# Full route setup: socat proxy + tailscale serve mapping + configured flag.
setup_route() { # $1 name  $2 host  $3 port  $4 ts_port  $5 ts_path  $6 local_port  $7 verify
    start_socat "$1" "$2" "$3" "$6" || return 1
    if [ "$5" = "/" ]; then
        ts serve --https="$4" --bg "http://127.0.0.1:$6" || {
            log "ERROR: tailscale serve --https=$4 failed for $1 -- leaving unconfigured so the monitor retries"
            return 1
        }
    else
        ts serve --https="$4" --set-path="$5" --bg "http://127.0.0.1:$6" || {
            log "ERROR: tailscale serve $5 failed for $1 -- leaving unconfigured so the monitor retries"
            return 1
        }
    fi
    if [ "$7" = "y" ] && ! ts serve status 2>/dev/null | grep -q ":$4 "; then
        log "ERROR: serve mapping :$4 missing after configuration for $1 -- leaving unconfigured so the monitor retries"
        return 1
    fi
    touch "/tmp/$1-serve-configured"
    log "$1 configured: tailnet :$4$5 -> 127.0.0.1:$6 -> $2:$3"
}

# Boot-time setup for one route: wait for the target (boot_attempts x 10s),
# then configure. Falls through to the monitor's deferred setup on failure.
boot_route() { # $1..$10 = one route-table row
    if [ "$2" != "true" ]; then
        log "$1 tailscale integration disabled"
        return 0
    fi
    sleep 2
    log "Configuring $1 serve..."
    i=0
    while [ "$i" -lt "$9" ]; do
        if probe "$3" "$4" "$8"; then
            setup_route "$1" "$3" "$4" "$5" "$6" "$7" "${10}" \
                || log "WARN: $1 setup failed -- monitor loop will retry"
            return 0
        fi
        i=$((i + 1))
        if [ "$i" -lt "$9" ]; then
            log "$1 not ready yet (attempt $i/$9), waiting 10s..."
            sleep 10
        fi
    done
    log "WARN: $1 not reachable -- monitor loop will configure it when it comes online"
}

# Monitor-cycle check for one route: deferred setup if never configured,
# otherwise keep its socat alive (and, when verify=y, its serve mapping).
monitor_route() { # $1 name $2 enabled $3 host $4 port $5 ts_port $6 ts_path $7 local_port $8 probe_path $9 verify
    [ "$2" = "true" ] || return 0
    if [ ! -f "/tmp/$1-serve-configured" ]; then
        if probe "$3" "$4" "$8"; then
            log "$1 is now online, performing deferred setup..."
            setup_route "$1" "$3" "$4" "$5" "$6" "$7" "$9" \
                || log "ERROR: deferred $1 setup failed, will retry next cycle"
        fi
    elif [ -f "/tmp/socat-$1.pid" ]; then
        if ! kill -0 "$(cat "/tmp/socat-$1.pid")" 2>/dev/null; then
            log "WARN: $1 socat proxy has died, restarting..."
            start_socat "$1" "$3" "$4" "$7" || log "ERROR: failed to restart $1 proxy"
        fi
        if [ "$9" = "y" ] && ! ts serve status 2>/dev/null | grep -q ":$5 "; then
            log "WARN: $1 serve mapping :$5 missing -- clearing flag to reconfigure next cycle"
            rm -f "/tmp/$1-serve-configured"
        fi
    fi
}

# Tailscale reconnection recovery: re-auth, then re-add whatever serve
# mappings went missing (full reset only if the root OpenWebUI serve is gone).
recover_connection() {
    ts up \
      --auth-key="${TAILSCALE_AUTH_KEY}" \
      --hostname="${TS_HOSTNAME:-openwebui}" \
      --accept-dns="${TS_ACCEPT_DNS:-false}" || true
    sleep 10
    serve_now=$(ts serve status 2>/dev/null || true)
    if ! echo "$serve_now" | grep -q "127.0.0.1:8080"; then
        log "Root serve missing -- reconfiguring serve from scratch..."
        ts serve reset || true
        ts serve --https=443 --bg http://127.0.0.1:8080 || true
        serve_now=""
    fi
    routes | while IFS='|' read -r name en host port tsport tspath lport ppath attempts verify; do
        [ "$en" = "true" ] || continue
        if ! echo "$serve_now" | grep -q "127.0.0.1:$lport"; then
            if probe "$host" "$port" "$ppath"; then
                log "Re-adding missing $name serve configuration..."
                rm -f "/tmp/$name-serve-configured"
                setup_route "$name" "$host" "$port" "$tsport" "$tspath" "$lport" "$verify" \
                    || log "ERROR: failed to re-add $name serve"
            fi
        fi
    done
}

# ---------------------------------------------------------------------------
# 1) Fresh socket, wait for network (handles container-restart races)
# ---------------------------------------------------------------------------
log "Starting Tailscale with autonomous management..."
rm -f "$TS_SOCK"
if ! wait_for_network; then
    log "ERROR: cannot proceed without network connectivity"
    exit 1
fi

# 2) tailscaled with persistent state (userspace networking: shared netns)
/usr/local/bin/tailscaled \
  --socket="$TS_SOCK" \
  --statedir="$STATE_DIR" \
  --tun=userspace-networking &

log "Waiting for tailscaled socket..."
n=15
while [ ! -S "$TS_SOCK" ] && [ "$n" -gt 0 ]; do
    sleep 1
    n=$((n - 1))
done
if [ ! -S "$TS_SOCK" ]; then
    log "ERROR: $TS_SOCK not found after 15 seconds"
    ps aux | grep tailscaled || true
    ls -la /tmp/ || true
    exit 1
fi
log "tailscaled socket ready"

# 3) Join the tailnet (reuses the persisted device identity)
log "Connecting to Tailscale network..."
ts up \
  --auth-key="${TAILSCALE_AUTH_KEY}" \
  --hostname="${TS_HOSTNAME:-openwebui}" \
  --accept-dns="${TS_ACCEPT_DNS:-false}"

n=30
while [ "$n" -gt 0 ]; do
    if ts status >/dev/null 2>&1; then
        log "Tailscale connected"
        break
    fi
    sleep 2
    n=$((n - 2))
done
if [ "$n" -le 0 ]; then
    log "WARN: Tailscale connection timeout, continuing anyway..."
fi

write_tailnet_info && log "Wrote $STATE_DIR/tailnet-info.json"

# 4) Serve routes: reset once, root OpenWebUI, then the table
log "Configuring Tailscale serve..."
ts serve reset
# OpenWebUI shares this network namespace -- direct, no socat, root of :443
ts serve --https=443 --bg http://127.0.0.1:8080

routes | while IFS='|' read -r name en host port tsport tspath lport ppath attempts verify; do
    boot_route "$name" "$en" "$host" "$port" "$tsport" "$tspath" "$lport" "$ppath" "$attempts" "$verify"
done

log "Tailscale serve configured; enabled tailnet routes:"
log "  - openwebui: https://<tailnet-host>/ -> 127.0.0.1:8080"
routes | while IFS='|' read -r name en host port tsport tspath lport ppath attempts verify; do
    [ "$en" = "true" ] || continue
    if [ "$tspath" = "/" ]; then
        log "  - $name: https://<tailnet-host>:$tsport/ -> $host:$port"
    else
        log "  - $name: https://<tailnet-host>$tspath -> $host:$port"
    fi
done

# 5) Background monitor: heal socats, deferred routes, tailnet connection
(
    log "Starting autonomous monitoring..."
    while true; do
        sleep 60
        routes | while IFS='|' read -r name en host port tsport tspath lport ppath attempts verify; do
            monitor_route "$name" "$en" "$host" "$port" "$tsport" "$tspath" "$lport" "$ppath" "$verify"
        done
        # Keep tailnet-info fresh for the openwebui admin pipe
        write_tailnet_info >/dev/null 2>&1 || true
        if ! check_network; then
            log "WARN: network connectivity lost, container may need restart"
            continue
        fi
        if ! ts status >/dev/null 2>&1; then
            log "WARN: Tailscale disconnected, attempting reconnection..."
            recover_connection
        fi
    done
) &

log "Tailscale autonomous setup complete"
ts status || log "WARN: status check failed"
ts serve status || log "WARN: serve status check failed"

# Keep the container running
tail -f /dev/null
