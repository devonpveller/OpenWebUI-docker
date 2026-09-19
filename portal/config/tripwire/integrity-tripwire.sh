#!/bin/sh
# integrity-tripwire.sh — config-file hash sentinel.
#
# Modes:
#   init   — establish baseline if missing, else verify current vs baseline
#            and alert on drift. Run at container start.
#   check  — verify current vs baseline; alert on drift. Run on cron.
#   accept — overwrite baseline with current hashes. Manual ack after
#            deliberate config change.
#
# Inputs:
#   $ALERTER_URL              Default http://portal-alerter:8080/alert
#   /watch/Caddyfile          Bind-mounted (read-only)
#   /watch/configuration.yml  Bind-mounted (read-only)
#   /watch/users_database.yml Bind-mounted (read-only)
#   /state/baseline.sha256    Persistent volume, owned by this container
#
# The "accept" mode is intentionally manual — config changes that bypass
# operator awareness should fire an alert.

set -eu

MODE="${1:-check}"
ALERTER_URL="${ALERTER_URL:-http://portal-alerter:8080/alert}"
WATCH_DIR=/watch
STATE_DIR=/state
BASELINE="$STATE_DIR/baseline.sha256"

now_iso() { date -u +%FT%TZ; }

compute_hashes() {
  cd "$WATCH_DIR" && \
    find . -maxdepth 1 -type f \( -name 'Caddyfile' -o -name 'configuration.yml' -o -name 'users_database.yml' \) \
      -print0 | sort -z | xargs -0 sha256sum
}

post_alert() {
  event="$1"
  detail="$2"
  # JSON-escape detail before embedding it in log_line. The diff output is
  # multi-line; a literal newline inside a JSON string is an invalid control
  # character and the alerter rejects the whole POST with 400 "Bad control
  # character in string literal" (silently dropping the drift alert). Escape
  # backslash and quote first, then fold tab/CR/newline to their \x escapes.
  escaped=$(printf '%s' "$detail" | head -c 800 \
    | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' -e 's/\t/\\t/g' -e 's/\r//g' \
    | awk 'BEGIN{ORS=""} NR>1{printf "\\n"} {print}')
  body=$(printf '{"severity":"critical","event":"%s","timestamp_utc":"%s","log_line":"%s"}' \
    "$event" "$(now_iso)" "$escaped")
  if ! curl -fsS -X POST -H 'Content-Type: application/json' --data "$body" "$ALERTER_URL" >/dev/null 2>&1; then
    echo "[$(now_iso)] alerter POST failed for event=$event" >&2
  fi
}

ensure_state_dir() {
  mkdir -p "$STATE_DIR"
}

write_baseline() {
  ensure_state_dir
  compute_hashes > "$BASELINE"
  echo "[$(now_iso)] baseline written: $BASELINE"
}

verify_baseline() {
  ensure_state_dir
  current_file=/tmp/current.sha256
  compute_hashes > "$current_file"
  if ! diff -q "$current_file" "$BASELINE" >/dev/null 2>&1; then
    detail=$(diff "$current_file" "$BASELINE" | head -20)
    echo "[$(now_iso)] DRIFT detected:"
    echo "$detail"
    post_alert config.drift "$detail"
    rm -f "$current_file"
    return 1
  fi
  rm -f "$current_file"
  echo "[$(now_iso)] baseline verified"
  return 0
}

case "$MODE" in
  init)
    if [ ! -f "$BASELINE" ]; then
      write_baseline
    else
      verify_baseline || true
    fi
    ;;
  check)
    if [ ! -f "$BASELINE" ]; then
      echo "[$(now_iso)] no baseline found — establishing now"
      write_baseline
    else
      verify_baseline || true
    fi
    ;;
  accept)
    write_baseline
    ;;
  *)
    echo "Unknown mode: $MODE (expected init|check|accept)" >&2
    exit 2
    ;;
esac
