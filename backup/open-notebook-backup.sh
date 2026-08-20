#!/bin/sh
# backup/open-notebook-backup.sh
#
# Two-phase backup for Open Notebook:
#   1. SurrealDB logical export via `surreal export` (hot-safe, no
#      downtime). The output is a .surql text file -- replayable on
#      restore via `surreal import`. RocksDB itself is NOT tarred
#      (it would corrupt with active writes).
#   2. notebook_data tarball -- small bind-mount of application-layer
#      files (uploaded sources, etc.). Hot tar safe.
#
# Inputs:
#   SURREAL_URL              (default ws://surrealdb:8000/rpc)
#   SURREAL_HTTP_URL         (default http://surrealdb:8000 -- for /health probe)
#   SURREAL_USER             (from .env, default root)
#   SURREAL_PASSWORD         (from .env, default root)
#   SURREAL_NS               (default open_notebook)
#   SURREAL_DB               (default open_notebook)
#   NOTEBOOK_DATA_DIR        (default /notebook-data, bind-mount source)
#   BACKUP_DIR               (default /backups)
#   RETAIN_COUNT             (default 2 -- keep N most recent of each kind)

set -eu

SURREAL_URL="${SURREAL_URL:-ws://surrealdb:8000/rpc}"
SURREAL_HTTP_URL="${SURREAL_HTTP_URL:-http://surrealdb:8000}"
SURREAL_USER="${SURREAL_USER:?SURREAL_USER not set}"
SURREAL_PASSWORD="${SURREAL_PASSWORD:?SURREAL_PASSWORD not set}"
SURREAL_NS="${SURREAL_NS:-open_notebook}"
SURREAL_DB="${SURREAL_DB:-open_notebook}"
NOTEBOOK_DATA_DIR="${NOTEBOOK_DATA_DIR:-/notebook-data}"
BACKUP_DIR="${BACKUP_DIR:-/backups}"
RETAIN_COUNT="${RETAIN_COUNT:-2}"

mkdir -p "${BACKUP_DIR}"

# --- precheck ---------------------------------------------------------
# Phase 1 (surreal): the SurrealDB /health endpoint must answer 200.
# We use the surreal CLI's `version` subcommand which connects to the
# server and reports both client + server versions -- a true RPC roundtrip.
if ! surreal version --endpoint "${SURREAL_URL}" >/dev/null 2>&1; then
  echo "[$(date -u +%FT%TZ)] open-notebook PRECHECK SKIP (phase 1): surreal RPC unreachable at ${SURREAL_URL}"
  PHASE1_OK=0
else
  PHASE1_OK=1
fi

# Phase 2 (notebook_data): the bind mount must exist and be non-empty.
if [ ! -d "${NOTEBOOK_DATA_DIR}" ] || [ -z "$(ls -A "${NOTEBOOK_DATA_DIR}" 2>/dev/null)" ]; then
  echo "[$(date -u +%FT%TZ)] open-notebook PRECHECK SKIP (phase 2): ${NOTEBOOK_DATA_DIR} missing/empty"
  PHASE2_OK=0
else
  PHASE2_OK=1
fi

if [ "${PHASE1_OK}" = "0" ] && [ "${PHASE2_OK}" = "0" ]; then
  echo "[$(date -u +%FT%TZ)] open-notebook PRECHECK SKIP: both phases failed precheck -- exiting cleanly"
  exit 0
fi

TS=$(date -u +%Y%m%dT%H%M%SZ)
SURREAL_OUT="${BACKUP_DIR}/surreal-${TS}.surql.gz"
NOTEBOOK_OUT="${BACKUP_DIR}/notebook-data-${TS}.tar.gz"

# ===== Phase 1: SurrealDB logical export =====================
if [ "${PHASE1_OK}" = "1" ]; then
  SURREAL_TMP="/tmp/surreal-${TS}.surql"
  surreal export \
    --endpoint "${SURREAL_URL}" \
    --username "${SURREAL_USER}" \
    --password "${SURREAL_PASSWORD}" \
    --auth-level root \
    --namespace "${SURREAL_NS}" \
    --database "${SURREAL_DB}" \
    "${SURREAL_TMP}"
  gzip -c "${SURREAL_TMP}" > "${SURREAL_OUT}"
  rm -f "${SURREAL_TMP}"
  sha256sum "${SURREAL_OUT}" > "${SURREAL_OUT}.sha256"
fi

# ===== Phase 2: notebook_data tarball ========================
if [ "${PHASE2_OK}" = "1" ]; then
  cd "${NOTEBOOK_DATA_DIR}"
  tar czf "${NOTEBOOK_OUT}" .
  sha256sum "${NOTEBOOK_OUT}" > "${NOTEBOOK_OUT}.sha256"
fi

# --- retention: keep N most recent ------------------------------------
for pattern in 'surreal-*.surql.gz' 'notebook-data-*.tar.gz'; do
  ls -1t "${BACKUP_DIR}/"${pattern} 2>/dev/null \
    | tail -n +$((RETAIN_COUNT + 1)) \
    | while IFS= read -r old; do
        rm -f "${old}" "${old}.sha256"
        echo "[$(date -u +%FT%TZ)] open-notebook pruned: ${old}"
      done
done

if [ "${PHASE1_OK}" = "1" ]; then
  SURREAL_SIZE=$(stat -c%s "${SURREAL_OUT}" 2>/dev/null || wc -c < "${SURREAL_OUT}")
  echo "[$(date -u +%FT%TZ)] surreal export -> ${SURREAL_OUT} (${SURREAL_SIZE} bytes; retain=${RETAIN_COUNT})"
fi
if [ "${PHASE2_OK}" = "1" ]; then
  NB_SIZE=$(stat -c%s "${NOTEBOOK_OUT}" 2>/dev/null || wc -c < "${NOTEBOOK_OUT}")
  echo "[$(date -u +%FT%TZ)] notebook_data tar -> ${NOTEBOOK_OUT} (${NB_SIZE} bytes; retain=${RETAIN_COUNT})"
fi
