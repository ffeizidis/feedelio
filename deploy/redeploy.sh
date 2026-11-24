#!/bin/sh
# Rebuild the image from this checkout and replace the running container,
# keeping the feedelio-data volume. Uses Compose when the plugin is present.
set -eu
cd "$(dirname "$0")/.."
if docker compose version >/dev/null 2>&1; then
  docker compose build
  project=${COMPOSE_PROJECT_NAME:-$(basename "$PWD")}
  if docker volume inspect "${project}_feedelio-data" >/dev/null 2>&1; then
    docker compose stop
    deploy/migrate-volume.sh "${project}_feedelio-data" feedelio-data feedelio:local
  fi
  exec docker compose up -d
fi
docker build -t feedelio:local .
docker stop -t 60 feedelio >/dev/null 2>&1 || true
docker rm feedelio >/dev/null 2>&1 || true
exec docker run -d --name feedelio --restart unless-stopped \
  -e FEEDELIO_NO_AUTH="${FEEDELIO_NO_AUTH:-1}" -e FEEDELIO_TOKEN="${FEEDELIO_TOKEN:-}" \
  -e FEEDELIO_ALLOW_PRIVATE_NETWORK="${FEEDELIO_ALLOW_PRIVATE_NETWORK:-0}" \
  -p 127.0.0.1:8000:8000 -v feedelio-data:/data feedelio:local
