# ============================================================================
# Satori — Production container image
# ============================================================================
# Multi-stage build:
#   1. builder — installs Python dependencies into a venv
#   2. runtime — minimal final image, gunicorn as non-root, port 8080
#
# Build locally:    docker build -t satori:dev .
# Run locally:      docker run --rm -p 8080:8080 -e SATORI_STATE_BACKEND=firestore \
#                       -v $HOME/.config/gcloud:/home/satori/.config/gcloud:ro \
#                       satori:dev
# Health check:     curl http://localhost:8080/api/health
# ============================================================================

# ─── Stage 1: builder ────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Build deps needed by some wheels (grpc, pandas). Removed in runtime stage.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        build-essential \
        gcc \
        libpq-dev \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Layer-cache: copy requirements first
COPY requirements.txt .
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --upgrade pip \
 && /opt/venv/bin/pip install -r requirements.txt

# ─── Stage 2: runtime ────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    PORT=8080 \
    SATORI_STATE_BACKEND=firestore \
    PYTHONPATH=/app

# Runtime-only system libs (postgres client for psycopg, curl for healthcheck)
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        libpq5 \
        curl \
        ca-certificates \
        tini \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --system --gid 1001 satori \
 && useradd  --system --uid 1001 --gid satori --home /home/satori --create-home satori

# Pull venv from builder
COPY --from=builder /opt/venv /opt/venv

WORKDIR /app

# Copy app source. .dockerignore excludes data/, JSON state, secrets, __pycache__.
COPY --chown=satori:satori . /app

USER satori

EXPOSE 8080

# Container-level health check (Cloud Run also has its own).
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD curl -fsS "http://localhost:${PORT}/api/health" || exit 1

# tini = small init, reaps zombies and forwards SIGTERM cleanly to gunicorn.
ENTRYPOINT ["/usr/bin/tini", "--"]

# 4 gunicorn workers × UvicornWorker = async I/O via Starlette/Uvicorn.
# at min-instances=0 fairly well. Tune in the cloudbuild deploy step if needed.
CMD ["gunicorn", \
     "--bind", "0.0.0.0:8080", \
     "--workers", "4", \
     "--worker-class", "uvicorn.workers.UvicornWorker", \
     "--timeout", "60", \
     "--graceful-timeout", "30", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "main:app"]
