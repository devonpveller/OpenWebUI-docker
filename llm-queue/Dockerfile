# llm-queue — front-ended inference admission controller (design B2).
# Pinned base; rebuilt explicitly (Watchtower disabled for this service in compose).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# curl is used by the compose healthcheck (GET /healthz).
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY src ./src

RUN pip install --upgrade pip \
    && pip install .

# Drop privileges (compose also sets no-new-privileges). /data holds the analytics
# SQLite store (P3); creating it owned by appuser means a FRESH named volume mounted
# here inherits that ownership (Docker copies the image mountpoint's perms), so the
# unprivileged process can write without an entrypoint chown.
RUN useradd --create-home --uid 10021 appuser \
    && mkdir -p /data \
    && chown -R appuser:appuser /data
USER appuser

EXPOSE 8080

# Single worker on purpose: the admission state (semaphore, priority heap,
# rolling-T, running/waiting sets) is in-process and authoritative (design §4.4).
# A second worker would split the queue. Scale by tuning N, never by workers.
# --timeout-graceful-shutdown: on SIGTERM (docker stop/recreate) uvicorn stops
# accepting new connections and lets in-flight requests finish within this window
# before exiting (design §10.4 drain). Pairs with the recovery ordering.
CMD ["uvicorn", "llm_queue.main:app", "--host", "0.0.0.0", "--port", "8080", \
     "--workers", "1", "--timeout-graceful-shutdown", "30"]
