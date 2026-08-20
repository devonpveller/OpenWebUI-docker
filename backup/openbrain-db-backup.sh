#!/bin/sh
# backup/openbrain-db-backup.sh
#
# pg_dump-based backup of the openbrain-db (PostgreSQL 16 + pgvector).
# Logical backup -- hot-safe via PostgreSQL's MVCC. Custom format (-Fc)
# is compressed and supports parallel + selective restore.
#
# Inputs (env):
#   PGHOST          (default openbrain-db)
#   PGPORT          (default 5432)
#   POSTGRES_USER   (from env_file ./OB1/docker/.env)
#   POSTGRES_PASSWORD
#   POSTGRES_DB     (default openbrain)
#   BACKUP_DIR      (default /backups)
#   RETAIN_COUNT    (default 2; how many recent dumps to keep)
#
# Restore (per documentation/runbooks/restore-from-snapshot.md):
#   pg_restore -h openbrain-db -U $POSTGRES_USER -d $POSTGRES_DB \
#     --clean --if-exists --no-owner --no-acl <dump>

set -eu

PGHOST="${PGHOST:-openbrain-db}"
PGPORT="${PGPORT:-5432}"
PGDATABASE="${POSTGRES_DB:-openbrain}"
PGUSER="${POSTGRES_USER:?POSTGRES_USER not set}"
export PGPASSWORD="${POSTGRES_PASSWORD:?POSTGRES_PASSWORD not set}"

BACKUP_DIR="${BACKUP_DIR:-/backups}"
RETAIN_COUNT="${RETAIN_COUNT:-2}"

mkdir -p "${BACKUP_DIR}"

# --- precheck: only run if Postgres is accepting connections ----------
# pg_isready is the canonical Postgres liveness probe. It returns 0 only
# when the server is up AND ready to accept connections. If openbrain-db
# is restarting or recovering from a crash, this check fails and we skip
# rather than risk a torn dump or alarming alerts.
if ! pg_isready -h "${PGHOST}" -p "${PGPORT}" -U "${PGUSER}" -d "${PGDATABASE}" -t 10 -q; then
  echo "[$(date -u +%FT%TZ)] openbrain-db PRECHECK SKIP: ${PGHOST}:${PGPORT} not ready -- service unhealthy or down"
  exit 0
fi

# --- backup -----------------------------------------------------------
TS=$(date -u +%Y%m%dT%H%M%SZ)
OUT="${BACKUP_DIR}/openbrain-${TS}.dump"

# -Fc = custom format (compressed, supports pg_restore --jobs for parallel
# restore on large DBs). Includes pgvector vectors as opaque bytea blobs;
# pgvector extension is preserved via the extension entry in pg_dump output.
pg_dump \
  --host="${PGHOST}" \
  --port="${PGPORT}" \
  --username="${PGUSER}" \
  --dbname="${PGDATABASE}" \
  --format=custom \
  --no-owner \
  --no-acl \
  --file="${OUT}"

# Integrity sentinel: sha256 next to the dump. Same convention as other
# backups so the NAS sync can verify after mirror.
sha256sum "${OUT}" > "${OUT}.sha256"

# --- retention: keep N most recent dumps ------------------------------
ls -1t "${BACKUP_DIR}/openbrain-"*.dump 2>/dev/null \
  | tail -n +$((RETAIN_COUNT + 1)) \
  | while IFS= read -r old; do
      rm -f "${old}" "${old}.sha256"
      echo "[$(date -u +%FT%TZ)] openbrain-db pruned: ${old}"
    done

# Stats line for the log.
SIZE=$(stat -c%s "${OUT}" 2>/dev/null || wc -c < "${OUT}")
echo "[$(date -u +%FT%TZ)] openbrain pg_dump -> ${OUT} (${SIZE} bytes; retain=${RETAIN_COUNT})"
