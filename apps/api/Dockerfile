# Nullpoint API image — slim, non-root, no compiler in the final layer.
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
      curl build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv

COPY pyproject.toml README.md ./
COPY engine ./engine
COPY data ./data
COPY agents ./agents
COPY sandbox ./sandbox
COPY apps/api ./apps/api

RUN pip install --no-cache-dir -e . \
    && useradd --create-home --uid 10001 nullpoint \
    && mkdir -p /lake && chown -R nullpoint:nullpoint /srv /lake

USER nullpoint
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
