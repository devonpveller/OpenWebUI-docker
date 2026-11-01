# Integrity-tripwire sidecar — periodic config-file hash check.
#
# Custom image with supercronic instead of busybox crond — busybox crond
# requires root, supercronic runs as any user with a bind-mounted crontab.
# Per plan §2 hardening floor.
FROM alpine:3.21

ARG SUPERCRONIC_VERSION=v0.2.33
ARG SUPERCRONIC_SHA1=71b0d58cc53f6bd72cf2f293e09e294b79c666d8

RUN apk add --no-cache curl ca-certificates tini bash && \
    wget -q -O /usr/local/bin/supercronic \
      "https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VERSION}/supercronic-linux-amd64" && \
    echo "${SUPERCRONIC_SHA1}  /usr/local/bin/supercronic" | sha1sum -c - && \
    chmod +x /usr/local/bin/supercronic && \
    addgroup -g 10003 tripwire && \
    adduser  -u 10003 -G tripwire -s /sbin/nologin -D tripwire

USER 10003:10003
WORKDIR /home/tripwire

ENTRYPOINT ["/sbin/tini", "--"]
