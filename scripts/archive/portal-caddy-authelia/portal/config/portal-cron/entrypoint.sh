#!/bin/sh
# Render /etc/portal-crontab → /tmp/crontab.rendered with PORTAL_DIGEST_CRON
# substituted in, then exec supercronic on the rendered file.
set -eu

SRC=/etc/portal-crontab
DEST=/tmp/crontab.rendered

if [ ! -f "$SRC" ]; then
  echo "[portal-cron] no crontab at $SRC" >&2
  exit 1
fi

# envsubst only substitutes named env vars we explicitly pass; this avoids
# accidental substitution of $1 etc. in the curl invocation.
envsubst '${PORTAL_DIGEST_CRON}' < "$SRC" > "$DEST"

echo "[portal-cron] rendered crontab:"
sed 's/^/    /' "$DEST"

exec /usr/local/bin/supercronic -overlapping "$DEST"
