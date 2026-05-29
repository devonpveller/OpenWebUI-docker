#!/bin/sh
# authelia-watch.sh — tails Authelia + Caddy logs and POSTs alerts to portal-alerter.
#
# Triggers (plan §8 Step 6):
#   - >=5 authentication.failed from same IP within 5 min      → severity high
#   - authentication.success from IP not in known-ips.txt      → severity high
#   - regulation ban applied                                   → severity medium
#   - WebAuthn / TOTP credential added or removed              → severity high
#   - Authelia config_file_loaded                              → severity medium
#   - Caddy access log >=10 × 401 from same IP within 1 min    → severity medium
#
# This script has NO Gmail/OAuth knowledge. It only constructs JSON envelopes
# and POSTs them to $ALERTER_URL. The alerter container holds the OAuth token.
#
# Inputs:
#   $ALERTER_URL                  Default http://portal-alerter:8080/alert
#   /logs/authelia/authelia.log   Authelia structured log (JSON, one event per line)
#   /logs/caddy/caddy-access.log  Caddy access log (JSON, one event per line)
#   /data/known-ips.txt           Known source IPs, one per line
#
# Required packages (installed by entrypoint before exec):
#   curl, jq, inotify-tools

set -eu

ALERTER_URL="${ALERTER_URL:-http://portal-alerter:8080/alert}"
KNOWN_IPS_FILE="${KNOWN_IPS_FILE:-/data/known-ips.txt}"
AUTHELIA_LOG="${AUTHELIA_LOG:-/logs/authelia/authelia.log}"
CADDY_LOG="${CADDY_LOG:-/logs/caddy/caddy-access.log}"

# Rolling-window counters (in-memory; reset on container restart).
FAIL_BURST_DIR=/tmp/fail-bursts
FOUR01_BURST_DIR=/tmp/four01-bursts
mkdir -p "$FAIL_BURST_DIR" "$FOUR01_BURST_DIR"

now_unix() { date -u +%s; }
now_iso()  { date -u +%FT%TZ; }

post_alert() {
  severity="$1"
  event="$2"
  source_ip="$3"
  username="$4"
  log_line="$5"
  body=$(jq -n \
    --arg sev "$severity" \
    --arg evt "$event" \
    --arg ip  "$source_ip" \
    --arg usr "$username" \
    --arg ts  "$(now_iso)" \
    --arg ln  "$log_line" \
    '{severity:$sev, event:$evt, source_ip:$ip, username:$usr, timestamp_utc:$ts, log_line:$ln}')
  if ! curl -fsS -X POST -H 'Content-Type: application/json' --data "$body" "$ALERTER_URL" >/dev/null 2>&1; then
    echo "[$(now_iso)] alerter POST failed for event=$event source_ip=$source_ip" >&2
  fi
}

# Returns 0 if $1 is in known-ips.txt, 1 otherwise.
ip_is_known() {
  ip="$1"
  [ -f "$KNOWN_IPS_FILE" ] || return 1
  grep -qx "$ip" "$KNOWN_IPS_FILE"
}

# Increment a per-IP counter file with timestamp; return current count within window.
# Args: dir, ip, window_seconds
increment_burst() {
  dir="$1"; ip="$2"; window="$3"
  [ -z "$ip" ] && { echo 0; return; }
  safe_ip=$(echo "$ip" | tr -c '[:alnum:].' '_')
  f="$dir/$safe_ip"
  now=$(now_unix)
  # Append timestamp line, then prune older than window.
  echo "$now" >> "$f"
  cutoff=$((now - window))
  awk -v c="$cutoff" '$1 >= c' "$f" > "$f.tmp" && mv "$f.tmp" "$f"
  wc -l < "$f"
}

handle_authelia_event() {
  line="$1"
  msg=$(echo "$line"     | jq -r '.msg     // empty' 2>/dev/null || true)
  ip=$(echo "$line"      | jq -r '.remote_ip // empty' 2>/dev/null || true)
  user=$(echo "$line"    | jq -r '.username // empty' 2>/dev/null || true)
  ml=$(echo "$msg" | tr 'A-Z' 'a-z')

  case "$ml" in
    *"unsuccessful 1fa"*|*"unsuccessful 2fa"*)
      n=$(increment_burst "$FAIL_BURST_DIR" "$ip" 300)
      if [ "$n" -ge 5 ]; then
        post_alert high authentication.failed.burst "$ip" "$user" "$line"
        # Reset counter to avoid spamming
        safe_ip=$(echo "$ip" | tr -c '[:alnum:].' '_')
        : > "$FAIL_BURST_DIR/$safe_ip"
      fi
      ;;
    *"successful 1fa"*|*"successful 2fa"*)
      if [ -n "$ip" ] && ! ip_is_known "$ip"; then
        post_alert high authentication.success.new_ip "$ip" "$user" "$line"
      fi
      ;;
    *"banned"*)
      post_alert medium regulation.ban "$ip" "$user" "$line"
      ;;
    *"webauthn"*)
      post_alert high credential.webauthn.change "$ip" "$user" "$line"
      ;;
    *"totp"*)
      post_alert high credential.totp.change "$ip" "$user" "$line"
      ;;
    *"config_file_loaded"*|*"configuration reloaded"*)
      post_alert medium authelia.config.reload "$ip" "$user" "$line"
      ;;
  esac
}

handle_caddy_event() {
  line="$1"
  status=$(echo "$line" | jq -r '.status // 0' 2>/dev/null || echo 0)
  if [ "$status" = "401" ]; then
    ip=$(echo "$line" | jq -r '.request.headers."X-Forwarded-For"[0] // .request.remote_ip // empty' 2>/dev/null || true)
    [ -z "$ip" ] && return 0
    n=$(increment_burst "$FOUR01_BURST_DIR" "$ip" 60)
    if [ "$n" -ge 10 ]; then
      post_alert medium caddy.401.burst "$ip" "" "$line"
      safe_ip=$(echo "$ip" | tr -c '[:alnum:].' '_')
      : > "$FOUR01_BURST_DIR/$safe_ip"
    fi
  fi
}

tail_log() {
  # Robust against log rotation. inotifywait re-opens on MOVE_SELF or DELETE_SELF.
  path="$1"; handler="$2"
  while :; do
    if [ ! -f "$path" ]; then
      sleep 5
      continue
    fi
    # Start tail in background; kill it when rotation is detected.
    tail -F -n 0 "$path" 2>/dev/null | while IFS= read -r line; do
      [ -z "$line" ] && continue
      "$handler" "$line"
    done
  done
}

echo "[$(now_iso)] authelia-watcher starting; alerter target: $ALERTER_URL"
echo "[$(now_iso)] watching: $AUTHELIA_LOG and $CADDY_LOG"

tail_log "$AUTHELIA_LOG" handle_authelia_event &
tail_log "$CADDY_LOG"    handle_caddy_event &
wait
