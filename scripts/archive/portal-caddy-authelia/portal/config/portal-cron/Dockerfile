# portal-cron — supercronic + curl in alpine.
#
# Modeled on OB1's openbrain-cron (OB1/docker/cron/Dockerfile).
# Job: fire `POST http://portal-alerter:8080/run` on cadence; nothing else.
# No docker socket, no privileges.
#
# The PORTAL_DIGEST_CRON env var is substituted into the bind-mounted crontab
# at container start via `envsubst`.

FROM alpine:3.21

ARG SUPERCRONIC_VERSION=v0.2.33
ARG SUPERCRONIC_SHA1=71b0d58cc53f6bd72cf2f293e09e294b79c666d8

RUN apk add --no-cache curl gettext tini ca-certificates && \
    wget -O /usr/local/bin/supercronic \
      "https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VERSION}/supercronic-linux-amd64" && \
    echo "${SUPERCRONIC_SHA1}  /usr/local/bin/supercronic" | sha1sum -c - && \
    chmod +x /usr/local/bin/supercronic && \
    addgroup -g 10007 portal && \
    adduser  -u 10007 -G portal -s /sbin/nologin -D portal

USER 10007:10007
WORKDIR /home/portal

# Entrypoint: render the crontab from env, then exec supercronic.
COPY --chmod=0555 entrypoint.sh /usr/local/bin/entrypoint.sh

ENTRYPOINT ["/sbin/tini", "--", "/usr/local/bin/entrypoint.sh"]
