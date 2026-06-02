#!/bin/sh
# config/auth-notification-bridge/bridge.sh
#
# Tails Authelia's filesystem notifier output and forwards each completed
# record to portal-alerter as an /alert. This closes the gap left by
# Authelia's auth-net (internal: true) network isolation -- user-facing
# notifications (2FA enrollment OTPs, password resets, account-change
# confirmations) now reach the operator's Gmail via the same OAuth path
# that the security alerts use.
#
# At-most-once semantics: we use `tail -F -n 0` so we start at end-of-file.
# Records written BEFORE the bridge started, and records interrupted by a
# bridge restart, are NOT forwarded. That's intentional -- duplicating
# already-delivered OTPs is worse than missing one (user clicks "Resend").
#
# Inputs (env):
#   NOTIF_FILE       Default /data/notification.txt
#   ALERTER_URL      Default http://portal-alerter:8080/alert
#   ALERT_SEVERITY   Default info
#   ALERT_EVENT      Default authelia.user-notification

set -eu

NOTIF_FILE="${NOTIF_FILE:-/data/notification.txt}"
ALERTER_URL="${ALERTER_URL:-http://portal-alerter:8080/alert}"
ALERT_SEVERITY="${ALERT_SEVERITY:-info}"
ALERT_EVENT="${ALERT_EVENT:-authelia.user-notification}"

log() { echo "[$(date -u +%FT%TZ)] $*"; }

post_record() {
  rec="$1"
  # Extract Subject and Recipient lines for the alert metadata. Body is
  # the full record minus the trailing "Powered by Authelia" tag-line.
  subject=$(echo "$rec" | awk -F': ' '/^Subject:/ { sub(/^Subject: /,""); print; exit }')
  recipient=$(echo "$rec" | awk -F': ' '/^Recipient:/ { sub(/^Recipient: /,""); print; exit }')
  # log_line gets the subject + the most recent few lines of the body so
  # the alert email contains the actual code/link. Trim to ~2000 chars to
  # stay well under any alerter payload limits.
  body=$(echo "$rec" | sed '/^Powered by Authelia/d')
  log_line="Subject: ${subject} | Recipient: ${recipient} | Body: $(echo "$body" | head -c 2000)"

  # Build JSON payload using jq for safe escaping.
  payload=$(jq -n \
    --arg sev "$ALERT_SEVERITY" \
    --arg ev  "$ALERT_EVENT" \
    --arg ll  "$log_line" \
    '{severity:$sev, event:$ev, log_line:$ll}')

  # Post. wget --post-data is busybox-portable. We need wget 1.x with
  # --post-data (alpine 3.21 has it).
  response=$(echo "$payload" | wget -q -O - \
    --header='Content-Type: application/json' \
    --post-data="$payload" \
    "$ALERTER_URL" 2>&1) || {
      log "FORWARD FAILED (alerter unreachable or 4xx/5xx) subject='${subject}'"
      log "  alerter response: ${response}"
      return 1
    }
  log "FORWARDED subject='${subject}' alerter_response='${response}'"
}

# Wait for the notification file to exist. Authelia creates it on first
# notification, which may be after bridge container start.
wait_count=0
while [ ! -f "$NOTIF_FILE" ]; do
  log "waiting for $NOTIF_FILE to appear (${wait_count}s elapsed)"
  wait_count=$((wait_count + 5))
  sleep 5
done

log "watching $NOTIF_FILE -> $ALERTER_URL (severity=$ALERT_SEVERITY event=$ALERT_EVENT)"

# Stream-process the tail. Accumulate lines until we see the per-record
# trailer "Powered by Authelia", then forward and reset.
rec=""
tail -F -n 0 "$NOTIF_FILE" 2>/dev/null | while IFS= read -r line; do
  rec="${rec}${line}
"
  case "$line" in
    "Powered by Authelia"*)
      # Complete record -- forward and reset.
      post_record "$rec" || true
      rec=""
      ;;
  esac
done
