#!/bin/sh
# Mnemory nightly backup script
# Creates a compressed tarball of the /data volume and prunes old backups
# (count-based retention).
#
# Precheck: mnemory's HTTP gateway must be reachable; if it's down, sqlite
# WAL may be mid-flush and tarring captures a non-recoverable state.
#
# Inputs:
#   DATA_DIR        (default /data)
#   BACKUP_DIR      (default /backups)
#   RETAIN_COUNT    (default 2)
#   HEALTH_TCP      (default mnemory-cloud-gateway:8060; empty to skip)

set -eu

BACKUP_DIR="${BACKUP_DIR:-/backups}"
DATA_DIR="${DATA_DIR:-/data}"
RETAIN_COUNT="${RETAIN_COUNT:-2}"
# 8060, not 8080: the gateway serves /health on 8060. The 8080 default made the
# precheck skip (exit 0, no artifact, no alert) nightly from 2026-05-29 to 07-05.
HEALTH_TCP="${HEALTH_TCP:-mnemory-cloud-gateway:8060}"
TIMESTAMP="$(date -u +%Y%m%d-%H%M%S)"
BACKUP_FILE="${BACKUP_DIR}/mnemory-backup-${TIMESTAMP}.tar.gz"

echo "[$(date -u +%FT%TZ)] Starting mnemory backup..."

mkdir -p "${BACKUP_DIR}"

# --- precheck ---------------------------------------------------------
if [ ! -d "${DATA_DIR}" ] || [ -z "$(ls -A "${DATA_DIR}" 2>/dev/null)" ]; then
  echo "[$(date -u +%FT%TZ)] mnemory PRECHECK SKIP: ${DATA_DIR} missing/empty"
  exit 0
fi
if [ -n "${HEALTH_TCP}" ]; then
  host="${HEALTH_TCP%:*}"
  port="${HEALTH_TCP##*:}"
  if ! nc -z -w 5 "${host}" "${port}" 2>/dev/null; then
    echo "[$(date -u +%FT%TZ)] mnemory PRECHECK SKIP: ${HEALTH_TCP} unreachable -- service unhealthy or down"
    exit 0
  fi
fi

# --- backup -----------------------------------------------------------
# SQLite WAL mode and Qdrant embedded both handle concurrent reads safely.
tar czf "${BACKUP_FILE}" -C "${DATA_DIR}" .
sha256sum "${BACKUP_FILE}" > "${BACKUP_FILE}.sha256"

BACKUP_SIZE="$(du -h "${BACKUP_FILE}" | cut -f1)"
echo "[$(date -u +%FT%TZ)] Backup created: ${BACKUP_FILE} (${BACKUP_SIZE})"

# --- retention: keep N most recent ------------------------------------
ls -1t "${BACKUP_DIR}/mnemory-backup-"*.tar.gz 2>/dev/null \
  | tail -n +$((RETAIN_COUNT + 1)) \
  | while IFS= read -r old; do
      rm -f "${old}" "${old}.sha256"
      echo "[$(date -u +%FT%TZ)] Pruned old backup: ${old}"
    done

TOTAL="$(ls -1 "${BACKUP_DIR}/mnemory-backup-"*.tar.gz 2>/dev/null | wc -l)"
echo "[$(date -u +%FT%TZ)] Backup complete. ${TOTAL} backup(s) retained (retain=${RETAIN_COUNT})."
