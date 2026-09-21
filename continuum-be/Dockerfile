# syntax=docker/dockerfile:1.7

# ---------------------------------------------------------------------------
# Stage 1 — build the virtualenv
# ---------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS builder

# Must be >= the uv that generated uv.lock (revision 3), or --frozen fails.
COPY --from=ghcr.io/astral-sh/uv:0.11.7 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependencies first, in their own layer. Application code changes far more
# often than the lockfile, so this keeps rebuilds fast.
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

COPY src/ ./src/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev


# ---------------------------------------------------------------------------
# Stage 2 — runtime
# ---------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 continuum

WORKDIR /app

COPY --from=builder --chown=continuum:continuum /app/.venv /app/.venv
COPY --chown=continuum:continuum src/ ./src/
COPY --chown=continuum:continuum scripts/ ./scripts/

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    ENVIRONMENT=docker \
    HOST=0.0.0.0 \
    PORT=8000

USER continuum
EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=40s --retries=5 \
    CMD curl -fsS "http://localhost:${PORT}/api/v1/health" || exit 1

# The app starts itself. Collection bootstrap happens in the FastAPI lifespan,
# so there is no migrate/init step to run separately.
CMD ["sh", "-c", "exec uvicorn continuum.main:app --host ${HOST} --port ${PORT}"]
