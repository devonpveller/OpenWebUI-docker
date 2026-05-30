#!/bin/sh
# Nightly backup of caddy-data (ACME state + access logs + OCSP staples).
# Produces a timestamped tar.gz and a matching sha256 sentinel.
#
# Precheck: caddy must be answering on its public port (default :80) so we
# don't snapshot a half-written ACME state from a crashing container.
#
# Inputs:
#   DATA_DIR        (default /data)
#   BACKUP_DIR      (default /backups)
#   RETAIN_COUNT    (default 2)
#   HEALTH_TCP      (default caddy:80; empty to skip the probe)

set -eu

DATA_DIR="${DATA_DIR:-/data}"
BACKUP_DIR="${BACKUP_DIR:-/backups}"
RETAIN_COUNT="${RETAIN_COUNT:-2}"
HEALTH_TCP="${HEALTH_TCP:-caddy:80}"

mkdir -p "${BACKUP_DIR}"

# --- precheck ---------------------------------------------------------
if [ ! -d "${DATA_DIR}" ] || [ -z "$(ls -A "${DATA_DIR}" 2>/dev/null)" ]; then
  echo "[$(date -u +%FT%TZ)] caddy PRECHECK SKIP: ${DATA_DIR} missing/empty"
  exit 0
fi
if [ -n "${HEALTH_TCP}" ]; then
  host="${HEALTH_TCP%:*}"
  port="${HEALTH_TCP##*:}"
  if ! nc -z -w 5 "${host}" "${port}" 2>/dev/null; then
    echo "[$(date -u +%FT%TZ)] caddy PRECHECK SKIP: ${HEALTH_TCP} unreachable -- service unhealthy or down"
    exit 0
  fi
fi

# --- backup -----------------------------------------------------------
TS=$(date -u +%Y%m%dT%H%M%SZ)
OUT="${BACKUP_DIR}/caddy-${TS}.tar.gz"

cd "${DATA_DIR}"
tar czf "${OUT}" .
sha256sum "${OUT}" > "${OUT}.sha256"

# --- retention --------------------------------------------------------
ls -1t "${BACKUP_DIR}/caddy-"*.tar.gz 2>/dev/null \
  | tail -n +$((RETAIN_COUNT + 1)) \
  | while IFS= read -r old; do
      rm -f "${old}" "${old}.sha256"
      echo "[$(date -u +%FT%TZ)] caddy pruned: ${old}"
    done

echo "[$(date -u +%FT%TZ)] Backed up to ${OUT} (retain=${RETAIN_COUNT})"
