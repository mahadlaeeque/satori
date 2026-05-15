# ============================================================================
# Satori — Production container image (distroless + bundled React build)
# ============================================================================
# Three-stage build:
#   1. frontend-builder — Node 20, runs `npm ci && npm run build` to produce
#      the Vite/React static bundle at /frontend/dist.
#   2. python-builder   — uv installs deps into a flat /pyroot directory.
#   3. runtime          — Google distroless Python 3 (nonroot). Picks up the
#      python packages, the application source, and the pre-built React app.
#
# Why this shape:
#   * uv installs ~10× faster than pip (Backend baseline).
#   * Distroless runtime ships only Python + stdlib + CA certs — no shell,
#     no apt, no curl. Tiny image (~65 MB), smaller attack surface, faster
#     Cloud Run cold-starts. (Containers baseline.)
#   * `pip --target` instead of a venv avoids the classic distroless+venv
#     footgun (the venv's `python` is a symlink to the builder's interpreter
#     path, which doesn't exist inside distroless). A flat package dir is
#     copied over verbatim and picked up via PYTHONPATH.
#   * Gunicorn is invoked via `python -m gunicorn` instead of the console
#     script, because the console script's shebang points at the builder's
#     Python and would fail in distroless.
#   * Building the React app in-image means you never have to remember to
#     `npm run build` locally before pushing. Every `git push` produces a
#     fully self-contained image with the latest frontend baked in.
# ============================================================================

# ─── Stage 1: frontend-builder ───────────────────────────────────────────────
FROM node:20-slim AS frontend-builder

WORKDIR /frontend

# Layer-cache: copy manifests first so dep installs aren't busted by source
# edits. package-lock.json* glob makes the COPY tolerant of its absence.
COPY frontend/package.json frontend/package-lock.json* ./

# `npm ci` is preferred when a lockfile exists — strict, reproducible. Falls
# back to `npm install` if the lockfile is missing.
RUN if [ -f package-lock.json ]; then \
        npm ci --no-audit --no-fund ; \
    else \
        npm install --no-audit --no-fund ; \
    fi

# Now bring in the rest of the frontend source and build.
COPY frontend/ ./
RUN npm run build
# Output is at /frontend/dist — copied into the runtime stage below.


# ─── Stage 2: python-builder ─────────────────────────────────────────────────
# Pinned to 3.11 to match the distroless runtime — wheel ABI compatibility
# is strict across Python minor versions for compiled packages (grpc, pandas).
FROM python:3.11-slim AS python-builder

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


# ─── Stage 3: runtime (distroless) ───────────────────────────────────────────
# Distroless Python ships with the Python 3.11 interpreter + stdlib + CA
# certs + tzdata, and nothing else. Cloud Run handles signal forwarding to
# PID 1 (no tini needed) and runs its own healthchecks against /api/health
# (no HEALTHCHECK directive needed).
FROM gcr.io/distroless/python3-debian12:nonroot AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/pyroot:/app \
    PORT=8080 \
    SATORI_STATE_BACKEND=firestore

# Python packages.
COPY --from=python-builder --chown=nonroot:nonroot /pyroot /pyroot

# Application source. .dockerignore excludes data/, JSON state, secrets,
# __pycache__, node_modules, and editor cruft.
COPY --chown=nonroot:nonroot . /app

# React build artefacts on top — overlays /app/frontend/dist with the
# Node-built bundle. FastAPI's main.py auto-mounts /app/frontend/dist at
# the /app route when the directory exists.
COPY --from=frontend-builder --chown=nonroot:nonroot /frontend/dist /app/frontend/dist

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
