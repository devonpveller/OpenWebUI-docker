#!/bin/sh
# backup/generic-tar-backup.sh
#
# Generic hot-tar backup for any source that's safe to tar while live
# (immutable files, append-only logs, application config). NOT for
# databases under active write load -- those need their own scripts
# (pg_dump, surreal export, sqlite .backup, etc.).
#
# Usage: this script reads ALL configuration from env vars:
#   DATA_DIR        Source directory inside the container (mount point)
#   BACKUP_DIR      Destination directory (host bind mount)
#   PREFIX          Filename prefix (e.g., 'smolcrawl', 'tailscale')
#   RETAIN_COUNT    How many most-recent archives to keep (default 2)
#   HEALTH_TCP      Optional TCP probe (e.g. "smolcrawl-pipelines:8080"
#                   or "tailscale:41641"). Skips backup if unreachable.
#                   Empty = skip probe (dir-nonempty check only).
#
# Output: ${BACKUP_DIR}/${PREFIX}-<UTC-ts>.tar.gz + .sha256 sentinel.

set -eu

: "${DATA_DIR:?DATA_DIR not set}"
: "${BACKUP_DIR:?BACKUP_DIR not set}"
: "${PREFIX:?PREFIX not set}"
RETAIN_COUNT="${RETAIN_COUNT:-2}"
HEALTH_TCP="${HEALTH_TCP:-}"
MIN_AGE_SECS="${MIN_AGE_SECS:-0}"

mkdir -p "${BACKUP_DIR}"

# --- min-age guard: skip if a fresh-enough backup already exists -------
# Opt-in via MIN_AGE_SECS>0 (e.g. the ~120 GB lm-models model store). With a
# sleep-loop entrypoint the script runs on every container start; without this
# a restart storm — or a recreate right after a manual capture — would re-tar
# a huge, rarely-changing source for nothing. Cheap sources leave it at 0.
if [ "${MIN_AGE_SECS}" -gt 0 ] 2>/dev/null; then
  _newest=$(ls -1t "${BACKUP_DIR}/${PREFIX}-"*.tar.gz 2>/dev/null | head -n1)
  if [ -n "${_newest}" ]; then
    _age=$(( $(date -u +%s) - $(stat -c %Y "${_newest}" 2>/dev/null || echo 0) ))
    if [ "${_age}" -lt "${MIN_AGE_SECS}" ]; then
      echo "[$(date -u +%FT%TZ)] ${PREFIX} SKIP: newest backup is ${_age}s old (< MIN_AGE_SECS=${MIN_AGE_SECS})"
      exit 0
    fi
  fi
fi

# --- precheck: don't capture broken state -----------------------------
# Three checks, all must pass:
#   1. DATA_DIR exists
#   2. DATA_DIR is non-empty (an empty dir -> empty tarball -> /MIR on the
#      NAS would clobber the previous-good archive)
#   3. Optional TCP probe (if HEALTH_TCP is set, that host:port must answer
#      within 5 seconds). This catches "service is broken / restart loop"
#      where the data dir might be torn mid-write.
if [ ! -d "${DATA_DIR}" ]; then
  echo "[$(date -u +%FT%TZ)] ${PREFIX} PRECHECK SKIP: ${DATA_DIR} does not exist"
  exit 0
fi
if [ -z "$(ls -A "${DATA_DIR}" 2>/dev/null)" ]; then
  echo "[$(date -u +%FT%TZ)] ${PREFIX} PRECHECK SKIP: ${DATA_DIR} is empty"
  exit 0
fi
if [ -n "${HEALTH_TCP}" ]; then
  host="${HEALTH_TCP%:*}"
  port="${HEALTH_TCP##*:}"
  if ! nc -z -w 5 "${host}" "${port}" 2>/dev/null; then
    echo "[$(date -u +%FT%TZ)] ${PREFIX} PRECHECK SKIP: ${HEALTH_TCP} unreachable -- service unhealthy or down"
    exit 0
  fi
fi

# --- backup -----------------------------------------------------------
TS=$(date -u +%Y%m%dT%H%M%SZ)
OUT="${BACKUP_DIR}/${PREFIX}-${TS}.tar.gz"

cd "${DATA_DIR}"
tar czf "${OUT}" .
sha256sum "${OUT}" > "${OUT}.sha256"

# --- retention: keep N most recent archives ---------------------------
# Sort by mtime desc, skip the first RETAIN_COUNT, delete the rest plus
# their .sha256 sentinels.
ls -1t "${BACKUP_DIR}/${PREFIX}-"*.tar.gz 2>/dev/null \
  | tail -n +$((RETAIN_COUNT + 1)) \
  | while IFS= read -r old; do
      rm -f "${old}" "${old}.sha256"
      echo "[$(date -u +%FT%TZ)] ${PREFIX} pruned: ${old}"
    done

SIZE=$(stat -c%s "${OUT}" 2>/dev/null || wc -c < "${OUT}")
echo "[$(date -u +%FT%TZ)] ${PREFIX} tar -> ${OUT} (${SIZE} bytes; retain=${RETAIN_COUNT})"
