#!/bin/sh
set -eu

# Mnemory nightly backup script
# Creates a compressed tarball of the /data volume and prunes old backups.

BACKUP_DIR="${BACKUP_DIR:-/backups}"
DATA_DIR="${DATA_DIR:-/data}"
RETAIN_DAYS="${RETAIN_DAYS:-7}"
TIMESTAMP="$(date -u +%Y%m%d-%H%M%S)"
BACKUP_FILE="${BACKUP_DIR}/mnemory-backup-${TIMESTAMP}.tar.gz"

echo "[$(date -u +%FT%TZ)] Starting mnemory backup..."

# Ensure backup directory exists
mkdir -p "${BACKUP_DIR}"

# Create compressed tarball of the entire data directory.
# The mnemory container is paused during backup via docker-compose
# depends_on, or the data is read from a snapshot-safe volume mount.
# SQLite WAL mode and Qdrant embedded both handle concurrent reads safely.
tar czf "${BACKUP_FILE}" -C "${DATA_DIR}" .

BACKUP_SIZE="$(du -h "${BACKUP_FILE}" | cut -f1)"
echo "[$(date -u +%FT%TZ)] Backup created: ${BACKUP_FILE} (${BACKUP_SIZE})"

# Prune backups older than RETAIN_DAYS
PRUNED=0
find "${BACKUP_DIR}" -name "mnemory-backup-*.tar.gz" -mtime "+${RETAIN_DAYS}" -type f | while read -r old; do
    rm -f "${old}"
    echo "[$(date -u +%FT%TZ)] Pruned old backup: ${old}"
    PRUNED=$((PRUNED + 1))
done

TOTAL="$(find "${BACKUP_DIR}" -name "mnemory-backup-*.tar.gz" -type f | wc -l)"
echo "[$(date -u +%FT%TZ)] Backup complete. ${TOTAL} backup(s) retained."
