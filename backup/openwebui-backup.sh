#!/bin/sh
# OpenWebUI nightly backup script
# Creates a compressed tarball of the /app/backend/data volume and
# prunes old backups (count-based retention).
#
# Precheck: openwebui must be answering on its API port; otherwise we
# may capture a partial chat DB write or an in-progress migration.
#
# Inputs:
#   DATA_DIR        (default /data)
#   BACKUP_DIR      (default /backups)
#   RETAIN_COUNT    (default 2)
#   HEALTH_TCP      (default openwebui:8080; empty to skip)
#   PIGZ_THREADS    (default 8)
#
# --- 2026-08-20 throughput fix -----------------------------------------
# This backup was taking ~50 MINUTES every night (measured 5 nights running,
# 02:00 -> ~02:50) on a ~40 GB volume. That made a pre-cutover snapshot
# impossible to fit in a maintenance window during the 0.11.0 upgrade.
#
# Two changes:
#  1. pigz (parallel gzip) instead of gzip when available. Benchmarked at
#     8x faster on this data (466 MB sample: 16s -> 2s). Output is a normal
#     gzip stream, so .tar.gz stays readable by plain gzip/tar -- no change
#     to the restore path. Falls back to gzip if pigz is missing.
#     Threads are capped (not $(nproc)) because this container is memory-
#     limited to 1g on purpose (see the compose comment) and runs at 02:00
#     alongside the OB1 scheduled slice.
#  2. cache/ is EXCLUDED. It was 7.9 GB of the volume and is entirely
#     regenerable: cache/embedding holds HuggingFace model snapshots
#     (bge-reranker-v2-m3, MiniLM, cross-encoder) that OWUI re-downloads on
#     first boot, plus whisper/ and audio/ TTS-STT caches. Nothing in cache/
#     is user data.
#     >> RESTORE NOTE: after restoring, the first boot needs internet to
#     >> re-pull the embedding/reranker models. Everything else is intact.
#
# NOT excluded, deliberately: vector_db/ (~29 GB, 72% of the volume). It is
# derived data, but rebuilding it means re-embedding thousands of files, so
# it stays in until someone decides OWUI RAG is truly retired.
# -----------------------------------------------------------------------

set -eu

BACKUP_DIR="${BACKUP_DIR:-/backups}"
DATA_DIR="${DATA_DIR:-/data}"
RETAIN_COUNT="${RETAIN_COUNT:-2}"
HEALTH_TCP="${HEALTH_TCP:-openwebui:8080}"
PIGZ_THREADS="${PIGZ_THREADS:-8}"

echo "[$(date -u +%FT%TZ)] Starting OpenWebUI backup..."

mkdir -p "${BACKUP_DIR}"

# --- precheck ---------------------------------------------------------
if [ ! -d "${DATA_DIR}" ] || [ -z "$(ls -A "${DATA_DIR}" 2>/dev/null)" ]; then
  echo "[$(date -u +%FT%TZ)] openwebui PRECHECK SKIP: ${DATA_DIR} missing/empty"
  exit 0
fi
if [ -n "${HEALTH_TCP}" ]; then
  host="${HEALTH_TCP%:*}"
  port="${HEALTH_TCP##*:}"
  if ! nc -z -w 5 "${host}" "${port}" 2>/dev/null; then
    echo "[$(date -u +%FT%TZ)] openwebui PRECHECK SKIP: ${HEALTH_TCP} unreachable -- service unhealthy or down"
    exit 0
  fi
fi

# --- backup -----------------------------------------------------------
TIMESTAMP="$(date -u +%Y%m%d-%H%M%S)"
BACKUP_FILE="${BACKUP_DIR}/openwebui-backup-${TIMESTAMP}.tar.gz"

if command -v pigz >/dev/null 2>&1; then
  COMPRESSOR="pigz -p ${PIGZ_THREADS}"
  echo "[$(date -u +%FT%TZ)] Compressor: pigz -p ${PIGZ_THREADS} (parallel)"
else
  COMPRESSOR="gzip"
  echo "[$(date -u +%FT%TZ)] Compressor: gzip (pigz not installed -- backup will be markedly slower)"
fi

START_EPOCH="$(date +%s)"

# --exclude is relative to -C ${DATA_DIR}. Keep the exclusion list in sync
# with the header comment above.
tar cf - -C "${DATA_DIR}" --exclude='./cache' . | ${COMPRESSOR} > "${BACKUP_FILE}"

sha256sum "${BACKUP_FILE}" > "${BACKUP_FILE}.sha256"

ELAPSED=$(( $(date +%s) - START_EPOCH ))
BACKUP_SIZE="$(du -h "${BACKUP_FILE}" | cut -f1)"
echo "[$(date -u +%FT%TZ)] Backup created: ${BACKUP_FILE} (${BACKUP_SIZE}) in ${ELAPSED}s"

# --- retention: keep N most recent ------------------------------------
ls -1t "${BACKUP_DIR}/openwebui-backup-"*.tar.gz 2>/dev/null \
  | tail -n +$((RETAIN_COUNT + 1)) \
  | while IFS= read -r old; do
      rm -f "${old}" "${old}.sha256"
      echo "[$(date -u +%FT%TZ)] Pruned old backup: ${old}"
    done

TOTAL="$(ls -1 "${BACKUP_DIR}/openwebui-backup-"*.tar.gz 2>/dev/null | wc -l)"
echo "[$(date -u +%FT%TZ)] Backup complete. ${TOTAL} backup(s) retained (retain=${RETAIN_COUNT})."
