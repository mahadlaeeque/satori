# ============================================================================
# Satori — Production container image (distroless)
# ============================================================================
# Multi-stage build:
#   1. builder — uses `uv` to install deps into a flat directory (/pyroot)
#   2. runtime — Google distroless Python 3 (nonroot), gunicorn on port 8080
#
# Why this shape:
#   * uv installs ~10× faster than pip (Backend baseline).
#   * Distroless runtime ships only Python + stdlib + CA certs — no shell,
#     no apt, no curl. Tiny image (~60 MB), smaller attack surface, faster
#     Cloud Run cold-starts. (Containers baseline.)
#   * `pip --target` instead of a venv avoids the classic distroless+venv
#     footgun (the venv's `python` is a symlink to the builder's interpreter
#     path, which doesn't exist inside distroless). A flat package dir is
#     copied over verbatim and picked up via PYTHONPATH — no symlinks to
#     break across stages.
#   * Gunicorn is invoked via `python -m gunicorn` instead of the console
#     script, because the console script's shebang points at the builder's
#     Python and would fail in distroless.
#   * The `:nonroot` tag pre-creates UID/GID 65532 and runs as that user.
#
# Build locally:    docker build -t satori:dev .
# Run locally:      docker run --rm -p 8080:8080 -e SATORI_STATE_BACKEND=firestore \
#                       -v $HOME/.config/gcloud:/home/nonroot/.config/gcloud:ro \
#                       satori:dev
# Health check:     curl http://localhost:8080/api/health
#                   (HEALTHCHECK directive intentionally absent — distroless
#                   has no curl, and Cloud Run runs its own liveness probe.)
# ============================================================================

# ─── Stage 1: builder ────────────────────────────────────────────────────────
# Pinned to 3.11 to match the distroless runtime — wheel ABI compatibility
# is strict across Python minor versions for compiled packages (grpc, pandas).
FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

# Build deps for wheels that compile from source (grpc, pandas, etc.). These
# never make it into the runtime image.
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

# Install into /pyroot as a flat package directory. We bypass venvs entirely
# so there are no symlinks to break when we copy this directory across to
# the distroless runtime stage.
RUN uv pip install \
        --python "$(which python)" \
        --target /pyroot \
        --no-cache \
        -r requirements.txt


# ─── Stage 2: runtime (distroless) ───────────────────────────────────────────
# Distroless Python ships with the Python 3.11 interpreter + stdlib + CA
# certs + tzdata, and nothing else. Cloud Run handles signal forwarding to
# PID 1, so no tini needed. Cloud Run handles healthchecks, so no
# HEALTHCHECK directive needed.
FROM gcr.io/distroless/python3-debian12:nonroot AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/pyroot:/app \
    PORT=8080 \
    SATORI_STATE_BACKEND=firestore

# Bring in the installed package tree.
COPY --from=builder --chown=nonroot:nonroot /pyroot /pyroot

# Bring in the app source. .dockerignore excludes data/, JSON state,
# secrets, __pycache__, node_modules, and editor cruft.
COPY --chown=nonroot:nonroot . /app

WORKDIR /app

EXPOSE 8080

# `python -m gunicorn` works because the gunicorn package ships a __main__.py
# that re-exports the CLI entry point. This avoids depending on the
# console-script shebang, which points at the builder's interpreter path
# and would break inside distroless.
ENTRYPOINT ["python", "-m", "gunicorn", \
            "--bind", "0.0.0.0:8080", \
            "--workers", "4", \
            "--worker-class", "uvicorn.workers.UvicornWorker", \
            "--timeout", "60", \
            "--graceful-timeout", "30", \
            "--access-logfile", "-", \
            "--error-logfile", "-", \
            "main:app"]
