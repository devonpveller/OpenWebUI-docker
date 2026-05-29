#!/bin/sh
# Nightly backup of authelia-data (sqlite + notifications + JSON log).
# Loss of this volume invalidates all TOTP/WebAuthn enrollments.
set -eu

TS=$(date -u +%Y%m%dT%H%M%SZ)
OUT="${BACKUP_DIR}/authelia-${TS}.tar.gz"

cd "${DATA_DIR}"
tar czf "${OUT}" .
sha256sum "${OUT}" > "${OUT}.sha256"

find "${BACKUP_DIR}" -name 'authelia-*.tar.gz'        -mtime "+${RETAIN_DAYS}" -delete
find "${BACKUP_DIR}" -name 'authelia-*.tar.gz.sha256' -mtime "+${RETAIN_DAYS}" -delete

echo "[$(date -u +%FT%TZ)] Backed up to ${OUT}"
