#!/bin/sh
# backup/openbrain-wiki-backup.sh
#
# Hot-safe tar of the openbrain-wiki-data volume. Contents are a git
# working tree + compiled markdown; git objects are immutable once
# written, so tarring while the wiki compiler runs is safe.
#
# Inputs:
#   DATA_DIR       (default /data)
#   BACKUP_DIR     (default /backups)
#   RETAIN_COUNT   (default 2)
#
# Precheck: data dir non-empty + contains .git (the wiki ALWAYS keeps a
# git repo; missing .git means the volume was wiped or has not been
# initialized, and a tar of that state would clobber a good archive).

set -eu

DATA_DIR="${DATA_DIR:-/data}"
BACKUP_DIR="${BACKUP_DIR:-/backups}"
RETAIN_COUNT="${RETAIN_COUNT:-2}"

mkdir -p "${BACKUP_DIR}"

# --- precheck ---------------------------------------------------------
if [ ! -d "${DATA_DIR}" ]; then
  echo "[$(date -u +%FT%TZ)] openbrain-wiki PRECHECK SKIP: ${DATA_DIR} does not exist"
  exit 0
fi
if [ -z "$(ls -A "${DATA_DIR}" 2>/dev/null)" ]; then
  echo "[$(date -u +%FT%TZ)] openbrain-wiki PRECHECK SKIP: ${DATA_DIR} is empty"
  exit 0
fi
if [ ! -d "${DATA_DIR}/.git" ]; then
  echo "[$(date -u +%FT%TZ)] openbrain-wiki PRECHECK SKIP: ${DATA_DIR}/.git missing -- volume not initialized or corrupt"
  exit 0
fi

# --- backup -----------------------------------------------------------
TS=$(date -u +%Y%m%dT%H%M%SZ)
OUT="${BACKUP_DIR}/openbrain-wiki-${TS}.tar.gz"

cd "${DATA_DIR}"
tar czf "${OUT}" .
sha256sum "${OUT}" > "${OUT}.sha256"

# --- retention: keep N most recent ------------------------------------
ls -1t "${BACKUP_DIR}/openbrain-wiki-"*.tar.gz 2>/dev/null \
  | tail -n +$((RETAIN_COUNT + 1)) \
  | while IFS= read -r old; do
      rm -f "${old}" "${old}.sha256"
      echo "[$(date -u +%FT%TZ)] openbrain-wiki pruned: ${old}"
    done

SIZE=$(stat -c%s "${OUT}" 2>/dev/null || wc -c < "${OUT}")
echo "[$(date -u +%FT%TZ)] openbrain-wiki tar -> ${OUT} (${SIZE} bytes; retain=${RETAIN_COUNT})"

# --- wiki-assets (gitignored binaries: uploaded images/PDFs) ----------
# These live in a SEPARATE volume and are NOT in the git vault, so the tar
# above misses them. Snapshot them on their own when present (the volume is
# usually empty until uploads happen — X.2). No .git precheck (binaries).
ASSETS_DIR="${ASSETS_DIR:-/assets}"
if [ -d "${ASSETS_DIR}" ] && [ -n "$(ls -A "${ASSETS_DIR}" 2>/dev/null)" ]; then
  AOUT="${BACKUP_DIR}/openbrain-wiki-assets-${TS}.tar.gz"
  ( cd "${ASSETS_DIR}" && tar czf "${AOUT}" . )
  sha256sum "${AOUT}" > "${AOUT}.sha256"
  ls -1t "${BACKUP_DIR}/openbrain-wiki-assets-"*.tar.gz 2>/dev/null \
    | tail -n +$((RETAIN_COUNT + 1)) \
    | while IFS= read -r old; do rm -f "${old}" "${old}.sha256"; done
  ASIZE=$(stat -c%s "${AOUT}" 2>/dev/null || wc -c < "${AOUT}")
  echo "[$(date -u +%FT%TZ)] openbrain-wiki-assets tar -> ${AOUT} (${ASIZE} bytes)"
else
  echo "[$(date -u +%FT%TZ)] openbrain-wiki-assets SKIP: ${ASSETS_DIR} absent/empty (no uploads yet)"
fi
