#!/bin/sh
set -eu

# Mnemory restore script
# Restores a backup tarball into the data directory.
#
# Usage:
#   docker run --rm \
#     -v memory_mnemory-data:/data \
#     -v ./backups/mnemory:/backups:ro \
#     alpine sh -c "sh /backups/../../backup/mnemory-restore.sh /backups/mnemory-backup-YYYYMMDD-HHMMSS.tar.gz"
#
# IMPORTANT: Stop the memory plane before restoring! (own compose project
# since Part K 2026-08-21; run from the repo root)
#   docker compose -f memory/docker-compose.yml --env-file .env stop mnemory mnemory-cloud-gateway
#   <run restore>
#   docker compose -f memory/docker-compose.yml --env-file .env start mnemory mnemory-cloud-gateway

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
