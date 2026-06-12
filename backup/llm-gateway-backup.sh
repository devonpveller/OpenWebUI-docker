#!/bin/sh
# llm-gateway nightly backup — pg_dump of the LiteLLM spend-log database.
#
# The spend-log DB is NON-AUTHORITATIVE telemetry: losing it means losing
# request history, with no functional impact on inference. This backup exists
# only to preserve the demand/capacity ledger across a DB loss.
#
# Inputs (env):
#   BACKUP_DIR    (default /backups)
#   PGHOST        (default llm-gateway-db)
#   PGUSER        (default litellm)
#   PGDATABASE    (default litellm)
#   PGPASSWORD    (required — injected from ${LITELLM_DB_PASSWORD})
#   RETAIN_DAYS   (default 7)

set -eu

BACKUP_DIR="${BACKUP_DIR:-/backups}"
RETAIN_DAYS="${RETAIN_DAYS:-7}"
PGHOST="${PGHOST:-llm-gateway-db}"
PGUSER="${PGUSER:-litellm}"
PGDATABASE="${PGDATABASE:-litellm}"
TIMESTAMP="$(date -u +%Y%m%d-%H%M%S)"
OUT="${BACKUP_DIR}/llm-gateway-${TIMESTAMP}.sql.gz"

mkdir -p "${BACKUP_DIR}"
echo "[$(date -u +%FT%TZ)] llm-gateway-backup: pg_dump ${PGDATABASE}@${PGHOST} ..."

# --- precheck: DB reachable ------------------------------------------------
if ! pg_isready -h "${PGHOST}" -U "${PGUSER}" -d "${PGDATABASE}" -t 5 >/dev/null 2>&1; then
  echo "[$(date -u +%FT%TZ)] PRECHECK SKIP: ${PGHOST} not ready"
  exit 0
fi

# --- dump ------------------------------------------------------------------
pg_dump -h "${PGHOST}" -U "${PGUSER}" "${PGDATABASE}" | gzip > "${OUT}"
echo "[$(date -u +%FT%TZ)] wrote ${OUT} ($(du -h "${OUT}" | cut -f1))"

# --- retention: delete dumps older than RETAIN_DAYS ------------------------
find "${BACKUP_DIR}" -name 'llm-gateway-*.sql.gz' -type f -mtime "+${RETAIN_DAYS}" -delete 2>/dev/null || true

TOTAL="$(ls -1 "${BACKUP_DIR}"/llm-gateway-*.sql.gz 2>/dev/null | wc -l)"
echo "[$(date -u +%FT%TZ)] llm-gateway-backup complete. ${TOTAL} dump(s) retained (retain=${RETAIN_DAYS}d)."
