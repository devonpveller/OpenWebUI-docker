#!/bin/sh
set -eu

# Mnemory restore script
# Restores a backup tarball into the data directory.
#
# Usage:
#   docker run --rm \
#     -v mnemory-data:/data \
#     -v ./backups:/backups:ro \
#     alpine sh -c "apk add --no-cache tar && sh /backups/restore.sh /backups/mnemory-backup-YYYYMMDD-HHMMSS.tar.gz"
#
# IMPORTANT: Stop the mnemory container before restoring!
#   docker compose stop mnemory
#   <run restore>
#   docker compose start mnemory

BACKUP_FILE="${1:-}"
DATA_DIR="${DATA_DIR:-/data}"

if [ -z "${BACKUP_FILE}" ]; then
    echo "Usage: restore.sh <backup-file.tar.gz>"
    echo ""
    echo "Available backups:"
    ls -lht /backups/mnemory-backup-*.tar.gz 2>/dev/null || echo "  (none found in /backups/)"
    exit 1
fi

if [ ! -f "${BACKUP_FILE}" ]; then
    echo "Error: Backup file not found: ${BACKUP_FILE}"
    exit 1
fi

echo "[$(date -u +%FT%TZ)] Restoring from: ${BACKUP_FILE}"
echo "[$(date -u +%FT%TZ)] Target: ${DATA_DIR}"
echo ""
echo "WARNING: This will overwrite all data in ${DATA_DIR}."
echo "Make sure the mnemory container is stopped first!"
echo ""

# Clear existing data and restore
rm -rf "${DATA_DIR:?}"/*
tar xzf "${BACKUP_FILE}" -C "${DATA_DIR}"

echo "[$(date -u +%FT%TZ)] Restore complete. Start the mnemory container to resume."
