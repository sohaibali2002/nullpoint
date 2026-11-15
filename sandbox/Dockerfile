# Image used by DockerSandbox to execute a scanned signal with the native engine.
# Build from the repository root:
#   docker build -f sandbox/Dockerfile -t nullpoint-sandbox:latest .
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

COPY pyproject.toml README.md ./
COPY engine ./engine
COPY data ./data
COPY agents ./agents
COPY sandbox ./sandbox
COPY apps/api ./apps/api

RUN pip install --no-cache-dir -e .

# The runtime container is started with --user 65534 and a read-only root filesystem.
CMD ["python", "-I", "/work/main.py"]
