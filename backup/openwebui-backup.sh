#!/bin/sh
set -eu

# OpenWebUI nightly backup script
# Creates a compressed tarball of the /app/backend/data volume and prunes old backups.

BACKUP_DIR="${BACKUP_DIR:-/backups}"
DATA_DIR="${DATA_DIR:-/data}"
RETAIN_DAYS="${RETAIN_DAYS:-7}"
TIMESTAMP="$(date -u +%Y%m%d-%H%M%S)"
BACKUP_FILE="${BACKUP_DIR}/openwebui-backup-${TIMESTAMP}.tar.gz"

echo "[$(date -u +%FT%TZ)] Starting OpenWebUI backup..."

mkdir -p "${BACKUP_DIR}"

tar czf "${BACKUP_FILE}" -C "${DATA_DIR}" .

BACKUP_SIZE="$(du -h "${BACKUP_FILE}" | cut -f1)"
echo "[$(date -u +%FT%TZ)] Backup created: ${BACKUP_FILE} (${BACKUP_SIZE})"

find "${BACKUP_DIR}" -name "openwebui-backup-*.tar.gz" -mtime "+${RETAIN_DAYS}" -type f | while read -r old; do
    rm -f "${old}"
    echo "[$(date -u +%FT%TZ)] Pruned old backup: ${old}"
done

TOTAL="$(find "${BACKUP_DIR}" -name "openwebui-backup-*.tar.gz" -type f | wc -l)"
echo "[$(date -u +%FT%TZ)] Backup complete. ${TOTAL} backup(s) retained."