# Bridge image for Authelia file-notifier -> portal-alerter forwarder.
# Tiny: alpine + jq + tini. Busybox provides wget/tail/awk/sed already.
FROM alpine:3.21

RUN apk add --no-cache jq tini ca-certificates \
 && addgroup -g 10001 -S authnotif \
 && adduser  -u 10001 -G authnotif -S -h /nonexistent -s /sbin/nologin authnotif

# Healthcheck-friendly STOPSIGNAL so tini propagates.
STOPSIGNAL SIGTERM

ENTRYPOINT ["/sbin/tini", "--"]
CMD ["/scripts/bridge.sh"]
