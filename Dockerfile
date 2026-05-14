# ============================================================================
# Satori — Production container image
# ============================================================================
# Multi-stage build:
#   1. builder — uses `uv` to install Python dependencies into a venv
#   2. runtime — python:3.11-slim, gunicorn as non-root, port 8080
#
# Why this shape:
#   * uv installs ~10× faster than pip (Backend baseline check).
#   * python:3.11-slim is small (~150 MB) and we keep paths consistent between
#     stages so the venv's python symlinks resolve correctly. Distroless + venv
#     is a known footgun (symlink to system python breaks across stages); we
#     accept a slightly larger runtime for stability.
#   * Non-root UID 1001 satisfies the "container runs as non-root" baseline.
#
# Build locally:    docker build -t satori:dev .
# Run locally:      docker run --rm -p 8080:8080 -e SATORI_STATE_BACKEND=firestore \
#                       -v $HOME/.config/gcloud:/home/satori/.config/gcloud:ro \
#                       satori:dev
# Health check:     curl http://localhost:8080/api/health
# ============================================================================

# ─── Stage 1: builder ────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

# Build deps needed by wheels that compile from source (grpc, pandas).
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        build-essential \
        gcc \
 && rm -rf /var/lib/apt/lists/*

# Bootstrap uv into the system Python.
RUN pip install --no-cache-dir "uv>=0.4.0"

WORKDIR /build

# Layer-cache: copy only the dependency manifest first so subsequent source
# edits don't bust the install cache.
COPY requirements.txt ./

# Install into /opt/venv using uv. uv reads requirements.txt natively and
# is meaningfully faster than pip on cold-cache builds. pyproject.toml + uv.lock
# live in the repo for local-dev reproducibility but aren't required by the
# image build.
RUN python -m venv /opt/venv \
 && uv pip install --python /opt/venv/bin/python --no-cache -r requirements.txt


# ─── Stage 2: runtime ────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    PORT=8080 \
    SATORI_STATE_BACKEND=firestore \
    PYTHONPATH=/app

# Runtime-only system libs: curl for healthcheck, tini for proper signal
# forwarding (gunicorn → workers under SIGTERM).
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
        tini \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --system --gid 1001 satori \
 && useradd  --system --uid 1001 --gid satori --home /home/satori --create-home satori

# Pull the venv from the builder. Paths match (both are python:3.11-slim)
# so the venv's python symlink resolves correctly.
COPY --from=builder /opt/venv /opt/venv

WORKDIR /app

# Copy app source. .dockerignore excludes data/, JSON state, secrets, __pycache__.
COPY --chown=satori:satori . /app

USER satori

EXPOSE 8080

# Container-level health check (Cloud Run also has its own).
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD curl -fsS "http://localhost:${PORT}/api/health" || exit 1

# tini reaps zombie workers and forwards SIGTERM cleanly to gunicorn.
ENTRYPOINT ["/usr/bin/tini", "--"]

# 4 gunicorn workers × UvicornWorker = async I/O via Starlette/Uvicorn.
# Reasonable for 1 vCPU / 1 GiB Cloud Run revisions with concurrency=80.
CMD ["gunicorn", \
     "--bind", "0.0.0.0:8080", \
     "--workers", "4", \
     "--worker-class", "uvicorn.workers.UvicornWorker", \
     "--timeout", "60", \
     "--graceful-timeout", "30", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "main:app"]
