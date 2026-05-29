# Authelia-watcher sidecar — log tailer that POSTs alerts to portal-alerter.
#
# Custom image so the apt deps (curl, jq, inotify-tools) are baked in at
# BUILD time instead of `apk add` at startup, enabling non-root UID at
# runtime per plan §2 hardening floor.
FROM alpine:3.21

RUN apk add --no-cache curl jq inotify-tools tini bash && \
    addgroup -g 10002 watcher && \
    adduser  -u 10002 -G watcher -s /sbin/nologin -D watcher

USER 10002:10002
WORKDIR /home/watcher

# tini for proper signal forwarding to the bash subshell that fans out the
# two `tail -F` loops in authelia-watch.sh.
ENTRYPOINT ["/sbin/tini", "--"]
CMD ["/scripts/watch.sh"]
