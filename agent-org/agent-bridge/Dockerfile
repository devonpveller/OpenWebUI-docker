# agent-bridge — governance-gated orchestration service (PLAN §5.1).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# git is needed for last-owner provenance (git-blame, P5.4).
RUN apt-get update \
    && apt-get install -y --no-install-recommends git curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# App code + the config artifacts the bridge reads relative to WORKDIR:
#   profiles/  (role registry, Pc.3)   charters/ (system_prompt_refs, §4)
#   floor/     (hard-rules + stop-gate enforcement, §4.2)   hooks/ (floor hook, P3.3)
COPY app ./app
COPY profiles ./profiles
COPY charters ./charters
COPY floor ./floor
COPY hooks ./hooks

# Non-root (defense in depth). State (SQLite dev / Postgres prod) is external.
RUN useradd -u 10030 -m bridge && chown -R bridge /app
USER bridge

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=20s \
    CMD curl -fsS --max-time 5 http://localhost:8000/health || exit 1

CMD ["python", "-m", "app.main"]
