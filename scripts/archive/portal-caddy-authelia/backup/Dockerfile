# Shared backup container image — supercronic + tar/sha256sum (busybox).
#
# Parameterized via BACKUP_UID build arg; each backup service builds with
# its own UID (10004 for caddy-backup, 10005 for authelia-backup) so
# named-volume ownership stays distinct per plan §2 hardening floor.
FROM alpine:3.21

ARG SUPERCRONIC_VERSION=v0.2.33
ARG SUPERCRONIC_SHA1=71b0d58cc53f6bd72cf2f293e09e294b79c666d8
ARG BACKUP_UID=10004
ARG BACKUP_USER=backup

RUN apk add --no-cache tini ca-certificates bash && \
    wget -q -O /usr/local/bin/supercronic \
      "https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VERSION}/supercronic-linux-amd64" && \
    echo "${SUPERCRONIC_SHA1}  /usr/local/bin/supercronic" | sha1sum -c - && \
    chmod +x /usr/local/bin/supercronic && \
    addgroup -g ${BACKUP_UID} ${BACKUP_USER} && \
    adduser  -u ${BACKUP_UID} -G ${BACKUP_USER} -s /sbin/nologin -D ${BACKUP_USER}

USER ${BACKUP_UID}:${BACKUP_UID}
WORKDIR /home/${BACKUP_USER}

ENTRYPOINT ["/sbin/tini", "--"]
