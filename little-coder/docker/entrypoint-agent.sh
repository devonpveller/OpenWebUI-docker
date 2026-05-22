#!/bin/sh
# Entrypoint for the little-coder agent image (and lc-mcpo, same image).
# Named volumes mount root-owned; hand them to the unprivileged user, install
# the pi extension into that user's pi config, then drop privileges.
set -e

mkdir -p /var/lib/little-coder/journals \
         /var/lib/little-coder/skill \
         /var/lib/little-coder/cohorts \
         /var/lib/little-coder/polyglot \
         /workspace
chown -R lc:lc /var/lib/little-coder /workspace 2>/dev/null || true

# pi auto-discovers extensions from ~/.pi/extensions/ (see pi-extension/README).
mkdir -p /home/lc/.pi/extensions
cp -r /opt/little-coder/pi-extensions/. /home/lc/.pi/extensions/ 2>/dev/null || true
chown -R lc:lc /home/lc/.pi

exec gosu lc "$@"
