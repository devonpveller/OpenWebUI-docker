#!/bin/sh
set -eu

# little-coder nightly backup.
# Tarballs the four expertise volumes (journals, skill, cohorts, polyglot)
# and prunes old backups. The workspace volume is intentionally NOT backed up
# — it is project-scoped and re-clonable (design §3.6, plan §3).

BACKUP_DIR="${BACKUP_DIR:-/backups}"
DATA_DIR="${DATA_DIR:-/data}"
RETAIN_DAYS="${RETAIN_DAYS:-7}"
TIMESTAMP="$(date -u +%Y%m%d-%H%M%S)"
BACKUP_FILE="${BACKUP_DIR}/little-coder-backup-${TIMESTAMP}.tar.gz"

echo "[$(date -u +%FT%TZ)] Starting little-coder backup..."
mkdir -p "${BACKUP_DIR}"

# Journals are append-only and cohorts/skill are rebuildable, so a tar of the
# live volumes is consistent enough — a worst case captures a journal prefix.
tar czf "${BACKUP_FILE}" -C "${DATA_DIR}" .

BACKUP_SIZE="$(du -h "${BACKUP_FILE}" | cut -f1)"
echo "[$(date -u +%FT%TZ)] Backup created: ${BACKUP_FILE} (${BACKUP_SIZE})"

# Prune backups older than RETAIN_DAYS.
find "${BACKUP_DIR}" -name 'little-coder-backup-*.tar.gz' -mtime "+${RETAIN_DAYS}" \
    -type f -exec rm -f {} \;

TOTAL="$(find "${BACKUP_DIR}" -name 'little-coder-backup-*.tar.gz' -type f | wc -l)"
echo "[$(date -u +%FT%TZ)] Backup complete. ${TOTAL} backup(s) retained."
