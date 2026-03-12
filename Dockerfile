###############################################################################
# Multi-stage Dockerfile – FastAPI + uv
#
# Stages
#   base     – shared Python + uv tooling, env vars
#   dev      – dev deps only; source is bind-mounted at runtime (hot-reload)
#   builder  – prod deps + project installed non-editable into /app/.venv
#   prod     – lean runtime: no uv, non-root user, healthcheck
#
# Build targets:
#   Dev  → image built by compose.dev.yaml  (target: dev)
#   Prod → image built by compose.yaml      (target: prod)
#
# Python version
#   Controlled by the build arg PYTHON_VERSION (default: 3.14).
#   Used by ALL stages so base, dev, and prod always run identical Python.
#   To upgrade: docker build --build-arg PYTHON_VERSION=3.15 ...
###############################################################################

ARG PYTHON_VERSION=3.14

# ── base: shared tooling ──────────────────────────────────────────────────────
FROM python:${PYTHON_VERSION}-slim-bookworm AS base

# curl is used by the HEALTHCHECK in both dev and prod.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Pin uv to a specific version for reproducible builds.
# Update the tag when you want to upgrade uv.
# See: https://github.com/astral-sh/uv/releases
COPY --from=ghcr.io/astral-sh/uv:0.10.8 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONFAULTHANDLER=1

WORKDIR /app


# ── dev: all deps including dev group; source bind-mounted from host ──────────
FROM base AS dev

# The dev venv lives at /opt/venv — outside the /app source tree.
# This means the bind-mount (./app:/app/app) never shadows the installed
# packages, eliminating the need for an anonymous Docker volume and the
# painful "docker compose down -v" workflow when dependencies change.
# A named volume (dev_venv:/opt/venv) in compose.dev.yaml preserves the
# venv across container restarts and survives plain `docker compose down`.
ENV UV_PROJECT_ENVIRONMENT=/opt/venv

# Install ALL deps (incl. dev group) without the project itself.
#
# Using --mount=type=bind for pyproject.toml and uv.lock means these files are
# available during this layer's RUN but are NOT copied into the image, keeping
# the layer cache tight: it only invalidates when the lock file changes, not
# when application source changes.
#
# --mount=type=cache keeps the uv download cache across rebuilds.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project

# Activate the venv for all subsequent commands and at container runtime.
ENV PATH="/opt/venv/bin:$PATH"

# Source is bind-mounted via compose.dev.yaml, so hot-reload works without
# rebuilding the image.
CMD ["fastapi", "dev", "app/main.py", "--host", "0.0.0.0", "--port", "8000"]


# ── builder: production deps + project, fully installed ──────────────────────
FROM base AS builder

# Step 1 – install production deps only (cache-friendly layer).
# This layer is rebuilt only when uv.lock or pyproject.toml change.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-dev --no-editable

# Step 2 – copy source and install the project itself.
# This layer is rebuilt whenever any source file changes, but because deps are
# already cached in the layer above, it's very fast.
COPY app /app/app
COPY pyproject.toml uv.lock README.md /app/

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable


# ── prod: lean runtime image ──────────────────────────────────────────────────
FROM python:${PYTHON_VERSION}-slim-bookworm AS prod

# Non-root user – defence-in-depth if the container is ever compromised.
RUN groupadd --system --gid 1001 app \
    && useradd --system --uid 1001 --gid app --no-create-home app

WORKDIR /app

# Copy the pre-built venv from builder – uv itself is NOT included.
COPY --from=builder --chown=app:app /app/.venv /app/.venv

# Copy application source (needed because we run via `fastapi run app/main.py`).
COPY --chown=app:app app /app/app
COPY --chown=app:app pyproject.toml uv.lock README.md /app/

USER app

ENV \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONFAULTHANDLER=1

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -fsSL http://localhost:8000/health || exit 1

# --proxy-headers is required when running behind Caddy (or any reverse proxy)
# so that FastAPI sees the real client IP and correct scheme.
CMD ["fastapi", "run", "app/main.py", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--proxy-headers"]
