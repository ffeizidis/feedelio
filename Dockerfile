# syntax=docker/dockerfile:1

# --- Stage 1: build the SPA -------------------------------------------------
FROM node:24-slim AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# --- Stage 2: resolve Python dependencies -----------------------------------
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS backend
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app
# Dependencies first, so a source-only change does not reinstall the world.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src/ ./src/
RUN uv sync --frozen --no-dev

# --- Stage 3: runtime -------------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime
ENV PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    FEEDELIO_DB_PATH=/data/feedelio.sqlite \
    FEEDELIO_STATIC_DIR=/app/static

RUN useradd --create-home --uid 10001 feedelio \
    && mkdir -p /data && chown feedelio:feedelio /data

WORKDIR /app
COPY --from=backend --chown=feedelio:feedelio /app/.venv /app/.venv
COPY --from=backend --chown=feedelio:feedelio /app/src /app/src
COPY --from=frontend --chown=feedelio:feedelio /build/dist /app/static

USER feedelio
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health')"

CMD ["uvicorn", "feedelio.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
