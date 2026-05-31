# Tunnel-watcher sidecar -- periodic HTTP probe of cloudflared /ready;
# alerts via portal-alerter when the Cloudflare tunnel is unhealthy.
# Same hardening pattern as authelia-watcher.
FROM alpine:3.21

RUN apk add --no-cache curl jq tini \
 && addgroup -g 10003 tunwatch \
 && adduser  -u 10003 -G tunwatch -s /sbin/nologin -D tunwatch

USER 10003:10003
WORKDIR /home/tunwatch

ENTRYPOINT ["/sbin/tini", "--"]
CMD ["/scripts/watch.sh"]
