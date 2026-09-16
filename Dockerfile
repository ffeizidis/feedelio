FROM node:24-slim AS web
WORKDIR /app/web
COPY web/package*.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.11.14 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY feedelio/ ./feedelio/
RUN uv sync --frozen --no-dev --no-editable && \
    useradd --uid 10001 --create-home feedelio && \
    mkdir -p /data && chown feedelio:feedelio /data
COPY --from=web /app/web/dist ./web/dist
COPY deploy/supervisord.conf /app/supervisord.conf
ENV PATH="/app/.venv/bin:$PATH" FEEDELIO_DATA=/data FEEDELIO_STATIC=/app/web/dist PYTHONUNBUFFERED=1
USER feedelio
VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)"
CMD ["supervisord", "-c", "/app/supervisord.conf"]
