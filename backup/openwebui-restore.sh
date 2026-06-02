#!/bin/sh
set -eu

# OpenWebUI restore script
# Restores a backup tarball into the OpenWebUI data volume.

BACKUP_FILE="${1:-}"
DATA_DIR="${DATA_DIR:-/data}"

if [ -z "${BACKUP_FILE}" ]; then
    echo "Usage: restore.sh <backup-file.tar.gz>"
    echo ""
    echo "Available backups:"
    ls -lht /backups/openwebui-backup-*.tar.gz 2>/dev/null || echo "  (none found in /backups/)"
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
echo "Make sure the openwebui container is stopped first!"
echo ""

rm -rf "${DATA_DIR:?}"/*
tar xzf "${BACKUP_FILE}" -C "${DATA_DIR}"

echo "[$(date -u +%FT%TZ)] Restore complete. Start the openwebui container to resume."