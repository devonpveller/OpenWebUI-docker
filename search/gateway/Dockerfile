# Private Search Gateway — FastAPI + MCP server image.
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

# Drop privileges (compose also sets no-new-privileges).
RUN useradd --create-home --uid 10001 appuser
USER appuser

EXPOSE 8080

# HTTP API. The MCP stdio server is launched separately by the mcpo service
# via:  python -m gateway.mcp_server
CMD ["uvicorn", "gateway.main:app", "--host", "0.0.0.0", "--port", "8080"]
