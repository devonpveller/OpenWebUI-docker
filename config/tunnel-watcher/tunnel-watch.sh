#!/bin/sh
# config/tunnel-watcher/tunnel-watch.sh
#
# Polls cloudflared's /ready endpoint on a fixed interval. If the tunnel
# is down for >= FAILURES_BEFORE_ALERT consecutive checks (default 3 =
# 90s by default), fires a HIGH alert via portal-alerter. When the
# tunnel recovers, fires an INFO event so the operator knows it's back.
#
# This catches the "silent failure" mode where cloudflared is running
# but its connections to Cloudflare's edge are unhealthy -- e.g. token
# expired, network partition, Cloudflare incident.
#
# Inputs (env):
#   CLOUDFLARED_URL          Default http://cloudflared:2000/ready
#   ALERTER_URL              Default http://portal-alerter:8080/alert
#   POLL_SEC                 Default 30  (seconds between checks)
#   FAILURES_BEFORE_ALERT    Default 3   (consecutive failures before HIGH)

set -eu

CLOUDFLARED_URL="${CLOUDFLARED_URL:-http://cloudflared:2000/ready}"
ALERTER_URL="${ALERTER_URL:-http://portal-alerter:8080/alert}"
POLL_SEC="${POLL_SEC:-30}"
FAILURES_BEFORE_ALERT="${FAILURES_BEFORE_ALERT:-3}"

log() { echo "[$(date -u +%FT%TZ)] $*"; }

post_alert() {
  sev="$1"; ev="$2"; ll="$3"
  body=$(jq -n --arg s "$sev" --arg e "$ev" --arg l "$ll" \
    '{severity:$s, event:$e, log_line:$l}')
  if ! curl -fsS --max-time 5 -X POST -H 'Content-Type: application/json' \
       --data "$body" "$ALERTER_URL" >/dev/null 2>&1; then
    log "alerter POST failed (event=$ev)"
  fi
}

probe() {
  # /ready returns JSON with `{"connectorId":"...","connections":[...]}`
  # on success and 503 on failure. We treat anything non-2xx as down.
  curl -fsS --max-time 5 "$CLOUDFLARED_URL" 2>/dev/null
}

log "tunnel-watcher starting; probing $CLOUDFLARED_URL every ${POLL_SEC}s"

consecutive_failures=0
alerted_down=0    # 1 once we've fired the down alert, prevents re-alerting
initial_up=0     # 0 until first successful probe -- log once we're up
probe_count=0
HEARTBEAT_EVERY=${HEARTBEAT_EVERY:-120}   # log "still alive" every N probes (60min default)

while :; do
  probe_count=$((probe_count + 1))
  if probe_out=$(probe); then
    # Tunnel UP
    if [ "$initial_up" = "0" ]; then
      log "first successful probe: $(echo "$probe_out" | head -c 200)"
      initial_up=1
    fi
    if [ "$alerted_down" = "1" ]; then
      log "tunnel recovered"
      post_alert info cloudflared.tunnel.recovered "Tunnel recovered after ${consecutive_failures} failed probes. Response: $(echo "$probe_out" | head -c 200)"
      alerted_down=0
    fi
    consecutive_failures=0
    # Periodic heartbeat so silence isn't ambiguous.
    if [ $((probe_count % HEARTBEAT_EVERY)) = 0 ]; then
      log "heartbeat: ${probe_count} probes performed, tunnel up"
    fi
  else
    consecutive_failures=$((consecutive_failures + 1))
    log "probe failed (consecutive=$consecutive_failures threshold=$FAILURES_BEFORE_ALERT)"
    if [ "$consecutive_failures" -ge "$FAILURES_BEFORE_ALERT" ] && [ "$alerted_down" = "0" ]; then
      post_alert high cloudflared.tunnel.down "Cloudflare tunnel /ready failed ${consecutive_failures} consecutive probes (${POLL_SEC}s each). Portal may be unreachable from the internet."
      alerted_down=1
    fi
  fi
  sleep "$POLL_SEC"
done
