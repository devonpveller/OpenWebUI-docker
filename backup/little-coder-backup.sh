#!/bin/sh
# little-coder nightly backup.
# Tarballs the four expertise volumes (journals, skill, cohorts, polyglot)
# and prunes old backups (count-based retention). The workspace volume is
# intentionally NOT backed up -- it is project-scoped and re-clonable
# (design 3.6, plan 3).
#
# Precheck: the data dir must be non-empty. We DO NOT TCP-probe little-coder
# itself because it's a long-running batch process that may legitimately
# be idle; the data dir non-empty check is the floor that catches an
# accidentally-wiped volume.
#
# Inputs:
#   DATA_DIR        (default /data)
#   BACKUP_DIR      (default /backups)
#   RETAIN_COUNT    (default 2)

set -eu

BACKUP_DIR="${BACKUP_DIR:-/backups}"
DATA_DIR="${DATA_DIR:-/data}"
RETAIN_COUNT="${RETAIN_COUNT:-2}"
TIMESTAMP="$(date -u +%Y%m%d-%H%M%S)"
BACKUP_FILE="${BACKUP_DIR}/little-coder-backup-${TIMESTAMP}.tar.gz"

echo "[$(date -u +%FT%TZ)] Starting little-coder backup..."
mkdir -p "${BACKUP_DIR}"

# --- precheck ---------------------------------------------------------
if [ ! -d "${DATA_DIR}" ] || [ -z "$(ls -A "${DATA_DIR}" 2>/dev/null)" ]; then
  echo "[$(date -u +%FT%TZ)] little-coder PRECHECK SKIP: ${DATA_DIR} missing/empty"
  exit 0
fi

# --- backup -----------------------------------------------------------
# Journals are append-only and cohorts/skill are rebuildable, so a tar
# of the live volumes is consistent enough -- a worst case captures a
# journal prefix.
tar czf "${BACKUP_FILE}" -C "${DATA_DIR}" .
sha256sum "${BACKUP_FILE}" > "${BACKUP_FILE}.sha256"

BACKUP_SIZE="$(du -h "${BACKUP_FILE}" | cut -f1)"
echo "[$(date -u +%FT%TZ)] Backup created: ${BACKUP_FILE} (${BACKUP_SIZE})"

# --- retention: keep N most recent ------------------------------------
ls -1t "${BACKUP_DIR}/little-coder-backup-"*.tar.gz 2>/dev/null \
  | tail -n +$((RETAIN_COUNT + 1)) \
  | while IFS= read -r old; do
      rm -f "${old}" "${old}.sha256"
      echo "[$(date -u +%FT%TZ)] little-coder pruned: ${old}"
    done

TOTAL="$(ls -1 "${BACKUP_DIR}/little-coder-backup-"*.tar.gz 2>/dev/null | wc -l)"
echo "[$(date -u +%FT%TZ)] Backup complete. ${TOTAL} backup(s) retained (retain=${RETAIN_COUNT})."
