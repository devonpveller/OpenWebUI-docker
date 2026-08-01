#!/bin/sh
# backup/pg-backup.sh
#
# Generic pg_dump backup for any PostgreSQL database, driven entirely by env so
# one script serves multiple *-backup sidecars (agent-bridge-db, mattermost-db).
# Custom format (-Fc): compressed + supports pg_restore --jobs (parallel/selective
# restore). Logical dump = hot-safe via PostgreSQL MVCC.
#
# Inputs (env):
#   BACKUP_NAME     REQUIRED — filename prefix + log label (e.g. agent-bridge-db)
#   PGHOST          REQUIRED — DB host (service name on the shared network)
#   PGPORT          (default 5432)
#   PGUSER / POSTGRES_USER          REQUIRED
#   PGDATABASE / POSTGRES_DB        REQUIRED
#   PGPASSWORD / POSTGRES_PASSWORD  REQUIRED
#   BACKUP_DIR      (default /backups)
#   RETAIN_COUNT    (default 7 — how many recent dumps to keep)
#
# Restore:
#   pg_restore -h <host> -U <user> -d <db> --clean --if-exists --no-owner --no-acl <dump>

set -eu

BACKUP_NAME="${BACKUP_NAME:?BACKUP_NAME not set}"
PGHOST="${PGHOST:?PGHOST not set}"
PGPORT="${PGPORT:-5432}"
PGUSER="${PGUSER:-${POSTGRES_USER:?PGUSER/POSTGRES_USER not set}}"
PGDATABASE="${PGDATABASE:-${POSTGRES_DB:?PGDATABASE/POSTGRES_DB not set}}"
export PGPASSWORD="${PGPASSWORD:-${POSTGRES_PASSWORD:?PGPASSWORD/POSTGRES_PASSWORD not set}}"

BACKUP_DIR="${BACKUP_DIR:-/backups}"
RETAIN_COUNT="${RETAIN_COUNT:-7}"

mkdir -p "${BACKUP_DIR}"

# --- precheck: wait (bounded) for Postgres to accept connections ------------
# pg_isready returns 0 only when the server is up AND ready. On a stack restart
# the DB is often still booting when this sidecar's sleep-loop fires its once-
# a-day attempt; a bare check would SKIP and then the loop waits another ~24h.
# Retry for up to READY_WAIT_SECS so a restart no longer costs a whole backup
# cycle. Still a clean SKIP (not a torn dump / false alarm) if it never comes up.
READY_WAIT_SECS="${READY_WAIT_SECS:-300}"
_waited=0
until pg_isready -h "${PGHOST}" -p "${PGPORT}" -U "${PGUSER}" -d "${PGDATABASE}" -t 10 -q; do
  if [ "${_waited}" -ge "${READY_WAIT_SECS}" ]; then
    echo "[$(date -u +%FT%TZ)] ${BACKUP_NAME} PRECHECK SKIP: ${PGHOST}:${PGPORT} not ready after ${READY_WAIT_SECS}s"
    exit 0
  fi
  echo "[$(date -u +%FT%TZ)] ${BACKUP_NAME} waiting for ${PGHOST}:${PGPORT} to be ready (${_waited}/${READY_WAIT_SECS}s)"
  sleep 15
  _waited=$((_waited + 15))
done

# --- backup -----------------------------------------------------------------
TS=$(date -u +%Y%m%dT%H%M%SZ)
OUT="${BACKUP_DIR}/${BACKUP_NAME}-${TS}.dump"

pg_dump \
  --host="${PGHOST}" \
  --port="${PGPORT}" \
  --username="${PGUSER}" \
  --dbname="${PGDATABASE}" \
  --format=custom \
  --no-owner \
  --no-acl \
  --file="${OUT}"

# Integrity sentinel next to the dump — same convention as the other backups so
# the NAS sync can verify after mirror.
sha256sum "${OUT}" > "${OUT}.sha256"

# --- retention: keep N most recent dumps ------------------------------------
ls -1t "${BACKUP_DIR}/${BACKUP_NAME}-"*.dump 2>/dev/null \
  | tail -n +$((RETAIN_COUNT + 1)) \
  | while IFS= read -r old; do
      rm -f "${old}" "${old}.sha256"
      echo "[$(date -u +%FT%TZ)] ${BACKUP_NAME} pruned: ${old}"
    done

SIZE=$(stat -c%s "${OUT}" 2>/dev/null || wc -c < "${OUT}")
echo "[$(date -u +%FT%TZ)] ${BACKUP_NAME} pg_dump -> ${OUT} (${SIZE} bytes; retain=${RETAIN_COUNT})"
