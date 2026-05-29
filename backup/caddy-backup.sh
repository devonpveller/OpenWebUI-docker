#!/bin/sh
# Nightly backup of caddy-data (ACME state + access logs + OCSP staples).
# Produces a timestamped tar.gz and a matching sha256 sentinel.
set -eu

TS=$(date -u +%Y%m%dT%H%M%SZ)
OUT="${BACKUP_DIR}/caddy-${TS}.tar.gz"

cd "${DATA_DIR}"
tar czf "${OUT}" .
sha256sum "${OUT}" > "${OUT}.sha256"

find "${BACKUP_DIR}" -name 'caddy-*.tar.gz'        -mtime "+${RETAIN_DAYS}" -delete
find "${BACKUP_DIR}" -name 'caddy-*.tar.gz.sha256' -mtime "+${RETAIN_DAYS}" -delete

echo "[$(date -u +%FT%TZ)] Backed up to ${OUT}"
